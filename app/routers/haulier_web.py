"""
Haulier-facing dashboard: my company, my vehicles, find backhaul, planned routes.
Only for users with role=haulier; data filtered by haulier_id.
"""
import uuid
from pathlib import Path
from typing import Optional, Tuple, Union

from fastapi import APIRouter, Depends, Query, Request, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import delete as sa_delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import models
from app.auth import get_current_driver_optional, get_current_user_optional, require_haulier
from app.config import get_settings
from app.database import get_db
from app.services.matching import find_matching_loads
from app.services.insurance_status import calculate_insurance_status, finalize_vehicle_insurance_upload

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _haulier_or_redirect(request: Request, db: Session) -> Union[Tuple[models.User, models.Haulier], RedirectResponse]:
    """Return (user, haulier) or RedirectResponse. Haulier role only."""
    redirect = require_haulier(request, db)
    if redirect is not None:
        return redirect
    user = get_current_user_optional(request, db)
    if not user or not user.haulier_id:
        return RedirectResponse(url="/login", status_code=302)
    haulier = db.get(models.Haulier, user.haulier_id)
    if not haulier:
        return RedirectResponse(url="/login", status_code=302)
    return (user, haulier)

def _driver_visible_group_jobs(
    db: Session,
    active_job: models.BackhaulJob,
    haulier: models.Haulier,
    actor_driver: Optional[models.Driver],
) -> list[models.BackhaulJob]:
    """Incomplete jobs the driver may act on: same multi-drop group, or a single job."""
    q = (
        db.query(models.BackhaulJob)
        .join(models.Vehicle, models.BackhaulJob.vehicle_id == models.Vehicle.id)
        .filter(models.Vehicle.haulier_id == haulier.id)
        .filter(models.BackhaulJob.completed_at.is_(None))
    )
    if active_job.job_group_uuid:
        q = q.filter(models.BackhaulJob.job_group_uuid == active_job.job_group_uuid)
        if actor_driver is not None:
            # Whole run visible once any grouped job is assigned to this driver (peers may still be null in edge cases).
            q = q.filter(
                (models.BackhaulJob.driver_id == actor_driver.id)
                | (models.BackhaulJob.driver_id.is_(None))
            )
        return q.order_by(models.BackhaulJob.matched_at.asc()).all()
    if actor_driver is not None:
        q = q.filter(models.BackhaulJob.driver_id == actor_driver.id)
    return [active_job]


def _haulier_or_driver_context(
    request: Request, db: Session
) -> Union[Tuple[models.Haulier, Optional[models.Driver]], RedirectResponse]:
    """Return (haulier, optional driver actor) for shared driver workflow."""
    driver = get_current_driver_optional(request, db)
    if driver:
        haulier = db.get(models.Haulier, driver.haulier_id)
        if not haulier:
            return RedirectResponse(url="/driver-login", status_code=302)
        return (haulier, driver)
    result = _haulier_or_redirect(request, db)
    if isinstance(result, RedirectResponse):
        return result
    _, haulier = result
    return (haulier, None)

