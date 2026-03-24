from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app import models, schemas
from app.config import get_settings
from app.database import get_db
from app.services.payment_fees import compute_job_payment_splits


router = APIRouter()


@router.get("/{payment_id}", response_model=schemas.PaymentRead)
def get_payment(
    payment_id: int,
    db: Session = Depends(get_db),
) -> models.Payment:
    payment = db.get(models.Payment, payment_id)
    if not payment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found")
    return payment


@router.post("/{payment_id}/collect", response_model=schemas.PaymentRead)
def collect_payment(
    payment_id: int,
    db: Session = Depends(get_db),
) -> models.Payment:
    """
    Mark payment as collected from the loader/shipper (e.g. on collection or job start).
    Status: RESERVED → CAPTURED. In production this would be triggered by Stripe capture
    or your payment provider when the loader is charged.
    """
    payment = db.get(models.Payment, payment_id)
    if not payment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found")
    if payment.status != models.PaymentStatusEnum.RESERVED.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Payment not in RESERVED state (current: {payment.status})",
        )
    payment.status = models.PaymentStatusEnum.CAPTURED.value
    db.add(payment)
    db.commit()
    db.refresh(payment)
    return payment


@router.post("/{payment_id}/payout", response_model=schemas.PaymentRead)
def payout_to_haulier(
    payment_id: int,
    db: Session = Depends(get_db),
) -> models.Payment:
    """
    Trigger Stripe Connect transfer to the haulier for this payment, then mark as paid_out.
    Payment must be RESERVED or CAPTURED. Haulier must have payment_account_id (Stripe Connect acct_...).
    """
    payment = db.get(models.Payment, payment_id)
    if not payment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found")
    if payment.status not in (
        models.PaymentStatusEnum.RESERVED.value,
        models.PaymentStatusEnum.CAPTURED.value,
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Payment not in RESERVED/CAPTURED state (current: {payment.status})",
        )
    from app.services.stripe_payout import try_payout_to_haulier
    ok, err = try_payout_to_haulier(payment, db)
    if not ok and err:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Payout failed: {err}",
        )
    payment.status = models.PaymentStatusEnum.PAID_OUT.value
    db.add(payment)
    db.commit()
    db.refresh(payment)
    return payment


@router.get("/", response_model=list[schemas.PaymentRead])
def list_payments(
    db: Session = Depends(get_db),
) -> list[models.Payment]:
    return db.query(models.Payment).order_by(models.Payment.created_at.desc()).all()


@router.post(
    "/simulate-reserve",
    response_model=schemas.PaymentRead,
    status_code=status.HTTP_201_CREATED,
)
def simulate_reserve_payment(
    backhaul_job_id: int,
    amount_gbp: float,
    fee_gbp: float = 0.0,
    db: Session = Depends(get_db),
) -> models.Payment:
    """
    Placeholder endpoint that simulates reserving a payment for a backhaul job.

    In production this would be driven by a payment provider (e.g. Stripe), not called directly.
    """
    job = db.get(models.BackhaulJob, backhaul_job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid backhaul_job_id")

    settings = get_settings()
    if fee_gbp and fee_gbp > 0:
        net_payout_gbp = round(amount_gbp - fee_gbp, 2)
        flat_fee_gbp = round(float(getattr(settings, "loader_flat_fee_gbp", 5.0) or 0.0), 2)
    else:
        splits = compute_job_payment_splits(amount_gbp, settings)
        fee_gbp = splits.fee_gbp
        net_payout_gbp = splits.net_payout_gbp
        flat_fee_gbp = splits.flat_fee_gbp

    payment = models.Payment(
        backhaul_job_id=backhaul_job_id,
        amount_gbp=amount_gbp,
        fee_gbp=fee_gbp,
        net_payout_gbp=net_payout_gbp,
        flat_fee_gbp=flat_fee_gbp,
        status=models.PaymentStatusEnum.RESERVED.value,
    )
    db.add(payment)
    db.commit()
    db.refresh(payment)
    return payment

