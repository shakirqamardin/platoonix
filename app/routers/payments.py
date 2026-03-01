from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app import models, schemas
from app.database import get_db


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

    payment = models.Payment(
        backhaul_job_id=backhaul_job_id,
        amount_gbp=amount_gbp,
        fee_gbp=fee_gbp,
        net_payout_gbp=amount_gbp - fee_gbp,
        status=models.PaymentStatusEnum.RESERVED.value,
    )
    db.add(payment)
    db.commit()
    db.refresh(payment)
    return payment

