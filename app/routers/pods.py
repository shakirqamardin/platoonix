import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, status, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import models, schemas
from app.database import get_db


router = APIRouter()

# ePOD uploads: saved under static so /static/uploads/pods/... is served
POD_UPLOAD_DIR = Path(__file__).resolve().parent.parent.parent / "static" / "uploads" / "pods"
POD_ALLOWED_SUFFIXES = {".pdf", ".jpg", ".jpeg", ".png", ".heic"}
POD_MAX_SIZE = 10 * 1024 * 1024  # 10 MB


class ConfirmCollectionBody(BaseModel):
    backhaul_job_id: int


@router.post("/confirm-collection")
def confirm_collection(
    body: ConfirmCollectionBody,
    db: Session = Depends(get_db),
):
    """
    Confirm collection (pickup): charge the loader (load + flat fee) when Stripe is configured,
    then mark the job collected and payment RESERVED -> CAPTURED. ePOD / delivery then pays the haulier only.
    """
    job = db.get(models.BackhaulJob, body.backhaul_job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Backhaul job not found")
    if job.collected_at:
        return {"ok": True, "message": "Collection already confirmed", "collected_at": job.collected_at}

    payment = (
        db.query(models.Payment)
        .filter(models.Payment.backhaul_job_id == body.backhaul_job_id)
        .order_by(models.Payment.created_at.asc())
        .first()
    )
    if payment and payment.status == models.PaymentStatusEnum.RESERVED.value:
        from app.services.stripe_loader_charge import try_charge_loader_for_job

        ok_charge, charge_err = try_charge_loader_for_job(payment, db)
        if not ok_charge:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Loader payment failed: {charge_err}. Fix the card or Stripe customer, then retry.",
            )
        payment.status = models.PaymentStatusEnum.CAPTURED.value
        db.add(payment)

    job.collected_at = datetime.now(timezone.utc)
    db.add(job)

    db.commit()
    db.refresh(job)
    return {"ok": True, "collected_at": job.collected_at, "payment_status": payment.status if payment else None}


@router.post("/upload", status_code=status.HTTP_201_CREATED)
async def upload_epod_file(file: UploadFile = File(...)):
    """
    Upload an ePOD file (proof of delivery). Returns file_url to use in POST /api/pods (create POD).
    Allowed: PDF, JPG, PNG, HEIC. Max 10 MB.
    """
    if not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No filename")
    suffix = Path(file.filename).suffix.lower()
    if suffix not in POD_ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Allowed types: PDF, JPG, PNG, HEIC",
        )
    content = await file.read()
    if len(content) > POD_MAX_SIZE:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File too large (max 10 MB)")

    POD_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = f"{uuid.uuid4().hex}{suffix}"
    path = POD_UPLOAD_DIR / safe_name
    path.write_bytes(content)

    file_url = f"/static/uploads/pods/{safe_name}"
    return {"file_url": file_url}


@router.get("/", response_model=list[schemas.PODRead])
def list_pods(
    backhaul_job_id: Optional[int] = None,
    db: Session = Depends(get_db),
) -> list[models.POD]:
    """List PODs, optionally filtered by backhaul_job_id."""
    q = db.query(models.POD)
    if backhaul_job_id is not None:
        q = q.filter(models.POD.backhaul_job_id == backhaul_job_id)
    return q.order_by(models.POD.created_at.desc()).all()


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
        from app.services import vehicle_availability as vehicle_availability_svc

        vehicle_availability_svc.refresh_vehicle_availability(db, job.vehicle_id)

    # ePOD confirmed → pay haulier (loader was charged at collection)
    payment = (
        db.query(models.Payment)
        .filter(models.Payment.backhaul_job_id == pod.backhaul_job_id)
        .order_by(models.Payment.created_at.asc())
        .first()
    )
    if payment:
        if payment.status == models.PaymentStatusEnum.RESERVED.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Collection must be confirmed before delivery (payment still reserved).",
            )
        if payment.status != models.PaymentStatusEnum.CAPTURED.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Payment cannot be released (status: {payment.status})",
            )
        from app.services.stripe_payout import try_payout_to_haulier

        ok, err = try_payout_to_haulier(payment, db)
        if not ok and err:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Payout failed: {err}. Check haulier payment account and Stripe config.",
            )
        payment.status = models.PaymentStatusEnum.PAID_OUT.value
        db.add(payment)

    db.commit()
    db.refresh(pod)
    return pod

