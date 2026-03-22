"""
Send transactional email via SendGrid API. Falls back to SMTP if SendGrid not configured.
"""
from sqlalchemy.orm import Session

from app import models
from app.config import get_settings


def send_email(to_email: str, subject: str, body_text: str) -> bool:
    """
    Send a plain-text email using SendGrid API (preferred) or SMTP fallback.
    Returns True if sent, False if skipped (no config) or failed.
    """
    return False  # Temporarily disable all email
    
    settings = get_settings()
    # ... rest of code
    
    # Try SendGrid API first
    sendgrid_key = getattr(settings, 'sendgrid_api_key', None)
    if sendgrid_key:
        return _send_via_sendgrid(to_email, subject, body_text, sendgrid_key, settings.smtp_from_email)
    
    # Fallback to SMTP
    if not settings.smtp_host or not settings.smtp_user or not settings.smtp_password:
        return False
    
    to_email = (to_email or "").strip()
    if not to_email:
        return False
    
    try:
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = settings.smtp_from_email
        msg["To"] = to_email
        msg.attach(MIMEText(body_text, "plain", "utf-8"))

        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
            server.starttls()
            server.login(settings.smtp_user, settings.smtp_password)
            server.sendmail(settings.smtp_from_email, [to_email], msg.as_string())
        return True
    except Exception as e:
        print(f"[EMAIL] SMTP error: {e}")
        return False


def _send_via_sendgrid(to_email: str, subject: str, body_text: str, api_key: str, from_email: str) -> bool:
    """Send email using SendGrid API."""
    to_email = (to_email or "").strip()
    if not to_email:
        return False
    
    try:
        import requests
        
        data = {
            "personalizations": [{"to": [{"email": to_email}]}],
            "from": {"email": from_email},
            "subject": subject,
            "content": [{"type": "text/plain", "value": body_text}]
        }
        
        response = requests.post(
            "https://api.sendgrid.com/v3/mail/send",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            json=data,
            timeout=10
        )
        
        if response.status_code == 202:
            print(f"[EMAIL] SendGrid: Email sent to {to_email}")
            return True
        else:
            print(f"[EMAIL] SendGrid error: {response.status_code} - {response.text}")
            return False
            
    except Exception as e:
        print(f"[EMAIL] SendGrid exception: {e}")
        return False


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
