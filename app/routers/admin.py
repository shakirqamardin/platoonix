"""Dedicated admin monitoring pages (session-based admin login)."""
from __future__ import annotations

import logging
import mimetypes
from datetime import date, datetime, timedelta, timezone
from typing import Union

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app import models
from app.auth import get_session_user_id, require_admin
from app.services.insurance_status import get_insurance_storage_dir, remove_insurance_file_if_exists
from app.services.in_app_notifications import haulier_office_user_ids, record_user_notifications
from app.services.referral_program import (
    REFERRAL_CAP,
    count_active_referral_discounts,
    count_successful_referrals,
)
from app.database import get_db
from app.services import vehicle_availability as vehicle_availability_svc
from app.services.stripe_loader_charge import try_refund_loader_charge

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _delete_approval_tokens_for_interest_ids(db: Session, interest_ids: list[int]) -> None:
    if not interest_ids:
        return
    tok = getattr(models, "BackhaulApprovalToken", None)
    if tok is None:
        return
    db.query(tok).filter(tok.load_interest_id.in_(interest_ids)).delete(synchronize_session=False)


def _delete_load_interests_for_load(db: Session, load_id: int) -> None:
    li_ids = [r[0] for r in db.query(models.LoadInterest.id).filter(models.LoadInterest.load_id == load_id).all()]
    _delete_approval_tokens_for_interest_ids(db, li_ids)
    db.query(models.LoadInterest).filter(models.LoadInterest.load_id == load_id).delete(synchronize_session=False)


def _delete_vehicle_interests_and_tokens(db: Session, vehicle_id: int) -> None:
    li_ids = [r[0] for r in db.query(models.LoadInterest.id).filter(models.LoadInterest.vehicle_id == vehicle_id).all()]
    _delete_approval_tokens_for_interest_ids(db, li_ids)
    db.query(models.LoadInterest).filter(models.LoadInterest.vehicle_id == vehicle_id).delete(synchronize_session=False)


def _purge_backhaul_job(db: Session, job: models.BackhaulJob) -> int:
    """Remove dependent rows and the job; clear vehicle.current_job_id if it points here. Returns vehicle_id."""
    vid = int(job.vehicle_id)
    vehicle = db.get(models.Vehicle, vid)
    if vehicle and vehicle.current_job_id == job.id:
        vehicle.current_job_id = None
        vehicle.available_from = None
        db.add(vehicle)

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
                logger.exception("admin _purge_backhaul_job: refund failed for payment %s", payment.id)

    db.query(models.DriverLocation).filter(models.DriverLocation.job_id == job.id).delete(synchronize_session=False)
    db.query(models.JobRating).filter(models.JobRating.job_id == job.id).delete(synchronize_session=False)
    db.query(models.POD).filter(models.POD.backhaul_job_id == job.id).delete(synchronize_session=False)
    db.query(models.Payment).filter(models.Payment.backhaul_job_id == job.id).delete(synchronize_session=False)
    db.delete(job)
    return vid


