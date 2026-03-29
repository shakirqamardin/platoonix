from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app import models, schemas
from app.database import get_db
from app.services.dvla import DvlaError, lookup_vehicle_by_registration


router = APIRouter()


@router.post("/", response_model=schemas.VehicleRead, status_code=status.HTTP_201_CREATED)
def create_vehicle(
    vehicle_in: schemas.VehicleCreate,
    db: Session = Depends(get_db),
) -> models.Vehicle:
    haulier = db.get(models.Haulier, vehicle_in.haulier_id)
    if not haulier:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid haulier_id")

    existing = (
        db.query(models.Vehicle)
        .filter(models.Vehicle.registration == vehicle_in.registration.upper())
        .one_or_none()
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Vehicle with this registration already exists",
        )

    vehicle = models.Vehicle(
        haulier_id=vehicle_in.haulier_id,
        registration=vehicle_in.registration.upper(),
        vehicle_type=vehicle_in.vehicle_type,
        trailer_type=vehicle_in.trailer_type,
        capacity_weight_kg=vehicle_in.capacity_weight_kg,
        capacity_volume_m3=vehicle_in.capacity_volume_m3,
        has_tail_lift=vehicle_in.has_tail_lift,
        has_moffett=vehicle_in.has_moffett,
        has_temp_control=vehicle_in.has_temp_control,
        is_adr_certified=vehicle_in.is_adr_certified,
    )

    # Enrich with DVLA vehicle data if configured
    try:
        dvla_data = lookup_vehicle_by_registration(vehicle.registration)
    except DvlaError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc

    if dvla_data:
        vehicle.euro_status = dvla_data.get("euroStatus")
        vehicle.fuel_type = dvla_data.get("fuelType")
        vehicle.dvla_raw = dvla_data

    db.add(vehicle)
    db.commit()
    db.refresh(vehicle)
    return vehicle


@router.get("/{vehicle_id}", response_model=schemas.VehicleRead)
def get_vehicle(
    vehicle_id: int,
    db: Session = Depends(get_db),
) -> models.Vehicle:
    vehicle = db.get(models.Vehicle, vehicle_id)
    if not vehicle:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vehicle not found")
    return vehicle


@router.get("/", response_model=list[schemas.VehicleRead])
def list_vehicles(
    db: Session = Depends(get_db),
) -> list[models.Vehicle]:
    return db.query(models.Vehicle).order_by(models.Vehicle.created_at.desc()).all()


@router.delete("/{vehicle_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_vehicle(
    vehicle_id: int,
    db: Session = Depends(get_db),
) -> None:
    vehicle = db.get(models.Vehicle, vehicle_id)
    if not vehicle:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vehicle not found")
    if db.query(models.BackhaulJob).filter(models.BackhaulJob.vehicle_id == vehicle_id).first():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete: vehicle has backhaul jobs",
        )
    if db.query(models.HaulierRoute).filter(models.HaulierRoute.vehicle_id == vehicle_id).first():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete: remove vehicle from planned routes first",
        )
    db.query(models.LoadInterest).filter(models.LoadInterest.vehicle_id == vehicle_id).delete()
    db.delete(vehicle)
    db.commit()

