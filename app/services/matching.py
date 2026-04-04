"""
Smart matching: find open loads within radius that suit a vehicle (trailer type, capacity).
Distances are road miles via OpenRouteService, Mapbox Matrix, or Google Distance Matrix (in that order) — not straight-line.
"""
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from app import models
from app.config import get_settings
from app.services.geocode import get_lat_lon, normalize_postcode
from app.services.road_distance import (
    road_corridor_distances,
    road_distances_from_origin_to_postcodes,
    single_road_miles_between_postcodes,
)


def vehicle_satisfies_load_equipment_hard(
    vehicle: models.Vehicle,
    load: models.Load,
) -> bool:
    """
    When load marks a requirement True, the vehicle must have the matching capability.
    If not, exclude this load from results (not a near match).
    """
    if getattr(load, "requires_tail_lift", False) and not getattr(vehicle, "has_tail_lift", False):
        return False
    if getattr(load, "requires_forklift", False) and not getattr(vehicle, "has_moffett", False):
        return False
    if getattr(load, "requires_temp_control", False) and not getattr(vehicle, "has_temp_control", False):
        return False
    if getattr(load, "requires_adr", False) and not getattr(vehicle, "is_adr_certified", False):
        return False
    return True


def find_matching_loads(
    vehicle_id: int,
    origin_postcode: str,
    db: Session,
    radius_miles: Optional[int] = None,
) -> List[Tuple[models.Load, float, bool, List[str]]]:
    """
    Find open loads whose pickup is within radius_miles (road) of origin_postcode, considering vehicle
    trailer type and capacity. Returns list of (load, distance_miles, is_perfect_match, mismatch_reasons) sorted by match quality then distance.
    """
    settings = get_settings()
    radius = radius_miles if radius_miles is not None else settings.default_backhaul_radius_miles

    vehicle = db.get(models.Vehicle, vehicle_id)
    if not vehicle:
        return []

    origin_postcode = (origin_postcode or "").strip().upper()
    if not get_lat_lon(origin_postcode):
        return []

    open_loads = (
        db.query(models.Load)
        .filter(models.Load.status == models.LoadStatusEnum.OPEN.value)
        .all()
    )

    pickup_pcs = [load.pickup_postcode for load in open_loads]
    dist_map, src = road_distances_from_origin_to_postcodes(
        origin_postcode,
        pickup_pcs,
        settings.openrouteservice_api_key,
        settings.mapbox_access_token,
        settings.google_maps_api_key,
    )
    if src == "none":
        return []

    results = []
    for load in open_loads:
        pc = normalize_postcode(load.pickup_postcode)
        distance = dist_map.get(pc)
        if distance is None or distance > radius:
            continue

        if not vehicle_satisfies_load_equipment_hard(vehicle, load):
            continue

        # Check for perfect match vs near match
        is_perfect_match = True
        mismatch_reasons = []

        req = load.requirements or {}
        if not isinstance(req, dict):
            req = {}

        required_vehicle_type = (req.get("vehicle_type") or "").strip().lower()
        if required_vehicle_type and (vehicle.vehicle_type or "").strip().lower() != required_vehicle_type:
            is_perfect_match = False
            mismatch_reasons.append(f"Vehicle type: need {required_vehicle_type}")

        required_trailer = (req.get("trailer_type") or "").strip().lower()
        if required_trailer and (vehicle.trailer_type or "").strip().lower() != required_trailer:
            is_perfect_match = False
            mismatch_reasons.append(f"Trailer: need {required_trailer}")

        if vehicle.capacity_weight_kg is not None and vehicle.capacity_weight_kg > 0:
            if (load.weight_kg or 0) > vehicle.capacity_weight_kg:
                continue
        if vehicle.capacity_volume_m3 is not None and vehicle.capacity_volume_m3 > 0:
            if (load.volume_m3 or 0) > vehicle.capacity_volume_m3:
                continue

        results.append((load, round(distance, 1), is_perfect_match, mismatch_reasons))

    # Sort perfect matches first, then by distance
    results.sort(key=lambda x: (not x[2], x[1]))
    return results


