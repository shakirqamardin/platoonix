"""GPS + photo verification (Tier 3)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Tuple

from PIL import Image
from PIL.ExifTags import GPSTAGS, TAGS

from app.services.distance import haversine_miles
from app.services.geocode import get_lat_lon


def _ratio_to_float(x) -> float:
    try:
        return float(x.numerator) / float(x.denominator)
    except Exception:
        return float(x)


def _convert_to_degrees(value) -> float:
    """Convert EXIF GPS DMS tuple to decimal degrees."""
    if not value or len(value) < 3:
        return 0.0
    d, m, s = value[0], value[1], value[2]
    deg = _ratio_to_float(d) + _ratio_to_float(m) / 60.0 + _ratio_to_float(s) / 3600.0
    return deg


def _gps_from_exif_dict(gps_info: dict) -> Optional[Tuple[float, float]]:
    if not gps_info:
        return None
    lat = gps_info.get("GPSLatitude") or gps_info.get(2)
    lng = gps_info.get("GPSLongitude") or gps_info.get(4)
    lat_ref = gps_info.get("GPSLatitudeRef") or gps_info.get(1)
    lng_ref = gps_info.get("GPSLongitudeRef") or gps_info.get(3)
    if lat is None or lng is None:
        return None
    lat_d = _convert_to_degrees(lat)
    lng_d = _convert_to_degrees(lng)
    if isinstance(lat_ref, bytes):
        lat_ref = lat_ref.decode("ascii", errors="ignore")
    if isinstance(lng_ref, bytes):
        lng_ref = lng_ref.decode("ascii", errors="ignore")
    if lat_ref == "S":
        lat_d = -lat_d
    if lng_ref == "W":
        lng_d = -lng_d
    return (lat_d, lng_d)


def extract_gps_from_photo(photo_path: str) -> Optional[Tuple[float, float, Optional[datetime]]]:
    """
    Extract GPS coordinates and best-effort timestamp from photo EXIF.
    Returns (latitude, longitude, timestamp) or None.
    """
    try:
        with Image.open(photo_path) as image:
            exif = image.getexif()
            if exif is None:
                return None
            ts: Optional[datetime] = None
            for tag_id, val in exif.items():
                name = TAGS.get(tag_id, tag_id)
                if name == "DateTime":
                    try:
                        ts = datetime.strptime(str(val), "%Y:%m:%d %H:%M:%S")
                    except Exception:
                        pass
            gps_decoded: dict = {}
            if hasattr(exif, "get_ifd"):
                gps_ifd = exif.get_ifd(0x8825)
                if gps_ifd:
                    gps_decoded = {GPSTAGS.get(k, k): v for k, v in gps_ifd.items()}
            if not gps_decoded:
                return None
            coords = _gps_from_exif_dict(gps_decoded)
            if not coords:
                return None
            lat, lng = coords
            return (lat, lng, ts)
    except Exception:
        return None


def verify_gps_location(
    photo_lat: float,
    photo_lng: float,
    delivery_postcode: str,
    photo_timestamp: Optional[datetime],
) -> bool:
    """True if within 1 mile of delivery postcode centroid and photo is recent (24h)."""
    delivery_coords = get_lat_lon(delivery_postcode)
    if not delivery_coords:
        return False
    dlat, dlng = delivery_coords
    dist = haversine_miles(photo_lat, photo_lng, dlat, dlng)
    if dist > 1.0:
        return False
    now = datetime.now(timezone.utc)
    if photo_timestamp is None:
        return False
    pt = photo_timestamp
    if pt.tzinfo is None:
        pt = pt.replace(tzinfo=timezone.utc)
    hours = abs((now - pt).total_seconds() / 3600.0)
    if hours > 24:
        return False
    return True
