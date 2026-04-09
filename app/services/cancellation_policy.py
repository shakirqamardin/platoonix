"""
Time-based cancellation tiers for loaders (matched loads) and hauliers.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from app import models
from app.config import get_settings


def pickup_reference_time(load: models.Load, job: Optional[models.BackhaulJob]) -> Optional[datetime]:
    """Best-effort pickup moment for policy (UTC-aware)."""
    if load.pickup_window_start:
        t = load.pickup_window_start
        if t.tzinfo is None:
            return t.replace(tzinfo=timezone.utc)
        return t
    if job:
        base = job.matched_at or job.accepted_at or job.created_at
        if base:
            if base.tzinfo is None:
                base = base.replace(tzinfo=timezone.utc)
            return base + timedelta(hours=24)
    return None


def hours_until_pickup(load: models.Load, job: Optional[models.BackhaulJob], now: Optional[datetime] = None) -> float:
    """Hours until pickup; large positive if far future; negative if past."""
    if now is None:
        now = datetime.now(timezone.utc)
    pt = pickup_reference_time(load, job)
    if not pt:
        return 9999.0
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return (pt - now).total_seconds() / 3600.0


def loader_matched_cancellation_tier(
    hours: float,
) -> Tuple[bool, float, str]:
    """
    Returns (blocked, fee_gbp, tier_key).
    blocked=True => cannot cancel via platform.
    """
    s = get_settings()
    free_h = float(getattr(s, "free_cancellation_hours", 24))
    warn_h = float(getattr(s, "warning_cancellation_hours", 12))
    pen_h = float(getattr(s, "penalty_cancellation_hours", 2))
    fee_warn = float(getattr(s, "cancellation_fee_warning_gbp", 25.0))
    fee_pen = float(getattr(s, "cancellation_fee_penalty_gbp", 50.0))

    # Past pickup: not "too close" — allow self-serve cancel (fee £0; stale/overdue loads).
    if hours < 0:
        return (False, 0.0, "pickup_overdue")
    # Future pickup within penalty window only — same idea as open_load_cancel_blocked.
    if hours < pen_h:
        return (True, 0.0, "blocked")
    if hours >= free_h:
        return (False, 0.0, "free")
    if hours >= warn_h:
        return (False, fee_warn, "warning")
    return (False, fee_pen, "penalty")


def haulier_cancellation_penalty_kind(hours: float) -> str:
    """
    For normal (non-emergency) haulier cancellations: none | warning | strike | blocked.
    Blocked (< penalty window) must cancel via emergency flow or contact support.
    """
    s = get_settings()
    free_h = float(getattr(s, "free_cancellation_hours", 24))
    warn_h = float(getattr(s, "warning_cancellation_hours", 12))
    pen_h = float(getattr(s, "penalty_cancellation_hours", 2))
    if hours < pen_h:
        return "blocked"
    if hours >= free_h:
        return "none"
    if hours >= warn_h:
        return "warning"
    return "strike"


def open_load_cancel_blocked(hours: float) -> bool:
    """Loaders cannot cancel open (unmatched) loads less than penalty window before pickup."""
    s = get_settings()
    pen_h = float(getattr(s, "penalty_cancellation_hours", 2))
    # Only block when pickup is still in the future but inside the window (not overdue).
    return 0.0 <= hours < pen_h
