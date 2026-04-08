"""
Loader-facing dashboard: my loads, planned loads, who's interested.
Only for users with role=loader; data filtered by loader_id.
"""
import logging
from typing import Optional, Tuple, Union

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app import models
from app.auth import get_current_user_optional, require_loader
from app.config import get_settings
from app.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _checkout_public_base_url(request: Request, settings) -> str:
    """HTTPS public origin for Stripe success/cancel URLs. Prefer env over request.base_url (often http behind Railway)."""
    pub = (getattr(settings, "public_app_base_url", None) or "").strip().rstrip("/")
    if pub:
        return pub
    return str(request.base_url).rstrip("/")


def _form_checkbox(form, name: str) -> bool:
    v = form.get(name)
    if v is None:
        return False
    s = str(v).strip().lower()
    return s in ("on", "true", "1", "yes")


def _loader_get_load_owned(db: Session, load_id: int, loader_id: int) -> Optional[models.Load]:
    load = db.get(models.Load, load_id)
    if not load or load.loader_id != loader_id:
        return None
    return load


def _fmt_dt_for_input(dt) -> str:
    if dt is None:
        return ""
    from datetime import timezone as tz

    if getattr(dt, "tzinfo", None) is not None:
        dt = dt.astimezone(tz.utc)
    return dt.strftime("%Y-%m-%dT%H:%M")


def _primary_job_for_load(db: Session, load_id: int) -> Optional[models.BackhaulJob]:
    return (
        db.query(models.BackhaulJob)
        .filter(models.BackhaulJob.load_id == load_id)
        .order_by(models.BackhaulJob.matched_at.desc())
        .first()
    )


def _notify_hauliers_load_cancelled(db: Session, job: models.BackhaulJob, load: models.Load) -> None:
    vehicle = db.get(models.Vehicle, job.vehicle_id)
    if not vehicle:
        return
    from app.services.email_sender import send_email

    users = db.query(models.User).filter(models.User.haulier_id == vehicle.haulier_id).all()
    jref = job.display_number
    for u in users:
        if u.email:
            send_email(
                u.email,
                "Platoonix: load cancelled by shipper",
                f"The load \"{load.shipper_name}\" ({load.pickup_postcode} → {load.delivery_postcode}) has been cancelled by the loader.\n\n"
                f"Job: {jref}.\n",
            )


def _loader_or_redirect(request: Request, db: Session) -> Union[Tuple[models.User, models.Loader], RedirectResponse]:
    redirect = require_loader(request, db)
    if redirect is not None:
        return redirect
    user = get_current_user_optional(request, db)
    if not user or not user.loader_id:
        return RedirectResponse(url="/login", status_code=302)
    loader = db.get(models.Loader, user.loader_id)
    if not loader:
        return RedirectResponse(url="/login", status_code=302)
    return (user, loader)


@router.get("/loader", response_class=HTMLResponse)
def loader_dashboard(
    request: Request,
    db: Session = Depends(get_db),
):
    """Redirect loaders to main dashboard - same interface for everyone."""
    result = _loader_or_redirect(request, db)
    if isinstance(result, RedirectResponse):
        return result
    # Redirect to main dashboard
    return RedirectResponse(url="/?section=find", status_code=302)


