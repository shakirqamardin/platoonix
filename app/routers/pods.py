from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app import models, schemas
from app.database import get_db


router = APIRouter()


@router.post("/", response_model=schemas.PODRead, status_code=status.HTTP_201_CREATED)
def create_pod(
    pod_in: schemas.PODCreate,
    db: Session = Depends(get_db),
) -> models.POD:
    job = db.get(models.BackhaulJob, pod_in.backhaul_job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid backhaul_job_id")

    pod = models.POD(
        backhaul_job_id=pod_in.backhaul_job_id,
        file_url=pod_in.file_url,
        notes=pod_in.notes,
    )
    db.add(pod)
    db.commit()
    db.refresh(pod)
    return pod


@router.post("/{pod_id}/confirm", response_model=schemas.PODRead)
def confirm_pod(
    pod_id: int,
    db: Session = Depends(get_db),
) -> models.POD:
    pod = db.get(models.POD, pod_id)
    if not pod:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="POD not found")

    pod.status = models.PODStatusEnum.CONFIRMED.value
    pod.confirmed_at = datetime.now(timezone.utc)
    db.add(pod)

    # Mark the related job as completed (if it exists)
    job = db.get(models.BackhaulJob, pod.backhaul_job_id)
    if job and not job.completed_at:
        job.completed_at = datetime.now(timezone.utc)
        db.add(job)

    # ePOD confirmed → pay haulier (net_payout_gbp); platform keeps fee_gbp
    payment = (
        db.query(models.Payment)
        .filter(models.Payment.backhaul_job_id == pod.backhaul_job_id)
        .order_by(models.Payment.created_at.asc())
        .first()
    )
    if payment:
        if payment.status not in (
            models.PaymentStatusEnum.RESERVED.value,
            models.PaymentStatusEnum.CAPTURED.value,
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Payment not collectable (status: {payment.status})",
            )
        payment.status = models.PaymentStatusEnum.PAID_OUT.value
        db.add(payment)

    db.commit()
    db.refresh(pod)
    return pod

