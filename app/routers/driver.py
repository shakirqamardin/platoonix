"""
Driver API: my job(s), update location (live GPS), set status.
Driver-led flow: reached_pickup -> collected (captures payment) -> departed_pickup -> reached_delivery -> delivered (ePOD, payout).
Auth: session-based; haulier role only (driver is haulier's user).
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app import models, schemas
from app.auth import get_current_user_optional
from app.database import get_db

router = APIRouter(prefix="/api/driver", tags=["driver"])


def _get_haulier_user(request: Request, db: Session) -> tuple[models.User, models.Haulier]:
    """Return (user, haulier) or raise 401. Requires haulier role."""
    user = get_current_user_optional(request, db)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not logged in")
    if user.role not in ("haulier", "admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Driver API is for haulier accounts")
    haulier_id = user.haulier_id
    if user.role == "admin":
        haulier_id = request.query_params.get("haulier_id") or request.headers.get("X-Haulier-Id")
        if not haulier_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Admin must pass haulier_id or X-Haulier-Id")
        try:
            haulier_id = int(haulier_id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid haulier_id")
    if not haulier_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No haulier linked to this account")
    haulier = db.get(models.Haulier, haulier_id)
    if not haulier:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Haulier not found")
    return (user, haulier)


def _job_belongs_to_haulier(job_id: int, haulier: models.Haulier, db: Session) -> models.BackhaulJob | None:
    job = db.get(models.BackhaulJob, job_id)
    if not job:
        return None
    vehicle = db.get(models.Vehicle, job.vehicle_id)
    if not vehicle or vehicle.haulier_id != haulier.id:
        return None
    return job


def _job_to_driver_read(job: models.BackhaulJob, db: Session) -> schemas.DriverJobRead:
    load = db.get(models.Load, job.load_id)
    shipper = (load.shipper_name or "") if load else ""
    pickup = (load.pickup_postcode or "") if load else ""
    delivery = (load.delivery_postcode or "") if load else ""
    payment = (
        db.query(models.Payment)
        .filter(models.Payment.backhaul_job_id == job.id)
        .order_by(models.Payment.created_at.asc())
        .first()
    )
    return schemas.DriverJobRead(
        id=job.id,
        vehicle_id=job.vehicle_id,
        load_id=job.load_id,
        pickup_postcode=pickup,
        delivery_postcode=delivery,
        shipper_name=shipper,
        matched_at=job.matched_at,
        reached_pickup_at=job.reached_pickup_at,
        collected_at=job.collected_at,
        departed_pickup_at=job.departed_pickup_at,
        reached_delivery_at=job.reached_delivery_at,
        completed_at=job.completed_at,
        last_lat=job.last_lat,
        last_lng=job.last_lng,
        location_updated_at=job.location_updated_at,
        payment_status=payment.status if payment else None,
    )


@router.get("/jobs/{job_id}", response_model=schemas.DriverJobRead)
def get_my_job(
    job_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Get one job (for driver app)."""
    _, haulier = _get_haulier_user(request, db)
    job = _job_belongs_to_haulier(job_id, haulier, db)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found or not yours")
    return _job_to_driver_read(job, db)


@router.get("/jobs", response_model=list[schemas.DriverJobRead])
def list_my_jobs(
    request: Request,
    db: Session = Depends(get_db),
    active_only: bool = True,
):
    """List backhaul jobs for the current haulier. active_only=true excludes completed jobs."""
    _, haulier = _get_haulier_user(request, db)
    q = (
        db.query(models.BackhaulJob)
        .join(models.Vehicle, models.BackhaulJob.vehicle_id == models.Vehicle.id)
        .filter(models.Vehicle.haulier_id == haulier.id)
        .order_by(models.BackhaulJob.matched_at.desc())
    )
    if active_only:
        q = q.filter(models.BackhaulJob.completed_at.is_(None))
    jobs = q.all()
    return [_job_to_driver_read(j, db) for j in jobs]


@router.post("/jobs/{job_id}/location", response_model=schemas.DriverJobRead)
def update_job_location(
    job_id: int,
    body: schemas.DriverLocationUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    """Update driver's live GPS for this job. All parties can see position via GET /api/driver/jobs or track page."""
    _, haulier = _get_haulier_user(request, db)
    job = _job_belongs_to_haulier(job_id, haulier, db)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found or not yours")
    job.last_lat = body.lat
    job.last_lng = body.lng
    job.location_updated_at = datetime.now(timezone.utc)
    db.add(job)
    db.commit()
    db.refresh(job)
    return _job_to_driver_read(job, db)


@router.post("/jobs/{job_id}/status", response_model=schemas.DriverJobRead)
def update_job_status(
    job_id: int,
    body: schemas.DriverStatusUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Driver sets milestone. Payment: 'collected' triggers capture (RESERVED -> CAPTURED).
    Allowed: reached_pickup, collected, departed_pickup, reached_delivery.
    Delivery (completed) is via ePOD upload + confirm, not this endpoint.
    """
    _, haulier = _get_haulier_user(request, db)
    job = _job_belongs_to_haulier(job_id, haulier, db)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found or not yours")
    now = datetime.now(timezone.utc)
    status_val = (body.status or "").strip().lower()
    if status_val == "reached_pickup":
        job.reached_pickup_at = now
    elif status_val == "collected":
        job.collected_at = now
        payment = (
            db.query(models.Payment)
            .filter(models.Payment.backhaul_job_id == job.id)
            .order_by(models.Payment.created_at.asc())
            .first()
        )
        if payment and payment.status == models.PaymentStatusEnum.RESERVED.value:
            payment.status = models.PaymentStatusEnum.CAPTURED.value
            db.add(payment)
    elif status_val == "departed_pickup":
        job.departed_pickup_at = now
    elif status_val == "reached_delivery":
        job.reached_delivery_at = now
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="status must be one of: reached_pickup, collected, departed_pickup, reached_delivery",
        )
    db.add(job)
    db.commit()
    db.refresh(job)
    return _job_to_driver_read(job, db)
