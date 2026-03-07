"""
Send transactional email via SMTP. No-op if SMTP is not configured.
"""
from sqlalchemy.orm import Session

from app import models
from app.config import get_settings


def send_email(to_email: str, subject: str, body_text: str) -> bool:
    """
    Send a plain-text email. Returns True if sent, False if skipped (no config) or failed.
    """
    settings = get_settings()
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
    except Exception:
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
