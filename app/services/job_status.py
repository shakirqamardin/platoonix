"""Shared driver/office job milestone updates (same rules as /api/driver/jobs/{id}/status)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app import models


def apply_driver_status_milestone(db: Session, job: models.BackhaulJob, status_val: str) -> Optional[str]:
    """
    Set one milestone on the job. Does not commit.
    Returns None on success, or an error message string on failure.
    """
    now = datetime.now(timezone.utc)
    s = (status_val or "").strip().lower()
    if job.completed_at:
        return "Job already completed. Status updates are locked."
    if s == "reached_pickup":
        if job.reached_pickup_at:
            return None
        job.reached_pickup_at = now
    elif s == "collected":
        if not job.reached_pickup_at:
            return "Mark 'Reached collection' first."
        if job.collected_at:
            return None
        payment = (
            db.query(models.Payment)
            .filter(models.Payment.backhaul_job_id == job.id)
            .order_by(models.Payment.created_at.asc())
            .first()
        )
        if payment and payment.status == models.PaymentStatusEnum.RESERVED.value:
            from app.services.stripe_loader_charge import try_charge_loader_for_job

            ok_charge, charge_err = try_charge_loader_for_job(payment, db)
            if not ok_charge:
                return f"Loader payment failed: {charge_err}. Fix the card or Stripe customer, then retry."
            payment.status = models.PaymentStatusEnum.CAPTURED.value
            db.add(payment)
        job.collected_at = now
    elif s == "departed_pickup":
        if not job.collected_at:
            return "Mark 'Collected' first."
        if job.departed_pickup_at:
            return None
        job.departed_pickup_at = now
    elif s == "reached_delivery":
        if not job.departed_pickup_at:
            return "Mark 'Departed' first."
        if job.reached_delivery_at:
            return None
        job.reached_delivery_at = now
    else:
        return "status must be one of: reached_pickup, collected, departed_pickup, reached_delivery"
    db.add(job)
    return None
