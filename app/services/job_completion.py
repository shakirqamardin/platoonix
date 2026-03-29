"""Finalize delivery: POD + completed_at + payout (same as driver ePOD submit)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app import models


def finalize_job_with_pod_upload(
    db: Session,
    job: models.BackhaulJob,
    file_url: str,
    notes: Optional[str],
) -> Optional[str]:
    """
    Create confirmed POD, set completed_at, run Stripe payout if payment CAPTURED.
    Refreshes vehicle availability. Caller should not commit before this returns success.
    Returns None on success, or error message.
    """
    pod = models.POD(backhaul_job_id=job.id, file_url=file_url, notes=notes)
    db.add(pod)
    db.flush()
    pod.status = models.PODStatusEnum.CONFIRMED.value
    pod.confirmed_at = datetime.now(timezone.utc)
    job.completed_at = datetime.now(timezone.utc)
    db.add(job)
    db.flush()

    payment = (
        db.query(models.Payment)
        .filter(models.Payment.backhaul_job_id == job.id)
        .order_by(models.Payment.created_at.asc())
        .first()
    )
    if payment:
        if payment.status == models.PaymentStatusEnum.RESERVED.value:
            return "Confirm+collection+first"
        if payment.status == models.PaymentStatusEnum.CAPTURED.value:
            from app.services.stripe_payout import try_payout_to_haulier

            ok_pay, pay_err = try_payout_to_haulier(payment, db)
            if not ok_pay and pay_err:
                return f"Payout failed: {pay_err}"
            payment.status = models.PaymentStatusEnum.PAID_OUT.value
            db.add(payment)

    from app.services import vehicle_availability as vehicle_availability_svc

    vehicle_availability_svc.refresh_vehicle_availability(db, job.vehicle_id)
    return None
