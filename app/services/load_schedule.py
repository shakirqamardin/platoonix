"""Map simple collection/delivery dates + time slots to UTC window datetimes (London)."""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Optional, Tuple

from zoneinfo import ZoneInfo

LON = ZoneInfo("Europe/London")
UTC = timezone.utc

VALID_SLOTS = frozenset({"morning", "afternoon", "evening", "flexible"})


def slot_bounds_local(slot: str) -> tuple[time, time]:
    s = slot if slot in VALID_SLOTS else "flexible"
    if s == "morning":
        return time(8, 0), time(12, 0)
    if s == "afternoon":
        return time(12, 0), time(17, 0)
    if s == "evening":
        return time(17, 0), time(20, 0)
    return time(6, 0), time(22, 0)


def local_window_to_utc(d: date, slot: str) -> tuple[datetime, datetime]:
    t0, t1 = slot_bounds_local(slot)
    start_l = datetime.combine(d, t0, tzinfo=LON)
    end_l = datetime.combine(d, t1, tzinfo=LON)
    return start_l.astimezone(UTC), end_l.astimezone(UTC)


def schedule_to_utc_windows(
    pickup_date: date,
    pickup_tw: str,
    delivery_date: date,
    delivery_tw: str,
) -> tuple[datetime, datetime, datetime, datetime]:
    ps, pe = local_window_to_utc(pickup_date, pickup_tw)
    ds, de = local_window_to_utc(delivery_date, delivery_tw)
    return ps, pe, ds, de


def _hour_to_slot(h: int) -> str:
    if h < 12:
        return "morning"
    if h < 17:
        return "afternoon"
    if h < 22:
        return "evening"
    return "flexible"


def infer_schedule_from_datetimes(
    pickup_window_start: Optional[datetime],
    delivery_window_start: Optional[datetime],
) -> tuple[Optional[date], Optional[str], Optional[date], Optional[str]]:
    """Backfill simple fields from legacy UTC timestamps."""
    if not pickup_window_start:
        return None, None, None, None
    ps = pickup_window_start
    if ps.tzinfo is None:
        ps = ps.replace(tzinfo=UTC)
    pl = ps.astimezone(LON)
    pd, ph = pl.date(), pl.hour
    ptw = _hour_to_slot(ph)
    if delivery_window_start:
        ds = delivery_window_start
        if ds.tzinfo is None:
            ds = ds.replace(tzinfo=UTC)
        dl = ds.astimezone(LON)
        dd, dh = dl.date(), dl.hour
        dtw = _hour_to_slot(dh)
    else:
        dd, dtw = pd, ptw
    return pd, ptw, dd, dtw


def slot_label(slot: Optional[str]) -> str:
    if not slot:
        return ""
    labels = {
        "morning": "Morning (8am–12pm)",
        "afternoon": "Afternoon (12pm–5pm)",
        "evening": "Evening (5pm–8pm)",
        "flexible": "Flexible / anytime",
    }
    return labels.get(slot, slot.replace("_", " ").title())
