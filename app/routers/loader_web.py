"""
Loader-facing dashboard: my loads, planned loads, who's interested.
Only for users with role=loader; data filtered by loader_id.
"""
import logging
from datetime import date
from typing import Optional, Tuple, Union

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
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


def _interest_accepted_to_suggested(db: Session, load_id: int, vehicle_id: int) -> None:
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
        wt = form.get("weight_tonnes")
        if wt is not None and str(wt).strip():
            weight_kg = float(str(wt).strip()) * 1000.0
    except (TypeError, ValueError):
        pass
    if weight_kg is None:
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

    from datetime import date as date_cls

    from app.services.load_schedule import VALID_SLOTS, schedule_to_utc_windows

    now = datetime.now(timezone.utc)
    pd_raw = (form.get("pickup_date") or "").strip()
    dd_raw = (form.get("delivery_date") or "").strip()
    pickup_tw = (form.get("pickup_time_window") or "flexible").strip().lower()
    delivery_tw = (form.get("delivery_time_window") or "flexible").strip().lower()
    if pickup_tw not in VALID_SLOTS:
        pickup_tw = "flexible"
    if delivery_tw not in VALID_SLOTS:
        delivery_tw = "flexible"
    ps = pe = ds = de = None  # type: ignore[assignment]
    pickup_d_val = None
    delivery_d_val = None
    if pd_raw and dd_raw:
        try:
            pickup_d_val = date_cls.fromisoformat(pd_raw)
            delivery_d_val = date_cls.fromisoformat(dd_raw)
            ps, pe, ds, de = schedule_to_utc_windows(
                pickup_d_val, pickup_tw, delivery_d_val, delivery_tw
            )
        except ValueError:
            pickup_d_val = None
            delivery_d_val = None
    if ps is None:
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
        pickup_date=pickup_d_val,
        pickup_time_window=pickup_tw if pickup_d_val else None,
        delivery_date=delivery_d_val,
        delivery_time_window=delivery_tw if delivery_d_val else None,
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
    from datetime import date as date_cls

    from app.services.load_schedule import infer_schedule_from_datetimes

    ipd, iptw, idd, idtw = infer_schedule_from_datetimes(
        load.pickup_window_start,
        load.delivery_window_start or load.pickup_window_start,
    )
    pickup_date_str = ""
    delivery_date_str = ""
    if load.pickup_date:
        pickup_date_str = load.pickup_date.isoformat()
    elif ipd:
        pickup_date_str = ipd.isoformat()
    if load.delivery_date:
        delivery_date_str = load.delivery_date.isoformat()
    elif idd:
        delivery_date_str = idd.isoformat()
    pickup_tw_val = load.pickup_time_window or iptw or "flexible"
    delivery_tw_val = load.delivery_time_window or idtw or "flexible"
    wton = ""
    if load.weight_kg is not None:
        wton = ("%s" % round(load.weight_kg / 1000.0, 3)).rstrip("0").rstrip(".")
    return templates.TemplateResponse(
        "loader_load_edit.html",
        {
            "request": request,
            "load": load,
            "vehicle_type": vt,
            "trailer_type": tt,
            "pickup_date": pickup_date_str,
            "delivery_date": delivery_date_str,
            "pickup_time_window": pickup_tw_val,
            "delivery_time_window": delivery_tw_val,
            "weight_tonnes": wton,
            "load_form_date_min": date_cls.today().isoformat(),
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

    from datetime import date as date_cls
    from datetime import datetime, timezone

    from app.services.load_schedule import VALID_SLOTS, schedule_to_utc_windows
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
    pd_raw = (form.get("pickup_date") or "").strip()
    dd_raw = (form.get("delivery_date") or "").strip()
    pickup_tw = (form.get("pickup_time_window") or "flexible").strip().lower()
    delivery_tw = (form.get("delivery_time_window") or "flexible").strip().lower()
    if pickup_tw not in VALID_SLOTS:
        pickup_tw = "flexible"
    if delivery_tw not in VALID_SLOTS:
        delivery_tw = "flexible"
    ps = pe = ds = de = None  # type: ignore[assignment]
    pickup_d_val = None
    delivery_d_val = None
    if pd_raw and dd_raw:
        try:
            pickup_d_val = date_cls.fromisoformat(pd_raw)
            delivery_d_val = date_cls.fromisoformat(dd_raw)
            ps, pe, ds, de = schedule_to_utc_windows(
                pickup_d_val, pickup_tw, delivery_d_val, delivery_tw
            )
        except ValueError:
            pickup_d_val = None
            delivery_d_val = None
    if ps is None:
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
    load.pickup_date = pickup_d_val
    load.pickup_time_window = pickup_tw if pickup_d_val else None
    load.delivery_date = delivery_d_val
    load.delivery_time_window = delivery_tw if delivery_d_val else None
    _wt = form.get("weight_tonnes")
    if _wt is not None:
        if str(_wt).strip():
            try:
                load.weight_kg = float(str(_wt).strip()) * 1000.0
            except ValueError:
                pass
        else:
            load.weight_kg = None
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
    from datetime import datetime, timezone

    from app.services.cancellation_emails import notify_hauliers_loader_cancelled
    from app.services.cancellation_policy import (
        hours_until_pickup,
        loader_matched_cancellation_tier,
        open_load_cancel_blocked,
        pickup_reference_time,
    )

    result = _loader_or_redirect(request, db)
    if isinstance(result, RedirectResponse):
        return result
    _user, loader = result
    load = _loader_get_load_owned(db, load_id, loader.id)
    if not load:
        return RedirectResponse(url="/?section=loads&load_error=not_found", status_code=303)
    if load.status == models.LoadStatusEnum.CANCELLED.value:
        return RedirectResponse(url="/?section=loads&load_error=already_cancelled", status_code=303)

    now = datetime.now(timezone.utc)
    job = _primary_job_for_load(db, load_id)
    h = hours_until_pickup(load, job, now)

    refund_warning = False
    vehicle_id_to_refresh: Optional[int] = None
    tier_key = "unmatched"

    if not job:
        if open_load_cancel_blocked(h):
            pt = pickup_reference_time(load, None)
            ps = pt.strftime("%d+%b+%Y+%H%3A%M") if pt else ""
            return RedirectResponse(
                url=f"/?section=loads&cancel_blocked=1&pickup_time={ps}",
                status_code=303,
            )
        load.status = models.LoadStatusEnum.CANCELLED.value
        load.cancelled_at = now
        load.cancelled_by_user_id = _user.id
        load.cancellation_fee_gbp = None
        load.cancellation_reason = "loader_cancel:unmatched"
        db.add(load)
        for li in db.query(models.LoadInterest).filter(models.LoadInterest.load_id == load.id).all():
            if (li.status or "") not in ("declined",):
                li.status = "declined"
                db.add(li)
    else:
        if job.completed_at:
            return RedirectResponse(url="/?section=loads&load_error=cannot_cancel_completed", status_code=303)
        if job.collected_at:
            return RedirectResponse(url="/?section=loads&load_error=cannot_cancel_collected", status_code=303)
        payment_chk = (
            db.query(models.Payment)
            .filter(models.Payment.backhaul_job_id == job.id)
            .order_by(models.Payment.created_at.asc())
            .first()
        )
        if payment_chk and (payment_chk.status or "").strip().lower() == models.PaymentStatusEnum.PAID_OUT.value:
            return RedirectResponse(url="/?section=loads&load_error=cannot_cancel_paid_out", status_code=303)

        blocked, _fee, tier_key = loader_matched_cancellation_tier(h)
        if blocked:
            pt = pickup_reference_time(load, job)
            ps = pt.strftime("%d+%b+%Y+%H%3A%M") if pt else ""
            return RedirectResponse(
                url=f"/?section=loads&cancel_blocked=1&pickup_time={ps}",
                status_code=303,
            )

        payment = (
            db.query(models.Payment)
            .filter(models.Payment.backhaul_job_id == job.id)
            .order_by(models.Payment.created_at.asc())
            .first()
        )
        if payment and (payment.status or "").strip().lower() == models.PaymentStatusEnum.CAPTURED.value:
            from app.services.stripe_loader_charge import try_refund_loader_charge

            ok_ref, _err = try_refund_loader_charge(payment, db, refund_amount_gbp=None)
            if not ok_ref:
                refund_warning = True
            db.add(payment)

        try:
            notify_hauliers_loader_cancelled(db, job, load, 0.0, tier_key, commit_in_app=False)
        except Exception:
            logger.exception("notify haulier load cancelled")

        vehicle_id_to_refresh = job.vehicle_id
        db.query(models.POD).filter(models.POD.backhaul_job_id == job.id).delete()
        db.query(models.Payment).filter(models.Payment.backhaul_job_id == job.id).delete()
        _interest_accepted_to_suggested(db, load.id, job.vehicle_id)
        db.delete(job)
        load.status = models.LoadStatusEnum.OPEN.value
        load.reopened_at = now
        load.cancelled_at = None
        load.cancellation_fee_gbp = None
        load.cancellation_reason = "loader_cancel_reopen"
        load.load_priority = "normal"
        db.add(load)

    loader.cancellation_count = int(loader.cancellation_count or 0) + 1
    loader.last_cancellation_at = now
    db.add(loader)

    db.commit()

    if vehicle_id_to_refresh is not None:
        try:
            from app.services import vehicle_availability as vehicle_availability_svc

            vehicle_availability_svc.refresh_vehicle_availability(db, vehicle_id_to_refresh)
            db.commit()
        except Exception:
            logger.exception("refresh_vehicle_availability after cancel")

    q = "section=loads&load_cancelled=1"
    if refund_warning:
        q += "&refund_warning=1"
    return RedirectResponse(url=f"/?{q}", status_code=303)


@router.post("/loader/jobs/{job_id}/report-no-show", response_class=RedirectResponse)
async def loader_report_no_show(
    job_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Loader reports haulier no-show / late / issue (confirmed jobs only)."""
    from datetime import datetime, timezone

    from app.services.cancellation_emails import notify_no_show_report
    from app.services.stripe_loader_charge import try_refund_loader_charge

    result = _loader_or_redirect(request, db)
    if isinstance(result, RedirectResponse):
        return result
    user, loader = result
    job = db.get(models.BackhaulJob, job_id)
    if not job:
        return RedirectResponse(url="/?section=matches&no_show_error=not_found", status_code=303)
    load = db.get(models.Load, job.load_id)
    if not load or load.loader_id != loader.id:
        return RedirectResponse(url="/?section=matches&no_show_error=not_found", status_code=303)
    if job.completed_at or job.collected_at:
        return RedirectResponse(url="/?section=matches&no_show_error=too_late", status_code=303)

    form = await request.form()
    reason = (form.get("reason") or "").strip().lower()
    vehicle = db.get(models.Vehicle, job.vehicle_id)
    haulier = db.get(models.Haulier, vehicle.haulier_id) if vehicle else None
    if not haulier:
        return RedirectResponse(url="/?section=matches&no_show_error=not_found", status_code=303)

    now = datetime.now(timezone.utc)

    if reason == "no_contact":
        job.no_show_reported_at = now
        job.no_show_reported_by_user_id = user.id
        db.add(job)

        payment = (
            db.query(models.Payment)
            .filter(models.Payment.backhaul_job_id == job.id)
            .order_by(models.Payment.created_at.asc())
            .first()
        )
        if payment and (payment.status or "").strip().lower() == models.PaymentStatusEnum.CAPTURED.value:
            try_refund_loader_charge(payment, db, refund_amount_gbp=None)
            db.add(payment)

        haulier.no_show_count = int(haulier.no_show_count or 0) + 1
        db.add(haulier)

        load.status = models.LoadStatusEnum.OPEN.value
        load.load_priority = "emergency"
        load.reopened_at = now
        load.cancellation_reason = "loader_no_show_report"
        db.add(load)

        vid = job.vehicle_id
        try:
            notify_no_show_report(db, job, load, haulier, loader, commit_in_app=False)
        except Exception:
            logger.exception("notify_no_show_report")

        db.query(models.POD).filter(models.POD.backhaul_job_id == job.id).delete()
        db.query(models.Payment).filter(models.Payment.backhaul_job_id == job.id).delete()
        db.delete(job)
        db.commit()

        try:
            from app.services import vehicle_availability as vehicle_availability_svc

            vehicle_availability_svc.refresh_vehicle_availability(db, vid)
            db.commit()
        except Exception:
            logger.exception("refresh_vehicle_availability after no-show")

        return RedirectResponse(url="/?section=matches&no_show_reported=1", status_code=303)

    if reason == "running_late":
        job.late_notification_at = now
        db.add(job)
        db.commit()
        return RedirectResponse(url="/?section=matches&late_noted=1", status_code=303)

    if reason == "vehicle_issue":
        job.issue_reported_at = now
        job.issue_type = "vehicle"
        db.add(job)
        db.commit()
        return RedirectResponse(url="/?section=matches&issue_noted=1", status_code=303)

    return RedirectResponse(url="/?section=matches&no_show_error=invalid", status_code=303)


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
    from urllib.parse import quote_plus

    from app.services.insurance_status import vehicle_may_accept_loads

    veh = db.get(models.Vehicle, interest.vehicle_id)
    if not veh or not vehicle_may_accept_loads(veh):
        return RedirectResponse(
            url="/?section=matches&msg=" + quote_plus("insurance_not_verified"),
            status_code=303,
        )
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
    from app.services.referral_program import haulier_referral_fee_multiplier, loader_referral_fee_multiplier

    vehicle = db.get(models.Vehicle, interest.vehicle_id)
    today = date.today()
    h_mult = haulier_referral_fee_multiplier(db, vehicle.haulier_id if vehicle else None, today)
    l_mult = loader_referral_fee_multiplier(db, load.loader_id, today)

    splits = compute_job_payment_splits(
        amount_gbp,
        settings,
        haulier_fee_multiplier=h_mult,
        loader_flat_fee_multiplier=l_mult,
    )

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
    from app.services.qr_verification import ensure_qr_for_load

    ensure_qr_for_load(db, load)
    db.commit()
    from app.services import vehicle_availability as vehicle_availability_svc

    vehicle_availability_svc.refresh_vehicle_availability(db, job.vehicle_id)
    db.commit()
    return RedirectResponse(url="/?section=matches&job_created=1", status_code=303)


@router.get("/loader/loads/{load_id}/qr.png")
def loader_load_qr_png(
    load_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """PNG QR for delivery verification (loader must own the load)."""
    redir = require_loader(request, db)
    if redir is not None:
        return redir
    user = get_current_user_optional(request, db)
    if not user or not user.loader_id:
        return RedirectResponse(url="/login", status_code=302)
    load = _loader_get_load_owned(db, load_id, user.loader_id)
    if not load:
        return RedirectResponse(url="/?section=loads", status_code=302)
    from app.services.qr_verification import ensure_qr_for_load, qr_png_bytes

    ensure_qr_for_load(db, load)
    try:
        db.commit()
    except Exception:
        db.rollback()
        return RedirectResponse(url="/?section=loads", status_code=302)
    return Response(content=qr_png_bytes(load.qr_code), media_type="image/png")


@router.post("/loader/jobs/{job_id}/confirm-delivery", response_class=RedirectResponse)
def loader_confirm_pending_delivery(
    job_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Confirm proof of delivery after manual / fallback verification."""
    redir = require_loader(request, db)
    if redir is not None:
        return redir
    user = get_current_user_optional(request, db)
    if not user or not user.loader_id:
        return RedirectResponse(url="/login", status_code=302)
    job = db.get(models.BackhaulJob, job_id)
    if not job or job.haulier_cancelled_at:
        return RedirectResponse(url="/?section=loads&msg=job_not_found", status_code=303)
    load = db.get(models.Load, job.load_id)
    if not load or load.loader_id != user.loader_id:
        return RedirectResponse(url="/?section=loads&msg=not_your_job", status_code=303)
    if job.completed_at or job.verification_status != "awaiting_loader":
        return RedirectResponse(url="/?section=loads&msg=nothing_to_confirm", status_code=303)
    pod = (
        db.query(models.POD)
        .filter(models.POD.backhaul_job_id == job.id)
        .filter(models.POD.status == models.PODStatusEnum.PENDING.value)
        .order_by(models.POD.created_at.desc())
        .first()
    )
    if not pod:
        return RedirectResponse(url="/?section=loads&msg=no_pod", status_code=303)
    from app.services.job_completion import confirm_pending_pod_and_release

    err = confirm_pending_pod_and_release(db, job, pod, auto_confirmed=False)
    if err:
        db.rollback()
        from urllib.parse import quote_plus

        return RedirectResponse(
            url="/?section=loads&msg=" + quote_plus(err or "confirm_failed")[:200],
            status_code=303,
        )
    try:
        db.commit()
    except Exception:
        db.rollback()
        return RedirectResponse(url="/?section=loads&msg=save_failed", status_code=303)
    return RedirectResponse(url="/?section=loads&delivery_confirmed=1", status_code=303)