@router.get("/driver", response_class=HTMLResponse)
def driver_page(
    request: Request,
    db: Session = Depends(get_db),
):
    """Driver-led view: one active job, status buttons, share live location, loads on route home. Haulier only."""
    result = _haulier_or_driver_context(request, db)
    if isinstance(result, RedirectResponse):
        return result
    haulier, actor_driver = result
    q = (
        db.query(models.BackhaulJob)
        .join(models.Vehicle, models.BackhaulJob.vehicle_id == models.Vehicle.id)
        .filter(models.Vehicle.haulier_id == haulier.id)
        .filter(models.BackhaulJob.completed_at.is_(None))
    )
    if actor_driver is not None:
        q = q.filter(models.BackhaulJob.driver_id == actor_driver.id)
    requested_job_id: Optional[int] = None
    raw_jid = (request.query_params.get("job_id") or "").strip()
    if raw_jid:
        try:
            requested_job_id = int(raw_jid)
        except ValueError:
            requested_job_id = None
    if requested_job_id is not None:
        active_job = q.filter(models.BackhaulJob.id == requested_job_id).first()
        if active_job is None:
            active_job = q.order_by(models.BackhaulJob.matched_at.desc()).first()
    else:
        active_job = q.order_by(models.BackhaulJob.matched_at.desc()).first()
    available_jobs = []
    loads_on_route_home = []
    show_route_home_hint = False
    base_postcode_used = None
    if active_job:
        load = db.get(models.Load, active_job.load_id)
        vehicle = db.get(models.Vehicle, active_job.vehicle_id)
        if load and vehicle:
            # Base: driver override (query) > vehicle base > company (haulier) base
            return_to = (request.query_params.get("return_to") or "").strip().upper()
            base = (return_to or (vehicle.base_postcode or "").strip() or (haulier.base_postcode or "").strip())
            base_postcode_used = base or None
            if base:
                from app.services.matching import find_matching_loads_along_route
                pairs = find_matching_loads_along_route(
                    active_job.vehicle_id,
                    load.delivery_postcode,
                    base,
                    db,
                )
                loads_on_route_home = [{"load": l, "distance_miles": d} for l, d in pairs]
            else:
                show_route_home_hint = True
    group_jobs: list[models.BackhaulJob] = []
    is_multi_drop = False
    group_pickup_all_reached = False
    group_pickup_all_collected = False
    group_pickup_all_departed = False
    if active_job:
        group_jobs = _driver_visible_group_jobs(db, active_job, haulier, actor_driver)
        is_multi_drop = len(group_jobs) > 1
        if is_multi_drop:
            group_pickup_all_reached = all(g.reached_pickup_at for g in group_jobs)
            group_pickup_all_collected = all(g.collected_at for g in group_jobs)
            group_pickup_all_departed = all(g.departed_pickup_at for g in group_jobs)

    elif actor_driver is not None:
        # Driver login with no assigned active job: show unassigned jobs for this haulier so driver can claim one.
        unassigned = (
            db.query(models.BackhaulJob)
            .join(models.Vehicle, models.BackhaulJob.vehicle_id == models.Vehicle.id)
            .filter(models.Vehicle.haulier_id == haulier.id)
            .filter(models.BackhaulJob.completed_at.is_(None))
            .filter(models.BackhaulJob.driver_id.is_(None))
            .order_by(models.BackhaulJob.matched_at.desc())
            .all()
        )
        for j in unassigned:
            load = db.get(models.Load, j.load_id)
            vehicle = db.get(models.Vehicle, j.vehicle_id)
            available_jobs.append(
                {
                    "job": j,
                    "display_number": j.display_number,
                    "shipper_name": (load.shipper_name if load else ""),
                    "pickup_postcode": (load.pickup_postcode if load else ""),
                    "delivery_postcode": (load.delivery_postcode if load else ""),
                    "vehicle_registration": (vehicle.registration if vehicle else ""),
                }
            )
    return templates.TemplateResponse(
        "driver.html",
        {
            "request": request,
            "active_job": active_job,
            "group_jobs": group_jobs,
            "is_multi_drop": is_multi_drop,
            "group_pickup_all_reached": group_pickup_all_reached,
            "group_pickup_all_collected": group_pickup_all_collected,
            "group_pickup_all_departed": group_pickup_all_departed,
            "available_jobs": available_jobs,
            "loads_on_route_home": loads_on_route_home,
            "show_route_home_hint": show_route_home_hint,
            "base_postcode_used": base_postcode_used,
            "is_driver_login": bool(actor_driver),
            "dashboard_url": ("/driver-login" if actor_driver else "/?section=matches"),
        },
    )


