"""
Loader-facing dashboard: my loads, planned loads, who's interested.
Only for users with role=loader; data filtered by loader_id.
"""
from typing import Tuple, Union

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app import models
from app.auth import get_current_user_optional, require_loader
from app.database import get_db

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


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
    return RedirectResponse(url="/", status_code=302)


@router.post("/loader/profile", response_class=RedirectResponse)
async def loader_update_profile(
    request: Request,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Save loader company name, contact, and Stripe Customer ID for card charges at collection."""
    result = _loader_or_redirect(request, db)
    if isinstance(result, RedirectResponse):
        return result
    _user, loader = result
    form = await request.form()
    loader.name = (form.get("name") or loader.name or "").strip()
    loader.contact_email = (form.get("contact_email") or loader.contact_email or "").strip()
    loader.contact_phone = (form.get("contact_phone") or "").strip() or None
    loader.contact_name = (form.get("contact_name") or "").strip() or None
    loader.stripe_customer_id = (form.get("stripe_customer_id") or "").strip() or None
    db.commit()
    return RedirectResponse(url="/?section=company&profile_saved=1", status_code=303)


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
    return RedirectResponse(url="/loader?load_added=1", status_code=303)


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
        return RedirectResponse(url="/loader?delete_error=Load+not+found", status_code=303)
    if db.query(models.BackhaulJob).filter(models.BackhaulJob.load_id == load_id).first():
        return RedirectResponse(url="/loader?delete_error=Load+has+jobs", status_code=303)
    db.query(models.LoadInterest).filter(models.LoadInterest.load_id == load_id).delete()
    db.delete(load)
    db.commit()
    return RedirectResponse(url="/loader?deleted=load", status_code=303)


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
    return RedirectResponse(url="/loader?planned_added=1", status_code=303)


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
        return RedirectResponse(url="/loader?delete_error=Planned+load+not+found", status_code=303)
    db.query(models.LoadInterest).filter(models.LoadInterest.planned_load_id == planned_id).delete()
    db.delete(pl)
    db.commit()
    return RedirectResponse(url="/loader", status_code=303)


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
        return RedirectResponse(url="/loader", status_code=303)
    try:
        load_interest_id = int(load_interest_id)
    except (TypeError, ValueError):
        return RedirectResponse(url="/loader", status_code=303)
    interest = db.get(models.LoadInterest, load_interest_id)
    if not interest or interest.status != "expressed":
        return RedirectResponse(url="/loader", status_code=303)
    load = None
    if interest.load_id:
        load = db.get(models.Load, interest.load_id)
        if not load or load.loader_id != loader.id:
            return RedirectResponse(url="/loader?delete_error=Load+not+yours", status_code=303)
        if load.status == models.LoadStatusEnum.MATCHED.value:
            interest.status = "accepted"
            db.commit()
            return RedirectResponse(url="/loader?already_matched=1", status_code=303)
    else:
        pl = db.get(models.PlannedLoad, interest.planned_load_id) if interest.planned_load_id else None
        if not pl or pl.loader_id != loader.id:
            return RedirectResponse(url="/loader?delete_error=Planned+load+not+yours", status_code=303)
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

    job = models.BackhaulJob(
        vehicle_id=interest.vehicle_id,
        load_id=load.id,
        matched_at=datetime.now(timezone.utc),
        accepted_at=datetime.now(timezone.utc),
    )
    db.add(job)
    db.flush()

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
    return RedirectResponse(url="/loader?job_created=1", status_code=303)
