"""Derive BackhaulJob.driver_id when a loader accepts a LoadInterest."""
from typing import Optional

from sqlalchemy.orm import Session

from app import models


def resolve_driver_id_for_accepted_interest(
    db: Session, interest: models.LoadInterest
) -> Optional[int]:
    """
    If a driver (not office) expressed interest, use that driver.
    Otherwise, if exactly one Driver row is pinned to this vehicle for the haulier, use them.
    """
    eid = getattr(interest, "expressing_driver_id", None)
    if eid is not None:
        d = db.get(models.Driver, eid)
        if d and d.haulier_id == interest.haulier_id:
            if d.vehicle_id is None or d.vehicle_id == interest.vehicle_id:
                return d.id
        return None

    candidates = (
        db.query(models.Driver)
        .filter(
            models.Driver.haulier_id == interest.haulier_id,
            models.Driver.vehicle_id == interest.vehicle_id,
        )
        .all()
    )
    if len(candidates) == 1:
        return candidates[0].id
    return None
