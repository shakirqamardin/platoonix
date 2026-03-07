"""
UK postcode geocoding via postcodes.io (no API key required).
"""
from typing import Optional, Tuple
from urllib.parse import quote

import httpx

POSTCODES_IO_URL = "https://api.postcodes.io/postcodes"


def normalize_postcode(postcode: str) -> str:
    """Uppercase, strip, collapse any whitespace to none. postcodes.io accepts space or no space."""
    if not postcode:
        return ""
    s = " ".join((postcode or "").split()).strip().upper()  # collapse all whitespace
    return s.replace(" ", "")


def format_postcode_for_api(code: str) -> str:
    """UK postcodes often work better with a space before the last 3 chars (e.g. B21 2RN)."""
    if not code or len(code) < 5:
        return (code or "").strip()
    if " " in code:
        return code.strip().upper()
    # Insert space before last 3 characters (inward code)
    return (code[:-3] + " " + code[-3:]).strip().upper()


def _lookup(client: httpx.Client, api_code: str, timeout: float = 15.0) -> Optional[Tuple[float, float]]:
    """Single attempt: GET postcodes.io with URL-encoded path. Returns (lat, lon) or None."""
    path = quote(api_code, safe="")
    url = f"{POSTCODES_IO_URL}/{path}"
    try:
        r = client.get(url, timeout=timeout)
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


def get_lat_lon(postcode: str) -> Optional[Tuple[float, float]]:
    """
    Resolve a UK postcode to (latitude, longitude) using postcodes.io.
    Returns None if the postcode is invalid or the API fails.
    Tries formatted (with space) first, then compact (no space) as fallback.
    """
    code = normalize_postcode(postcode)
    if not code or len(code) < 5:
        return None
    api_code = format_postcode_for_api(code)
    compact = code  # no space
    try:
        with httpx.Client(timeout=15.0) as client:
            result = _lookup(client, api_code)
            if result is not None:
                return result
            if compact != api_code:
                result = _lookup(client, compact)
            if result is not None:
                return result
            # One retry for flaky networks (e.g. server to postcodes.io)
            result = _lookup(client, api_code)
            if result is not None:
                return result
            if compact != api_code:
                result = _lookup(client, compact)
            return result
    except Exception:
        return None
