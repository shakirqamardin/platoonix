"""
Send transactional email via SendGrid API. Falls back to SMTP if SendGrid not configured.
"""
import logging
from typing import Optional, Union
from urllib.parse import urlparse

from fastapi import BackgroundTasks
from sqlalchemy.orm import Session

from app import models
from app.config import get_settings

logger = logging.getLogger(__name__)


def send_email(to_email: str, subject: str, body_text: str) -> bool:
    """
    Send a plain-text email using SendGrid API (preferred) or SMTP fallback.
    Returns True if sent, False if skipped (no config) or failed.
    """
    settings = get_settings()
    to_email = (to_email or "").strip()
    if not to_email:
        return False

    # SendGrid rejects null/empty content; keep JSON-safe strings
    subject = (subject or "").strip() or "(no subject)"
    body_text = str(body_text if body_text is not None else "")
    from_email = (settings.smtp_from_email or "").strip()
    if not from_email:
        print("[EMAIL] smtp_from_email is empty; set SMTP_FROM_EMAIL / verified SendGrid sender.")
        return False

    sendgrid_key = getattr(settings, "sendgrid_api_key", None)
    if sendgrid_key and str(sendgrid_key).strip():
        return _send_via_sendgrid(to_email, subject, body_text, str(sendgrid_key).strip(), from_email)

    if not settings.smtp_host or not settings.smtp_user or not settings.smtp_password:
        return False

    try:
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = from_email
        msg["To"] = to_email
        msg.attach(MIMEText(body_text, "plain", "utf-8"))

        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
            server.starttls()
            server.login(settings.smtp_user, settings.smtp_password)
            server.sendmail(from_email, [to_email], msg.as_string())
        return True
    except Exception as e:
        print(f"[EMAIL] SMTP error: {e}")
        return False


def _send_via_sendgrid(to_email: str, subject: str, body_text: str, api_key: str, from_email: str) -> bool:
    """Send email using SendGrid API."""
    try:
        import requests
        import time
        settings = get_settings()

        data = {
            "personalizations": [{"to": [{"email": to_email}]}],
            "from": {"email": from_email},
            "subject": subject,
            "content": [{"type": "text/plain", "value": body_text}],
        }
        retries = max(0, int(getattr(settings, "email_retry_count", 2)))
        timeout = max(5, int(getattr(settings, "email_send_timeout_seconds", 15)))
        transient_codes = {408, 425, 429, 500, 502, 503, 504}
        for attempt in range(retries + 1):
            response = requests.post(
                "https://api.sendgrid.com/v3/mail/send",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=data,
                timeout=timeout,
            )
            if response.status_code == 202:
                print(f"[EMAIL] SendGrid: queued for {to_email} (from {from_email})")
                return True
            if response.status_code in transient_codes and attempt < retries:
                # Short exponential backoff for transient gateway/rate-limit failures.
                time.sleep(0.5 * (2 ** attempt))
                continue
            detail = response.text
            if response.status_code == 403 and "Sender Identity" in detail:
                print(
                    f"[EMAIL] SendGrid 403: from address {from_email!r} is not a verified "
                    "Sender Identity in SendGrid. Set SMTP_FROM_EMAIL in Railway to a verified "
                    "sender (Settings → Sender Authentication), then redeploy."
                )
            print(f"[EMAIL] SendGrid error: {response.status_code} - {detail}")
            return False

    except Exception as e:
        print(f"[EMAIL] SendGrid exception: {e}")
        return False


def _run_loader_interest_email(interest_id: int) -> None:
    """Background task: own DB session; request session must not be used after return."""
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        interest = db.get(models.LoadInterest, interest_id)
        if interest:
            email_loader_interest(interest, db)
    finally:
        db.close()


def _run_haulier_job_email(job_id: int) -> None:
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        job = db.get(models.BackhaulJob, job_id)
        if job:
            email_haulier_job_created(job, db)
    finally:
        db.close()


