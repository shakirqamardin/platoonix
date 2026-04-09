"""Dedicated admin monitoring pages (session-based admin login)."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Union

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload

from app import models
from app.auth import get_session_user_id, require_admin
from app.database import get_db
from app.services import vehicle_availability as vehicle_availability_svc
from app.services.stripe_loader_charge import try_refund_loader_charge

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/admin/dashboard", response_class=HTMLResponse, response_model=None)
def admin_monitoring_dashboard(
    request: Request,
    db: Session = Depends(get_db),
) -> Union[HTMLResponse, RedirectResponse]:
    """User stats, growth, and platform activity (admin only)."""
    redirect = require_admin(request, db)
    if redirect:
        return redirect
    uid = get_session_user_id(request)
    current_user = db.get(models.User, uid) if uid else None
    if not current_user:
        return RedirectResponse(url="/admin/login", status_code=302)

    now = datetime.now(timezone.utc)
    seven_days_ago = now - timedelta(days=7)
    thirty_days_ago = now - timedelta(days=30)

    total_users = db.query(models.User).count()
    loader_count = db.query(models.User).filter(models.User.loader_id.isnot(None)).count()
    haulier_count = db.query(models.User).filter(models.User.haulier_id.isnot(None)).count()
    driver_count = db.query(models.Driver).count()

    new_this_week = db.query(models.User).filter(models.User.created_at >= seven_days_ago).count()
    new_this_month = db.query(models.User).filter(models.User.created_at >= thirty_days_ago).count()

    recent_users = (
        db.query(models.User)
        .options(joinedload(models.User.loader), joinedload(models.User.haulier))
        .filter(models.User.created_at >= seven_days_ago)
        .order_by(models.User.created_at.desc())
        .limit(50)
        .all()
    )

    total_loads = db.query(models.Load).count()
    active_loads = db.query(models.Load).filter(models.Load.status == models.LoadStatusEnum.OPEN.value).count()
    total_jobs = db.query(models.BackhaulJob).count()
    completed_jobs = (
        db.query(models.BackhaulJob).filter(models.BackhaulJob.completed_at.isnot(None)).count()
    )

    return templates.TemplateResponse(
        "admin_dashboard.html",
        {
            "request": request,
            "current_user": current_user,
            "total_users": total_users,
            "loader_count": loader_count,
            "haulier_count": haulier_count,
            "driver_count": driver_count,
            "new_this_week": new_this_week,
            "new_this_month": new_this_month,
            "recent_users": recent_users,
            "total_loads": total_loads,
            "active_loads": active_loads,
            "total_jobs": total_jobs,
            "completed_jobs": completed_jobs,
        },
    )


@router.get("/admin/users", response_class=HTMLResponse, response_model=None)
def admin_users_list(
    request: Request,
    db: Session = Depends(get_db),
) -> Union[HTMLResponse, RedirectResponse]:
    """Paginated-style list of office users (admin only)."""
    redirect = require_admin(request, db)
    if redirect:
        return redirect
    uid = get_session_user_id(request)
    current_user = db.get(models.User, uid) if uid else None
    if not current_user:
        return RedirectResponse(url="/admin/login", status_code=302)

    users = (
        db.query(models.User)
        .options(joinedload(models.User.loader), joinedload(models.User.haulier))
        .order_by(models.User.created_at.desc())
        .limit(200)
        .all()
    )

    return templates.TemplateResponse(
        "admin_users.html",
        {
            "request": request,
            "current_user": current_user,
            "users": users,
        },
    )


@router.post("/admin/cleanup-old-jobs", response_model=None)
def cleanup_old_jobs(
    request: Request,
    db: Session = Depends(get_db),
) -> Union[RedirectResponse, JSONResponse]:
    """
    Admin: remove stale backhaul jobs (matched 30+ days ago, never collected/completed, not soft-cancelled).
    Reopens the load and resets the accepted LoadInterest to suggested. For dev / test data cleanup.
    """
    redirect = require_admin(request, db)
    if redirect:
        return redirect

    now = datetime.now(timezone.utc)
    thirty_days_ago = now - timedelta(days=30)

    old_jobs = (
        db.query(models.BackhaulJob)
        .filter(
            models.BackhaulJob.matched_at < thirty_days_ago,
            models.BackhaulJob.completed_at.is_(None),
            models.BackhaulJob.collected_at.is_(None),
            models.BackhaulJob.haulier_cancelled_at.is_(None),
        )
        .all()
    )

    vehicle_ids: set[int] = set()
    count = 0

    for job in old_jobs:
        load = db.get(models.Load, job.load_id)
        if load:
            load.status = models.LoadStatusEnum.OPEN.value
            load.reopened_at = now
            db.add(load)

        for payment in (
            db.query(models.Payment)
            .filter(models.Payment.backhaul_job_id == job.id)
            .order_by(models.Payment.created_at.asc())
            .all()
        ):
            if (payment.status or "").strip().lower() == models.PaymentStatusEnum.CAPTURED.value:
                try:
                    try_refund_loader_charge(payment, db, refund_amount_gbp=None)
                    db.add(payment)
                except Exception:
                    logger.exception("cleanup_old_jobs: refund failed for payment %s", payment.id)

        db.query(models.DriverLocation).filter(models.DriverLocation.job_id == job.id).delete(
            synchronize_session=False
        )
        db.query(models.JobRating).filter(models.JobRating.job_id == job.id).delete(
            synchronize_session=False
        )
        db.query(models.POD).filter(models.POD.backhaul_job_id == job.id).delete(
            synchronize_session=False
        )
        db.query(models.Payment).filter(models.Payment.backhaul_job_id == job.id).delete(
            synchronize_session=False
        )

        interest = (
            db.query(models.LoadInterest)
            .filter(
                models.LoadInterest.load_id == job.load_id,
                models.LoadInterest.vehicle_id == job.vehicle_id,
                models.LoadInterest.status == "accepted",
            )
            .first()
        )
        if interest:
            interest.status = "suggested"
            db.add(interest)

        vehicle_ids.add(int(job.vehicle_id))
        db.delete(job)
        count += 1

    try:
        db.commit()
    except Exception:
        logger.exception("cleanup_old_jobs: commit failed")
        db.rollback()
        if (request.headers.get("accept") or "").find("application/json") >= 0:
            return JSONResponse(
                {"success": False, "deleted": 0, "message": "Database error; nothing deleted."},
                status_code=500,
            )
        return RedirectResponse(url="/admin/dashboard?cleanup_error=1", status_code=303)

    try:
        for vid in vehicle_ids:
            vehicle_availability_svc.refresh_vehicle_availability(db, vid)
        db.commit()
    except Exception:
        logger.exception("cleanup_old_jobs: refresh_vehicle_availability batch failed")
        db.rollback()

    payload = {
        "success": True,
        "deleted": count,
        "message": f"Cleaned up {count} old abandoned job(s) (30+ days, no collection).",
    }
    if (request.headers.get("accept") or "").find("application/json") >= 0:
        return JSONResponse(payload)

    return RedirectResponse(
        url=f"/admin/dashboard?cleaned_jobs={count}",
        status_code=303,
    )
