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


def suggest_vehicle_form_from_dvla(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Map DVLA API response to form fields: vehicle_type (artic/rigid/van), optional trailer hint.
    DVLA returns revenueWeight, wheelplan, make, model, colour, yearOfManufacture, motStatus, taxStatus, fuelType, etc.

    Note: motStatus may be the literal string "No details held by DVLA" — that is a normal enum value
    meaning DVLA does not expose MOT test data in this API for that vehicle, not a failure. Live MOT
    history is held by DVSA (separate MOT History API / check-mot.service.gov.uk).
    """
    year_val = data.get("yearOfManufacture")
    try:
        year_int = int(year_val) if year_val is not None else None
    except (TypeError, ValueError):
        year_int = None

    out: Dict[str, Any] = {
        "vehicle_type": "rigid",
        "trailer_type": None,
        "make": data.get("make"),
        "model": data.get("model"),
        "colour": data.get("colour"),
        "year": year_int,
        "mot_status": data.get("motStatus"),
        "tax_status": data.get("taxStatus"),
        "fuel_type": data.get("fuelType"),
        "revenue_weight_kg": data.get("revenueWeight"),
    }
    rev_kg = None
    try:
        rw = data.get("revenueWeight")
        if rw is not None:
            rev_kg = int(rw)
    except (TypeError, ValueError):
        pass
    wheelplan = (data.get("wheelplan") or "").lower()
    if rev_kg is not None:
        if rev_kg >= 18000 or "artic" in wheelplan or "3 or more" in wheelplan:
            out["vehicle_type"] = "artic"
        elif rev_kg >= 3500 or "rigid" in wheelplan or "2 axle" in wheelplan:
            out["vehicle_type"] = "rigid"
        else:
            out["vehicle_type"] = "van"
    elif "artic" in wheelplan or "3 or more" in wheelplan:
        out["vehicle_type"] = "artic"
    elif "rigid" in wheelplan or "2 axle" in wheelplan:
        out["vehicle_type"] = "rigid"
    else:
        out["vehicle_type"] = "van"
    return out

