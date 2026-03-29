"""Aggregates and queries for mutual job ratings (loader ↔ haulier)."""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app import models


def _avg_count_for_hauliers(db: Session, haulier_ids: list[int]) -> dict[int, tuple[float, int]]:
    if not haulier_ids:
        return {}
    rows = (
        db.query(models.JobRating.rated_haulier_id, func.avg(models.JobRating.rating), func.count())
        .filter(models.JobRating.rated_haulier_id.in_(haulier_ids))
        .group_by(models.JobRating.rated_haulier_id)
        .all()
    )
    return {int(hid): (float(avg), int(cnt)) for hid, avg, cnt in rows if hid is not None}


def _avg_count_for_loaders(db: Session, loader_ids: list[int]) -> dict[int, tuple[float, int]]:
    if not loader_ids:
        return {}
    rows = (
        db.query(models.JobRating.rated_loader_id, func.avg(models.JobRating.rating), func.count())
        .filter(models.JobRating.rated_loader_id.in_(loader_ids))
        .group_by(models.JobRating.rated_loader_id)
        .all()
    )
    return {int(lid): (float(avg), int(cnt)) for lid, avg, cnt in rows if lid is not None}


def format_haulier_line(summary: Optional[tuple[float, int]]) -> str:
    if not summary or summary[1] <= 0:
        return "No ratings yet"
    avg, n = summary
    return f"{avg:.1f}★ ({n} deliveries)"


def haulier_rating_lines_map(db: Session, haulier_ids: list[int]) -> dict[int, str]:
    """Display string per haulier id for loader-facing interest cards."""
    uniq = list(dict.fromkeys(int(h) for h in haulier_ids if h))
    if not uniq:
        return {}
    sums = _avg_count_for_hauliers(db, uniq)
    return {hid: format_haulier_line(sums.get(hid)) for hid in uniq}


def format_loader_line(summary: Optional[tuple[float, int]]) -> str:
    if not summary or summary[1] <= 0:
        return "No ratings yet"
    avg, n = summary
    return f"{avg:.1f}★ ({n} loads posted)"


def haulier_summary(db: Session, haulier_id: int) -> tuple[Optional[float], int]:
    row = (
        db.query(func.avg(models.JobRating.rating), func.count())
        .filter(models.JobRating.rated_haulier_id == haulier_id)
        .one()
    )
    avg, cnt = row[0], int(row[1] or 0)
    if cnt == 0:
        return None, 0
    return float(avg), cnt


def loader_summary(db: Session, loader_id: int) -> tuple[Optional[float], int]:
    row = (
        db.query(func.avg(models.JobRating.rating), func.count())
        .filter(models.JobRating.rated_loader_id == loader_id)
        .one()
    )
    avg, cnt = row[0], int(row[1] or 0)
    if cnt == 0:
        return None, 0
    return float(avg), cnt


def recent_reviews_for_haulier(db: Session, haulier_id: int, limit: int = 8) -> list[dict[str, Any]]:
    rows = (
        db.query(models.JobRating)
        .filter(models.JobRating.rated_haulier_id == haulier_id)
        .order_by(models.JobRating.created_at.desc())
        .limit(limit)
        .all()
    )
    out: list[dict[str, Any]] = []
    for r in rows:
        u = db.get(models.User, r.rater_user_id)
        out.append(
            {
                "rating": r.rating,
                "comment": r.comment,
                "created_at": r.created_at,
                "rater_label": (u.email if u else "User"),
            }
        )
    return out


def recent_reviews_for_loader(db: Session, loader_id: int, limit: int = 8) -> list[dict[str, Any]]:
    rows = (
        db.query(models.JobRating)
        .filter(models.JobRating.rated_loader_id == loader_id)
        .order_by(models.JobRating.created_at.desc())
        .limit(limit)
        .all()
    )
    out: list[dict[str, Any]] = []
    for r in rows:
        u = db.get(models.User, r.rater_user_id)
        out.append(
            {
                "rating": r.rating,
                "comment": r.comment,
                "created_at": r.created_at,
                "rater_label": (u.email if u else "User"),
            }
        )
    return out


def has_rated_job(db: Session, job_id: int, rater_user_id: int) -> bool:
    return (
        db.query(models.JobRating.id)
        .filter(models.JobRating.job_id == job_id, models.JobRating.rater_user_id == rater_user_id)
        .first()
        is not None
    )


