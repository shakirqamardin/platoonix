"""Dedicated admin monitoring pages (session-based admin login)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Union

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload

from app import models
from app.auth import get_session_user_id, require_admin
from app.database import get_db

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