def schedule_loader_interest_email(background_tasks: BackgroundTasks, interest_id: int) -> None:
    """Queue loader notification after interest is saved (non-blocking)."""
    if interest_id:
        background_tasks.add_task(_run_loader_interest_email, interest_id)


def schedule_haulier_job_email(background_tasks: BackgroundTasks, job_id: int) -> None:
    """Queue haulier notification after job is created (non-blocking)."""
    if job_id:
        background_tasks.add_task(_run_haulier_job_email, job_id)


def email_loader_interest(interest: "models.LoadInterest", db: Session) -> bool:
    """
    Send "A haulier has shown interest in your load" to the loader who owns the load/planned load.
    Returns True if sent, False otherwise.
    """
    loader_id = None
    route_desc = ""
    if interest.load_id:
        load = db.get(models.Load, interest.load_id)
        if not load:
            return False
        loader_id = load.loader_id
        route_desc = f"{load.pickup_postcode} → {load.delivery_postcode}"
    elif interest.planned_load_id:
        pl = db.get(models.PlannedLoad, interest.planned_load_id)
        if not pl:
            return False
        loader_id = pl.loader_id
        route_desc = f"{pl.pickup_postcode} → {pl.delivery_postcode} (planned)"
    if not loader_id:
        return False
    user = db.query(models.User).filter(models.User.loader_id == loader_id).first()
    if not user or not user.email:
        return False
    haulier = db.get(models.Haulier, interest.haulier_id)
    haulier_name = haulier.name if haulier else "A haulier"
    vehicle = db.get(models.Vehicle, interest.vehicle_id)
    reg = vehicle.registration if vehicle else f"vehicle #{interest.vehicle_id}"
    subject = "Platoonix: a haulier has shown interest in your load"
    body = (
        f"{haulier_name} (vehicle {reg}) has shown interest in your load: {route_desc}\n\n"
        "Log in to Platoonix to accept or decline.\n"
    )
    return send_email(user.email, subject, body)


def email_haulier_job_created(job: "models.BackhaulJob", db: Session) -> bool:
    """
    Send "Your interest was accepted - job created!" to the haulier.
    Returns True if sent, False otherwise.
    """
    vehicle = db.get(models.Vehicle, job.vehicle_id)
    if not vehicle or not vehicle.haulier_id:
        return False

    user = db.query(models.User).filter(models.User.haulier_id == vehicle.haulier_id).first()
    if not user or not user.email:
        return False

    load = db.get(models.Load, job.load_id)
    if not load:
        return False

    route_desc = f"{load.pickup_postcode} → {load.delivery_postcode}"

    subject = "Platoonix: Your interest was accepted - Job created!"
    body = (
        f"Great news! The loader has accepted your interest.\n\n"
        f"Job #{job.id} has been created:\n"
        f"Route: {route_desc}\n"
        f"Vehicle: {vehicle.registration}\n\n"
        f"Log in to Platoonix to view job details and start tracking.\n"
    )
    return send_email(user.email, subject, body)


def _registration_public_origin(dashboard_link: str) -> str:
    p = urlparse((dashboard_link or "").strip() or "https://platoonix.co.uk/")
    if p.scheme and p.netloc:
        return f"{p.scheme}://{p.netloc}"
    return "https://platoonix.co.uk"


