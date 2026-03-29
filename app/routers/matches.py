from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from typing import Optional

from app import models, schemas
from app.config import get_settings
from app.database import get_db
from app.services.matching import find_matching_loads


router = APIRouter()
settings = get_settings()


@router.post(
    "/assign",
    response_model=schemas.BackhaulJobRead,
    status_code=status.HTTP_201_CREATED,
)
def assign_load_to_vehicle(
    body: schemas.BackhaulAssignRequest,
    db: Session = Depends(get_db),
) -> models.BackhaulJob:
    """
    Simple helper endpoint to link a vehicle to a load and
    simulate reserving payment for that backhaul job.
    """
    vehicle = db.get(models.Vehicle, body.vehicle_id)
    if not vehicle:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid vehicle_id")

    load = db.get(models.Load, body.load_id)
    if not load:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid load_id")

    # Create the backhaul job link
    job = models.BackhaulJob(
        vehicle_id=body.vehicle_id,
        load_id=body.load_id,
        matched_at=datetime.now(timezone.utc),
    )
    db.add(job)

    # Update load status to matched
    load.status = models.LoadStatusEnum.MATCHED.value
    db.add(load)

    from app.services.payment_fees import compute_job_payment_splits

    if body.fee_gbp is not None:
        fee_gbp = round(float(body.fee_gbp), 2)
        net_payout_gbp = round(body.amount_gbp - fee_gbp, 2)
        flat_fee_gbp = round(float(getattr(settings, "loader_flat_fee_gbp", 5.0) or 0.0), 2)
    else:
        splits = compute_job_payment_splits(body.amount_gbp, settings)
        fee_gbp = splits.fee_gbp
        net_payout_gbp = splits.net_payout_gbp
        flat_fee_gbp = splits.flat_fee_gbp

    payment = models.Payment(
        backhaul_job_id=job.id,  # will be filled after flush
        amount_gbp=body.amount_gbp,
        fee_gbp=fee_gbp,
        net_payout_gbp=net_payout_gbp,
        flat_fee_gbp=flat_fee_gbp,
        status=models.PaymentStatusEnum.RESERVED.value,
    )

    db.flush()  # assign job.id before we use it
    payment.backhaul_job_id = job.id
    db.add(payment)

    from app.services.job_groups import try_link_new_job_pickup_group

    try_link_new_job_pickup_group(db, job)

    db.commit()
    db.refresh(job)
    from app.services import vehicle_availability as vehicle_availability_svc

    vehicle_availability_svc.refresh_vehicle_availability(db, job.vehicle_id)
    db.commit()

    return job


@router.get("/backhaul-for-vehicle/{vehicle_id}", response_model=list[schemas.BackhaulJobRead])
def find_backhaul_for_vehicle(
    vehicle_id: int,
    db: Session = Depends(get_db),
) -> list[models.BackhaulJob]:
    """
    Returns existing BackhaulJob rows for this vehicle (jobs already assigned).
    For smart matching of open loads by location and vehicle/trailer, use find-for-vehicle.
    """
    jobs = (
        db.query(models.BackhaulJob)
        .filter(models.BackhaulJob.vehicle_id == vehicle_id)
        .order_by(models.BackhaulJob.matched_at.desc())
        .all()
    )
    return jobs


@router.get("/find-for-vehicle", response_model=list[schemas.LoadMatchResult])
def find_loads_for_vehicle(
    vehicle_id: int,
    origin_postcode: str,
    radius_miles: Optional[int] = None,
    db: Session = Depends(get_db),
) -> list[schemas.LoadMatchResult]:
    """
    Smart matching: find open loads within radius (default 25 miles) of origin postcode
    that suit the vehicle (trailer type and capacity). Sorted by distance.
    """
    pairs = find_matching_loads(vehicle_id, origin_postcode.strip(), db, radius_miles)
    return [
        schemas.LoadMatchResult(load=load, distance_miles=dist)
        for load, dist in pairs
    ]

