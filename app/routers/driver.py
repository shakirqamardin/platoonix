"""
Driver API: my job(s), update location (live GPS), set status.
Driver-led flow: reached_pickup -> collected (captures payment) -> departed_pickup -> reached_delivery -> delivered (ePOD, payout).
Auth: session-based; shared access for haulier office and driver login.
"""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app import models, schemas
from app.auth import get_current_driver_optional, get_current_user_optional
from app.database import get_db

router = APIRouter(prefix="/api/driver", tags=["driver"])


def _get_actor_haulier(
    request: Request, db: Session
) -> tuple[Optional[models.User], Optional[models.Driver], models.Haulier]:
    """Return actor (user or driver) and haulier."""
    driver = get_current_driver_optional(request, db)
    if driver:
        haulier = db.get(models.Haulier, driver.haulier_id)
        if not haulier:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Haulier not found")
        return (None, driver, haulier)

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
    return (user, None, haulier)


def _job_belongs_to_haulier(
    job_id: int,
    haulier: models.Haulier,
    db: Session,
    actor_driver: Optional[models.Driver] = None,
) -> Optional[models.BackhaulJob]:
    job = db.get(models.BackhaulJob, job_id)
    if not job:
        return None
    vehicle = db.get(models.Vehicle, job.vehicle_id)
    if not vehicle or vehicle.haulier_id != haulier.id:
        return None
    if actor_driver is not None and job.driver_id not in (None, actor_driver.id):
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
        driver_id=job.driver_id,
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
        job_group_uuid=job.job_group_uuid,
    )


@router.get("/jobs/{job_id}", response_model=schemas.DriverJobRead)
def get_my_job(
    job_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Get one job (for driver app)."""
    _, driver, haulier = _get_actor_haulier(request, db)
    job = _job_belongs_to_haulier(job_id, haulier, db, actor_driver=driver)
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
    _, driver, haulier = _get_actor_haulier(request, db)
    q = (
        db.query(models.BackhaulJob)
        .join(models.Vehicle, models.BackhaulJob.vehicle_id == models.Vehicle.id)
        .filter(models.Vehicle.haulier_id == haulier.id)
        .order_by(models.BackhaulJob.matched_at.desc())
    )
    if active_only:
        q = q.filter(models.BackhaulJob.completed_at.is_(None))
    if driver is not None:
        q = q.filter(models.BackhaulJob.driver_id == driver.id)
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
    _, driver, haulier = _get_actor_haulier(request, db)
    job = _job_belongs_to_haulier(job_id, haulier, db, actor_driver=driver)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found or not yours")
    job.last_lat = body.lat
    job.last_lng = body.lng
    job.location_updated_at = datetime.now(timezone.utc)
    db.add(job)
    db.commit()
    db.refresh(job)
    return _job_to_driver_read(job, db)


@router.get("/route-home", response_model=list)
def loads_on_route_home(
    request: Request,
    db: Session = Depends(get_db),
    from_postcode: str = "",
    to_postcode: str = "",
    vehicle_id: Optional[int] = None,
):
    """
    Find open loads within 25 miles of the route from from_postcode (e.g. delivery) to to_postcode (e.g. base).
    Driver Manchester → Milton Keynes: returns jobs along that corridor.
    Requires haulier auth; vehicle_id must belong to your haulier (or pass job_id to use job's vehicle + delivery/base).
    """
    _, driver, haulier = _get_actor_haulier(request, db)
    if not from_postcode or not to_postcode:
        return []
    vid = vehicle_id
    if vid is None:
        return []
    vehicle = db.get(models.Vehicle, vid)
    if not vehicle or vehicle.haulier_id != haulier.id:
        return []
    from app.services.matching import find_matching_loads_along_route
    pairs = find_matching_loads_along_route(vid, from_postcode.strip(), to_postcode.strip(), db)
    return [
        {
            "load_id": l.id,
            "shipper_name": l.shipper_name,
            "pickup_postcode": l.pickup_postcode,
            "delivery_postcode": l.delivery_postcode,
            "distance_miles": d,
        }
        for l, d, _, _ in pairs
    ]


@router.post("/jobs/{job_id}/status", response_model=schemas.DriverJobRead)
def update_job_status(
    job_id: int,
    body: schemas.DriverStatusUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Driver sets milestone. 'collected' charges the loader (Stripe) then RESERVED -> CAPTURED.
    Allowed: reached_pickup, collected, departed_pickup, reached_delivery.
    Delivery payout is via ePOD upload + confirm, not this endpoint.
    """
    _, driver, haulier = _get_actor_haulier(request, db)
    job = _job_belongs_to_haulier(job_id, haulier, db, actor_driver=driver)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found or not yours")
    status_val = (body.status or "").strip().lower()
    from app.services.job_status import apply_driver_status_milestone

    err = apply_driver_status_milestone(db, job, status_val)
    if err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=err)
    db.commit()
    db.refresh(job)
    return _job_to_driver_read(job, db)


@router.post("/jobs/{job_id}/assign-driver", response_model=schemas.DriverJobRead)
def assign_driver(
    job_id: int,
    body: schemas.DriverAssignRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Assign/unassign a driver to a job.
    - Haulier/admin can assign any driver from the same haulier.
    - Driver actor cannot reassign.
    Body JSON: {"driver_id": <int|null>}
    """
    user, actor_driver, haulier = _get_actor_haulier(request, db)
    if actor_driver is not None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Drivers cannot reassign jobs")
    job = _job_belongs_to_haulier(job_id, haulier, db)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found or not yours")
    driver_id = body.driver_id
    if driver_id in ("", None):
        job.driver_id = None
    else:
        try:
            driver_id = int(driver_id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid driver_id")
        driver = db.get(models.Driver, driver_id)
        if not driver or driver.haulier_id != haulier.id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Driver not found for this haulier")
        # First-to-act wins: don't silently replace an existing different assignment.
        if job.driver_id is not None and job.driver_id != driver.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Job already assigned. Unassign first to change driver.",
            )
        job.driver_id = driver.id
    db.add(job)
    db.commit()
    db.refresh(job)
    return _job_to_driver_read(job, db)