def _run_registration_emails(
    user_email: str,
    user_name: str,
    user_type: str,
    company_name: str,
    dashboard_link: str,
    tutorial_link: str,
    vehicle_setup_link: str,
    admin_panel_link: str,
    registered_at_iso: str,
    user_id: Optional[int] = None,
    contact_phone: Optional[str] = None,
    driver_id: Optional[int] = None,
) -> None:
    """Background task: send user welcome + admin registration notification + optional in-app welcome."""
    from app.database import SessionLocal
    from app.services.in_app_notifications import record_user_notifications

    role = (user_type or "").strip().lower()
    safe_name = (user_name or "there").strip() or "there"
    safe_company = (company_name or "").strip() or "N/A"
    safe_dashboard = (dashboard_link or "").strip()
    safe_tutorial = (tutorial_link or "").strip()
    safe_vehicle_setup = (vehicle_setup_link or "").strip()
    safe_admin_panel = (admin_panel_link or "").strip()
    safe_timestamp = (registered_at_iso or "").strip() or "N/A"
    safe_phone = (contact_phone or "").strip() or "Not provided"
    origin = _registration_public_origin(safe_dashboard)
    pricing_link = f"{origin}/pricing"
    api_docs_link = f"{origin}/api-docs"
    driver_login_link = f"{origin}/driver-login"
    login_link = f"{origin}/login"
    settings = get_settings()
    loader_min = float(settings.loader_flat_fee_gbp or 5.0)
    loader_pct = float(settings.loader_fee_percent_of_load or 2.0)
    haulier_keep_pct = max(0.0, 100.0 - float(settings.platform_fee_percent or 8.0))

    db_count: Union[int, str] = "?"
    try:
        _db = SessionLocal()
        try:
            db_count = _db.query(models.User).count()
        finally:
            _db.close()
    except Exception:
        logger.exception("registration email: user count failed")

    if role == "loader":
        welcome_subject = "Welcome to Platoonix - Let's Post Your First Load!"
        welcome_body = f"""Dear {safe_name},

Welcome to Platoonix!

You're now registered as a loader. Here's how to get started:

STEP 1: Add a Payment Card
- Go to Settings → Company & Billing
- Click "Add Payment Card"
- Enter your card details securely

STEP 2: Post Your First Load
- Click "Add Load" in your dashboard
- Enter pickup and delivery postcodes
- Choose vehicle type
- Set your budget
- Click "Post Load"

STEP 3: Wait for Matches
- Hauliers will see your load
- You'll get notifications when interested
- Review their profile and ratings
- Accept the best match!

NEED HELP?
Email: support@platoonix.co.uk
View Pricing: {pricing_link}
API Docs: {api_docs_link}

Your platform fee: £{loader_min:.0f} minimum OR {loader_pct:.0f}% of load value (whichever is greater)

Questions? Just reply to this email!

Best regards,
The Platoonix Team

P.S. Your first load is the hardest - after that it's easy"""
        try:
            send_email(user_email, welcome_subject, welcome_body)
        except Exception as e:
            logger.error("Failed to send loader welcome email: %s", e)
    elif role == "haulier":
        welcome_subject = "Welcome to Platoonix - Find Your First Backhaul!"
        welcome_body = f"""Dear {safe_name},

Welcome to Platoonix!

You're now registered as a haulier. Here's how to find backhauls:

STEP 1: Add Your Vehicles
- Click "Vehicles" in your dashboard
- Add your fleet details
- Verify with DVLA (instant check)
- Add insurance information

STEP 2: Find Your First Backhaul
- Click "Find Backhaul"
- Enter where you are (current location)
- Enter where you're heading (base/destination)
- See loads within 25 miles of your route!

STEP 3: Accept & Earn
- Review load details
- Check payment (budget minus {float(settings.platform_fee_percent or 8.0):.0f}% platform fee)
- Click "Accept"
- Coordinate pickup
- Get paid after delivery!

BANK DETAILS FOR PAYOUTS:
When you complete your first job, you'll be asked to add your bank details
(sort code + account number) for payouts. It's a one-time 2-minute setup.

NEED HELP?
Email: support@platoonix.co.uk
View Pricing: {pricing_link}
API Docs: {api_docs_link}

You keep {haulier_keep_pct:.0f}% of every load value. We take {float(settings.platform_fee_percent or 8.0):.0f}% as our platform fee.

Questions? Just reply to this email!

Best regards,
The Platoonix Team

P.S. Turn those empty miles into profit!"""
        try:
            send_email(user_email, welcome_subject, welcome_body)
        except Exception as e:
            logger.error("Failed to send haulier welcome email: %s", e)
    elif role == "driver":
        welcome_subject = "Welcome to Platoonix - Driver Access"
        welcome_body = f"""Dear {safe_name},

Welcome to Platoonix!

Your haulier has added you as a driver. You can now:

- View assigned jobs in your driver dashboard
- Update job status (collected, in transit, delivered)
- Upload proof of delivery (ePOD)
- Track your deliveries

Login at: {driver_login_link}

Need help? Contact your haulier or email support@platoonix.co.uk

Best regards,
The Platoonix Team"""
        try:
            send_email(user_email, welcome_subject, welcome_body)
        except Exception as e:
            logger.error("Failed to send driver welcome email: %s", e)
    else:
        welcome_subject = "Welcome to Platoonix"
        welcome_body = f"""Dear {user_email},

Welcome to Platoonix!

Your account has been created. Please complete your profile to get started.

Login at: {login_link}

Need help? Email support@platoonix.co.uk

Best regards,
The Platoonix Team"""
        try:
            send_email(user_email, welcome_subject, welcome_body)
        except Exception as e:
            logger.error("Failed to send generic welcome email: %s", e)

    admin_subject = f"New {role.title() if role else 'Unknown'} Registration - Platoonix"
    admin_body = f"""New user registered on Platoonix!

USER DETAILS:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Name: {safe_name}
Email: {user_email}
Type: {role.title() if role else 'Unknown'}
Company: {safe_company}
Phone: {safe_phone}
Registered: {safe_timestamp}

ACCOUNT ID: {user_id or 'N/A'}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

View in admin panel: {safe_admin_panel}

NEXT STEPS:
- User received welcome email with onboarding instructions
- Monitor their first activity
- Reach out if they need help getting started

This is registration #{db_count} on the platform.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Platoonix Admin Alerts
"""
    try:
        send_email("platoonixltd@gmail.com", admin_subject, admin_body)
    except Exception as e:
        logger.error("Failed to send admin registration notification: %s", e)

    if user_id and role in ("loader", "haulier"):
        try:
            db = SessionLocal()
            try:
                record_user_notifications(
                    db,
                    [user_id],
                    title="Welcome to Platoonix!",
                    body=f"Your {role} account is ready. Open Company to finish profile and billing.",
                    link_url=f"{origin}/?section=company",
                    kind="welcome",
                    priority="normal",
                    commit=True,
                )
            finally:
                db.close()
        except Exception as e:
            logger.error("Failed to create welcome in-app notification: %s", e)

    if role == "driver" and driver_id:
        try:
            db = SessionLocal()
            try:
                db.add(
                    models.AppNotification(
                        user_id=None,
                        driver_id=driver_id,
                        title="Welcome to Platoonix!",
                        body="Your driver account is ready. Sign in at Driver login and open Run job when assigned.",
                        link_url=f"{origin}/driver",
                        kind="welcome",
                        priority="normal",
                    )
                )
                db.commit()
            finally:
                db.close()
        except Exception as e:
            logger.error("Failed to create driver welcome in-app notification: %s", e)


def schedule_registration_emails(
    background_tasks: BackgroundTasks,
    *,
    user_email: str,
    user_name: str,
    user_type: str,
    company_name: str,
    dashboard_link: str,
    tutorial_link: str,
    vehicle_setup_link: str,
    admin_panel_link: str,
    registered_at_iso: str,
    user_id: Optional[int] = None,
    contact_phone: Optional[str] = None,
    driver_id: Optional[int] = None,
) -> None:
    """Queue welcome + admin registration emails (non-blocking)."""
    if not (user_email or "").strip():
        return
    background_tasks.add_task(
        _run_registration_emails,
        user_email,
        user_name,
        user_type,
        company_name,
        dashboard_link,
        tutorial_link,
        vehicle_setup_link,
        admin_panel_link,
        registered_at_iso,
        user_id,
        contact_phone,
        driver_id,
    )
