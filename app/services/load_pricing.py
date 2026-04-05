"""Suggested load budgets from distance, vehicle/trailer surcharges, and urgency."""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app import models
from app.config import get_settings
from app.services.road_distance import resolve_distance_miles

# Per-mile base by vehicle (unknown / "any" uses van rate)
GBP_PER_MILE_BY_VEHICLE = {
    "van": 1.50,
    "rigid": 2.00,
    "artic": 2.75,
}

VEHICLE_SURCHARGE_GBP = {
    "artic": 50.0,
    "rigid": 25.0,
    "van": 0.0,
}

# curtain_sider matches form value
TRAILER_SURCHARGE_GBP = {
    "curtain_sider": 0.0,
    "refrigerated": 25.0,
    "box": 10.0,
    "flatbed": 15.0,
    "other": 5.0,
}

URGENCY_WITHIN_HOURS = 24
URGENCY_MULTIPLIER = 1.20


def round_to_nearest_half_gbp(amount: float) -> float:
    """Round to nearest £0.50 (e.g. £182.37 → £182.50, £245.23 → £245.00)."""
    if amount <= 0:
        return 0.0
    return round(amount * 2.0) / 2.0


def _per_mile_gbp(vehicle_type: Optional[str]) -> float:
    vt = (vehicle_type or "").strip().lower()
    if not vt:
        return GBP_PER_MILE_BY_VEHICLE["van"]
    return float(GBP_PER_MILE_BY_VEHICLE.get(vt, GBP_PER_MILE_BY_VEHICLE["van"]))


def _vehicle_surcharge(vehicle_type: Optional[str]) -> float:
    if not vehicle_type:
        return 0.0
    return float(VEHICLE_SURCHARGE_GBP.get(str(vehicle_type).strip().lower(), 0.0))


def _trailer_surcharge(trailer_type: Optional[str]) -> float:
    if not trailer_type:
        return 0.0
    return float(TRAILER_SURCHARGE_GBP.get(str(trailer_type).strip().lower(), 0.0))


def pickup_is_urgent(pickup_window_start: Optional[datetime], now: Optional[datetime] = None) -> bool:
    """True if pickup start is between now and now+24h (exclusive of far future)."""
    if pickup_window_start is None:
        return False
    now = now or datetime.now(timezone.utc)
    ps = pickup_window_start
    if ps.tzinfo is None:
        ps = ps.replace(tzinfo=timezone.utc)
    delta = (ps - now).total_seconds()
    return 0 <= delta <= URGENCY_WITHIN_HOURS * 3600


def compute_suggested_price_gbp(
    distance_miles: float,
    vehicle_type: Optional[str],
    trailer_type: Optional[str],
    urgent: bool,
) -> tuple[float, dict[str, Any]]:
    rate = _per_mile_gbp(vehicle_type)
    base = distance_miles * rate
    v_sur = _vehicle_surcharge(vehicle_type)
    t_sur = _trailer_surcharge(trailer_type)
    subtotal = base + v_sur + t_sur
    if urgent:
        pre_round = subtotal * URGENCY_MULTIPLIER
    else:
        pre_round = subtotal
    total = round_to_nearest_half_gbp(max(0.0, pre_round))
    sub_rounded = round(subtotal, 2)
    breakdown = {
        "distance_miles": round(distance_miles, 1),
        "rate_per_mile_gbp": rate,
        "base_gbp": round(base, 2),
        "vehicle_surcharge_gbp": v_sur,
        "trailer_surcharge_gbp": t_sur,
        "urgent": urgent,
        "subtotal_gbp": sub_rounded,
        "subtotal_before_urgency_gbp": sub_rounded,
        "pre_round_gbp": round(pre_round, 2),
        "suggested_gbp": total,
    }
    return total, breakdown


