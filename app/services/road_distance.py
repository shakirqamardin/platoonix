"""Driving or straight-line distance between UK postcodes (Google optional, Haversine fallback)."""
from __future__ import annotations

from typing import Literal, Optional, Tuple

import httpx

from app.services.distance import haversine_miles
from app.services.geocode import format_postcode_for_api, get_lat_lon, normalize_postcode


def _haversine_miles_between_postcodes(pickup: str, delivery: str) -> Optional[float]:
    a = get_lat_lon(pickup)
    b = get_lat_lon(delivery)
    if not a or not b:
        return None
    return round(haversine_miles(a[0], a[1], b[0], b[1]), 1)


def google_driving_distance_miles(
    pickup_postcode: str,
    delivery_postcode: str,
    api_key: str,
    timeout: float = 12.0,
) -> Optional[Tuple[float, str]]:
    """
    Google Distance Matrix API. Returns (miles, source_label) or None.
    origins/destinations as postcode strings (UK).
    """
    if not api_key or not api_key.strip():
        return None
    o = normalize_postcode(pickup_postcode)
    d = normalize_postcode(delivery_postcode)
    if len(o) < 5 or len(d) < 5:
        return None
    # Prefer formatted postcodes for geocoding quality
    origin = format_postcode_for_api(o)
    dest = format_postcode_for_api(d)
    url = "https://maps.googleapis.com/maps/api/distancematrix/json"
    params = {
        "origins": origin,
        "destinations": dest,
        "units": "imperial",
        "key": api_key.strip(),
    }
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.get(url, params=params)
            if r.status_code != 200:
                return None
            data = r.json()
    except Exception:
        return None
    if data.get("status") not in ("OK",):
        return None
    rows = data.get("rows") or []
    if not rows:
        return None
    elements = (rows[0] or {}).get("elements") or []
    if not elements:
        return None
    el = elements[0]
    if el.get("status") not in ("OK",):
        return None
    dist = el.get("distance") or {}
    meters = dist.get("value")
    if meters is None:
        return None
    miles = float(meters) / 1609.344
    return (round(miles, 1), "Google road distance")


def resolve_distance_miles(
    pickup_postcode: str,
    delivery_postcode: str,
    google_api_key: Optional[str],
) -> Tuple[Optional[float], Literal["google", "haversine"], str]:
    """
    Return (miles, source, note). miles None if postcodes invalid.
    """
    if google_api_key:
        g = google_driving_distance_miles(pickup_postcode, delivery_postcode, google_api_key)
        if g is not None:
            return g[0], "google", g[1]
    h = _haversine_miles_between_postcodes(pickup_postcode, delivery_postcode)
    if h is None:
        return None, "haversine", "Could not resolve postcodes"
    return h, "haversine", "Straight-line miles (set GOOGLE_MAPS_API_KEY for road distance)"
