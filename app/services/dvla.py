from typing import Any, Dict, Optional

import httpx

from app.config import get_settings


class DvlaError(Exception):
    pass


def lookup_vehicle_by_registration(registration: str) -> Optional[Dict[str, Any]]:
    """
    Call DVLA Vehicle Enquiry Service to get vehicle details.

    Returns a dict of the DVLA response on success, or None if the
    service is not configured (no API key).
    """
    settings = get_settings()
    if not settings.dvla_api_key:
        # DVLA not configured yet; caller can safely skip enrichment.
        return None

    url = f"{settings.dvla_base_url}/vehicle-enquiry/v1/vehicles"
    headers = {
        "x-api-key": settings.dvla_api_key,
        "Content-Type": "application/json",
    }
    payload = {"registrationNumber": registration.replace(" ", "").upper()}

    try:
        response = httpx.post(url, json=payload, headers=headers, timeout=10.0)
    except httpx.RequestError as exc:
        raise DvlaError(f"Error contacting DVLA service: {exc}") from exc

    if response.status_code == 404:
        # Vehicle not found – treat as no enrichment rather than an error.
        return None

    if response.status_code >= 400:
        raise DvlaError(
            f"DVLA service error {response.status_code}: {response.text}"
        )

    return response.json()

