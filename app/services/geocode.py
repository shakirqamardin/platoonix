"""
UK postcode geocoding via postcodes.io (no API key required).
"""
from typing import Optional, Tuple

import httpx

POSTCODES_IO_URL = "https://api.postcodes.io/postcodes"


def normalize_postcode(postcode: str) -> str:
    """Uppercase and strip; postcodes.io accepts space or no space."""
    return (postcode or "").strip().upper().replace(" ", "")


def get_lat_lon(postcode: str) -> Optional[Tuple[float, float]]:
    """
    Resolve a UK postcode to (latitude, longitude) using postcodes.io.
    Returns None if the postcode is invalid or the API fails.
    """
    code = normalize_postcode(postcode)
    if not code:
        return None
    try:
        with httpx.Client(timeout=10.0) as client:
            r = client.get(f"{POSTCODES_IO_URL}/{code}")
            if r.status_code != 200:
                return None
            data = r.json()
            if not data.get("result"):
                return None
            lat = data["result"].get("latitude")
            lon = data["result"].get("longitude")
            if lat is None or lon is None:
                return None
            return (float(lat), float(lon))
    except Exception:
        return None
