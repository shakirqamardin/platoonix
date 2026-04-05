"""Driving distance between UK postcodes: OpenRouteService, Mapbox Matrix, or Google (no straight-line fallback)."""
from __future__ import annotations

from typing import Dict, List, Literal, Optional, Tuple

import httpx

from app.services.geocode import format_postcode_for_api, get_lat_lon, normalize_postcode

DistanceSource = Literal["openrouteservice", "mapbox", "google", "none"]

# Try HGV first, then car (some API keys / tiers reject driving-hgv or return 4xx for matrix).
ORS_MATRIX_PROFILES = ("driving-hgv", "driving-car")
ORS_MATRIX_API = "https://api.openrouteservice.org/v2/matrix"
# HGV profile for haulage; batch size kept conservative for free-tier limits
ORS_MATRIX_MAX_DESTINATIONS = 50
# Mapbox Matrix: max 25 coordinates per request (origin + destinations)
MAPBOX_MATRIX_MAX_DESTINATIONS = 24
GOOGLE_MATRIX_MAX_DESTINATIONS = 25
MAPBOX_MATRIX_BASE = "https://api.mapbox.com/directions-matrix/v1/mapbox/driving"


def _lonlat(lat: float, lon: float) -> List[float]:
    return [lon, lat]


def _ors_strip_bearer_prefix(api_key: str) -> str:
    k = api_key.strip()
    if k.lower().startswith("bearer "):
        k = k[7:].strip()
    return k


def _ors_meters_to_miles(meters: float) -> float:
    return round(float(meters) / 1609.344, 1)


def _ors_call_matrix(api_key: str, body: dict, timeout: float) -> Optional[dict]:
    """
    POST ORS matrix. Distances are in metres (omit units in body — avoids 4xx on some deployments).
    Tries driving-hgv then driving-car; Authorization as raw key then Bearer <key>.
    """
    key = _ors_strip_bearer_prefix(api_key)
    if not key:
        return None
    for profile in ORS_MATRIX_PROFILES:
        url = f"{ORS_MATRIX_API}/{profile}"
        for auth_val in (key, f"Bearer {key}"):
            headers = {"Authorization": auth_val, "Content-Type": "application/json"}
            try:
                with httpx.Client(timeout=timeout) as client:
                    r = client.post(url, json=body, headers=headers)
                    if r.status_code == 200:
                        return r.json()
            except Exception:
                continue
    return None


def ors_matrix_one_to_many_miles(
    api_key: str,
    origin_lat: float,
    origin_lon: float,
    dest_latlons: List[Tuple[float, float]],
    timeout: float = 45.0,
) -> List[Optional[float]]:
    """
    Road distances in miles from one origin to many destinations (same order as dest_latlons).
    Uses OpenRouteService matrix API. None for unreachable pairs.
    """
    if not api_key or not api_key.strip() or not dest_latlons:
        return [None] * len(dest_latlons)
    out: List[Optional[float]] = []
    for start in range(0, len(dest_latlons), ORS_MATRIX_MAX_DESTINATIONS):
        chunk = dest_latlons[start : start + ORS_MATRIX_MAX_DESTINATIONS]
        locations = [_lonlat(origin_lat, origin_lon)] + [_lonlat(lat, lon) for lat, lon in chunk]
        n = len(locations)
        body = {
            "locations": locations,
            "sources": [0],
            "destinations": list(range(1, n)),
            "metrics": ["distance"],
        }
        data = _ors_call_matrix(api_key, body, timeout)
        if not data:
            out.extend([None] * len(chunk))
            continue
        rows = data.get("distances") or []
        if not rows or not isinstance(rows[0], list):
            out.extend([None] * len(chunk))
            continue
        for v in rows[0]:
            if v is None:
                out.append(None)
            else:
                try:
                    out.append(_ors_meters_to_miles(v))
                except (TypeError, ValueError):
                    out.append(None)
    return out


