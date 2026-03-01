"""
Distance between two lat/lon points (Haversine formula) in miles.
"""
import math

EARTH_RADIUS_MILES = 3958.8


def haversine_miles(
    lat1: float, lon1: float,
    lat2: float, lon2: float,
) -> float:
    """Return distance in miles between two WGS84 coordinates."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return EARTH_RADIUS_MILES * c
