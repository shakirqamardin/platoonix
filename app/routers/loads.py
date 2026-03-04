from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app import models, schemas
from app.database import get_db


router = APIRouter()


@router.post("/", response_model=schemas.LoadRead, status_code=status.HTTP_201_CREATED)
def create_load(
    load_in: schemas.LoadCreate,
    db: Session = Depends(get_db),
) -> models.Load:
    load = models.Load(
        shipper_name=load_in.shipper_name,
        pickup_postcode=load_in.pickup_postcode.upper(),
        delivery_postcode=load_in.delivery_postcode.upper(),
        pickup_window_start=load_in.pickup_window_start,
        pickup_window_end=load_in.pickup_window_end,
        delivery_window_start=load_in.delivery_window_start,
        delivery_window_end=load_in.delivery_window_end,
        weight_kg=load_in.weight_kg,
        volume_m3=load_in.volume_m3,
        requirements=load_in.requirements,
        budget_gbp=load_in.budget_gbp,
    )
    db.add(load)
    db.commit()
    db.refresh(load)
    from app.services.alert_stream import notify_new_load
    notify_new_load(load, db)
    return load


@router.get("/{load_id}", response_model=schemas.LoadRead)
def get_load(
    load_id: int,
    db: Session = Depends(get_db),
) -> models.Load:
    load = db.get(models.Load, load_id)
    if not load:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Load not found")
    return load


@router.get("/", response_model=list[schemas.LoadRead])
def list_loads(
    db: Session = Depends(get_db),
) -> list[models.Load]:
    return db.query(models.Load).order_by(models.Load.created_at.desc()).all()


@router.delete("/{load_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_load(
    load_id: int,
    db: Session = Depends(get_db),
) -> None:
    load = db.get(models.Load, load_id)
    if not load:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Load not found")
    if db.query(models.BackhaulJob).filter(models.BackhaulJob.load_id == load_id).first():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete: load has backhaul jobs",
        )
    db.query(models.LoadInterest).filter(models.LoadInterest.load_id == load_id).delete()
    db.delete(load)
    db.commit()
