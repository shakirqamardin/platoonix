"""Platform pricing: % commission on load value + optional flat fee charged to the loader."""
from dataclasses import dataclass

from app.config import Settings


@dataclass(frozen=True)
class JobPaymentSplits:
    """amount_gbp = load/job price; haulier receives amount - percent fee; loader also pays flat_fee."""

    amount_gbp: float
    fee_gbp: float
    net_payout_gbp: float
    flat_fee_gbp: float

    @property
    def total_loader_charge_gbp(self) -> float:
        return round(self.amount_gbp + self.flat_fee_gbp, 2)


def compute_job_payment_splits(amount_gbp: float, settings: Settings) -> JobPaymentSplits:
    """
    - Platform keeps fee_gbp = amount_gbp * platform_fee_percent / 100 (e.g. 8%).
    - Haulier receives net_payout_gbp = amount_gbp - fee_gbp.
    - Loader pays total_loader_charge_gbp = amount_gbp + flat_fee_gbp (flat covers card/ops; configurable).
    """
    amt = max(0.0, float(amount_gbp or 0.0))
    pct = float(getattr(settings, "platform_fee_percent", 8.0) or 0.0)
    flat = float(getattr(settings, "loader_flat_fee_gbp", 5.0) or 0.0)
    fee_gbp = round(amt * (pct / 100.0), 2)
    net_payout_gbp = round(amt - fee_gbp, 2)
    flat_fee_gbp = round(flat, 2)
    return JobPaymentSplits(
        amount_gbp=round(amt, 2),
        fee_gbp=fee_gbp,
        net_payout_gbp=net_payout_gbp,
        flat_fee_gbp=flat_fee_gbp,
    )
