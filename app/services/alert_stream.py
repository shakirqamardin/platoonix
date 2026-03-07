"""
Real-time alerts for hauliers: when a new load is posted that matches their vehicle
and location, push an event over SSE. Also: when planned routes match, alert hauliers
to show interest. Subscriptions are (vehicle_id, origin_postcode).
"""
import queue
import threading
from typing import Any, Dict, List

from sqlalchemy.orm import Session

from app import models
from app.services.matching import load_matches_vehicle

# Thread-safe: sync code (load creation) puts, async SSE gets via executor
_subscriptions: List[Dict[str, Any]] = []
_lock = threading.Lock()


def add_subscription(vehicle_id: int, origin_postcode: str) -> queue.Queue:
    """Register a listener; returns a queue that will receive alert dicts."""
    q = queue.Queue()
    with _lock:
        _subscriptions.append({
            "vehicle_id": vehicle_id,
            "origin_postcode": origin_postcode.strip(),
            "queue": q,
        })
    return q


def remove_subscription(q: queue.Queue) -> None:
    """Unregister a listener."""
    with _lock:
        _subscriptions[:] = [s for s in _subscriptions if s["queue"] is not q]


def _get_subscriptions() -> List[Dict[str, Any]]:
    with _lock:
        return list(_subscriptions)


def _create_suggested_interest_and_email(
    load: models.Load,
    haulier_id: int,
    vehicle_id: int,
    db: Session,
    seen_emails: set,
    origin_label: str,
) -> None:
    """Create LoadInterest(suggested) for this load+vehicle if not exists; send email once per haulier."""
    existing = (
        db.query(models.LoadInterest)
        .filter(
            models.LoadInterest.load_id == load.id,
            models.LoadInterest.haulier_id == haulier_id,
            models.LoadInterest.vehicle_id == vehicle_id,
        )
        .first()
    )
    if not existing:
        interest = models.LoadInterest(
            haulier_id=haulier_id,
            vehicle_id=vehicle_id,
            load_id=load.id,
            planned_load_id=None,
            status="suggested",
        )
        db.add(interest)
        try:
            db.commit()
        except Exception:
            db.rollback()

    try:
        from app.services.email_sender import send_email
        user = db.query(models.User).filter(models.User.haulier_id == haulier_id).first()
        if not user or not user.email or user.email in seen_emails:
            return
        seen_emails.add(user.email)
        vehicle = db.get(models.Vehicle, vehicle_id)
        reg = vehicle.registration if vehicle else f"Vehicle {vehicle_id}"
        subject = "Platoonix: new load match for your vehicle"
        body = (
            f"A new load matches your vehicle {reg} ({origin_label}).\n\n"
            f"Load: {load.shipper_name}\n"
            f"Route: {load.pickup_postcode} → {load.delivery_postcode}\n"
            f"Log in to Platoonix to view and show interest.\n"
        )
        send_email(user.email, subject, body)
    except Exception:
        pass


