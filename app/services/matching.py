"""
Smart matching: find open loads within radius that suit a vehicle (trailer type, capacity).
"""
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from app import models
from app.config import get_settings
from app.services.distance import haversine_miles
from app.services.geocode import get_lat_lon


def find_matching_loads(
    vehicle_id: int,
    origin_postcode: str,
    db: Session,
    radius_miles: Optional[int] = None,
) -> List[Tuple[models.Load, float]]:
    """
    Find open loads within radius_miles of origin_postcode that match the vehicle's
    trailer type and capacity. Returns list of (load, distance_miles) sorted by distance.
    """
    settings = get_settings()
    radius = radius_miles if radius_miles is not None else settings.default_backhaul_radius_miles

    vehicle = db.get(models.Vehicle, vehicle_id)
    if not vehicle:
        return []

    origin_ll = get_lat_lon(origin_postcode)
    if not origin_ll:
        return []

    open_loads = (
        db.query(models.Load)
        .filter(models.Load.status == models.LoadStatusEnum.OPEN.value)
        .all()
    )

    results = []
    for load in open_loads:
        pickup_ll = get_lat_lon(load.pickup_postcode)
        if not pickup_ll:
            continue
        distance = haversine_miles(
            origin_ll[0], origin_ll[1],
            pickup_ll[0], pickup_ll[1],
        )
        if distance > radius:
            continue

        # Trailer type: if load specifies requirements.trailer_type, vehicle must match
        req = load.requirements or {}
        required_trailer = req.get("trailer_type") if isinstance(req, dict) else None
        if required_trailer is not None and required_trailer != "":
            if (vehicle.trailer_type or "").strip().lower() != str(required_trailer).strip().lower():
                continue

        # Capacity: load must fit vehicle capacity when vehicle has limits
        if vehicle.capacity_weight_kg is not None and vehicle.capacity_weight_kg > 0:
            if (load.weight_kg or 0) > vehicle.capacity_weight_kg:
                continue
        if vehicle.capacity_volume_m3 is not None and vehicle.capacity_volume_m3 > 0:
            if (load.volume_m3 or 0) > vehicle.capacity_volume_m3:
                continue

        results.append((load, round(distance, 1)))

    results.sort(key=lambda x: x[1])
    return results


def load_matches_vehicle(
    load: models.Load,
    vehicle_id: int,
    origin_postcode: str,
    db: Session,
    radius_miles: Optional[int] = None,
) -> bool:
    """
    Return True if this load is within radius of origin_postcode and matches
    the vehicle's trailer type and capacity. Used for real-time alerts when a new load is posted.
    """
    settings = get_settings()
    radius = radius_miles if radius_miles is not None else settings.default_backhaul_radius_miles

    vehicle = db.get(models.Vehicle, vehicle_id)
    if not vehicle:
        return False

    origin_ll = get_lat_lon(origin_postcode)
    if not origin_ll:
        return False

    pickup_ll = get_lat_lon(load.pickup_postcode)
    if not pickup_ll:
        return False

    distance = haversine_miles(
        origin_ll[0], origin_ll[1],
        pickup_ll[0], pickup_ll[1],
    )
    if distance > radius:
        return False

    req = load.requirements or {}
    required_trailer = req.get("trailer_type") if isinstance(req, dict) else None
    if required_trailer is not None and required_trailer != "":
        if (vehicle.trailer_type or "").strip().lower() != str(required_trailer).strip().lower():
            return False

    if vehicle.capacity_weight_kg is not None and vehicle.capacity_weight_kg > 0:
        if (load.weight_kg or 0) > vehicle.capacity_weight_kg:
            return False
    if vehicle.capacity_volume_m3 is not None and vehicle.capacity_volume_m3 > 0:
        if (load.volume_m3 or 0) > vehicle.capacity_volume_m3:
            return False

    return True


def planned_load_matches_route(
    planned_load: models.PlannedLoad,
    route: models.HaulierRoute,
    db: Session,
    radius_miles: Optional[int] = None,
) -> bool:
    """
    Return True if this planned load is on the same day, within radius of the route's
    empty-at postcode, and the route's vehicle matches the load (trailer, capacity).
    """
    if planned_load.day_of_week != route.day_of_week:
        return False
    settings = get_settings()
    radius = radius_miles if radius_miles is not None else settings.default_backhaul_radius_miles

    vehicle = db.get(models.Vehicle, route.vehicle_id)
    if not vehicle:
        return False

    origin_ll = get_lat_lon(route.empty_at_postcode)
    if not origin_ll:
        return False
    pickup_ll = get_lat_lon(planned_load.pickup_postcode)
    if not pickup_ll:
        return False
    distance = haversine_miles(
        origin_ll[0], origin_ll[1],
        pickup_ll[0], pickup_ll[1],
    )
    if distance > radius:
        return False

    req = planned_load.requirements or {}
    required_trailer = req.get("trailer_type") if isinstance(req, dict) else None
    if required_trailer is not None and required_trailer != "":
        if (vehicle.trailer_type or "").strip().lower() != str(required_trailer).strip().lower():
            return False
    if vehicle.capacity_weight_kg is not None and vehicle.capacity_weight_kg > 0:
        if (planned_load.weight_kg or 0) > vehicle.capacity_weight_kg:
            return False
    if vehicle.capacity_volume_m3 is not None and vehicle.capacity_volume_m3 > 0:
        if (planned_load.volume_m3 or 0) > vehicle.capacity_volume_m3:
            return False
    return True


def find_route_matches(
    db: Session,
) -> List[Tuple[models.PlannedLoad, models.HaulierRoute]]:
    """
    Find all (planned_load, haulier_route) pairs that match: same day,
    load pickup within 25 miles of route's empty postcode, vehicle matches.
    """
    planned = db.query(models.PlannedLoad).all()
    routes = db.query(models.HaulierRoute).all()
    results = []
    for pl in planned:
        for route in routes:
            if planned_load_matches_route(pl, route, db):
                results.append((pl, route))
    return results