@router.post("/loader/profile", response_class=RedirectResponse)
async def loader_update_profile(
    request: Request,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Save loader company contact details."""
    result = _loader_or_redirect(request, db)
    if isinstance(result, RedirectResponse):
        return result
    _user, loader = result
    form = await request.form()
    loader.name = (form.get("name") or loader.name or "").strip()
    loader.contact_email = (form.get("contact_email") or loader.contact_email or "").strip()
    loader.contact_phone = (form.get("contact_phone") or "").strip() or None
    loader.contact_name = (form.get("contact_name") or "").strip() or None
    db.commit()
    return RedirectResponse(url="/?section=company&profile_saved=1", status_code=303)


@router.post("/loader/billing/setup", response_class=RedirectResponse)
def loader_setup_payment_method(
    request: Request,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Create Stripe setup session to collect/update loader card on file."""
    result = _loader_or_redirect(request, db)
    if isinstance(result, RedirectResponse):
        return result
    _user, loader = result

    settings = get_settings()
    stripe_key = (settings.stripe_secret_key or "").strip()
    if not stripe_key:
        return RedirectResponse(url="/?section=company&payment_error=stripe_not_configured", status_code=303)

    try:
        import stripe

        stripe.api_key = stripe_key
        if not (loader.stripe_customer_id or "").strip():
            customer = stripe.Customer.create(
                email=(loader.contact_email or "").strip() or None,
                name=(loader.name or "").strip() or None,
                metadata={"loader_id": str(loader.id)},
            )
            loader.stripe_customer_id = customer.id
            db.commit()

        base_url = _checkout_public_base_url(request, settings)
        session = stripe.checkout.Session.create(
            mode="setup",
            payment_method_types=["card"],
            customer=loader.stripe_customer_id.strip(),
            currency="gbp",
            success_url=f"{base_url}/?section=company&payment_setup=1",
            cancel_url=f"{base_url}/?section=company&payment_setup_cancelled=1",
        )
        return RedirectResponse(url=session.url, status_code=303)
    except Exception:
        logger.exception("[STRIPE] loader_setup_payment_method failed")
        return RedirectResponse(url="/?section=company&payment_error=setup_failed", status_code=303)


@router.post("/loader/loads", response_class=RedirectResponse)
async def loader_add_load(
    request: Request,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    result = _loader_or_redirect(request, db)
    if isinstance(result, RedirectResponse):
        return result
    user, loader = result
    form = await request.form()
    from datetime import datetime, timezone

    from app.services.upload_parser import parse_datetime_optional

    shipper_name = (form.get("shipper_name") or "").strip()
    booking_name = (form.get("booking_name") or "").strip() or None
    booking_ref = (form.get("booking_ref") or "").strip() or None
    pickup_postcode = (form.get("pickup_postcode") or "").strip().upper()
    delivery_postcode = (form.get("delivery_postcode") or "").strip().upper()
    weight_kg = None
    volume_m3 = None
    try:
        w = form.get("weight_kg")
        if w is not None and str(w).strip():
            weight_kg = float(w)
    except (TypeError, ValueError):
        pass
    try:
        v = form.get("volume_m3")
        if v is not None and str(v).strip():
            volume_m3 = float(v)
    except (TypeError, ValueError):
        pass
    pallets = None
    try:
        p = form.get("pallets")
        if p is not None and str(p).strip():
            pallets = float(p)
            if pallets and pallets > 0:
                from app.config import get_settings
                volume_m3 = pallets * get_settings().pallet_volume_m3
    except (TypeError, ValueError):
        pass
    required_vehicle_type = (form.get("required_vehicle_type") or "").strip().lower() or None
    required_trailer_type = (form.get("required_trailer_type") or "").strip().lower() or None
    requirements = {}
    if required_vehicle_type and required_vehicle_type != "any":
        requirements["vehicle_type"] = required_vehicle_type
    if required_trailer_type and required_trailer_type != "any":
        requirements["trailer_type"] = required_trailer_type
    requirements = requirements if requirements else None

    budget_val = None
    try:
        b = form.get("budget_gbp")
        if b is not None and str(b).strip():
            budget_val = float(b)
    except (TypeError, ValueError):
        pass

    now = datetime.now(timezone.utc)
    ps = parse_datetime_optional(form.get("pickup_window_start"))
    pe = parse_datetime_optional(form.get("pickup_window_end"))
    ds = parse_datetime_optional(form.get("delivery_window_start"))
    de = parse_datetime_optional(form.get("delivery_window_end"))
    if ps is None and pe is None:
        ps = pe = now
    else:
        if ps is None:
            ps = pe
        if pe is None:
            pe = ps
    if ds is None and de is None:
        ds = de = now
    else:
        if ds is None:
            ds = de
        if de is None:
            de = ds

    load = models.Load(
        loader_id=loader.id,
        shipper_name=shipper_name,
        booking_ref=booking_ref,
        booking_name=booking_name,
        pickup_postcode=pickup_postcode,
        delivery_postcode=delivery_postcode,
        pickup_window_start=ps,
        pickup_window_end=pe,
        delivery_window_start=ds,
        delivery_window_end=de,
        weight_kg=weight_kg,
        volume_m3=volume_m3,
        pallets=pallets,
        budget_gbp=budget_val,
        requirements=requirements,
        status=models.LoadStatusEnum.OPEN.value,
    )
    db.add(load)
    db.commit()
    db.refresh(load)
    try:
        from app.services.alert_stream import notify_new_load
        notify_new_load(load, db)
    except Exception:
        pass
    return RedirectResponse(url="/?section=loads&load_added=1", status_code=303)


@router.post("/loader/delete-load/{load_id}", response_class=RedirectResponse)
def loader_delete_load(
    load_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    result = _loader_or_redirect(request, db)
    if isinstance(result, RedirectResponse):
        return result
    user, loader = result
    load = db.get(models.Load, load_id)
    if not load or load.loader_id != loader.id:
        return RedirectResponse(url="/?section=loads&delete_error=Load+not+found", status_code=303)
    if db.query(models.BackhaulJob).filter(models.BackhaulJob.load_id == load_id).first():
        return RedirectResponse(url="/?section=loads&delete_error=Load+has+jobs", status_code=303)
    db.query(models.LoadInterest).filter(models.LoadInterest.load_id == load_id).delete()
    db.delete(load)
    db.commit()
    return RedirectResponse(url="/?section=loads&deleted=load", status_code=303)


@router.get("/loader/loads/{load_id}/edit", response_class=HTMLResponse, response_model=None)
def loader_edit_load_page(
    load_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> Union[HTMLResponse, RedirectResponse]:
    result = _loader_or_redirect(request, db)
    if isinstance(result, RedirectResponse):
        return result
    _user, loader = result
    load = _loader_get_load_owned(db, load_id, loader.id)
    if not load:
        return RedirectResponse(url="/?section=loads&load_error=not_found", status_code=303)
    if load.status != models.LoadStatusEnum.OPEN.value:
        return RedirectResponse(url="/?section=loads&load_error=cannot_edit_status", status_code=303)
    if _primary_job_for_load(db, load_id):
        return RedirectResponse(url="/?section=loads&load_error=cannot_edit_matched", status_code=303)
    req = load.requirements or {}
    vt = (req.get("vehicle_type") or "") if isinstance(req, dict) else ""
    tt = (req.get("trailer_type") or "") if isinstance(req, dict) else ""
    pallet_vol = get_settings().pallet_volume_m3
    return templates.TemplateResponse(
        "loader_load_edit.html",
        {
            "request": request,
            "load": load,
            "vehicle_type": vt,
            "trailer_type": tt,
            "pickup_window_start": _fmt_dt_for_input(load.pickup_window_start),
            "pickup_window_end": _fmt_dt_for_input(load.pickup_window_end),
            "delivery_window_start": _fmt_dt_for_input(load.delivery_window_start),
            "delivery_window_end": _fmt_dt_for_input(load.delivery_window_end),
            "pallet_volume_m3": pallet_vol,
            "loader_fee_minimum_gbp": get_settings().loader_flat_fee_gbp,
            "loader_fee_percent_of_load": get_settings().loader_fee_percent_of_load,
        },
    )


@router.post("/loader/loads/{load_id}/update", response_class=RedirectResponse)
async def loader_update_load(
    load_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    result = _loader_or_redirect(request, db)
    if isinstance(result, RedirectResponse):
        return result
    _user, loader = result
    load = _loader_get_load_owned(db, load_id, loader.id)
    if not load:
        return RedirectResponse(url="/?section=loads&load_error=not_found", status_code=303)
    if load.status != models.LoadStatusEnum.OPEN.value:
        return RedirectResponse(url="/?section=loads&load_error=cannot_edit_status", status_code=303)
    if _primary_job_for_load(db, load_id):
        return RedirectResponse(url="/?section=loads&load_error=cannot_edit_matched", status_code=303)

    from datetime import datetime, timezone

    from app.services.upload_parser import parse_datetime_optional

    form = await request.form()
    shipper_name = (form.get("shipper_name") or "").strip()
    booking_name = (form.get("booking_name") or "").strip() or None
    booking_ref = (form.get("booking_ref") or "").strip() or None
    pickup_postcode = (form.get("pickup_postcode") or "").strip().upper()
    delivery_postcode = (form.get("delivery_postcode") or "").strip().upper()
    vehicle_type_required = (form.get("vehicle_type_required") or "").strip().lower() or None
    trailer_type_required = (form.get("trailer_type_required") or "").strip().lower() or None

    pallets_val = None
    try:
        p = form.get("pallets")
        if p is not None and str(p).strip():
            pallets_val = float(p)
    except (TypeError, ValueError):
        pass
    volume_m3 = None
    try:
        c = form.get("cubic_metres")
        if c is not None and str(c).strip():
            volume_m3 = float(c)
    except (TypeError, ValueError):
        pass
    if pallets_val and pallets_val > 0:
        volume_m3 = pallets_val * get_settings().pallet_volume_m3

    budget_val = None
    try:
        b = form.get("budget_gbp")
        if b is not None and str(b).strip():
            budget_val = float(b)
    except (TypeError, ValueError):
        pass

    notes = (form.get("load_notes") or "").strip() or None

    now = datetime.now(timezone.utc)
    ps = parse_datetime_optional(form.get("pickup_window_start"))
    pe = parse_datetime_optional(form.get("pickup_window_end"))
    ds = parse_datetime_optional(form.get("delivery_window_start"))
    de = parse_datetime_optional(form.get("delivery_window_end"))
    if ps is None and pe is None:
        ps = pe = now
    else:
        if ps is None:
            ps = pe
        if pe is None:
            pe = ps
    if ds is None and de is None:
        ds = de = now
    else:
        if ds is None:
            ds = de
        if de is None:
            de = ds

    if not shipper_name or not pickup_postcode or not delivery_postcode:
        return RedirectResponse(url=f"/loader/loads/{load_id}/edit?error=missing", status_code=303)

    requirements: dict = {}
    if vehicle_type_required and vehicle_type_required != "any":
        requirements["vehicle_type"] = vehicle_type_required
    if trailer_type_required and trailer_type_required != "any":
        requirements["trailer_type"] = trailer_type_required
    requirements = requirements if requirements else None

    load.shipper_name = shipper_name
    load.booking_ref = booking_ref
    load.booking_name = booking_name
    load.pickup_postcode = pickup_postcode
    load.delivery_postcode = delivery_postcode
    load.pickup_window_start = ps
    load.pickup_window_end = pe
    load.delivery_window_start = ds
    load.delivery_window_end = de
    load.pallets = pallets_val
    load.volume_m3 = volume_m3
    load.budget_gbp = budget_val
    load.requirements = requirements
    load.requires_tail_lift = _form_checkbox(form, "requires_tail_lift")
    load.requires_forklift = _form_checkbox(form, "requires_forklift")
    load.requires_temp_control = _form_checkbox(form, "requires_temp_control")
    load.requires_adr = _form_checkbox(form, "requires_adr")
    load.load_notes = notes
    db.add(load)
    db.commit()
    return RedirectResponse(url="/?section=loads&load_updated=1", status_code=303)


@router.post("/loader/loads/{load_id}/cancel", response_class=RedirectResponse)
def loader_cancel_load(
    load_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    result = _loader_or_redirect(request, db)
    if isinstance(result, RedirectResponse):
        return result
    _user, loader = result
    load = _loader_get_load_owned(db, load_id, loader.id)
    if not load:
        return RedirectResponse(url="/?section=loads&load_error=not_found", status_code=303)
    if load.status == models.LoadStatusEnum.CANCELLED.value:
        return RedirectResponse(url="/?section=loads&load_error=already_cancelled", status_code=303)

    job = _primary_job_for_load(db, load_id)
    payment = None
    if job:
        if job.completed_at:
            return RedirectResponse(url="/?section=loads&load_error=cannot_cancel_completed", status_code=303)
        if job.collected_at:
            return RedirectResponse(url="/?section=loads&load_error=cannot_cancel_collected", status_code=303)
        payment = (
            db.query(models.Payment)
            .filter(models.Payment.backhaul_job_id == job.id)
            .order_by(models.Payment.created_at.asc())
            .first()
        )
        if payment and (payment.status or "").strip().lower() == models.PaymentStatusEnum.PAID_OUT.value:
            return RedirectResponse(url="/?section=loads&load_error=cannot_cancel_paid_out", status_code=303)

    refund_warning = False
    if payment and (payment.status or "").strip().lower() == models.PaymentStatusEnum.CAPTURED.value:
        from app.services.stripe_loader_charge import try_refund_loader_charge

        ok_ref, _err = try_refund_loader_charge(payment, db)
        if not ok_ref:
            refund_warning = True
        else:
            db.add(payment)

    load.status = models.LoadStatusEnum.CANCELLED.value
    db.add(load)

    for li in db.query(models.LoadInterest).filter(models.LoadInterest.load_id == load.id).all():
        if (li.status or "") not in ("declined",):
            li.status = "declined"
            db.add(li)

    db.commit()

    if job:
        try:
            _notify_hauliers_load_cancelled(db, job, load)
        except Exception:
            logger.exception("notify haulier load cancelled")
        try:
            from app.services import vehicle_availability as vehicle_availability_svc

            vehicle_availability_svc.refresh_vehicle_availability(db, job.vehicle_id)
            db.commit()
        except Exception:
            logger.exception("refresh_vehicle_availability after cancel")

    q = "section=loads&load_cancelled=1"
    if refund_warning:
        q += "&refund_warning=1"
    return RedirectResponse(url=f"/?{q}", status_code=303)


@router.post("/loader/planned-loads", response_class=RedirectResponse)
async def loader_add_planned(
    request: Request,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    result = _loader_or_redirect(request, db)
    if isinstance(result, RedirectResponse):
        return result
    user, loader = result
    form = await request.form()
    try:
        day = int(form.get("day_of_week", 0))
    except (TypeError, ValueError):
        day = 0
    pl = models.PlannedLoad(
        loader_id=loader.id,
        shipper_name=(form.get("shipper_name") or "").strip(),
        pickup_postcode=(form.get("pickup_postcode") or "").strip().upper(),
        delivery_postcode=(form.get("delivery_postcode") or "").strip().upper(),
        day_of_week=day,
        recurrence=(form.get("recurrence") or "weekly").strip(),
    )
    db.add(pl)
    db.commit()
    db.refresh(pl)
    from app.services.alert_stream import notify_route_match
    from app.services.matching import planned_load_matches_route
    for route in db.query(models.HaulierRoute).all():
        if planned_load_matches_route(pl, route, db):
            notify_route_match(pl, route, db)
    return RedirectResponse(url="/?section=routes&planned_added=1", status_code=303)


@router.post("/loader/delete-planned/{planned_id}", response_class=RedirectResponse)
def loader_delete_planned(
    planned_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    result = _loader_or_redirect(request, db)
    if isinstance(result, RedirectResponse):
        return result
    user, loader = result
    pl = db.get(models.PlannedLoad, planned_id)
    if not pl or pl.loader_id != loader.id:
        return RedirectResponse(url="/?section=routes&delete_error=Planned+load+not+found", status_code=303)
    db.query(models.LoadInterest).filter(models.LoadInterest.planned_load_id == planned_id).delete()
    db.delete(pl)
    db.commit()
    return RedirectResponse(url="/?section=routes", status_code=303)


@router.post("/loader/show-interest", response_class=RedirectResponse)
async def loader_accept_interest(
    request: Request,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Loader accepts haulier interest -> create BackhaulJob + Payment placeholder."""
    from datetime import datetime, timezone
    from app.config import get_settings

    result = _loader_or_redirect(request, db)
    if isinstance(result, RedirectResponse):
        return result
    user, loader = result
    form = await request.form()
    load_interest_id = form.get("load_interest_id")
    if not load_interest_id:
        return RedirectResponse(url="/?section=matches", status_code=303)
    try:
        load_interest_id = int(load_interest_id)
    except (TypeError, ValueError):
        return RedirectResponse(url="/?section=matches", status_code=303)
    interest = db.get(models.LoadInterest, load_interest_id)
    if not interest or interest.status != "expressed":
        return RedirectResponse(url="/?section=matches", status_code=303)
    load = None
    if interest.load_id:
        load = db.get(models.Load, interest.load_id)
        if not load or load.loader_id != loader.id:
            return RedirectResponse(url="/?section=matches&delete_error=Load+not+yours", status_code=303)
        if load.status == models.LoadStatusEnum.MATCHED.value:
            interest.status = "accepted"
            db.commit()
            return RedirectResponse(url="/?section=matches&already_matched=1", status_code=303)
    else:
        pl = db.get(models.PlannedLoad, interest.planned_load_id) if interest.planned_load_id else None
        if not pl or pl.loader_id != loader.id:
            return RedirectResponse(url="/?section=matches&delete_error=Planned+load+not+yours", status_code=303)
        # Create a concrete Load from this planned load (one instance of the recurring job)
        now = datetime.now(timezone.utc)
        load = models.Load(
            loader_id=loader.id,
            shipper_name=pl.shipper_name,
            pickup_postcode=pl.pickup_postcode,
            delivery_postcode=pl.delivery_postcode,
            pickup_window_start=now,
            pickup_window_end=now,
            weight_kg=pl.weight_kg,
            volume_m3=pl.volume_m3,
            requirements=pl.requirements,
            budget_gbp=pl.budget_gbp,
            status=models.LoadStatusEnum.MATCHED.value,
        )
        db.add(load)
        db.flush()

    amount_gbp = float(load.budget_gbp or 0)
    settings = get_settings()
    from app.services.payment_fees import compute_job_payment_splits

    splits = compute_job_payment_splits(amount_gbp, settings)

    from app.services.job_driver_resolution import resolve_driver_id_for_accepted_interest

    job_driver_id = resolve_driver_id_for_accepted_interest(db, interest)

    job = models.BackhaulJob(
        vehicle_id=interest.vehicle_id,
        load_id=load.id,
        driver_id=job_driver_id,
        matched_at=datetime.now(timezone.utc),
        accepted_at=datetime.now(timezone.utc),
    )
    db.add(job)
    db.flush()

    from app.services.job_groups import try_link_new_job_pickup_group

    try_link_new_job_pickup_group(db, job)

    payment = models.Payment(
        backhaul_job_id=job.id,
        amount_gbp=splits.amount_gbp,
        fee_gbp=splits.fee_gbp,
        net_payout_gbp=splits.net_payout_gbp,
        flat_fee_gbp=splits.flat_fee_gbp,
        status=models.PaymentStatusEnum.RESERVED.value,
    )
    db.add(payment)

    if interest.load_id:
        load.status = models.LoadStatusEnum.MATCHED.value
        db.add(load)
    interest.status = "accepted"
    db.commit()
    from app.services import vehicle_availability as vehicle_availability_svc

    vehicle_availability_svc.refresh_vehicle_availability(db, job.vehicle_id)
    db.commit()
    return RedirectResponse(url="/?section=matches&job_created=1", status_code=303)
