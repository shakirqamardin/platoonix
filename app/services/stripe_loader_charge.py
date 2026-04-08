"""
Charge the loader (load value + flat platform fee) via Stripe at collection (pickup),
when the haulier confirms the load was collected — not at delivery.

Requires Loader.stripe_customer_id and a default payment method on that customer.
Skips silently if Stripe is not configured or loader has no customer (DB-only mode).
"""
from typing import Optional, Tuple

from sqlalchemy.orm import Session

from app import models
from app.config import get_settings


def try_charge_loader_for_job(payment: models.Payment, db: Session) -> Tuple[bool, Optional[str]]:
    """
    Create a PaymentIntent on the platform account for (amount_gbp + flat_fee_gbp).
    Call at collection (pickup), not at delivery.

    Returns (success, error_message). success=False only when Stripe is configured and charge fails.
    """
    settings = get_settings()
    if not settings.stripe_secret_key or not str(settings.stripe_secret_key).strip():
        return (True, None)

    if (payment.loader_stripe_payment_intent_id or "").strip():
        return (True, None)

    job = db.get(models.BackhaulJob, payment.backhaul_job_id)
    if not job:
        return (True, None)
    load = db.get(models.Load, job.load_id)
    if not load or not load.loader_id:
        return (True, None)
    loader = db.get(models.Loader, load.loader_id)
    if not loader or not (loader.stripe_customer_id or "").strip():
        print(
            "[STRIPE] Loader charge skipped: set Loader.stripe_customer_id (Stripe cus_...) "
            "and a default payment method on that customer to charge on delivery."
        )
        return (True, None)

    total = float(payment.amount_gbp or 0) + float(payment.flat_fee_gbp or 0)
    amount_pence = int(round(total * 100))
    if amount_pence <= 0:
        return (True, None)

    try:
        import stripe

        stripe.api_key = settings.stripe_secret_key.strip()
        customer_id = loader.stripe_customer_id.strip()
        customer = stripe.Customer.retrieve(customer_id)
        pm = None
        inv = getattr(customer, "invoice_settings", None)
        if inv is not None:
            pm = inv.get("default_payment_method") if hasattr(inv, "get") else getattr(inv, "default_payment_method", None)
        if not pm:
            print(
                "[STRIPE] Loader charge skipped: no default payment method on Stripe customer "
                f"{customer_id}. Add a card (Checkout or Customer Portal)."
            )
            return (True, None)

        pm_id = pm if isinstance(pm, str) else getattr(pm, "id", None) or (pm.get("id") if isinstance(pm, dict) else None)
        if not pm_id:
            return (True, None)

        intent = stripe.PaymentIntent.create(
            amount=amount_pence,
            currency="gbp",
            customer=customer_id,
            payment_method=pm_id,
            off_session=True,
            confirm=True,
            description=f"Platoonix job #{job.id} (load + platform fee)",
            metadata={"backhaul_job_id": str(job.id), "payment_id": str(payment.id)},
        )
        pid = getattr(intent, "id", None)
        if pid:
            payment.loader_stripe_payment_intent_id = pid
            db.add(payment)
        print(f"[STRIPE] Charged loader £{total:.2f} (PaymentIntent {pid}) for job {job.id}")
        return (True, None)
    except Exception as e:
        err = str(e)
        print(f"[STRIPE] Loader charge failed: {err}")
        return (False, err)


def try_refund_loader_charge(payment: models.Payment, db: Session) -> Tuple[bool, Optional[str]]:
    """
    Refund a captured loader PaymentIntent when a load is cancelled after charge.
    Skips if no Stripe key, no PI id, or payment not captured. Returns (ok, error_message).
    """
    settings = get_settings()
    if not settings.stripe_secret_key or not str(settings.stripe_secret_key).strip():
        return (True, None)
    pid = (payment.loader_stripe_payment_intent_id or "").strip()
    if not pid:
        return (True, None)
    if (payment.status or "").strip().lower() != models.PaymentStatusEnum.CAPTURED.value:
        return (True, None)
    try:
        import stripe

        stripe.api_key = settings.stripe_secret_key.strip()
        intent = stripe.PaymentIntent.retrieve(pid, expand=["latest_charge"])
        ch_id = getattr(intent, "latest_charge", None)
        if ch_id is not None and not isinstance(ch_id, str):
            ch_id = getattr(ch_id, "id", None)
        if isinstance(ch_id, str) and ch_id.startswith("ch_"):
            pass
        else:
            charges = getattr(intent, "charges", None)
            data = getattr(charges, "data", None) if charges is not None else None
            if not data and isinstance(intent, dict):
                data = (intent.get("charges") or {}).get("data")
            if not data:
                return (True, None)
            ch0 = data[0]
            ch_id = getattr(ch0, "id", None) or (ch0.get("id") if isinstance(ch0, dict) else None)
        if not ch_id:
            return (False, "No charge id on PaymentIntent")
        stripe.Refund.create(charge=ch_id)
        print(f"[STRIPE] Refunded loader charge for payment {payment.id} (PI {pid})")
        return (True, None)
    except Exception as e:
        err = str(e)
        print(f"[STRIPE] Loader refund failed: {err}")
        return (False, err)
