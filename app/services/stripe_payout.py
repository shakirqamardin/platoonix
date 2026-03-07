"""
Stripe Connect payouts: transfer net_payout_gbp to haulier's connected account
when a payment is marked paid (e.g. on POD confirm). No-op if Stripe not configured
or haulier has no payment_account_id.
"""
from typing import Optional, Tuple

from sqlalchemy.orm import Session

from app import models
from app.config import get_settings


def try_payout_to_haulier(payment: models.Payment, db: Session) -> Tuple[bool, Optional[str]]:
    """
    If Stripe is configured and the haulier has a Connect account id (payment_account_id),
    create a Transfer to that account for payment.net_payout_gbp.
    Returns (success, error_message). success=True means transferred or skipped (no config);
    success=False means Stripe error (error_message set).
    """
    settings = get_settings()
    if not settings.stripe_secret_key or not settings.stripe_secret_key.strip():
        return (True, None)  # skip, no config

    job = db.get(models.BackhaulJob, payment.backhaul_job_id)
    if not job:
        return (True, None)
    vehicle = db.get(models.Vehicle, job.vehicle_id)
    if not vehicle:
        return (True, None)
    haulier = db.get(models.Haulier, vehicle.haulier_id)
    if not haulier or not (haulier.payment_account_id or "").strip():
        return (True, None)  # no Connect account, skip

    account_id = (haulier.payment_account_id or "").strip()
    if not account_id.lower().startswith("acct_"):
        return (False, "Haulier payment_account_id should be a Stripe Connect account (acct_...)")

    amount_pence = int(round((payment.net_payout_gbp or 0) * 100))
    if amount_pence <= 0:
        return (True, None)

    try:
        import stripe
        stripe.api_key = settings.stripe_secret_key
        transfer = stripe.Transfer.create(
            amount=amount_pence,
            currency="gbp",
            destination=account_id,
            description=f"Platoonix payout for job {job.id}",
        )
        if getattr(transfer, "id", None):
            payment.provider_payment_id = transfer.id
        return (True, None)
    except Exception as e:
        return (False, str(e))
