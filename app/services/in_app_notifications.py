"""Persist in-app notifications for haulier office users and drivers (no email required)."""
from typing import Set, Tuple

from sqlalchemy.orm import Session

from app import models


def _dedupe_key_load_haulier(load_id: int, haulier_id: int) -> Tuple[int, int]:
    return (load_id, haulier_id)


def record_live_load_notifications(
    db: Session,
    load: models.Load,
    vehicle_id: int,
    already_notified: Set[Tuple[int, int]],
) -> None:
    """One row per haulier user + eligible drivers for this company/vehicle (deduped per load+haulier)."""
    vehicle = db.get(models.Vehicle, vehicle_id)
    if not vehicle:
        return
    haulier_id = vehicle.haulier_id
    key = _dedupe_key_load_haulier(load.id, haulier_id)
    if key in already_notified:
        return
    already_notified.add(key)

    title = "Live load match"
    body = f"{load.shipper_name}: {load.pickup_postcode} → {load.delivery_postcode}"
    link = "/?section=matches"
    kind = "live_load_match"

    for u in (
        db.query(models.User)
        .filter(models.User.haulier_id == haulier_id, models.User.role == "haulier")
        .all()
    ):
        db.add(
            models.AppNotification(
                user_id=u.id,
                driver_id=None,
                title=title,
                body=body,
                link_url=link,
                kind=kind,
            )
        )
    for d in db.query(models.Driver).filter(models.Driver.haulier_id == haulier_id).all():
        if d.vehicle_id is not None and d.vehicle_id != vehicle_id:
            continue
        db.add(
            models.AppNotification(
                user_id=None,
                driver_id=d.id,
                title=title,
                body=body,
                link_url="/?section=find",
                kind=kind,
            )
        )
    try:
        db.commit()
    except Exception:
        db.rollback()


def record_suggested_load_notifications(
    db: Session,
    load: models.Load,
    haulier_id: int,
    vehicle_id: int,
    already_notified: Set[Tuple[int, int]],
) -> None:
    """When the system creates a suggested match (base postcode / planned route), notify in-app once per load+haulier."""
    key = _dedupe_key_load_haulier(load.id, haulier_id)
    if key in already_notified:
        return
    already_notified.add(key)

    title = "Suggested load match"
    body = f"{load.shipper_name}: {load.pickup_postcode} → {load.delivery_postcode}"
    kind = "suggested_match"

    for u in (
        db.query(models.User)
        .filter(models.User.haulier_id == haulier_id, models.User.role == "haulier")
        .all()
    ):
        db.add(
            models.AppNotification(
                user_id=u.id,
                driver_id=None,
                title=title,
                body=body,
                link_url="/?section=matches",
                kind=kind,
            )
        )
    for d in db.query(models.Driver).filter(models.Driver.haulier_id == haulier_id).all():
        if d.vehicle_id is not None and d.vehicle_id != vehicle_id:
            continue
        db.add(
            models.AppNotification(
                user_id=None,
                driver_id=d.id,
                title=title,
                body=body,
                link_url="/?section=find",
                kind=kind,
            )
        )
    try:
        db.commit()
    except Exception:
        db.rollback()
