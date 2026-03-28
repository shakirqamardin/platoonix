"""
Real-time alerts: when a new load is posted that matches a subscriber's vehicle and
empty location (and optional base / return postcode for corridor matching), push SSE.
Also: planned-route matches. Subscriptions are (vehicle_id, origin_postcode, destination_postcode).
"""
import queue
import threading
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy.orm import Session

from app import models
from app.services.matching import load_matches_empty_to_base_corridor, load_matches_vehicle

# Thread-safe: sync code (load creation) puts, async SSE gets via executor
_subscriptions: List[Dict[str, Any]] = []
_lock = threading.Lock()


def _norm_pc(s: str) -> str:
    return " ".join((s or "").strip().split()).upper()


def add_subscription(
    vehicle_id: int,
    origin_postcode: str,
    destination_postcode: Optional[str] = None,
) -> queue.Queue:
    """Register a listener; optional destination = base / return for 25mi corridor + origin (same as Find Backhaul)."""
    q = queue.Queue()
    with _lock:
        _subscriptions.append(
            {
                "vehicle_id": vehicle_id,
                "origin_postcode": _norm_pc(origin_postcode),
                "destination_postcode": _norm_pc(destination_postcode or ""),
                "queue": q,
            }
        )
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
    seen_in_app: Optional[Set[Tuple[int, int]]] = None,
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

    if seen_in_app is not None:
        try:
            from app.services.in_app_notifications import record_suggested_load_notifications

            record_suggested_load_notifications(db, load, haulier_id, vehicle_id, seen_in_app)
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

    from app.services.in_app_notifications import record_live_load_notifications

    live_in_app_done: Set[Tuple[int, int]] = set()
    for sub in _get_subscriptions():
        try:
            dest = (sub.get("destination_postcode") or "").strip()
            if load_matches_empty_to_base_corridor(
                load,
                sub["vehicle_id"],
                sub["origin_postcode"],
                dest if dest else None,
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
                record_live_load_notifications(db, load, sub["vehicle_id"], live_in_app_done)
        except Exception:
            pass  # Don't let one subscriber break others

    try:
        seen_emails = set()
        seen_in_app: Set[Tuple[int, int]] = set()

        # Planned routes: create suggested LoadInterest + email
        routes = db.query(models.HaulierRoute).all()
        for route in routes:
            if not load_matches_vehicle(load, route.vehicle_id, route.empty_at_postcode or "", db):
                continue
            _create_suggested_interest_and_email(
                load,
                route.haulier_id,
                route.vehicle_id,
                db,
                seen_emails,
                "planned route " + (route.empty_at_postcode or ""),
                seen_in_app=seen_in_app,
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
                load,
                vehicle.haulier_id,
                vehicle.id,
                db,
                seen_emails,
                "base " + origin,
                seen_in_app=seen_in_app,
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
        seen_in_app: Set[Tuple[int, int]] = set()
        for load in open_loads:
            if not load_matches_vehicle(load, vehicle_id, origin, db):
                continue
            _create_suggested_interest_and_email(
                load,
                haulier_id,
                vehicle_id,
                db,
                seen_emails,
                origin_label,
                seen_in_app=seen_in_app,
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
    origin = _norm_pc(route.empty_at_postcode or "")
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