def load_matches_vehicle(
    load: models.Load,
    vehicle_id: int,
    origin_postcode: str,
    db: Session,
    radius_miles: Optional[int] = None,
) -> bool:
    """
    Return True if this load is within radius of origin_postcode (road) and matches
    the vehicle's trailer type and capacity. Used for real-time alerts when a new load is posted.
    """
    settings = get_settings()
    radius = radius_miles if radius_miles is not None else settings.default_backhaul_radius_miles

    vehicle = db.get(models.Vehicle, vehicle_id)
    if not vehicle:
        return False

    distance = single_road_miles_between_postcodes(
        origin_postcode,
        load.pickup_postcode,
        settings.openrouteservice_api_key,
        settings.mapbox_access_token,
        settings.google_maps_api_key,
    )
    if distance is None or distance > radius:
        return False

    if not vehicle_satisfies_load_equipment_hard(vehicle, load):
        return False

    req = load.requirements or {}
    if not isinstance(req, dict):
        req = {}
    required_vehicle_type = (req.get("vehicle_type") or "").strip().lower()
    if required_vehicle_type and (vehicle.vehicle_type or "").strip().lower() != required_vehicle_type:
        return False
    required_trailer = (req.get("trailer_type") or "").strip().lower()
    if required_trailer and (vehicle.trailer_type or "").strip().lower() != required_trailer:
        return False

    if vehicle.capacity_weight_kg is not None and vehicle.capacity_weight_kg > 0:
        if (load.weight_kg or 0) > vehicle.capacity_weight_kg:
            return False
    if vehicle.capacity_volume_m3 is not None and vehicle.capacity_volume_m3 > 0:
        if (load.volume_m3 or 0) > vehicle.capacity_volume_m3:
            return False

    return True


def load_matches_empty_to_base_corridor(
    load: models.Load,
    vehicle_id: int,
    origin_postcode: str,
    destination_postcode: Optional[str],
    db: Session,
    radius_miles: Optional[int] = None,
) -> bool:
    """
    True if this open load matches the subscriber's search: within default radius (e.g. 25mi)
    of empty location, or — when destination (base) is set — anywhere along the empty→base
    corridor (same rules as Find Backhaul merge of route + origin search).
    """
    dest = (destination_postcode or "").strip()
    if not dest:
        return load_matches_vehicle(load, vehicle_id, origin_postcode, db, radius_miles)
    route_results = find_matching_loads_along_route(
        vehicle_id, origin_postcode, dest, db, radius_miles
    )
    origin_results = find_matching_loads(vehicle_id, origin_postcode, db, radius_miles)
    matched_ids = {row[0].id for row in route_results} | {row[0].id for row in origin_results}
    return load.id in matched_ids


def planned_load_matches_route(
    planned_load: models.PlannedLoad,
    route: models.HaulierRoute,
    db: Session,
    radius_miles: Optional[int] = None,
) -> bool:
    """
    Return True if this planned load is on the same day, within radius of the route's
    empty-at postcode (road), and the route's vehicle matches the load (trailer, capacity).
    """
    if planned_load.day_of_week != route.day_of_week:
        return False
    settings = get_settings()
    radius = radius_miles if radius_miles is not None else settings.default_backhaul_radius_miles

    vehicle = db.get(models.Vehicle, route.vehicle_id)
    if not vehicle:
        return False

    distance = single_road_miles_between_postcodes(
        route.empty_at_postcode,
        planned_load.pickup_postcode,
        settings.openrouteservice_api_key,
        settings.mapbox_access_token,
        settings.google_maps_api_key,
    )
    if distance is None or distance > radius:
        return False

    req = planned_load.requirements or {}
    if not isinstance(req, dict):
        req = {}
    required_vehicle_type = (req.get("vehicle_type") or "").strip().lower()
    if required_vehicle_type and (vehicle.vehicle_type or "").strip().lower() != required_vehicle_type:
        return False
    required_trailer = (req.get("trailer_type") or "").strip().lower()
    if required_trailer and (vehicle.trailer_type or "").strip().lower() != required_trailer:
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