def google_matrix_one_to_many_miles(
    origin_postcode: str,
    dest_postcodes: List[str],
    api_key: str,
    timeout: float = 20.0,
) -> List[Optional[float]]:
    """Google Distance Matrix: one origin postcode to many destinations (same order)."""
    if not api_key or not api_key.strip() or not dest_postcodes:
        return [None] * len(dest_postcodes)
    o = normalize_postcode(origin_postcode)
    if len(o) < 5:
        return [None] * len(dest_postcodes)
    origin = format_postcode_for_api(o)
    key = api_key.strip()
    url = "https://maps.googleapis.com/maps/api/distancematrix/json"
    out: List[Optional[float]] = []
    for start in range(0, len(dest_postcodes), GOOGLE_MATRIX_MAX_DESTINATIONS):
        chunk = dest_postcodes[start : start + GOOGLE_MATRIX_MAX_DESTINATIONS]
        dest_parts = []
        for pc in chunk:
            d = normalize_postcode(pc)
            if len(d) < 5:
                dest_parts.append("")
            else:
                dest_parts.append(format_postcode_for_api(d))
        if any(not p for p in dest_parts):
            out.extend([None] * len(chunk))
            continue
        params = {
            "origins": origin,
            "destinations": "|".join(dest_parts),
            "units": "imperial",
            "key": key,
        }
        try:
            with httpx.Client(timeout=timeout) as client:
                r = client.get(url, params=params)
                if r.status_code != 200:
                    out.extend([None] * len(chunk))
                    continue
                data = r.json()
        except Exception:
            out.extend([None] * len(chunk))
            continue
        if data.get("status") not in ("OK",):
            out.extend([None] * len(chunk))
            continue
        rows = data.get("rows") or []
        if not rows:
            out.extend([None] * len(chunk))
            continue
        elements = (rows[0] or {}).get("elements") or []
        for el in elements:
            if not isinstance(el, dict) or el.get("status") not in ("OK",):
                out.append(None)
                continue
            dist = el.get("distance") or {}
            meters = dist.get("value")
            if meters is None:
                out.append(None)
            else:
                out.append(round(float(meters) / 1609.344, 1))
        if len(elements) < len(chunk):
            out.extend([None] * (len(chunk) - len(elements)))
    return out


def mapbox_matrix_one_to_many_miles(
    access_token: str,
    origin_lat: float,
    origin_lon: float,
    dest_latlons: List[Tuple[float, float]],
    timeout: float = 25.0,
) -> List[Optional[float]]:
    """Mapbox Matrix API: road distances in miles from one origin to many (lat, lon) points."""
    if not access_token or not access_token.strip() or not dest_latlons:
        return [None] * len(dest_latlons)
    token = access_token.strip()
    out: List[Optional[float]] = []
    for start in range(0, len(dest_latlons), MAPBOX_MATRIX_MAX_DESTINATIONS):
        chunk = dest_latlons[start : start + MAPBOX_MATRIX_MAX_DESTINATIONS]
        parts = [f"{origin_lon},{origin_lat}"] + [f"{lon},{lat}" for lat, lon in chunk]
        coord_path = ";".join(parts)
        url = f"{MAPBOX_MATRIX_BASE}/{coord_path}"
        params = {
            "access_token": token,
            "sources": "0",
            "annotations": "distance",
            "destinations": ";".join(str(i) for i in range(1, len(parts))),
        }
        try:
            with httpx.Client(timeout=timeout) as client:
                r = client.get(url, params=params)
                if r.status_code != 200:
                    out.extend([None] * len(chunk))
                    continue
                data = r.json()
        except Exception:
            out.extend([None] * len(chunk))
            continue
        dist_block = data.get("distances")
        if not dist_block or not isinstance(dist_block, list) or not dist_block[0]:
            out.extend([None] * len(chunk))
            continue
        row = dist_block[0]
        for j in range(len(chunk)):
            v = row[j] if j < len(row) else None
            if v is None:
                out.append(None)
            else:
                try:
                    out.append(round(float(v) / 1609.344, 1))
                except (TypeError, ValueError):
                    out.append(None)
    return out