def _reopen_load_after_job_removed(
    db: Session, load: models.Load, load_id: int, vehicle_id: int, now: datetime
) -> None:
    """Call after the BackhaulJob row is removed; pass load_id/vehicle_id from before delete."""
    load.status = models.LoadStatusEnum.OPEN.value
    load.reopened_at = now
    db.add(load)
    interest = (
        db.query(models.LoadInterest)
        .filter(
            models.LoadInterest.load_id == load_id,
            models.LoadInterest.vehicle_id == vehicle_id,
            models.LoadInterest.status == "accepted",
        )
        .first()
    )
    if interest:
        interest.status = "suggested"
        db.add(interest)


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

    referral_total = count_successful_referrals(db)
    referral_remaining = max(0, REFERRAL_CAP - referral_total)
    referral_active_discounts = count_active_referral_discounts(db, date.today())
    referral_progress_percent = min(100, int(round(100.0 * referral_total / REFERRAL_CAP))) if REFERRAL_CAP else 0

    return templates.TemplateResponse(
        "admin_dashboard.html",
        {
            "request": request,
            "current_user": current_user,
            "referral_cap": REFERRAL_CAP,
            "referral_total_signups": referral_total,
            "referral_spots_remaining": referral_remaining,
            "referral_active_discounts": referral_active_discounts,
            "referral_progress_percent": referral_progress_percent,
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


@router.post("/admin/force-delete-load/{load_id}", response_model=None)
def force_delete_load(
    load_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> Union[RedirectResponse, JSONResponse]:
    """Admin: permanently delete a load and all related jobs, payments, interests."""
    redirect = require_admin(request, db)
    if redirect:
        return redirect

    load = db.get(models.Load, load_id)
    if not load:
        return RedirectResponse(url="/?section=loads&admin_delete_error=load_not_found", status_code=303)

    vehicle_ids: set[int] = set()
    jobs = db.query(models.BackhaulJob).filter(models.BackhaulJob.load_id == load_id).all()
    try:
        for job in jobs:
            vehicle_ids.add(_purge_backhaul_job(db, job))
        _delete_load_interests_for_load(db, load_id)
        db.delete(load)
        db.commit()
    except IntegrityError:
        logger.exception("force_delete_load: integrity error load_id=%s", load_id)
        db.rollback()
        return RedirectResponse(url="/?section=loads&admin_delete_error=integrity", status_code=303)

    try:
        for vid in vehicle_ids:
            vehicle_availability_svc.refresh_vehicle_availability(db, vid)
        db.commit()
    except Exception:
        logger.exception("force_delete_load: refresh_vehicle_availability")
        db.rollback()

    if (request.headers.get("accept") or "").find("application/json") >= 0:
        return JSONResponse({"success": True, "deleted_load_id": load_id})
    return RedirectResponse(url="/?section=loads&admin_force_deleted=load", status_code=303)


@router.post("/admin/force-delete-job/{job_id}", response_model=None)
def force_delete_job(
    job_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> Union[RedirectResponse, JSONResponse]:
    """Admin: permanently delete one backhaul job; reopen the load."""
    redirect = require_admin(request, db)
    if redirect:
        return redirect

    job = db.get(models.BackhaulJob, job_id)
    if not job:
        return RedirectResponse(url="/?section=matches&admin_delete_error=job_not_found", status_code=303)

    load = db.get(models.Load, job.load_id)
    lid = int(job.load_id)
    vid = int(job.vehicle_id)
    now = datetime.now(timezone.utc)

    try:
        _purge_backhaul_job(db, job)
        if load:
            _reopen_load_after_job_removed(db, load, lid, vid, now)
        db.commit()
    except IntegrityError:
        logger.exception("force_delete_job: integrity error job_id=%s", job_id)
        db.rollback()
        return RedirectResponse(url="/?section=matches&admin_delete_error=integrity", status_code=303)

    try:
        vehicle_availability_svc.refresh_vehicle_availability(db, vid)
        db.commit()
    except Exception:
        logger.exception("force_delete_job: refresh_vehicle_availability")
        db.rollback()

    if (request.headers.get("accept") or "").find("application/json") >= 0:
        return JSONResponse({"success": True, "deleted_job_id": job_id})
    return RedirectResponse(url="/?section=matches&admin_force_deleted=job", status_code=303)


@router.post("/admin/force-delete-vehicle/{vehicle_id}", response_model=None)
def force_delete_vehicle(
    vehicle_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> Union[RedirectResponse, JSONResponse]:
    """Admin: delete all jobs on this vehicle, then the vehicle (routes, interests)."""
    redirect = require_admin(request, db)
    if redirect:
        return redirect

    vehicle = db.get(models.Vehicle, vehicle_id)
    if not vehicle:
        return RedirectResponse(url="/?section=vehicles&admin_delete_error=vehicle_not_found", status_code=303)

    jobs = db.query(models.BackhaulJob).filter(models.BackhaulJob.vehicle_id == vehicle_id).all()
    vids: set[int] = {vehicle_id}
    try:
        for job in jobs:
            vids.add(_purge_backhaul_job(db, job))
        db.query(models.HaulierRoute).filter(models.HaulierRoute.vehicle_id == vehicle_id).delete(
            synchronize_session=False
        )
        _delete_vehicle_interests_and_tokens(db, vehicle_id)
        db.delete(vehicle)
        db.commit()
    except IntegrityError:
        logger.exception("force_delete_vehicle: integrity error vehicle_id=%s", vehicle_id)
        db.rollback()
        return RedirectResponse(url="/?section=vehicles&admin_delete_error=integrity", status_code=303)

    try:
        for vid in vids:
            vehicle_availability_svc.refresh_vehicle_availability(db, vid)
        db.commit()
    except Exception:
        logger.exception("force_delete_vehicle: refresh_vehicle_availability")
        db.rollback()

    if (request.headers.get("accept") or "").find("application/json") >= 0:
        return JSONResponse({"success": True, "deleted_vehicle_id": vehicle_id})
    return RedirectResponse(url="/?section=vehicles&admin_force_deleted=vehicle", status_code=303)


@router.post("/admin/cleanup-all-test-data", response_model=None)
def cleanup_all_test_data(
    request: Request,
    db: Session = Depends(get_db),
) -> Union[RedirectResponse, JSONResponse]:
    """Admin: delete loads (and cascaded jobs) older than 7 days."""
    redirect = require_admin(request, db)
    if redirect:
        return redirect

    now = datetime.now(timezone.utc)
    seven_days_ago = now - timedelta(days=7)

    old_loads = (
        db.query(models.Load).filter(models.Load.created_at < seven_days_ago).order_by(models.Load.id).all()
    )
    deleted_count = 0
    all_vehicle_ids: set[int] = set()

    try:
        for load in old_loads:
            jobs = db.query(models.BackhaulJob).filter(models.BackhaulJob.load_id == load.id).all()
            for job in jobs:
                all_vehicle_ids.add(_purge_backhaul_job(db, job))
            _delete_load_interests_for_load(db, load.id)
            db.delete(load)
            deleted_count += 1
        db.commit()
    except IntegrityError:
        logger.exception("cleanup_all_test_data: integrity error")
        db.rollback()
        return RedirectResponse(url="/admin/dashboard?cleanup_all_error=1", status_code=303)

    try:
        for vid in all_vehicle_ids:
            vehicle_availability_svc.refresh_vehicle_availability(db, vid)
        db.commit()
    except Exception:
        logger.exception("cleanup_all_test_data: refresh_vehicle_availability")
        db.rollback()

    if (request.headers.get("accept") or "").find("application/json") >= 0:
        return JSONResponse({"success": True, "deleted_loads": deleted_count})

    return RedirectResponse(
        url=f"/admin/dashboard?deleted_old_loads={deleted_count}",
        status_code=303,
    )


@router.get("/admin/verify-insurance/{vehicle_id}", response_class=HTMLResponse, response_model=None)
def verify_insurance_page(
    vehicle_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> Union[HTMLResponse, RedirectResponse]:
    redirect = require_admin(request, db)
    if redirect:
        return redirect
    vehicle = db.get(models.Vehicle, vehicle_id)
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    haulier = db.get(models.Haulier, vehicle.haulier_id)
    return templates.TemplateResponse(
        "admin/verify_insurance.html",
        {
            "request": request,
            "vehicle": vehicle,
            "haulier": haulier,
        },
    )


@router.get("/admin/insurance-certificate/{vehicle_id}", response_model=None)
def admin_insurance_certificate_file(
    vehicle_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> Union[FileResponse, RedirectResponse]:
    redirect = require_admin(request, db)
    if redirect:
        return redirect
    vehicle = db.get(models.Vehicle, vehicle_id)
    if not vehicle or not vehicle.insurance_certificate_path:
        raise HTTPException(status_code=404, detail="Certificate not found")
    path = get_insurance_storage_dir() / vehicle.insurance_certificate_path
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Certificate file missing")
    media = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media, filename=path.name)


@router.post("/admin/verify-insurance/{vehicle_id}", response_model=None)
def verify_insurance_action(
    vehicle_id: int,
    request: Request,
    action: str = Form(...),
    rejection_reason: str = Form(""),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    redirect = require_admin(request, db)
    if redirect:
        return redirect
    uid = get_session_user_id(request)
    vehicle = db.get(models.Vehicle, vehicle_id)
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")

    now = datetime.now(timezone.utc)
    if action == "approve":
        vehicle.insurance_certificate_verified = True
        vehicle.insurance_verified_at = now
        vehicle.insurance_verified_by = int(uid) if uid else None
        vehicle.insurance_rejection_reason = None
        db.add(vehicle)
        db.commit()
        try:
            record_user_notifications(
                db,
                haulier_office_user_ids(db, vehicle.haulier_id),
                title="Insurance certificate approved",
                body=(
                    f"Your certificate for vehicle {vehicle.registration} was approved. "
                    "You can accept loads with this vehicle."
                ),
                link_url="/?section=vehicles",
                kind="insurance_verified",
                priority="normal",
            )
        except Exception:
            logger.exception("verify_insurance_action approve notify")
        return RedirectResponse(url="/admin/dashboard?insurance_verified=1", status_code=303)

    if action == "reject":
        remove_insurance_file_if_exists(vehicle.insurance_certificate_path)
        vehicle.insurance_certificate_path = None
        vehicle.insurance_certificate_verified = False
        vehicle.insurance_verified_at = None
        vehicle.insurance_verified_by = None
        vehicle.insurance_uploaded_at = None
        rr = (rejection_reason or "").strip()[:500]
        vehicle.insurance_rejection_reason = rr or None
        vehicle.insurance_status = "unknown"
        db.add(vehicle)
        db.commit()
        try:
            record_user_notifications(
                db,
                haulier_office_user_ids(db, vehicle.haulier_id),
                title="Insurance certificate rejected",
                body=(
                    f"Your insurance upload for {vehicle.registration} was rejected. "
                    f"{'Reason: ' + rr if rr else 'Please upload a valid certificate under Vehicles.'}"
                ),
                link_url="/?section=vehicles",
                kind="insurance_rejected",
                priority="important",
            )
        except Exception:
            logger.exception("verify_insurance_action reject notify")
        return RedirectResponse(url="/admin/dashboard?insurance_rejected=1", status_code=303)

    return RedirectResponse(url="/admin/dashboard?insurance_action_error=1", status_code=303)