def notify_new_load(load: models.Load, db: Session) -> None:
    """
    Called when a new load is created.
    - Push to SSE subscribers (Live alerts) who have that vehicle+postcode open.
    - For each planned route (HaulierRoute) that matches: create LoadInterest(suggested), email haulier.
    - For each vehicle with base_postcode that matches: create LoadInterest(suggested), email haulier.
    So matches appear automatically in Matches / Who's interested without typing postcode.
    """
    if load.status != models.LoadStatusEnum.OPEN.value:
        return
    for sub in _get_subscriptions():
        try:
            if load_matches_vehicle(
                load,
                sub["vehicle_id"],
                sub["origin_postcode"],
                db,
            ):
                msg = {
                    "type": "new_load",
                    "load_id": load.id,
                    "shipper_name": load.shipper_name,
                    "pickup_postcode": load.pickup_postcode,
                    "delivery_postcode": load.delivery_postcode,
                    "weight_kg": load.weight_kg,
                    "volume_m3": load.volume_m3,
                }
                sub["queue"].put(msg)
        except Exception:
            pass  # Don't let one subscriber break others

    try:
        from app.services.email_sender import send_email
        seen_emails = set()

        # Planned routes: create suggested LoadInterest + email
        routes = db.query(models.HaulierRoute).all()
        for route in routes:
            if not load_matches_vehicle(load, route.vehicle_id, route.empty_at_postcode or "", db):
                continue
            _create_suggested_interest_and_email(
                load, route.haulier_id, route.vehicle_id, db, seen_emails,
                "planned route " + (route.empty_at_postcode or ""),
            )

        # Vehicles with base_postcode: create suggested LoadInterest + email
        vehicles_with_base = db.query(models.Vehicle).filter(
            models.Vehicle.base_postcode.isnot(None),
            models.Vehicle.base_postcode != "",
        ).all()
        for vehicle in vehicles_with_base:
            origin = (vehicle.base_postcode or "").strip()
            if not origin or not load_matches_vehicle(load, vehicle.id, origin, db):
                continue
            _create_suggested_interest_and_email(
                load, vehicle.haulier_id, vehicle.id, db, seen_emails,
                "base " + origin,
            )
    except Exception:
        pass


def notify_matching_loads_for_vehicle(
    vehicle_id: int,
    origin_postcode: str,
    haulier_id: int,
    db: Session,
    origin_label: str = "location",
) -> None:
    """
    When a vehicle (with base_postcode) or a planned route is added: find all open loads
    that match this vehicle + postcode and create suggested LoadInterest so they appear
    in Matches / Who's interested without anyone typing a postcode.
    """
    origin = (origin_postcode or "").strip()
    if not origin:
        return
    try:
        open_loads = (
            db.query(models.Load)
            .filter(models.Load.status == models.LoadStatusEnum.OPEN.value)
            .all()
        )
        seen_emails = set()
        for load in open_loads:
            if not load_matches_vehicle(load, vehicle_id, origin, db):
                continue
            _create_suggested_interest_and_email(
                load, haulier_id, vehicle_id, db, seen_emails, origin_label,
            )
    except Exception:
        pass


def notify_route_match(
    planned_load: models.PlannedLoad,
    route: models.HaulierRoute,
    db: Session,
) -> None:
    """
    When a planned load matches a haulier route: push to any subscriber with
    that vehicle + postcode, and create a suggested LoadInterest so they can see
    it and click "Show interest".
    """
    # Push to live subscribers
    origin = (route.empty_at_postcode or "").strip()
    for sub in _get_subscriptions():
        try:
            if sub["vehicle_id"] != route.vehicle_id or sub["origin_postcode"] != origin:
                continue
            msg = {
                "type": "planned_load_match",
                "planned_load_id": planned_load.id,
                "shipper_name": planned_load.shipper_name,
                "pickup_postcode": planned_load.pickup_postcode,
                "delivery_postcode": planned_load.delivery_postcode,
                "day_of_week": planned_load.day_of_week,
                "weight_kg": planned_load.weight_kg,
                "volume_m3": planned_load.volume_m3,
                "message": "A planned load matches your route – show interest?",
            }
            sub["queue"].put(msg)
        except Exception:
            pass

    # Create suggested LoadInterest if not already present
    existing = (
        db.query(models.LoadInterest)
        .filter(
            models.LoadInterest.haulier_id == route.haulier_id,
            models.LoadInterest.vehicle_id == route.vehicle_id,
            models.LoadInterest.planned_load_id == planned_load.id,
        )
        .first()
    )
    if not existing:
        interest = models.LoadInterest(
            haulier_id=route.haulier_id,
            vehicle_id=route.vehicle_id,
            planned_load_id=planned_load.id,
            load_id=None,
            status="suggested",
        )
        db.add(interest)
        try:
            db.commit()
        except Exception:
            db.rollback()