@router.post("/driver/claim/{job_id}", response_class=RedirectResponse)
def driver_claim_job(
    job_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Driver login: claim an unassigned active job from their own haulier."""
    result = _haulier_or_driver_context(request, db)
    if isinstance(result, RedirectResponse):
        return result
    haulier, actor_driver = result
    if actor_driver is None:
        return RedirectResponse(url="/driver?error=Driver+login+required", status_code=303)
    job = _driver_job_for_haulier(job_id, haulier, db, actor_driver=None)
    if not job:
        return RedirectResponse(url="/driver?error=Job+not+found", status_code=303)
    if job.completed_at:
        return RedirectResponse(url="/driver?error=Job+already+completed", status_code=303)
    existing_active = (
        db.query(models.BackhaulJob)
        .filter(models.BackhaulJob.driver_id == actor_driver.id)
        .filter(models.BackhaulJob.completed_at.is_(None))
        .order_by(models.BackhaulJob.matched_at.desc())
        .first()
    )
    if existing_active and existing_active.id != job.id:
        same_group = (
            existing_active.job_group_uuid
            and job.job_group_uuid
            and existing_active.job_group_uuid == job.job_group_uuid
        )
        if not same_group:
            return RedirectResponse(
                url="/driver?error=You+already+have+an+active+job.+Complete+it+before+claiming+another",
                status_code=303,
            )
    if job.driver_id not in (None, actor_driver.id):
        return RedirectResponse(url="/driver?error=Job+already+assigned", status_code=303)
    job.driver_id = actor_driver.id
    from app.services.job_groups import propagate_group_driver

    propagate_group_driver(db, job, actor_driver.id)
    db.add(job)
    db.commit()
    return RedirectResponse(url="/driver", status_code=303)


def _driver_job_for_haulier(
    job_id: int,
    haulier: models.Haulier,
    db: Session,
    actor_driver: Optional[models.Driver] = None,
) -> Optional[models.BackhaulJob]:
    """Return job for this haulier; for driver actor, enforce assigned driver."""
    job = db.get(models.BackhaulJob, job_id)
    if not job:
        return None
    vehicle = db.get(models.Vehicle, job.vehicle_id)
    if not vehicle or vehicle.haulier_id != haulier.id:
        return None
    if actor_driver is not None and job.driver_id not in (None, actor_driver.id):
        return None
    return job


POD_UPLOAD_DIR = Path(__file__).resolve().parent.parent.parent / "static" / "uploads" / "pods"
POD_ALLOWED = {".pdf", ".jpg", ".jpeg", ".png", ".heic"}
POD_MAX_MB = 10


@router.get("/driver/epod", response_class=HTMLResponse)
def driver_epod_page(
    request: Request,
    db: Session = Depends(get_db),
    job_id: int = Query(None),
):
    """Driver: upload ePOD for a job. Completes delivery and triggers payout."""
    result = _haulier_or_driver_context(request, db)
    if isinstance(result, RedirectResponse):
        return result
    haulier, actor_driver = result
    if job_id is None:
        return RedirectResponse(url="/driver", status_code=302)
    job = _driver_job_for_haulier(job_id, haulier, db, actor_driver=actor_driver)
    if not job:
        return RedirectResponse(url="/driver?error=Job+not+found", status_code=302)
    if job.completed_at:
        return RedirectResponse(url="/driver?done=1", status_code=302)
    load = db.get(models.Load, job.load_id)
    return templates.TemplateResponse(
        "driver_epod.html",
        {"request": request, "job": job, "load": load, "job_id": job_id},
    )


@router.post("/driver/epod", response_class=RedirectResponse)
async def driver_epod_submit(
    request: Request,
    db: Session = Depends(get_db),
):
    """Driver: upload ePOD file → create POD → confirm (completes job + payout)."""
    result = _haulier_or_driver_context(request, db)
    if isinstance(result, RedirectResponse):
        return result
    haulier, actor_driver = result
    form = await request.form()
    try:
        job_id = int(form.get("job_id", 0))
    except (TypeError, ValueError):
        return RedirectResponse(url="/driver?error=Invalid+job", status_code=303)
    job = _driver_job_for_haulier(job_id, haulier, db, actor_driver=actor_driver)
    if not job:
        return RedirectResponse(url="/driver?error=Job+not+found", status_code=303)
    if job.completed_at:
        return RedirectResponse(url="/driver?done=1", status_code=303)

    file = form.get("file")
    if not file or not getattr(file, "filename", None):
        return RedirectResponse(url=f"/driver/epod?job_id={job_id}&error=No+file", status_code=303)
    suffix = Path(file.filename).suffix.lower()
    if suffix not in POD_ALLOWED:
        return RedirectResponse(url=f"/driver/epod?job_id={job_id}&error=Bad+file+type", status_code=303)
    content = await file.read()
    if len(content) > POD_MAX_MB * 1024 * 1024:
        return RedirectResponse(url=f"/driver/epod?job_id={job_id}&error=File+too+large", status_code=303)

    POD_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = f"{uuid.uuid4().hex}{suffix}"
    (POD_UPLOAD_DIR / safe_name).write_bytes(content)
    file_url = f"/static/uploads/pods/{safe_name}"

    from app.services.job_completion import finalize_job_with_pod_upload

    notes = (form.get("notes") or "").strip() or None
    err = finalize_job_with_pod_upload(db, job, file_url, notes)
    if err == "Confirm+collection+first":
        db.rollback()
        return RedirectResponse(
            url=f"/driver/epod?job_id={job_id}&error=Confirm+collection+first",
            status_code=303,
        )
    if err:
        db.rollback()
        from urllib.parse import quote_plus

        return RedirectResponse(
            url=f"/driver/epod?job_id={job_id}&error=" + quote_plus(err),
            status_code=303,
        )
    db.commit()
    return RedirectResponse(url="/driver?epod_done=1", status_code=303)


@router.get("/haulier", response_class=HTMLResponse)
def haulier_dashboard(
    request: Request,
    db: Session = Depends(get_db),
):
    """Redirect hauliers to main dashboard - same interface for everyone."""
    result = _haulier_or_redirect(request, db)
    if isinstance(result, RedirectResponse):
        return result
    # Redirect to main dashboard
    return RedirectResponse(url="/", status_code=302)


@router.get("/haulier/find-backhaul", response_class=HTMLResponse)
def haulier_find_backhaul(
    request: Request,
    db: Session = Depends(get_db),
):
    """Redirect to main dashboard find-backhaul - same interface for everyone."""
    result = _haulier_or_redirect(request, db)
    if isinstance(result, RedirectResponse):
        return result
    # Forward all query params to main find-backhaul
    query_string = str(request.query_params)
    redirect_url = f"/find-backhaul?{query_string}" if query_string else "/find-backhaul"
    return RedirectResponse(url=redirect_url, status_code=302)


@router.post("/haulier/profile", response_class=RedirectResponse)
async def haulier_update_profile(
    request: Request,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    result = _haulier_or_driver_context(request, db)
    if isinstance(result, RedirectResponse):
        return result
    haulier, actor_driver = result
    form = await request.form()
    haulier.name = (form.get("name") or haulier.name or "").strip()
    haulier.contact_email = (form.get("contact_email") or haulier.contact_email or "").strip()
    haulier.contact_phone = (form.get("contact_phone") or "").strip() or None
    haulier.payment_account_id = (form.get("payment_account_id") or "").strip() or None
    haulier.base_postcode = (form.get("base_postcode") or "").strip().upper() or None
    haulier.bank_account_name = (form.get("bank_account_name") or "").strip() or None
    haulier.sort_code = (form.get("sort_code") or "").strip().replace(" ", "") or None
    haulier.account_number = (form.get("account_number") or "").strip().replace(" ", "") or None
    db.commit()
    return RedirectResponse(url="/?section=company&profile_saved=1", status_code=303)


@router.post("/haulier/vehicles", response_class=RedirectResponse)
async def haulier_add_vehicle(
    request: Request,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    result = _haulier_or_driver_context(request, db)
    if isinstance(result, RedirectResponse):
        return result
    haulier, actor_driver = result
    from datetime import date as date_cls
    from urllib.parse import quote_plus

    from starlette.datastructures import UploadFile

    form = await request.form()
    registration = (form.get("registration") or "").upper().strip()
    vehicle_type = form.get("vehicle_type") or "rigid"
    trailer_type = (form.get("trailer_type") or "").strip() or None
    base_postcode = (form.get("base_postcode") or "").strip().upper() or None
    if not registration:
        return RedirectResponse(url="/?section=vehicles&delete_error=Registration+required", status_code=303)
    insurance_expiry_raw = form.get("insurance_expiry_date")
    insurance_file = form.get("insurance_certificate")
    if not insurance_expiry_raw or not str(insurance_expiry_raw).strip():
        return RedirectResponse(
            url="/?section=vehicles&delete_error=" + quote_plus("Insurance expiry date is required"),
            status_code=303,
        )
    try:
        insurance_expiry = date_cls.fromisoformat(str(insurance_expiry_raw).strip())
    except ValueError:
        return RedirectResponse(
            url="/?section=vehicles&delete_error=" + quote_plus("Invalid insurance expiry date"),
            status_code=303,
        )
    if not isinstance(insurance_file, UploadFile) or not getattr(insurance_file, "filename", None):
        return RedirectResponse(
            url="/?section=vehicles&delete_error=" + quote_plus("Insurance certificate file is required"),
            status_code=303,
        )
    if db.query(models.Vehicle).filter(models.Vehicle.registration == registration).first():
        return RedirectResponse(
            url="/?section=vehicles&delete_error=Registration+already+exists",
            status_code=303,
        )
    try:
        vehicle = models.Vehicle(
            haulier_id=haulier.id,
            registration=registration,
            vehicle_type=vehicle_type,
            trailer_type=trailer_type,
            base_postcode=base_postcode,
            insurance_expiry_date=insurance_expiry,
            insurance_status=calculate_insurance_status(insurance_expiry),
        )
        db.add(vehicle)
        db.commit()
        db.refresh(vehicle)
    except IntegrityError:
        db.rollback()
        return RedirectResponse(url="/?section=vehicles&delete_error=Could+not+save+vehicle", status_code=303)
    try:
        await finalize_vehicle_insurance_upload(db, vehicle, insurance_file)
    except ValueError as exc:
        try:
            db.delete(vehicle)
            db.commit()
        except Exception:
            db.rollback()
        return RedirectResponse(
            url="/?section=vehicles&delete_error=" + quote_plus(str(exc)),
            status_code=303,
        )
    if base_postcode:
        try:
            from app.services.alert_stream import notify_matching_loads_for_vehicle
            notify_matching_loads_for_vehicle(
                vehicle.id, base_postcode, haulier.id, db, origin_label="base",
            )
        except Exception:
            pass
    return RedirectResponse(url="/?section=vehicles&vehicle_added=1", status_code=303)


@router.post("/haulier/delete-vehicle/{vehicle_id}", response_class=RedirectResponse)
def haulier_delete_vehicle(
    vehicle_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    result = _haulier_or_driver_context(request, db)
    if isinstance(result, RedirectResponse):
        return result
    haulier, actor_driver = result
    vehicle = db.get(models.Vehicle, vehicle_id)
    if not vehicle or vehicle.haulier_id != haulier.id:
        return RedirectResponse(url="/?section=vehicles&delete_error=Vehicle+not+found", status_code=303)
    if db.query(models.BackhaulJob).filter(models.BackhaulJob.vehicle_id == vehicle_id).first():
        return RedirectResponse(url="/?section=vehicles&delete_error=Vehicle+has+jobs", status_code=303)
    if db.query(models.HaulierRoute).filter(models.HaulierRoute.vehicle_id == vehicle_id).first():
        return RedirectResponse(url="/?section=vehicles&delete_error=Remove+from+routes+first", status_code=303)
    try:
        db.execute(sa_delete(models.LoadInterest).where(models.LoadInterest.vehicle_id == vehicle_id))
        db.delete(vehicle)
        db.commit()
    except IntegrityError:
        db.rollback()
        return RedirectResponse(url="/?section=vehicles&delete_error=Cannot+delete+vehicle", status_code=303)
    return RedirectResponse(url="/?section=vehicles&deleted=vehicle", status_code=303)


@router.post("/haulier/routes", response_class=RedirectResponse)
async def haulier_add_route(
    request: Request,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    result = _haulier_or_driver_context(request, db)
    if isinstance(result, RedirectResponse):
        return result
    haulier, actor_driver = result
    form = await request.form()
    vehicle_id = int(form.get("vehicle_id", 0))
    vehicle = db.get(models.Vehicle, vehicle_id)
    if not vehicle or vehicle.haulier_id != haulier.id:
        return RedirectResponse(url="/?section=routes&delete_error=Invalid+vehicle", status_code=303)
    try:
        day = int(form.get("day_of_week", 0))
    except (TypeError, ValueError):
        day = 0
    route = models.HaulierRoute(
        haulier_id=haulier.id,
        vehicle_id=vehicle_id,
        empty_at_postcode=(form.get("empty_at_postcode") or "").strip().upper(),
        day_of_week=day,
        recurrence=(form.get("recurrence") or "weekly").strip(),
    )
    db.add(route)
    db.commit()
    db.refresh(route)
    from app.services.alert_stream import notify_route_match, notify_matching_loads_for_vehicle
    from app.services.matching import planned_load_matches_route
    for pl in db.query(models.PlannedLoad).all():
        if planned_load_matches_route(pl, route, db):
            notify_route_match(pl, route, db)
    notify_matching_loads_for_vehicle(
        route.vehicle_id, route.empty_at_postcode or "", route.haulier_id, db,
        origin_label="planned route",
    )
    return RedirectResponse(url="/?section=routes", status_code=303)


@router.post("/haulier/delete-route/{route_id}", response_class=RedirectResponse)
def haulier_delete_route(
    route_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    result = _haulier_or_driver_context(request, db)
    if isinstance(result, RedirectResponse):
        return result
    haulier, actor_driver = result
    route = db.get(models.HaulierRoute, route_id)
    if not route or route.haulier_id != haulier.id:
        return RedirectResponse(url="/?section=routes&delete_error=Route+not+found", status_code=303)
    db.delete(route)
    db.commit()
    return RedirectResponse(url="/?section=routes", status_code=303)