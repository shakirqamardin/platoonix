"""Optional grouping of backhaul jobs that share the same vehicle + collection postcode + driver (multi-drop)."""
from __future__ import annotations

import uuid
from typing import List, Optional

from sqlalchemy.orm import Session

from app import models


def normalize_postcode(pc: Optional[str]) -> str:
    if not pc:
        return ""
    return "".join(str(pc).upper().split())


def try_link_new_job_pickup_group(db: Session, job: models.BackhaulJob) -> None:
    """
    If another incomplete job exists for the same vehicle, same pickup (load), and same driver_id
    (including both unassigned), assign a shared job_group_uuid so the driver UI can batch pickup steps.
    Does not commit.
    """
    load = db.get(models.Load, job.load_id)
    if not load:
        return
    pickup = normalize_postcode(load.pickup_postcode)
    if not pickup:
        return

    peers: List[models.BackhaulJob] = (
        db.query(models.BackhaulJob)
        .join(models.Load, models.BackhaulJob.load_id == models.Load.id)
        .filter(models.BackhaulJob.id != job.id)
        .filter(models.BackhaulJob.vehicle_id == job.vehicle_id)
        .filter(models.BackhaulJob.completed_at.is_(None))
        .filter(models.BackhaulJob.driver_id == job.driver_id)
        .all()
    )
    matching: List[models.BackhaulJob] = []
    for p in peers:
        pl = db.get(models.Load, p.load_id)
        if pl and normalize_postcode(pl.pickup_postcode) == pickup:
            matching.append(p)
    if not matching:
        return

    existing_uuid: Optional[str] = None
    for p in matching:
        if p.job_group_uuid:
            existing_uuid = p.job_group_uuid
            break
    gid = existing_uuid or str(uuid.uuid4())
    job.job_group_uuid = gid
    for p in matching:
        p.job_group_uuid = gid
        db.add(p)
    db.add(job)


def propagate_group_driver(db: Session, job: models.BackhaulJob, driver_id: int) -> None:
    """Set driver_id on all incomplete jobs in the same group + vehicle when still unassigned."""
    if not job.job_group_uuid:
        return
    peers = (
        db.query(models.BackhaulJob)
        .filter(models.BackhaulJob.job_group_uuid == job.job_group_uuid)
        .filter(models.BackhaulJob.vehicle_id == job.vehicle_id)
        .filter(models.BackhaulJob.completed_at.is_(None))
        .all()
    )
    for p in peers:
        if p.driver_id is None:
            p.driver_id = driver_id
            db.add(p)