def _mapbox_many_to_one_to_location_miles(
    access_token: str,
    pickup_latlons: List[Tuple[float, float]],
    dest_lat: float,
    dest_lon: float,
    timeout: float = 25.0,
) -> List[Optional[float]]:
    """Road miles from each pickup to dest (same order)."""
    if not pickup_latlons:
        return []
    token = access_token.strip()
    out: List[Optional[float]] = []
    for start in range(0, len(pickup_latlons), MAPBOX_MATRIX_MAX_DESTINATIONS):
        chunk = pickup_latlons[start : start + MAPBOX_MATRIX_MAX_DESTINATIONS]
        parts = [f"{dest_lon},{dest_lat}"] + [f"{lon},{lat}" for lat, lon in chunk]
        coord_path = ";".join(parts)
        url = f"{MAPBOX_MATRIX_BASE}/{coord_path}"
        params = {
            "access_token": token,
            "sources": ";".join(str(i) for i in range(1, len(parts))),
            "destinations": "0",
            "annotations": "distance",
        }
        try:
            with httpx.Client(timeout=timeout) as client:
                r = client.get(url, params=params)
                if r.status_code != 200:
                    out.extend([None] * len(chunk))
                    continue
                data = r.json()
        except Exception:
            out.extend([None] * len(chunk))
            continue
        dist_block = data.get("distances") or []
        for i in range(len(chunk)):
            if i < len(dist_block) and dist_block[i] and dist_block[i][0] is not None:
                try:
                    out.append(round(float(dist_block[i][0]) / 1609.344, 1))
                except (TypeError, ValueError):
                    out.append(None)
            else:
                out.append(None)
    return out


def google_driving_distance_miles(
    pickup_postcode: str,
    delivery_postcode: str,
    api_key: str,
    timeout: float = 12.0,
) -> Optional[Tuple[float, str]]:
    """Single origin–destination pair via Google Distance Matrix."""
    res = google_matrix_one_to_many_miles(pickup_postcode, [delivery_postcode], api_key, timeout=timeout)
    if not res or res[0] is None:
        return None
    return (res[0], "Google road distance")


def ors_driving_distance_miles(
    pickup_postcode: str,
    delivery_postcode: str,
    api_key: str,
    timeout: float = 20.0,
) -> Optional[Tuple[float, str]]:
    """Single pair: geocode postcodes then ORS matrix."""
    a = get_lat_lon(pickup_postcode)
    b = get_lat_lon(delivery_postcode)
    if not a or not b:
        return None
    row = ors_matrix_one_to_many_miles(api_key, a[0], a[1], [b], timeout=timeout)
    if not row or row[0] is None:
        return None
    return (row[0], "OpenRouteService road distance")


def mapbox_driving_distance_miles(
    pickup_postcode: str,
    delivery_postcode: str,
    access_token: str,
    timeout: float = 20.0,
) -> Optional[Tuple[float, str]]:
    """Single pair via Mapbox Matrix (postcodes geocoded first)."""
    a = get_lat_lon(pickup_postcode)
    b = get_lat_lon(delivery_postcode)
    if not a or not b:
        return None
    row = mapbox_matrix_one_to_many_miles(access_token, a[0], a[1], [b], timeout=timeout)
    if not row or row[0] is None:
        return None
    return (row[0], "Mapbox road distance")


def road_distances_from_origin_to_postcodes(
    origin_postcode: str,
    pickup_postcodes: List[str],
    ors_api_key: Optional[str],
    mapbox_access_token: Optional[str],
    google_api_key: Optional[str],
) -> Tuple[Dict[str, Optional[float]], DistanceSource]:
    """
    Map normalized pickup postcode -> road miles from origin. Missing keys => no distances.
    """
    if not pickup_postcodes:
        return {}, "none"
    origin_pc = normalize_postcode(origin_postcode)
    if len(origin_pc) < 5:
        return {}, "none"
    origin_ll = get_lat_lon(origin_postcode)
    if not origin_ll:
        return {}, "none"

    # Preserve first-seen order of unique postcodes
    seen: Dict[str, None] = {}
    unique_pcs: List[str] = []
    for raw in pickup_postcodes:
        pc = normalize_postcode(raw or "")
        if len(pc) < 5 or pc in seen:
            continue
        seen[pc] = None
        unique_pcs.append(pc)

    if not unique_pcs:
        return {}, "none"

    result: Dict[str, Optional[float]] = {pc: None for pc in unique_pcs}

    if ors_api_key and ors_api_key.strip():
        latlons: List[Tuple[float, float]] = []
        valid_pcs: List[str] = []
        for pc in unique_pcs:
            ll = get_lat_lon(pc)
            if ll:
                latlons.append(ll)
                valid_pcs.append(pc)
            else:
                result[pc] = None
        if latlons:
            miles_list = ors_matrix_one_to_many_miles(
                ors_api_key, origin_ll[0], origin_ll[1], latlons
            )
            for pc, mi in zip(valid_pcs, miles_list):
                result[pc] = mi
        if any(v is not None for v in result.values()):
            return result, "openrouteservice"

    if mapbox_access_token and mapbox_access_token.strip():
        latlons_mb: List[Tuple[float, float]] = []
        valid_pcs_mb: List[str] = []
        for pc in unique_pcs:
            ll = get_lat_lon(pc)
            if ll:
                latlons_mb.append(ll)
                valid_pcs_mb.append(pc)
        if latlons_mb:
            miles_list = mapbox_matrix_one_to_many_miles(
                mapbox_access_token, origin_ll[0], origin_ll[1], latlons_mb
            )
            for pc, mi in zip(valid_pcs_mb, miles_list):
                result[pc] = mi
        if any(v is not None for v in result.values()):
            return result, "mapbox"

    if google_api_key and google_api_key.strip():
        miles_list = google_matrix_one_to_many_miles(
            origin_postcode, unique_pcs, google_api_key
        )
        for pc, mi in zip(unique_pcs, miles_list):
            if result.get(pc) is None:
                result[pc] = mi
        if any(v is not None for v in result.values()):
            return result, "google"

    return result, "none"