def _pickup_in_road_corridor(
    d_fp: Optional[float],
    d_pt: Optional[float],
    d_ft: Optional[float],
    radius: float,
) -> bool:
    """True if pickup is within radius of an endpoint or extra road vs direct leg is small."""
    if d_fp is None or d_pt is None:
        return False
    if min(d_fp, d_pt) <= radius:
        return True
    if d_ft is None:
        return False
    return (d_fp + d_pt - d_ft) <= 2 * radius


def _corridor_display_miles(
    d_fp: Optional[float],
    d_pt: Optional[float],
    d_ft: Optional[float],
    radius: float,
) -> float:
    """Single distance for sorting/display when pickup is in corridor."""
    if d_fp is None or d_pt is None:
        return float("inf")
    if min(d_fp, d_pt) <= radius:
        return min(d_fp, d_pt)
    if d_ft is not None:
        return max(0.0, round(d_fp + d_pt - d_ft, 1))
    return min(d_fp, d_pt)


def find_matching_loads_along_route(
    vehicle_id: int,
    from_postcode: str,
    to_postcode: str,
    db: Session,
    radius_miles: Optional[int] = None,
) -> List[Tuple[models.Load, float, bool, List[str]]]:
    """
    Find open loads whose pickup is within radius (road) of the empty→base corridor:
    near either end, or with small extra driving vs the direct road leg from→to.
    Returns list of (load, distance_miles, is_perfect_match, mismatch_reasons) sorted by match quality then distance.
    """
    settings = get_settings()
    radius = float(radius_miles if radius_miles is not None else settings.default_backhaul_radius_miles)

    vehicle = db.get(models.Vehicle, vehicle_id)
    if not vehicle:
        return []

    from_pc = (from_postcode or "").strip().upper()
    to_pc = (to_postcode or "").strip().upper()
    if not get_lat_lon(from_pc) or not get_lat_lon(to_pc):
        return []

    open_loads = (
        db.query(models.Load)
        .filter(models.Load.status == models.LoadStatusEnum.OPEN.value)
        .all()
    )

    pickup_pcs = [load.pickup_postcode for load in open_loads]
    d_ft, pair_miles, src = road_corridor_distances(
        from_pc,
        to_pc,
        pickup_pcs,
        settings.openrouteservice_api_key,
        settings.mapbox_access_token,
        settings.google_maps_api_key,
    )
    if src == "none":
        return []

    results = []
    for load in open_loads:
        pc = normalize_postcode(load.pickup_postcode)
        d_fp, d_pt = pair_miles.get(pc, (None, None))
        if not _pickup_in_road_corridor(d_fp, d_pt, d_ft, radius):
            continue

        min_dist = _corridor_display_miles(d_fp, d_pt, d_ft, radius)
        if min_dist == float("inf"):
            continue

        if not vehicle_satisfies_load_equipment_hard(vehicle, load):
            continue

        req = load.requirements or {}
        if not isinstance(req, dict):
            req = {}
        # Check for perfect match vs near match
        is_perfect_match = True
        mismatch_reasons = []

        required_vehicle_type = (req.get("vehicle_type") or "").strip().lower()
        if required_vehicle_type and (vehicle.vehicle_type or "").strip().lower() != required_vehicle_type:
            is_perfect_match = False
            mismatch_reasons.append(f"Vehicle type: need {required_vehicle_type}")

        required_trailer = (req.get("trailer_type") or "").strip().lower()
        if required_trailer and (vehicle.trailer_type or "").strip().lower() != required_trailer:
            is_perfect_match = False
            mismatch_reasons.append(f"Trailer: need {required_trailer}")
        if vehicle.capacity_weight_kg is not None and vehicle.capacity_weight_kg > 0:
            if (load.weight_kg or 0) > vehicle.capacity_weight_kg:
                continue
        if vehicle.capacity_volume_m3 is not None and vehicle.capacity_volume_m3 > 0:
            if (load.volume_m3 or 0) > vehicle.capacity_volume_m3:
                continue

        results.append((load, round(min_dist, 1), is_perfect_match, mismatch_reasons))

    # Sort perfect matches first, then by distance
    results.sort(key=lambda x: (not x[2], x[1]))
    return results