def human_summary_line(
    vehicle_type: Optional[str],
    trailer_type: Optional[str],
    distance_miles: float,
    distance_source: str,
    urgent: bool,
) -> str:
    vt = (vehicle_type or "").strip().lower()
    tt = (trailer_type or "").strip().lower()
    vt_label = vt if vt else "any vehicle"
    if tt == "curtain_sider":
        tt_label = "curtain"
    elif not tt:
        tt_label = "any trailer"
    else:
        tt_label = tt
    parts = [f"{distance_miles:.1f} mi ({distance_source})", vt_label, tt_label]
    if urgent:
        parts.append("+20% urgency")
    return ", ".join(parts)


def suggest_for_open_load(load: models.Load) -> dict[str, Any]:
    """
    Full suggestion for an open load (Find Backhaul). Uses requirements JSON for vehicle/trailer.
    """
    settings = get_settings()
    dist, src, note = resolve_distance_miles(
        load.pickup_postcode,
        load.delivery_postcode,
        settings.openrouteservice_api_key,
        settings.mapbox_access_token,
        settings.google_maps_api_key,
    )
    if dist is None:
        return {
            "suggested_gbp": None,
            "detail_line": note,
            "breakdown": None,
        }
    req = load.requirements or {}
    if not isinstance(req, dict):
        req = {}
    vt = (req.get("vehicle_type") or "").strip() or None
    tt = (req.get("trailer_type") or "").strip() or None
    urgent = pickup_is_urgent(load.pickup_window_start)
    total, breakdown = compute_suggested_price_gbp(dist, vt, tt, urgent)
    src_label = "road" if src in ("google", "openrouteservice", "mapbox") else "unavailable"
    line = human_summary_line(vt, tt, dist, src_label, urgent)
    return {
        "suggested_gbp": total,
        "detail_line": f"Market rate: £{total:.2f} ({line})",
        "breakdown": breakdown,
        "distance_note": note,
    }


def suggest_from_form_params(
    pickup_postcode: str,
    delivery_postcode: str,
    vehicle_type: Optional[str],
    trailer_type: Optional[str],
    pickup_window_start_iso: Optional[str],
) -> dict[str, Any]:
    """For load creation form (API). pickup_window_start_iso: optional ISO from datetime-local."""
    settings = get_settings()
    dist, src, note = resolve_distance_miles(
        pickup_postcode,
        delivery_postcode,
        settings.openrouteservice_api_key,
        settings.mapbox_access_token,
        settings.google_maps_api_key,
    )
    if dist is not None and not math.isfinite(float(dist)):
        dist = None
    urgent = False
    ps_dt: Optional[datetime] = None
    if pickup_window_start_iso and str(pickup_window_start_iso).strip():
        try:
            raw = str(pickup_window_start_iso).strip().replace("Z", "+00:00")
            ps_dt = datetime.fromisoformat(raw)
            if ps_dt.tzinfo is None:
                ps_dt = ps_dt.replace(tzinfo=timezone.utc)
            urgent = pickup_is_urgent(ps_dt)
        except (ValueError, TypeError):
            pass
    guidance_low = GBP_PER_MILE_BY_VEHICLE["van"]
    guidance_high = GBP_PER_MILE_BY_VEHICLE["artic"]
    if dist is None:
        return {
            "suggested_gbp": None,
            "detail_line": note or "Enter valid UK postcodes",
            "breakdown": None,
            "distance_miles": None,
            "distance_source": None,
            "guidance_per_mile_low": guidance_low,
            "guidance_per_mile_high": guidance_high,
            "guidance_typical_min_gbp": None,
            "guidance_typical_max_gbp": None,
        }
    vt = (vehicle_type or "").strip() or None
    tt = (trailer_type or "").strip() or None
    total, breakdown = compute_suggested_price_gbp(dist, vt, tt, urgent)
    dm = round(float(dist), 1)
    typical_min = round(dm * guidance_low, 2)
    typical_max = round(dm * guidance_high, 2)
    return {
        "suggested_gbp": total,
        "detail_line": None,
        "breakdown": breakdown,
        "distance_miles": dm,
        "distance_source": src,
        "distance_note": note,
        "urgent": urgent,
        "guidance_per_mile_low": guidance_low,
        "guidance_per_mile_high": guidance_high,
        "guidance_typical_min_gbp": typical_min,
        "guidance_typical_max_gbp": typical_max,
    }