def _ors_many_to_one_to_location_miles(
    api_key: str,
    pickup_latlons: List[Tuple[float, float]],
    dest_lat: float,
    dest_lon: float,
    timeout: float = 45.0,
) -> List[Optional[float]]:
    """Road miles from each pickup to dest (same order as pickup_latlons)."""
    if not pickup_latlons:
        return []
    out: List[Optional[float]] = []
    for start in range(0, len(pickup_latlons), ORS_MATRIX_MAX_DESTINATIONS):
        chunk = pickup_latlons[start : start + ORS_MATRIX_MAX_DESTINATIONS]
        locations = [_lonlat(dest_lat, dest_lon)] + [_lonlat(lat, lon) for lat, lon in chunk]
        n = len(locations)
        body = {
            "locations": locations,
            "sources": list(range(1, n)),
            "destinations": [0],
            "metrics": ["distance"],
        }
        data = _ors_call_matrix(api_key, body, timeout)
        if not data:
            out.extend([None] * len(chunk))
            continue
        dists = data.get("distances") or []
        for row in dists:
            if row and row[0] is not None:
                try:
                    out.append(_ors_meters_to_miles(row[0]))
                except (TypeError, ValueError):
                    out.append(None)
            else:
                out.append(None)
    return out


def road_corridor_distances(
    from_postcode: str,
    to_postcode: str,
    pickup_postcodes: List[str],
    ors_api_key: Optional[str],
    mapbox_access_token: Optional[str],
    google_api_key: Optional[str],
) -> Tuple[
    Optional[float],
    Dict[str, Tuple[Optional[float], Optional[float]]],
    DistanceSource,
]:
    """
    d_ft = road miles from -> to. For each pickup PC, (d_fp, d_pt) = road(from,pickup), road(pickup,to).
    """
    fpc = normalize_postcode(from_postcode or "")
    tpc = normalize_postcode(to_postcode or "")
    if len(fpc) < 5 or len(tpc) < 5:
        return None, {}, "none"

    seen: Dict[str, None] = {}
    unique_pcs: List[str] = []
    for raw in pickup_postcodes:
        pc = normalize_postcode(raw or "")
        if len(pc) < 5 or pc in seen:
            continue
        seen[pc] = None
        unique_pcs.append(pc)

    pair_miles: Dict[str, Tuple[Optional[float], Optional[float]]] = {pc: (None, None) for pc in unique_pcs}

    if ors_api_key and ors_api_key.strip():
        from_ll = get_lat_lon(from_postcode)
        to_ll = get_lat_lon(to_postcode)
        if from_ll and to_ll:
            d_ft_row = ors_matrix_one_to_many_miles(ors_api_key, from_ll[0], from_ll[1], [to_ll])
            d_ft = d_ft_row[0] if d_ft_row else None
            latlons: List[Tuple[float, float]] = []
            pcs_ok: List[str] = []
            for pc in unique_pcs:
                ll = get_lat_lon(pc)
                if ll:
                    latlons.append(ll)
                    pcs_ok.append(pc)
            d_fp_list = ors_matrix_one_to_many_miles(ors_api_key, from_ll[0], from_ll[1], latlons)
            d_pt_list = _ors_many_to_one_to_location_miles(ors_api_key, latlons, to_ll[0], to_ll[1])
            for i, pc in enumerate(pcs_ok):
                fp = d_fp_list[i] if i < len(d_fp_list) else None
                pt = d_pt_list[i] if i < len(d_pt_list) else None
                pair_miles[pc] = (fp, pt)
            if d_ft is not None or any(
                fp is not None or pt is not None for fp, pt in pair_miles.values()
            ):
                return d_ft, pair_miles, "openrouteservice"

    if mapbox_access_token and mapbox_access_token.strip():
        from_ll = get_lat_lon(from_postcode)
        to_ll = get_lat_lon(to_postcode)
        if from_ll and to_ll:
            tok = mapbox_access_token.strip()
            d_ft_row = mapbox_matrix_one_to_many_miles(tok, from_ll[0], from_ll[1], [to_ll])
            d_ft = d_ft_row[0] if d_ft_row else None
            latlons: List[Tuple[float, float]] = []
            pcs_ok: List[str] = []
            for pc in unique_pcs:
                ll = get_lat_lon(pc)
                if ll:
                    latlons.append(ll)
                    pcs_ok.append(pc)
            d_fp_list = mapbox_matrix_one_to_many_miles(tok, from_ll[0], from_ll[1], latlons)
            d_pt_list = _mapbox_many_to_one_to_location_miles(tok, latlons, to_ll[0], to_ll[1])
            for i, pc in enumerate(pcs_ok):
                fp = d_fp_list[i] if i < len(d_fp_list) else None
                pt = d_pt_list[i] if i < len(d_pt_list) else None
                pair_miles[pc] = (fp, pt)
            if d_ft is not None or any(
                fp is not None or pt is not None for fp, pt in pair_miles.values()
            ):
                return d_ft, pair_miles, "mapbox"

    if google_api_key and google_api_key.strip():
        g = google_driving_distance_miles(from_postcode, to_postcode, google_api_key)
        d_ft = g[0] if g else None
        d_fp_list = google_matrix_one_to_many_miles(from_postcode, unique_pcs, google_api_key)
        d_pt_list = google_matrix_one_to_many_miles(to_postcode, unique_pcs, google_api_key)
        for i, pc in enumerate(unique_pcs):
            fp = d_fp_list[i] if i < len(d_fp_list) else None
            pt = d_pt_list[i] if i < len(d_pt_list) else None
            pair_miles[pc] = (fp, pt)
        return d_ft, pair_miles, "google"

    return None, pair_miles, "none"