def pending_rating_items(db: Session, user: models.User) -> list[dict[str, Any]]:
    """Completed jobs where this user is a party but has not submitted their rating."""
    out: list[dict[str, Any]] = []
    if not user:
        return out

    if user.loader_id:
        jobs = (
            db.query(models.BackhaulJob)
            .join(models.Load, models.BackhaulJob.load_id == models.Load.id)
            .filter(models.Load.loader_id == user.loader_id)
            .filter(models.BackhaulJob.completed_at.isnot(None))
            .order_by(models.BackhaulJob.completed_at.desc())
            .limit(50)
            .all()
        )
        for job in jobs:
            if has_rated_job(db, job.id, user.id):
                continue
            vehicle = db.get(models.Vehicle, job.vehicle_id)
            if not vehicle:
                continue
            haulier = db.get(models.Haulier, vehicle.haulier_id)
            load = db.get(models.Load, job.load_id)
            out.append(
                {
                    "job": job,
                    "job_label": job.display_number,
                    "direction": "loader_to_haulier",
                    "title": "How was the delivery?",
                    "counterparty": haulier.name if haulier else "Haulier",
                    "route": f"{load.pickup_postcode if load else ''} → {load.delivery_postcode if load else ''}",
                }
            )

    if user.haulier_id:
        # Haulier office: rate the loader for completed jobs on our vehicles
        vehicle_ids = [
            row[0]
            for row in db.query(models.Vehicle.id).filter(models.Vehicle.haulier_id == user.haulier_id).all()
        ]
        if vehicle_ids:
            jobs = (
                db.query(models.BackhaulJob)
                .filter(models.BackhaulJob.vehicle_id.in_(vehicle_ids))
                .filter(models.BackhaulJob.completed_at.isnot(None))
                .order_by(models.BackhaulJob.completed_at.desc())
                .limit(50)
                .all()
            )
            for job in jobs:
                if has_rated_job(db, job.id, user.id):
                    continue
                load = db.get(models.Load, job.load_id)
                if not load or not load.loader_id:
                    continue
                loader = db.get(models.Loader, load.loader_id)
                out.append(
                    {
                        "job": job,
                        "job_label": job.display_number,
                        "direction": "haulier_to_loader",
                        "title": "How was the load / shipper?",
                        "counterparty": loader.name if loader else "Loader",
                        "route": f"{load.pickup_postcode} → {load.delivery_postcode}",
                    }
                )

    return out


def build_home_rating_context(
    db: Session,
    current_user: Optional[models.User],
    loads: list,
    vehicles: list,
) -> dict[str, Any]:
    ctx: dict[str, Any] = {
        "pending_job_ratings": [],
        "haulier_rating_line": None,
        "loader_rating_line": None,
        "haulier_reviews": [],
        "loader_reviews": [],
        "loader_rating_by_loader_id": {},
        "haulier_rating_by_haulier_id": {},
    }
    if current_user and getattr(current_user, "role", None) != "driver":
        ctx["pending_job_ratings"] = pending_rating_items(db, current_user)

    loader_ids = list({l.loader_id for l in loads if getattr(l, "loader_id", None)})
    haulier_ids = list({v.haulier_id for v in vehicles if getattr(v, "haulier_id", None)})

    lmap = _avg_count_for_loaders(db, loader_ids)
    hmap = _avg_count_for_hauliers(db, haulier_ids)
    ctx["loader_rating_by_loader_id"] = {lid: format_loader_line(lmap.get(lid)) for lid in loader_ids}
    ctx["haulier_rating_by_haulier_id"] = {hid: format_haulier_line(hmap.get(hid)) for hid in haulier_ids}

    if current_user and current_user.haulier_id:
        avg, n = haulier_summary(db, current_user.haulier_id)
        ctx["haulier_rating_line"] = format_haulier_line((avg, n) if n else None)
        ctx["haulier_reviews"] = recent_reviews_for_haulier(db, current_user.haulier_id)
    if current_user and current_user.loader_id:
        avg, n = loader_summary(db, current_user.loader_id)
        ctx["loader_rating_line"] = format_loader_line((avg, n) if n else None)
        ctx["loader_reviews"] = recent_reviews_for_loader(db, current_user.loader_id)

    return ctx


def loader_rating_lines_map(db: Session, loader_ids: list[int]) -> dict[int, str]:
    """Map loader_id → display string for tables (e.g. Matches)."""
    if not loader_ids:
        return {}
    m = _avg_count_for_loaders(db, list(set(loader_ids)))
    return {lid: format_loader_line(m.get(lid)) for lid in set(loader_ids)}


def enrich_matching_results_with_loader_ratings(db: Session, matching_results: list[dict]) -> None:
    if not matching_results:
        return
    loader_ids = list({m["load"].loader_id for m in matching_results if m.get("load") and m["load"].loader_id})
    summaries = _avg_count_for_loaders(db, loader_ids)
    for m in matching_results:
        load = m.get("load")
        if not load or not load.loader_id:
            m["loader_rating_line"] = None
        else:
            m["loader_rating_line"] = format_loader_line(summaries.get(load.loader_id))
