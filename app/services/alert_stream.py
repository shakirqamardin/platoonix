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


def notify_new_load(load: models.Load, db: Session) -> None:
    """
    Called when a new load is created. For each subscriber (vehicle_id, origin_postcode),
    if the load matches that vehicle, push an alert into their queue.
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