def resolve_distance_miles(
    pickup_postcode: str,
    delivery_postcode: str,
    ors_api_key: Optional[str],
    mapbox_access_token: Optional[str],
    google_api_key: Optional[str],
) -> Tuple[Optional[float], DistanceSource, str]:
    """
    Road miles only. Try OPENROUTESERVICE_API_KEY, then MAPBOX_ACCESS_TOKEN, then GOOGLE_MAPS_API_KEY.
    """
    if ors_api_key and ors_api_key.strip():
        o = ors_driving_distance_miles(pickup_postcode, delivery_postcode, ors_api_key)
        if o is not None:
            return o[0], "openrouteservice", o[1]
    if mapbox_access_token and mapbox_access_token.strip():
        m = mapbox_driving_distance_miles(pickup_postcode, delivery_postcode, mapbox_access_token)
        if m is not None:
            return m[0], "mapbox", m[1]
    if google_api_key and google_api_key.strip():
        g = google_driving_distance_miles(pickup_postcode, delivery_postcode, google_api_key)
        if g is not None:
            return g[0], "google", g[1]
    return (
        None,
        "none",
        "Road distance unavailable (set OPENROUTESERVICE_API_KEY, MAPBOX_ACCESS_TOKEN, or GOOGLE_MAPS_API_KEY)",
    )


def single_road_miles_between_postcodes(
    a_postcode: str,
    b_postcode: str,
    ors_api_key: Optional[str],
    mapbox_access_token: Optional[str],
    google_api_key: Optional[str],
) -> Optional[float]:
    """Convenience for one pair; returns miles or None."""
    mi, src, _ = resolve_distance_miles(
        a_postcode, b_postcode, ors_api_key, mapbox_access_token, google_api_key
    )
    return mi if src != "none" else None
