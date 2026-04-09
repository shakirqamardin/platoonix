"""
Cancellation rules: no financial penalties — reputation tracked elsewhere.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from app import models


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


def loader_matched_cancellation_tier(hours: float) -> Tuple[bool, float, str]:
    """
    Loader cancelling matched load — no fees. Block self-serve only after pickup time has passed.
    Returns (blocked, fee_gbp, tier_key).
    """
    if hours < 0:
        return (True, 0.0, "blocked")
    return (False, 0.0, "free")


def haulier_cancellation_penalty_kind(hours: float) -> str:
    """Deprecated: strikes removed; kept for import compatibility — always 'none'."""
    return "none"


def open_load_cancel_blocked(hours: float) -> bool:
    """Block only when pickup reference time is already in the past."""
    return hours < 0
