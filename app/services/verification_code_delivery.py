"""Issue 6-digit delivery codes on the load and notify loaders (in-app + email) — no SMS cost."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app import models
from app.services.email_sender import send_email
from app.services.in_app_notifications import loader_office_user_ids, record_user_notifications
from app.services.sms_verification import generate_sms_code

logger = logging.getLogger(__name__)


def issue_delivery_verification_code(db: Session, job: models.BackhaulJob) -> tuple[bool, str]:
    """
    Generate a code on the load, notify loader office users (bell), and email the loader company.
    Does not commit. Returns (ok, message for driver).
    """
    load = db.get(models.Load, job.load_id)
    if not load or not load.loader_id:
        return False, "No loader for load"
    loader = db.get(models.Loader, load.loader_id)
    if not loader:
        return False, "Loader not found"

    code = generate_sms_code()
    now = datetime.now(timezone.utc)
    load.sms_verification_code = code
    load.sms_code_sent_at = now
    load.sms_code_expires_at = now + timedelta(minutes=15)
    load.sms_code_used = False
    db.add(load)

    jref = job.display_number
    route = f"{load.pickup_postcode} → {load.delivery_postcode}"
    uids = loader_office_user_ids(db, loader.id)
    body = (
        f"Code: {code} (valid 15 min) — {jref}: {route}. "
        "Give this code to the driver only after you confirm delivery."
    )
    record_user_notifications(
        db,
        uids,
        title="Delivery verification code",
        body=body,
        link_url="/?section=loads",
        kind="verification_code",
        priority="critical",
        commit=False,
    )

    driver = db.get(models.Driver, job.driver_id) if job.driver_id else None
    driver_name = driver.name if driver else "Assigned driver"
    email_body = (
        f"Your driver has requested a delivery verification code.\n\n"
        f"CODE: {code}\n\n"
        f"Job: {jref}\n"
        f"Route: {route}\n"
        f"Driver: {driver_name}\n\n"
        "Give this code to the driver only after you have confirmed the goods are at your location.\n\n"
        "Code expires in 15 minutes.\n\n"
        "Platoonix\nsupport@platoonix.co.uk"
    )
    em = (loader.contact_email or "").strip()
    if em:
        try:
            send_email(em, f"Delivery verification code — {jref}", email_body)
        except Exception as e:
            logger.warning("verification code email failed: %s", e)

    if not uids and not em:
        return False, "Loader has no office users or email on file"

    parts = []
    if uids:
        parts.append("in-app notification")
    if em:
        parts.append("email")
    return True, "Code sent to loader via " + " and ".join(parts)
