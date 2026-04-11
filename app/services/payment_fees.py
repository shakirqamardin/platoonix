"""Platform pricing: % commission on load value + hybrid loader platform fee (min OR %, whichever is higher)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from app.config import Settings


@dataclass(frozen=True)
class JobPaymentSplits:
    """amount_gbp = load/job price; haulier receives amount - percent fee; loader pays amount + flat_fee_gbp."""

    amount_gbp: float
    fee_gbp: float
    net_payout_gbp: float
    flat_fee_gbp: float  # charged to loader (hybrid min / %)
    loader_fee_detail: str  # e.g. "£8.00 (2% of £400.00)" or "£5.00 (minimum)"

    @property
    def total_loader_charge_gbp(self) -> float:
        return round(self.amount_gbp + self.flat_fee_gbp, 2)


def compute_loader_platform_fee_gbp(
    amount_gbp: float,
    settings: Settings,
    *,
    fee_multiplier: float = 1.0,
) -> tuple[float, str]:
    """
    Loader platform fee = max(minimum, load_value × percent), optionally reduced by referral discount.
    Returns (fee_gbp, human detail for UI).
    """
    amt = max(0.0, float(amount_gbp or 0.0))
    if amt <= 0:
        return (0.0, "£0.00")

    mult = max(0.0, min(1.0, float(fee_multiplier or 1.0)))

    minimum = float(getattr(settings, "loader_flat_fee_gbp", 5.0) or 0.0)
    pct = float(getattr(settings, "loader_fee_percent_of_load", 2.0) or 0.0)
    pct_part = round(amt * (pct / 100.0), 2)
    fee = round(max(minimum, pct_part) * mult, 2)

    if pct_part + 1e-9 < minimum:
        detail = f"£{fee:.2f} (minimum)"
    else:
        detail = f"£{fee:.2f} ({pct:g}% of £{amt:.2f})"

    if mult < 1.0 - 1e-9:
        detail = f"{detail} (50% referral discount)"

    return (fee, detail)


def loader_platform_fee_payload(
    amount_gbp: Optional[float],
    settings: Settings,
    *,
    fee_multiplier: float = 1.0,
) -> Optional[dict[str, Any]]:
    """For API/JSON: fee breakdown when amount is set."""
    if amount_gbp is None:
        return None
    amt = float(amount_gbp or 0.0)
    if amt <= 0:
        return None
    fee, detail = compute_loader_platform_fee_gbp(amt, settings, fee_multiplier=fee_multiplier)
    return {
        "loader_platform_fee_gbp": fee,
        "loader_platform_fee_detail": detail,
        "loader_total_at_collection_gbp": round(amt + fee, 2),
    }


def compute_job_payment_splits(
    amount_gbp: float,
    settings: Settings,
    *,
    haulier_fee_multiplier: float = 1.0,
    loader_flat_fee_multiplier: float = 1.0,
) -> JobPaymentSplits:
    """
    - Platform keeps fee_gbp = amount_gbp * platform_fee_percent / 100 (e.g. 8%), scaled by haulier referral.
    - Haulier receives net_payout_gbp = amount_gbp - fee_gbp.
    - Loader pays amount_gbp + flat_fee_gbp where flat_fee uses loader referral multiplier when applicable.
    """
    amt = max(0.0, float(amount_gbp or 0.0))
    hm = max(0.0, min(1.0, float(haulier_fee_multiplier or 1.0)))
    lm = max(0.0, min(1.0, float(loader_flat_fee_multiplier or 1.0)))
    pct = float(getattr(settings, "platform_fee_percent", 8.0) or 0.0)
    fee_gbp = round(amt * (pct / 100.0) * hm, 2)
    net_payout_gbp = round(amt - fee_gbp, 2)
    flat_fee_gbp, loader_fee_detail = compute_loader_platform_fee_gbp(amt, settings, fee_multiplier=lm)
    return JobPaymentSplits(
        amount_gbp=round(amt, 2),
        fee_gbp=fee_gbp,
        net_payout_gbp=net_payout_gbp,
        flat_fee_gbp=flat_fee_gbp,
        loader_fee_detail=loader_fee_detail,
    )
