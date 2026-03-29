"""Denormalised vehicle availability: sync from active BackhaulJob rows + Load delivery dates."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app import models


def delivery_end_date(load: models.Load) -> Optional[date]:
    """Best estimate of when the vehicle is free after this load (date only)."""
    dt = load.delivery_window_end or load.delivery_window_start
    if dt is None:
        dt = load.pickup_window_end
    if dt is None:
        return None
    if isinstance(dt, datetime):
        return dt.date()
    return dt


def refresh_vehicle_availability(db: Session, vehicle_id: int) -> None:
    """
    Recompute Vehicle.current_job_id and Vehicle.available_from from incomplete jobs.
    Call after job create, complete, or delete.
    """
    vehicle = db.get(models.Vehicle, vehicle_id)
    if not vehicle:
        return
    active = (
        db.query(models.BackhaulJob)
        .filter(models.BackhaulJob.vehicle_id == vehicle_id)
        .filter(models.BackhaulJob.completed_at.is_(None))
        .all()
    )
    if not active:
        vehicle.current_job_id = None
        vehicle.available_from = None
        db.add(vehicle)
        return

    dates: list[date] = []
    for j in active:
        load = db.get(models.Load, j.load_id)
        if load:
            d = delivery_end_date(load)
            if d:
                dates.append(d)
    vehicle.available_from = max(dates) if dates else None
    primary = max(active, key=lambda j: j.id)
    vehicle.current_job_id = primary.id
    db.add(vehicle)


def refresh_all_vehicles(db: Session) -> None:
    for (vid,) in db.query(models.Vehicle.id).all():
        refresh_vehicle_availability(db, vid)


def vehicle_has_active_job(db: Session, vehicle_id: int) -> bool:
    return (
        db.query(models.BackhaulJob)
        .filter(models.BackhaulJob.vehicle_id == vehicle_id)
        .filter(models.BackhaulJob.completed_at.is_(None))
        .first()
        is not None
    )


def availability_ui(vehicle: models.Vehicle, today: date) -> Dict[str, Any]:
    """
    UI tier for lists and Find Backhaul:
    - free: available for new work
    - amber / red: on job; colour by how far away available_from is
    """
    if vehicle.current_job_id is None:
        return {
            "tier": "free",
            "label": "Available now",
            "selectable": True,
        }
    af = vehicle.available_from or today
    af_str = af.strftime("%d/%m")
    label = f"On job — available from {af_str}"
    days_away = (af - today).days
    tier = "amber" if days_away <= 7 else "red"
    return {
        "tier": tier,
        "label": label,
        "selectable": False,
        "available_from": af,
    }
