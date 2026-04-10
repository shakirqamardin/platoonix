"""Multi-tier delivery verification: QR, SMS, GPS photo, or manual loader confirm."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from sqlalchemy.orm import Session

from app import models
from app.services.gps_verification import extract_gps_from_photo, verify_gps_location
from app.services.in_app_notifications import loader_office_user_ids, record_user_notifications
from app.services.job_completion import finalize_job_with_pod_upload
from app.services.qr_verification import verify_qr_code
from app.services.sms_verification import mark_sms_code_used, verify_sms_code

logger = logging.getLogger(__name__)


def _notify_loader_pending_verification(db: Session, job: models.BackhaulJob, load: models.Load) -> None:
    try:
        uids = loader_office_user_ids(db, load.loader_id)
        jref = job.display_number
        route = f"{load.pickup_postcode} → {load.delivery_postcode}"
        record_user_notifications(
            db,
            uids,
            title=f"Confirm delivery — {jref}",
            body=f"Proof of delivery uploaded for {route}. Please confirm in the app within 48 hours.",
            link_url="/?section=loads",
            kind="delivery_verify",
            priority="important",
            commit=False,
        )
    except Exception:
        logger.exception("notify loader pending verification")


def _submit_pending_manual(
    db: Session,
    job: models.BackhaulJob,
    load: models.Load,
    file_url: str,
    notes: Optional[str],
    method: str,
) -> None:
    now = datetime.now(timezone.utc)
    pod = models.POD(
        backhaul_job_id=job.id,
        file_url=file_url,
        notes=notes,
        status=models.PODStatusEnum.PENDING.value,
    )
    job.verification_method = (method or "manual")[:20]
    job.verification_status = "awaiting_loader"
    job.auto_confirm_deadline = now + timedelta(hours=48)
    job.reached_delivery_at = job.reached_delivery_at or now
    db.add(pod)
    db.add(job)
    _notify_loader_pending_verification(db, job, load)


def process_driver_delivery(
    db: Session,
    job: models.BackhaulJob,
    load: models.Load,
    file_url: str,
    notes: Optional[str],
    verification_method: str,
    qr_code: Optional[str],
    sms_code: Optional[str],
    saved_photo_path: str,
) -> Tuple[str, Optional[str]]:
    """
    Returns (outcome, error_message).
    outcome: instant_ok | pending_manual | error
    """
    now = datetime.now(timezone.utc)
    method = (verification_method or "manual").strip().lower()
    job.reached_delivery_at = job.reached_delivery_at or now

    if method == "qr_code":
        if not qr_code or not verify_qr_code(qr_code, load.id, db):
            return ("error", "Invalid or already used QR code")
        load.qr_code_used = True
        load.qr_code_used_at = now
        db.add(load)
        job.verification_method = "qr_code"
        job.verification_status = "instant_verified"
        db.add(job)
        err = finalize_job_with_pod_upload(db, job, file_url, notes)
        if err:
            return ("error", err)
        return ("instant_ok", None)

    if method == "sms_code":
        if not sms_code or not verify_sms_code(sms_code, load.id, db):
            return ("error", "Invalid or expired SMS code")
        mark_sms_code_used(load, db)
        job.verification_method = "sms_code"
        job.verification_status = "instant_verified"
        db.add(job)
        err = finalize_job_with_pod_upload(db, job, file_url, notes)
        if err:
            return ("error", err)
        return ("instant_ok", None)

    if method == "gps_photo":
        gps_data = extract_gps_from_photo(saved_photo_path)
        if gps_data:
            lat, lng, ts = gps_data
            job.delivery_gps_lat = lat
            job.delivery_gps_lng = lng
            job.delivery_photo_timestamp = ts if ts else None
            db.add(job)
            if verify_gps_location(lat, lng, load.delivery_postcode, ts):
                job.gps_verified = True
                job.verification_method = "gps_photo"
                job.verification_status = "instant_verified"
                db.add(job)
                err = finalize_job_with_pod_upload(db, job, file_url, notes)
                if err:
                    return ("error", err)
                return ("instant_ok", None)
        _submit_pending_manual(db, job, load, file_url, notes, "gps_photo")
        return ("pending_manual", None)

    # manual (or unknown → treat as manual)
    _submit_pending_manual(db, job, load, file_url, notes, "manual")
    return ("pending_manual", None)


def run_auto_confirm_due_deliveries(db: Session) -> int:
    """Auto-confirm pending deliveries past deadline (48h). Returns count processed."""
    from app.services.job_completion import confirm_pending_pod_and_release

    now = datetime.now(timezone.utc)
    job_ids = [
        jid
        for (jid,) in (
            db.query(models.BackhaulJob.id)
            .filter(models.BackhaulJob.verification_status == "awaiting_loader")
            .filter(models.BackhaulJob.completed_at.is_(None))
            .filter(models.BackhaulJob.auto_confirm_deadline.isnot(None))
            .filter(models.BackhaulJob.auto_confirm_deadline <= now)
            .limit(30)
            .all()
        )
    ]
    n = 0
    for jid in job_ids:
        job = db.get(models.BackhaulJob, jid)
        if not job:
            continue
        pod = (
            db.query(models.POD)
            .filter(models.POD.backhaul_job_id == job.id)
            .filter(models.POD.status == models.PODStatusEnum.PENDING.value)
            .order_by(models.POD.created_at.desc())
            .first()
        )
        if not pod:
            continue
        err = confirm_pending_pod_and_release(db, job, pod, auto_confirmed=True)
        if err:
            logger.warning("auto_confirm job %s: %s", job.id, err)
            db.rollback()
            continue
        try:
            db.commit()
            n += 1
        except Exception:
            logger.exception("commit auto_confirm job %s", job.id)
            db.rollback()
    return n
