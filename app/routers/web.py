import logging
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Optional, Union

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Form, Query, Request, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import delete as sa_delete, update as sa_update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app import models
from app.auth import (
    get_current_admin,
    get_current_driver_optional,
    get_current_user,
    get_current_user_optional,
    hash_password,
    require_loader,
)
from app.config import get_settings
from app.database import get_db
from app.services.matching import find_matching_loads
from app.services import ratings as ratings_svc
from app.services import load_pricing as load_pricing_svc
from app.services import vehicle_availability as vehicle_availability_svc
from app.services.payment_fees import compute_loader_platform_fee_gbp
from app.services.email_sender import schedule_registration_emails
logger = logging.getLogger(__name__)

from app.services.insurance_status import (
    apply_insurance_status_to_vehicles,
    calculate_insurance_status,
    finalize_vehicle_insurance_upload,
)


router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _job_status_parts(job):
    """Jinja filter: {headline, detail} for Backhaul Jobs status column."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)

    def ago(dt):
        if not dt:
            return ""
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        secs = max(0, (now - dt).total_seconds())
        if secs < 60:
            return "just now"
        if secs < 3600:
            return f"{int(secs // 60)} min ago"
        if secs < 86400:
            return f"{int(secs // 3600)} hrs ago"
        return f"{int(secs // 86400)} days ago"

    if getattr(job, "haulier_cancelled_at", None):
        return {"headline": "Cancelled (haulier)", "detail": "Awaiting evidence" if getattr(job, "emergency_evidence_required", None) else ""}
    if getattr(job, "completed_at", None):
        return {"headline": "Completed", "detail": ago(job.completed_at)}
    if getattr(job, "reached_delivery_at", None):
        return {"headline": "At delivery", "detail": ago(job.reached_delivery_at)}
    if getattr(job, "departed_pickup_at", None):
        return {"headline": "En route", "detail": f"Departed {ago(job.departed_pickup_at)}"}
    if getattr(job, "collected_at", None):
        return {"headline": "Collected", "detail": ago(job.collected_at)}
    if getattr(job, "reached_pickup_at", None):
        return {"headline": "At pickup", "detail": ago(job.reached_pickup_at)}
    return {"headline": "Assigned", "detail": "Awaiting start"}


templates.env.filters["job_status_parts"] = _job_status_parts


def _whatsapp_href_load(load, base_url: str) -> str:
    if load is None:
        return "#"
    from app.whatsapp_share import build_whatsapp_send_url

    return build_whatsapp_send_url(load, base_url or "https://web-production-7ca42.up.railway.app")


templates.env.filters["whatsapp_href_load"] = _whatsapp_href_load

templates.env.globals["utcnow"] = lambda: datetime.now(timezone.utc)
templates.env.globals["timedelta"] = timedelta


def _haulier_or_admin_can_job(user: models.User, job: models.BackhaulJob, db: Session) -> bool:
    role = (getattr(user, "role", None) or "").strip().lower()
    if role == "admin":
        return True
    if role == "haulier" and user.haulier_id:
        vehicle = db.get(models.Vehicle, job.vehicle_id)
        return bool(vehicle and vehicle.haulier_id == user.haulier_id)
    return False


# Folder containing CSV templates (project root / static / templates)
TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent / "static" / "templates"


def _stripe_billing_configured() -> bool:
    """True when Stripe API key is set (loader card setup and dashboard links work)."""
    return bool((get_settings().stripe_secret_key or "").strip())


def _stripe_dashboard_urls() -> tuple[str, str, bool]:
    """
    (customers_root, connect_accounts_root, is_test_key).
    e.g. https://dashboard.stripe.com/test/customers — append /cus_xxx for a customer.
    """
    sk = (get_settings().stripe_secret_key or "").strip()
    is_test = not sk or sk.startswith("sk_test")
    if is_test:
        return (
            "https://dashboard.stripe.com/test/customers",
            "https://dashboard.stripe.com/test/connect/accounts",
            True,
        )
    return (
        "https://dashboard.stripe.com/customers",
        "https://dashboard.stripe.com/connect/accounts",
        False,
    )


@router.get("/terms", response_class=HTMLResponse)
def terms_page(request: Request) -> HTMLResponse:
    """Public Terms & Conditions page. No login required."""
    return templates.TemplateResponse(
        "terms.html",
        {"request": request, "last_updated": "31 March 2026"},
    )


@router.get("/privacy", response_class=HTMLResponse)
def privacy_page(request: Request) -> HTMLResponse:
    """Public Privacy Policy (UK GDPR). No login required."""
    return templates.TemplateResponse(
        "privacy.html",
        {"request": request, "last_updated": "31 March 2026"},
    )


@router.get("/api-docs", response_class=HTMLResponse)
def api_docs_page(request: Request) -> HTMLResponse:
    """Public API documentation. No login required."""
    return templates.TemplateResponse(
        "api_docs.html",
        {"request": request},
    )


@router.get("/pricing", response_class=HTMLResponse)
def pricing_page(request: Request) -> HTMLResponse:
    """Public pricing and fees explanation. No login required."""
    settings = get_settings()
    loader_min = float(settings.loader_flat_fee_gbp or 5)
    loader_pct = float(settings.loader_fee_percent_of_load or 2)
    haulier_pct = float(settings.platform_fee_percent or 8)

    ex1_load = 100.0
    ex1_pct_part = round(ex1_load * loader_pct / 100.0, 2)
    ex1_fee, _ = compute_loader_platform_fee_gbp(ex1_load, settings)
    ex1_total = round(ex1_load + ex1_fee, 2)

    ex2_load = 300.0
    ex2_pct_part = round(ex2_load * loader_pct / 100.0, 2)
    ex2_fee, _ = compute_loader_platform_fee_gbp(ex2_load, settings)
    ex2_total = round(ex2_load + ex2_fee, 2)

    example_service_fee_gbp = round(ex2_load * (haulier_pct / 100.0), 2)
    example_haulier_net_gbp = round(ex2_load - example_service_fee_gbp, 2)

    plat_loader_fee, _ = compute_loader_platform_fee_gbp(ex2_load, settings)
    plat_haulier_fee = example_service_fee_gbp
    plat_total_revenue = round(plat_loader_fee + plat_haulier_fee, 2)

    return templates.TemplateResponse(
        "pricing.html",
        {
            "request": request,
            "loader_min_fee_gbp": loader_min,
            "loader_fee_percent_of_load": loader_pct,
            "platform_fee_percent": haulier_pct,
            "ex1_load_gbp": ex1_load,
            "ex1_percent_of_load_gbp": ex1_pct_part,
            "ex1_booking_fee_gbp": ex1_fee,
            "ex1_total_gbp": ex1_total,
            "ex2_load_gbp": ex2_load,
            "ex2_percent_of_load_gbp": ex2_pct_part,
            "ex2_booking_fee_gbp": ex2_fee,
            "ex2_total_gbp": ex2_total,
            "example_haulier_load_gbp": ex2_load,
            "example_service_fee_gbp": example_service_fee_gbp,
            "example_haulier_net_gbp": example_haulier_net_gbp,
            "plat_example_load_gbp": ex2_load,
            "plat_loader_fee_gbp": plat_loader_fee,
            "plat_haulier_fee_gbp": plat_haulier_fee,
            "plat_total_revenue_gbp": plat_total_revenue,
        },
    )


@router.get("/confidentiality", response_class=HTMLResponse)
def confidentiality_page(request: Request) -> HTMLResponse:
    """Public Confidentiality & Non-Disclosure page. No login required."""
    return templates.TemplateResponse(
        "confidentiality.html",
        {"request": request, "last_updated": "March 2026"},
    )


def _json_safe_for_response(obj: object) -> object:
    """Replace NaN/inf floats so JSON responses are valid and clients do not break."""
    if isinstance(obj, dict):
        return {k: _json_safe_for_response(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe_for_response(v) for v in obj]
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    return obj


@router.get("/api/suggest-load-price")
def api_suggest_load_price(
    request: Request,
    pickup_postcode: str = Query(""),
    delivery_postcode: str = Query(""),
    vehicle_type: str = Query(""),
    trailer_type: str = Query(""),
    pickup_window_start: str = Query(""),
    budget_gbp: str = Query(""),
    db: Session = Depends(get_db),
):
    """Route miles + optional pricing guidance for Add Load; platform fee uses loader-entered budget when provided."""
    user = get_current_user_optional(request, db)
    if not user:
        return JSONResponse({"error": "Login required"}, status_code=401)
    data = load_pricing_svc.suggest_from_form_params(
        pickup_postcode.strip(),
        delivery_postcode.strip(),
        vehicle_type.strip() or None,
        trailer_type.strip() or None,
        pickup_window_start.strip() or None,
    )
    fee_basis: Optional[float] = None
    if budget_gbp and str(budget_gbp).strip():
        try:
            b = float(str(budget_gbp).strip())
            if b > 0:
                fee_basis = b
        except (TypeError, ValueError):
            pass
    if fee_basis is not None:
        from app.services.payment_fees import loader_platform_fee_payload

        extra = loader_platform_fee_payload(fee_basis, get_settings())
        if extra:
            data = {**data, **extra}
    return JSONResponse(_json_safe_for_response(data))


@router.get("/api/dvla-lookup")
def dvla_lookup(
    request: Request,
    reg: str = Query(..., min_length=2),
    db: Session = Depends(get_db),
):
    """DVLA lookup by registration; returns suggested vehicle_type and details for auto-filling add-vehicle form. Requires login."""
    from app.services.dvla import DvlaError, lookup_vehicle_by_registration, suggest_vehicle_form_from_dvla

    user = get_current_user_optional(request, db)
    if not user:
        return JSONResponse({"error": "Login required"}, status_code=401)
    reg = (reg or "").strip().upper().replace(" ", "")
    if len(reg) < 2:
        return JSONResponse({"error": "Registration required"}, status_code=400)

    settings = get_settings()
    if not settings.dvla_api_key:
        return JSONResponse(
            {
                "error": "DVLA is not configured on the server. Set DVLA_API_KEY in environment (e.g. Railway variables).",
                "configured": False,
            },
            status_code=503,
        )

    try:
        data = lookup_vehicle_by_registration(reg)
    except DvlaError as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    if not data:
        return JSONResponse(
            {"error": "No vehicle data returned from DVLA for this registration.", "vehicle_type": "rigid"},
            status_code=200,
        )
    return JSONResponse(suggest_vehicle_form_from_dvla(data))


@router.get("/download-templates/{name}", response_class=FileResponse)
def download_template(name: str) -> FileResponse:
    """Download a CSV template (hauliers, vehicles, or loads). Saves to your Downloads folder."""
    if name not in ("hauliers", "vehicles", "loads"):
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Unknown template")
    path = TEMPLATES_DIR / f"{name}.csv"
    if not path.exists():
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Template file not found")
    return FileResponse(path, filename=f"{name}.csv", media_type="text/csv")


def _vehicle_interest_line(vehicle: models.Vehicle) -> str:
    """Registration · type · trailer · equipment flags for loader-facing cards."""
    parts: list[str] = []
    if vehicle.registration:
        parts.append(str(vehicle.registration).upper())
    vt = (vehicle.vehicle_type or "").strip()
    if vt:
        parts.append(vt.capitalize())
    tt = (vehicle.trailer_type or "").strip()
    if tt:
        parts.append(tt.replace("_", " ").title())
    feats: list[str] = []
    if vehicle.has_tail_lift:
        feats.append("Tail lift")
    if vehicle.has_moffett:
        feats.append("Moffett")
    if vehicle.has_temp_control:
        feats.append("Temp")
    if vehicle.is_adr_certified:
        feats.append("ADR")
    if feats:
        parts.append(", ".join(feats))
    return " · ".join(p for p in parts if p)


def _distance_miles_pickup_to_base(
    pickup_postcode: str,
    vehicle: Optional[models.Vehicle],
    haulier: Optional[models.Haulier],
) -> Optional[float]:
    from app.config import get_settings
    from app.services.geocode import get_lat_lon
    from app.services.road_distance import single_road_miles_between_postcodes

    base = ""
    if vehicle and (vehicle.base_postcode or "").strip():
        base = vehicle.base_postcode.strip()
    elif haulier and (haulier.base_postcode or "").strip():
        base = haulier.base_postcode.strip()
    if not pickup_postcode or not base:
        return None
    if not get_lat_lon(pickup_postcode) or not get_lat_lon(base):
        return None
    settings = get_settings()
    return single_road_miles_between_postcodes(
        pickup_postcode,
        base,
        settings.openrouteservice_api_key,
        settings.mapbox_access_token,
        settings.google_maps_api_key,
    )


def _load_interests_display(load_interests_list, db: Session):
    """Build rows for Suggested Matches: route, loader rating (for hauliers), haulier/vehicle detail (for loaders)."""
    out = []
    loader_ids_for_ratings: list[int] = []
    haulier_ids = list(dict.fromkeys(i.haulier_id for i in load_interests_list if i.haulier_id))
    hr_map = ratings_svc.haulier_rating_lines_map(db, haulier_ids)
    for i in load_interests_list:
        shipper = collection = delivery = label = ""
        loader_id_val: Optional[int] = None
        load: Optional[models.Load] = None
        if i.load_id:
            load = db.get(models.Load, i.load_id)
            if load:
                shipper = load.shipper_name or ""
                collection = load.pickup_postcode or ""
                delivery = load.delivery_postcode or ""
                label = "Load %d" % load.id
                if load.loader_id:
                    loader_id_val = load.loader_id
                    loader_ids_for_ratings.append(loader_id_val)
        elif i.planned_load_id:
            pl = db.get(models.PlannedLoad, i.planned_load_id)
            if pl:
                shipper = pl.shipper_name or ""
                collection = pl.pickup_postcode or ""
                delivery = pl.delivery_postcode or ""
                label = "Planned %d" % pl.id
                if pl.loader_id:
                    loader_id_val = pl.loader_id
                    loader_ids_for_ratings.append(loader_id_val)

        h = db.get(models.Haulier, i.haulier_id) if i.haulier_id else None
        v = db.get(models.Vehicle, i.vehicle_id) if i.vehicle_id else None
        haulier_name = h.name if h else None
        haulier_rating_line = hr_map.get(i.haulier_id) if i.haulier_id else None
        vehicle_line = _vehicle_interest_line(v) if v else None
        location_line = None
        distance_miles = None
        distance_label = None
        haulier_contact = None
        if h:
            c_parts = []
            if h.contact_email:
                c_parts.append(h.contact_email)
            if h.contact_phone:
                c_parts.append(h.contact_phone)
            haulier_contact = " · ".join(c_parts) if c_parts else None
        if v and (v.base_postcode or "").strip():
            location_line = f"Vehicle base: {v.base_postcode.strip()}"
        elif h and (h.base_postcode or "").strip():
            location_line = f"Company base: {h.base_postcode.strip()}"
        if collection and (v or h):
            distance_miles = _distance_miles_pickup_to_base(collection, v, h)
            if distance_miles is not None:
                distance_label = f"{distance_miles} miles from collection"

        out.append({
            "interest": i,
            "load": load,
            "shipper": shipper,
            "collection": collection,
            "delivery": delivery,
            "label": label or ("Load %s" % (i.load_id or "") if i.load_id else "Planned %s" % (i.planned_load_id or "")),
            "_loader_id": loader_id_val,
            "haulier_name": haulier_name,
            "haulier_rating_line": haulier_rating_line,
            "vehicle_line": vehicle_line,
            "location_line": location_line,
            "distance_miles": distance_miles,
            "distance_label": distance_label,
            "haulier_contact": haulier_contact,
        })
    lr_map = ratings_svc.loader_rating_lines_map(db, loader_ids_for_ratings)
    for row in out:
        lid = row.pop("_loader_id", None)
        row["loader_rating_line"] = lr_map.get(lid) if lid is not None else None
    return out


def _require_user_or_driver_or_login(request: Request, db: Session) -> Optional[RedirectResponse]:
    """Office user or driver session may open the dashboard; guests go to /login."""
    if get_current_user_optional(request, db) is not None:
        return None
    if get_current_driver_optional(request, db) is not None:
        return None
    return RedirectResponse(url="/login", status_code=302)


def _driver_portal_user(driver: models.Driver) -> SimpleNamespace:
    """Template-facing user object for driver-only sessions (Find Backhaul / Matches)."""
    return SimpleNamespace(
        id=driver.id,
        email=driver.email,
        role="driver",
        haulier_id=driver.haulier_id,
        loader_id=None,
    )


def _vehicle_availability_map(vehicles: list) -> dict:
    """Per-vehicle labels for Find Backhaul + Vehicles tab (UK local date)."""
    from datetime import date

    if not vehicles:
        return {}
    t = date.today()
    return {v.id: vehicle_availability_svc.availability_ui(v, t) for v in vehicles}


def _build_onboarding_checklist(
    current_user,
    haulier_profile,
    loader_profile,
    vehicles: list,
    loads: list,
) -> Optional[dict]:
    """Return onboarding progress for new hauliers/loaders."""
    if not current_user:
        return None
    role = (getattr(current_user, "role", None) or "").strip().lower()
    if role not in {"haulier", "loader"}:
        return None

    if role == "haulier":
        company_done = bool(haulier_profile and (haulier_profile.name or "").strip() and (haulier_profile.contact_email or "").strip())
        vehicle_done = bool(vehicles)
        search_done = bool(getattr(current_user, "haulier_id", None) and any(getattr(l, "status", "") == models.LoadStatusEnum.OPEN.value for l in loads))
        steps = [
            {"label": "Complete company profile", "done": company_done, "href": "/?section=company"},
            {"label": "Add your first vehicle", "done": vehicle_done, "href": "/?section=vehicles"},
            {"label": "Find your first backhaul", "done": search_done, "href": "/?section=find"},
        ]
    else:
        company_done = bool(loader_profile and (loader_profile.name or "").strip() and (loader_profile.contact_email or "").strip())
        posted_done = bool(loads)
        payments_done = bool(loader_profile and (loader_profile.stripe_customer_id or "").strip())
        steps = [
            {"label": "Complete company profile", "done": company_done, "href": "/?section=company"},
            {"label": "Post your first load", "done": posted_done, "href": "/?section=loads"},
            {"label": "Add payment setup", "done": payments_done, "href": "/?section=company"},
        ]

    completed = sum(1 for s in steps if s["done"])
    total = len(steps)
    progress_percent = int(round((completed / total) * 100)) if total else 0
    return {
        "role": role,
        "steps": steps,
        "completed": completed,
        "total": total,
        "progress_percent": progress_percent,
        "show": completed < total,
        "help_href": "mailto:support@platoonix.co.uk",
    }


def _haulier_scoped_lists(
    db: Session, haulier: models.Haulier, driver_actor: Optional[models.Driver] = None
) -> dict:
    """Haulier dashboard lists; optional driver limits vehicles (and thus jobs) to one lorry."""
    vq = db.query(models.Vehicle).filter(models.Vehicle.haulier_id == haulier.id).order_by(models.Vehicle.registration)
    if driver_actor is not None and driver_actor.vehicle_id is not None:
        vq = vq.filter(models.Vehicle.id == driver_actor.vehicle_id)
    vehicles = vq.all()
    vehicle_ids = [v.id for v in vehicles]
    jobs = (
        db.query(models.BackhaulJob)
        .filter(models.BackhaulJob.vehicle_id.in_(vehicle_ids))
        .filter(models.BackhaulJob.haulier_cancelled_at.is_(None))
        .order_by(models.BackhaulJob.matched_at.desc())
        .all()
        if vehicle_ids
        else []
    )
    payments = db.query(models.Payment).filter(models.Payment.backhaul_job_id.in_([j.id for j in jobs])).all() if jobs else []
    loads = db.query(models.Load).order_by(models.Load.created_at.desc()).all()
    load_interests = db.query(models.LoadInterest).filter(models.LoadInterest.haulier_id == haulier.id).all()
    users = db.query(models.User).filter(models.User.haulier_id == haulier.id).order_by(models.User.email).all()
    drivers = db.query(models.Driver).filter(models.Driver.haulier_id == haulier.id).order_by(models.Driver.name).all()
    return {
        "vehicles": vehicles,
        "jobs": jobs,
        "payments": payments,
        "loads": loads,
        "load_interests": load_interests,
        "users": users,
        "drivers": drivers,
        "hauliers": [haulier],
        "planned_loads": [],
    }


@router.get("/", response_class=HTMLResponse)
def home(
    request: Request,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if get_current_user_optional(request, db) is None and get_current_driver_optional(request, db) is None:
        return templates.TemplateResponse("index.html", {"request": request})
    current_user = get_current_user_optional(request, db)
    driver_actor: Optional[models.Driver] = None
    if current_user is None:
        driver_actor = get_current_driver_optional(request, db)

    # Role-based filtering
    if driver_actor is not None:
        haulier = db.get(models.Haulier, driver_actor.haulier_id)
        if not haulier:
            return RedirectResponse(url="/driver-login", status_code=302)
        d = _haulier_scoped_lists(db, haulier, driver_actor)
        vehicles = d["vehicles"]
        jobs = d["jobs"]
        payments = d["payments"]
        loads = d["loads"]
        load_interests = d["load_interests"]
        users = d["users"]
        drivers = d["drivers"]
        hauliers = d["hauliers"]
        planned_loads = d["planned_loads"]
        current_user = _driver_portal_user(driver_actor)
    elif current_user and current_user.loader_id:
        # LOADER VIEW - only their loads
        loader = db.get(models.Loader, current_user.loader_id)
        loads = db.query(models.Load).filter(models.Load.loader_id == loader.id).order_by(models.Load.created_at.desc()).all()
        planned_loads = db.query(models.PlannedLoad).filter(models.PlannedLoad.loader_id == loader.id).order_by(models.PlannedLoad.created_at.desc()).all()
        load_ids = [l.id for l in loads]
        planned_ids = [p.id for p in planned_loads]
        
        # Only interests on their loads
        load_interests = []
        if load_ids:
            load_interests.extend(db.query(models.LoadInterest).filter(models.LoadInterest.load_id.in_(load_ids)).all())
        if planned_ids:
            load_interests.extend(db.query(models.LoadInterest).filter(models.LoadInterest.planned_load_id.in_(planned_ids)).all())
        
        # Only jobs for their loads (exclude soft-cancelled haulier rows kept for evidence)
        jobs = (
            db.query(models.BackhaulJob)
            .filter(
                models.BackhaulJob.load_id.in_(load_ids),
                models.BackhaulJob.haulier_cancelled_at.is_(None),
            )
            .order_by(models.BackhaulJob.matched_at.desc())
            .all()
            if load_ids
            else []
        )
        payments = db.query(models.Payment).filter(models.Payment.backhaul_job_id.in_([j.id for j in jobs])).all() if jobs else []
        
        # No vehicles or hauliers for loaders
        hauliers = []
        vehicles = []
        users = db.query(models.User).filter(models.User.loader_id == loader.id).order_by(models.User.email).all()
        drivers = []
    elif current_user and current_user.haulier_id:
        # HAULIER VIEW - only their vehicles and jobs
        haulier = db.get(models.Haulier, current_user.haulier_id)
        vehicles = db.query(models.Vehicle).filter(models.Vehicle.haulier_id == haulier.id).order_by(models.Vehicle.registration).all()

        vehicle_ids = [v.id for v in vehicles]
        jobs = (
            db.query(models.BackhaulJob)
            .filter(
                models.BackhaulJob.vehicle_id.in_(vehicle_ids),
                models.BackhaulJob.haulier_cancelled_at.is_(None),
            )
            .order_by(models.BackhaulJob.matched_at.desc())
            .all()
            if vehicle_ids
            else []
        )
        payments = db.query(models.Payment).filter(models.Payment.backhaul_job_id.in_([j.id for j in jobs])).all() if jobs else []
        
        # Show all loads (for searching)
        loads = db.query(models.Load).order_by(models.Load.created_at.desc()).all()
        
        # Show interests they've expressed
        load_interests = db.query(models.LoadInterest).filter(models.LoadInterest.haulier_id == haulier.id).all()
        
        # No loader-specific data
        hauliers = [haulier]  # Just their own company
        planned_loads = []
        users = db.query(models.User).filter(models.User.haulier_id == haulier.id).order_by(models.User.email).all()
        drivers = db.query(models.Driver).filter(models.Driver.haulier_id == haulier.id).order_by(models.Driver.name).all()
    elif current_user and (getattr(current_user, "role", None) or "").strip().lower() == "haulier":
        # Haulier login but no company linked (company deleted, or account not linked yet).
        # Do not use the admin branch here — it loads all data while the UI hides admin-only forms.
        vehicles = []
        loads = db.query(models.Load).order_by(models.Load.created_at.desc()).all()
        load_interests = []
        jobs = []
        payments = []
        planned_loads = []
        hauliers = []
        users = []
        drivers = []
    elif current_user and (getattr(current_user, "role", None) or "").strip().lower() == "admin":
        # ADMIN VIEW - see everything
        hauliers = db.query(models.Haulier).order_by(models.Haulier.created_at.desc()).all()
        vehicles = db.query(models.Vehicle).order_by(models.Vehicle.created_at.desc()).all()
        loads = db.query(models.Load).order_by(models.Load.created_at.desc()).all()
        jobs = (
            db.query(models.BackhaulJob)
            .filter(models.BackhaulJob.haulier_cancelled_at.is_(None))
            .order_by(models.BackhaulJob.matched_at.desc())
            .all()
        )
        payments = db.query(models.Payment).order_by(models.Payment.created_at.desc()).all()
        load_interests = db.query(models.LoadInterest).order_by(models.LoadInterest.created_at.desc()).all()
        
        users = db.query(models.User).order_by(models.User.email).all()
        drivers = db.query(models.Driver).order_by(models.Driver.name).all()
    else:
        # Fallback (e.g. loader without loader_id): minimal lists
        vehicles = []
        loads = db.query(models.Load).order_by(models.Load.created_at.desc()).all()
        load_interests = []
        jobs = []
        payments = []
        planned_loads = []
        hauliers = []
        users = []
        drivers = []

    load_interests_display = _load_interests_display(load_interests, db)

    uploaded = request.query_params.get("uploaded")
    errors_count = request.query_params.get("errors")
    upload_type = request.query_params.get("upload_type")
    delete_error = request.query_params.get("delete_error")
    deleted = request.query_params.get("deleted")
    driver_error = request.query_params.get("driver_error")
    driver_ok = request.query_params.get("driver_ok")
    team_error = request.query_params.get("team_error")
    team_ok = request.query_params.get("team_ok")
    create_login_error = request.query_params.get("create_login_error")
    create_login_ok = request.query_params.get("create_login_ok")
    try:
        open_loads_count = db.query(models.Load).filter(models.Load.status == models.LoadStatusEnum.OPEN.value).count()
    except Exception:
        open_loads_count = 0
    try:
        total_payout = float(sum((p.net_payout_gbp or 0) for p in payments))
    except Exception:
        total_payout = 0.0

    haulier_profile = None
    loader_profile = None
    if current_user and getattr(current_user, "role", None) != "driver" and current_user.haulier_id:
        haulier_profile = db.get(models.Haulier, current_user.haulier_id)
    elif current_user and current_user.loader_id:
        loader_profile = db.get(models.Loader, current_user.loader_id)

    _driver_for_find = driver_actor if driver_actor is not None else None
    default_find_vid = ""
    if _driver_for_find and _driver_for_find.vehicle_id:
        default_find_vid = str(_driver_for_find.vehicle_id)

    _scust, _sconn, _stest = _stripe_dashboard_urls()
    _stripe_ok = _stripe_billing_configured()

    rating_ctx = ratings_svc.build_home_rating_context(db, current_user, loads, vehicles)
    rating_ok = request.query_params.get("rating_ok")
    rating_error = request.query_params.get("rating_error")
    apply_insurance_status_to_vehicles(vehicles)
    vehicle_availability = _vehicle_availability_map(vehicles)
    _sl = (request.query_params.get("load_id") or "").strip()
    try:
        shared_load_id = int(_sl) if _sl else None
    except ValueError:
        shared_load_id = None
    _pub_base = get_settings().public_app_base_url
    onboarding_checklist = _build_onboarding_checklist(current_user, haulier_profile, loader_profile, vehicles, loads)
    _cset_home = get_settings()
    cancellation_ui_settings = {
        "free_h": _cset_home.free_cancellation_hours,
        "warn_h": _cset_home.warning_cancellation_hours,
        "pen_h": _cset_home.penalty_cancellation_hours,
        "fee_warn": _cset_home.cancellation_fee_warning_gbp,
        "fee_pen": _cset_home.cancellation_fee_penalty_gbp,
    }
    emergency_evidence_jobs: list = []
    if current_user and getattr(current_user, "role", None) == "haulier" and getattr(current_user, "haulier_id", None):
        _evids = [v.id for v in vehicles] if vehicles else []
        if _evids:
            emergency_evidence_jobs = (
                db.query(models.BackhaulJob)
                .filter(models.BackhaulJob.vehicle_id.in_(_evids))
                .filter(models.BackhaulJob.haulier_cancelled_at.isnot(None))
                .filter(models.BackhaulJob.emergency_cancellation.is_(True))
                .filter(models.BackhaulJob.emergency_evidence_required.is_(True))
                .filter(models.BackhaulJob.emergency_evidence_submitted_at.is_(None))
                .order_by(models.BackhaulJob.haulier_cancelled_at.desc())
                .all()
            )
    job_by_load_id = {}
    for j in jobs:
        prev = job_by_load_id.get(j.load_id)
        if prev is None or j.id > prev.id:
            job_by_load_id[j.load_id] = j

    total_users = 0
    loader_user_count = 0
    haulier_user_count = 0
    driver_count = 0
    recent_registrations: list = []
    if current_user and (getattr(current_user, "role", None) or "").strip().lower() == "admin":
        seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)
        total_users = db.query(models.User).count()
        loader_user_count = db.query(models.User).filter(models.User.loader_id.isnot(None)).count()
        haulier_user_count = db.query(models.User).filter(models.User.haulier_id.isnot(None)).count()
        driver_count = db.query(models.Driver).count()
        recent_registrations = (
            db.query(models.User)
            .options(joinedload(models.User.loader), joinedload(models.User.haulier))
            .filter(models.User.created_at >= seven_days_ago)
            .order_by(models.User.created_at.desc())
            .limit(20)
            .all()
        )

    return templates.TemplateResponse(
        "home.html",
        {
            "request": request,
            "users": users,
            "drivers": drivers,
            "hauliers": hauliers,
            "haulier_profile": haulier_profile,
            "loader_profile": loader_profile,
            "stripe_dashboard_customers_root": _scust,
            "stripe_dashboard_connect_accounts_root": _sconn,
            "stripe_is_test_mode": _stest,
            "stripe_billing_configured": _stripe_ok,
            "vehicles": vehicles,
            "loads": loads,
            "jobs": jobs,
            "job_by_load_id": job_by_load_id,
            "payments": payments,
            "load_interests": load_interests,
            "load_interests_display": load_interests_display,
            "emergency_evidence_jobs": emergency_evidence_jobs,
            "cancellation_ui_settings": cancellation_ui_settings,
            "uploaded": int(uploaded) if uploaded and uploaded.isdigit() else None,
            "upload_errors": int(errors_count) if errors_count and errors_count.isdigit() else None,
            "upload_type": upload_type or "",
            "delete_error": delete_error,
            "deleted": deleted,
            "driver_error": driver_error,
            "driver_ok": driver_ok,
            "team_error": team_error,
            "team_ok": team_ok,
            "create_login_error": create_login_error,
            "create_login_ok": create_login_ok,
            "open_loads_count": open_loads_count,
            "total_payout": total_payout,
            "matching_results": None,
            "find_vehicle_id": default_find_vid,
            "find_origin_postcode": "",
            "find_destination_postcode": "",
            "postcode_lookup_failed": False,
            "match_diagnostic": None,
            "find_vehicle_busy": False,
            "vehicle_availability": vehicle_availability,
            "platform_fee_percent": get_settings().platform_fee_percent,
            "loader_flat_fee_gbp": get_settings().loader_flat_fee_gbp,
            "loader_fee_minimum_gbp": get_settings().loader_flat_fee_gbp,
            "loader_fee_percent_of_load": get_settings().loader_fee_percent_of_load,
            "pallet_volume_m3": get_settings().pallet_volume_m3,
            "current_user_email": (current_user.email if current_user else ""),
            "current_user": current_user,
            "rating_ok": rating_ok,
            "rating_error": rating_error,
            "public_app_base_url": _pub_base,
            "shared_load_id": shared_load_id,
            "driver_can_find_backhauls": False,
            "driver_pending_approvals": [],
            "haulier_pending_backhaul_approvals": [],
            "approval_confirmation": None,
            "find_backhaul_msg": None,
            "onboarding_checklist": onboarding_checklist,
            "total_users": total_users,
            "loader_user_count": loader_user_count,
            "haulier_user_count": haulier_user_count,
            "driver_count": driver_count,
            "recent_registrations": recent_registrations,
            **rating_ctx,
        },
    )


@router.get("/rate-job/{job_id}", response_class=HTMLResponse, response_model=None)
def rate_job_page(
    request: Request,
    job_id: int,
    db: Session = Depends(get_db),
) -> Union[HTMLResponse, RedirectResponse]:
    """5-star rating form after job completion (loader ↔ haulier)."""
    user = get_current_user_optional(request, db)
    if not user or user.role == "driver":
        return RedirectResponse(url="/login", status_code=302)
    job = db.get(models.BackhaulJob, job_id)
    if not job or not job.completed_at:
        return RedirectResponse(url="/?section=loads&rating_error=invalid_job", status_code=303)
    if ratings_svc.has_rated_job(db, job.id, user.id):
        return RedirectResponse(url="/?section=company&rating_error=already_rated", status_code=303)
    load = db.get(models.Load, job.load_id)
    vehicle = db.get(models.Vehicle, job.vehicle_id)
    if not load or not vehicle:
        return RedirectResponse(url="/?rating_error=invalid_job", status_code=303)

    direction: Optional[str] = None
    title = ""
    question = ""
    if user.loader_id and load.loader_id == user.loader_id:
        direction = "loader_to_haulier"
        title = "How was the delivery?"
        question = "Rate the haulier for this completed job."
    elif user.haulier_id and vehicle.haulier_id == user.haulier_id and load.loader_id:
        direction = "haulier_to_loader"
        title = "How was the load / shipper?"
        question = "Rate the loader for this completed job."
    else:
        return RedirectResponse(url="/?section=find&rating_error=not_eligible", status_code=303)

    haulier = db.get(models.Haulier, vehicle.haulier_id)
    loader = db.get(models.Loader, load.loader_id) if load.loader_id else None
    return templates.TemplateResponse(
        "rate_job.html",
        {
            "request": request,
            "job": job,
            "load": load,
            "job_display": job.display_number,
            "direction": direction,
            "title": title,
            "question": question,
            "counterparty": (haulier.name if direction == "loader_to_haulier" else (loader.name if loader else "Loader")),
        },
    )


@router.post("/rate-job/{job_id}", response_class=RedirectResponse)
async def rate_job_submit(
    request: Request,
    job_id: int,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    user = get_current_user_optional(request, db)
    if not user or user.role == "driver":
        return RedirectResponse(url="/login", status_code=302)
    job = db.get(models.BackhaulJob, job_id)
    if not job or not job.completed_at:
        return RedirectResponse(url="/?rating_error=invalid_job", status_code=303)
    if ratings_svc.has_rated_job(db, job.id, user.id):
        return RedirectResponse(url="/?section=company&rating_error=already_rated", status_code=303)
    load = db.get(models.Load, job.load_id)
    vehicle = db.get(models.Vehicle, job.vehicle_id)
    if not load or not vehicle:
        return RedirectResponse(url="/?rating_error=invalid_job", status_code=303)

    form = await request.form()
    try:
        stars = int((form.get("rating") or "0").strip())
    except ValueError:
        stars = 0
    comment = (form.get("comment") or "").strip() or None
    if stars < 1 or stars > 5:
        return RedirectResponse(url=f"/rate-job/{job_id}?error=stars", status_code=303)

    rated_h: Optional[int] = None
    rated_l: Optional[int] = None
    if user.loader_id and load.loader_id == user.loader_id:
        rated_h = vehicle.haulier_id
    elif user.haulier_id and vehicle.haulier_id == user.haulier_id and load.loader_id:
        rated_l = load.loader_id
    else:
        return RedirectResponse(url="/?rating_error=not_eligible", status_code=303)

    row = models.JobRating(
        job_id=job.id,
        rater_user_id=user.id,
        rated_haulier_id=rated_h,
        rated_loader_id=rated_l,
        rating=stars,
        comment=comment,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return RedirectResponse(url="/?section=company&rating_error=duplicate", status_code=303)
    return RedirectResponse(url="/?section=company&rating_ok=1", status_code=303)


@router.post("/loads", response_class=RedirectResponse)
async def create_load(
    request: Request,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Create a new load. Loaders create loads, admin can create for any loader."""
    from app.auth import get_current_user_optional
    current_user = get_current_user_optional(request, db)
    
    if not current_user:
        return RedirectResponse(url="/login", status_code=302)
    
    form = await request.form()
    shipper_name = (form.get("shipper_name") or "").strip()
    booking_name = (form.get("booking_name") or "").strip() or None
    booking_ref = (form.get("booking_ref") or "").strip() or None
    pickup_postcode = (form.get("pickup_postcode") or "").strip().upper()
    delivery_postcode = (form.get("delivery_postcode") or "").strip().upper()
    vehicle_type_required = (form.get("vehicle_type_required") or "").strip() or None
    trailer_type_required = (form.get("trailer_type_required") or "").strip() or None
    pallets = form.get("pallets")
    cubic_metres = form.get("cubic_metres")
    budget_gbp = form.get("budget_gbp")

    from datetime import datetime, timezone
    from app.services.upload_parser import parse_datetime_optional

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
        return RedirectResponse(url="/?section=loads&error=Missing+required+fields", status_code=303)
    
    # Determine loader_id
    loader_id = None
    if current_user.loader_id:
        loader_id = current_user.loader_id
    elif current_user.role == "admin":
        loader_id = None
    else:
        return RedirectResponse(url="/?section=loads&error=Not+authorized", status_code=303)
    
    # Build requirements JSON
    requirements = {}
    if vehicle_type_required:
        requirements["vehicle_type"] = vehicle_type_required
    if trailer_type_required:
        requirements["trailer_type"] = trailer_type_required
    
    pallets_val = None
    try:
        if pallets is not None and str(pallets).strip():
            pallets_val = float(pallets)
    except (TypeError, ValueError):
        pass

    load = models.Load(
        shipper_name=shipper_name,
        booking_ref=booking_ref,
        booking_name=booking_name,
        pickup_postcode=pickup_postcode,
        delivery_postcode=delivery_postcode,
        pickup_window_start=ps,
        pickup_window_end=pe,
        delivery_window_start=ds,
        delivery_window_end=de,
        pallets=pallets_val,
        volume_m3=float(cubic_metres) if cubic_metres else None,
        budget_gbp=float(budget_gbp) if budget_gbp else None,
        requirements=requirements,
        requires_tail_lift=_form_checkbox(form, "requires_tail_lift"),
        requires_forklift=_form_checkbox(form, "requires_forklift"),
        requires_temp_control=_form_checkbox(form, "requires_temp_control"),
        requires_adr=_form_checkbox(form, "requires_adr"),
        status=models.LoadStatusEnum.OPEN.value,
        loader_id=loader_id,
    )
    db.add(load)
    db.commit()
    
    return RedirectResponse(url="/?section=loads&load_added=1", status_code=303)


def _match_diagnostic(vehicle_id: int, origin_postcode: str, db: Session):
    """Explain why each open load did or didn't match (for 'no matches' debugging)."""
    from app.config import get_settings
    from app.services.geocode import get_lat_lon, normalize_postcode
    from app.services.matching import vehicle_satisfies_load_equipment_hard
    from app.services.road_distance import road_distances_from_origin_to_postcodes

    vehicle = db.get(models.Vehicle, vehicle_id)
    if not vehicle:
        return {"origin_ok": False, "origin_reason": "Vehicle not found", "loads": []}
    if not get_lat_lon(origin_postcode):
        return {"origin_ok": False, "origin_reason": "Postcode lookup failed", "loads": []}
    settings = get_settings()
    radius = settings.default_backhaul_radius_miles
    open_loads = (
        db.query(models.Load)
        .filter(models.Load.status == models.LoadStatusEnum.OPEN.value)
        .all()
    )
    pickup_pcs = [load.pickup_postcode for load in open_loads]
    dist_map, src = road_distances_from_origin_to_postcodes(
        origin_postcode,
        pickup_pcs,
        settings.openrouteservice_api_key,
        settings.mapbox_access_token,
        settings.google_maps_api_key,
    )
    rows = []
    for load in open_loads:
        if not get_lat_lon(load.pickup_postcode):
            rows.append({"load": load, "reason": "Pickup postcode lookup failed", "distance_miles": None})
            continue
        pc = normalize_postcode(load.pickup_postcode)
        dist = dist_map.get(pc)
        if src == "none" or dist is None:
            rows.append(
                {
                    "load": load,
                    "reason": "Road distance unavailable (set OPENROUTESERVICE_API_KEY, MAPBOX_ACCESS_TOKEN, or GOOGLE_MAPS_API_KEY)",
                    "distance_miles": None,
                }
            )
            continue
        dist = round(dist, 1)
        if dist > radius:
            rows.append({"load": load, "reason": f"{dist} mi (over {radius} mi limit)", "distance_miles": dist})
            continue
        if not vehicle_satisfies_load_equipment_hard(vehicle, load):
            rows.append(
                {
                    "load": load,
                    "reason": "Equipment (load requirement not met by vehicle)",
                    "distance_miles": dist,
                }
            )
            continue
        req = load.requirements or {}
        required_trailer = req.get("trailer_type") if isinstance(req, dict) else None
        if required_trailer not in (None, ""):
            if (vehicle.trailer_type or "").strip().lower() != str(required_trailer).strip().lower():
                rows.append({"load": load, "reason": f"Trailer type (need {required_trailer})", "distance_miles": dist})
                continue
        if vehicle.capacity_weight_kg and vehicle.capacity_weight_kg > 0 and (load.weight_kg or 0) > vehicle.capacity_weight_kg:
            rows.append({"load": load, "reason": "Load too heavy for vehicle", "distance_miles": dist})
            continue
        if vehicle.capacity_volume_m3 and vehicle.capacity_volume_m3 > 0 and (load.volume_m3 or 0) > vehicle.capacity_volume_m3:
            rows.append({"load": load, "reason": "Load too large for vehicle", "distance_miles": dist})
            continue
        rows.append({"load": load, "reason": None, "distance_miles": dist})  # would match
    return {"origin_ok": True, "origin_reason": None, "loads": rows}


@router.get("/find-backhaul", response_class=HTMLResponse)
def find_backhaul_page(
    request: Request,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Run smart matching and render home with matching_results (vehicle_id + origin_postcode + optional destination_postcode from query)."""
    from app.services.geocode import get_lat_lon
    from app.services.matching import find_matching_loads, find_matching_loads_along_route

    login_redir = _require_user_or_driver_or_login(request, db)
    if login_redir is not None:
        return login_redir
    current_user = get_current_user_optional(request, db)
    driver_actor: Optional[models.Driver] = None
    if current_user is None:
        driver_actor = get_current_driver_optional(request, db)

    vehicle_id_raw = request.query_params.get("vehicle_id", "").strip()
    if driver_actor and driver_actor.vehicle_id and not vehicle_id_raw:
        vehicle_id_raw = str(driver_actor.vehicle_id)
    raw_origin = (request.query_params.get("origin_postcode") or "").strip()
    raw_dest = (request.query_params.get("destination_postcode") or "").strip()
    origin_postcode = " ".join(raw_origin.split()).strip() if raw_origin else ""  # collapse spaces
    destination_postcode = " ".join(raw_dest.split()).strip() if raw_dest else ""  # collapse spaces

    matching_results = None
    postcode_lookup_failed = False
    match_diagnostic = None
    find_vehicle_busy = False
    if vehicle_id_raw and origin_postcode:
        try:
            vehicle_id = int(vehicle_id_raw)
            v = db.get(models.Vehicle, vehicle_id)
            if v is not None and vehicle_availability_svc.vehicle_has_active_job(db, vehicle_id):
                matching_results = []
                find_vehicle_busy = True
            elif driver_actor:
                if not v or v.haulier_id != driver_actor.haulier_id:
                    matching_results = []
                elif driver_actor.vehicle_id and driver_actor.vehicle_id != vehicle_id:
                    matching_results = []
                else:
                    if destination_postcode:
                        route_pairs = find_matching_loads_along_route(vehicle_id, origin_postcode, destination_postcode, db)
                        origin_pairs = find_matching_loads(vehicle_id, origin_postcode, db)
                        all_pairs = route_pairs + origin_pairs
                        seen_load_ids = set()
                        unique_pairs = []
                        for load, dist, is_perfect, reasons in all_pairs:
                            if load.id not in seen_load_ids:
                                seen_load_ids.add(load.id)
                                unique_pairs.append((load, dist, is_perfect, reasons))
                        pairs = unique_pairs
                    else:
                        pairs = find_matching_loads(vehicle_id, origin_postcode, db)
                    matching_results = [
                        {"load": load, "distance_miles": dist, "is_perfect_match": is_perfect, "mismatch_reasons": reasons}
                        for load, dist, is_perfect, reasons in pairs
                    ]
                    if not matching_results:
                        open_count = db.query(models.Load).filter(models.Load.status == models.LoadStatusEnum.OPEN.value).count()
                        if open_count > 0:
                            if get_lat_lon(origin_postcode) is None:
                                postcode_lookup_failed = True
                            match_diagnostic = _match_diagnostic(vehicle_id, origin_postcode, db)
            else:
                # Office login (loader / haulier / admin): same search as before
                if destination_postcode:
                    route_pairs = find_matching_loads_along_route(vehicle_id, origin_postcode, destination_postcode, db)
                    origin_pairs = find_matching_loads(vehicle_id, origin_postcode, db)
                    all_pairs = route_pairs + origin_pairs
                    seen_load_ids = set()
                    unique_pairs = []
                    for load, dist, is_perfect, reasons in all_pairs:
                        if load.id not in seen_load_ids:
                            seen_load_ids.add(load.id)
                            unique_pairs.append((load, dist, is_perfect, reasons))
                    pairs = unique_pairs
                else:
                    pairs = find_matching_loads(vehicle_id, origin_postcode, db)
                matching_results = [
                    {"load": load, "distance_miles": dist, "is_perfect_match": is_perfect, "mismatch_reasons": reasons}
                    for load, dist, is_perfect, reasons in pairs
                ]
                if not matching_results:
                    open_count = db.query(models.Load).filter(models.Load.status == models.LoadStatusEnum.OPEN.value).count()
                    if open_count > 0:
                        if get_lat_lon(origin_postcode) is None:
                            postcode_lookup_failed = True
                        match_diagnostic = _match_diagnostic(vehicle_id, origin_postcode, db)
        except ValueError:
            matching_results = []

    if matching_results:
        from app.services.payment_fees import loader_platform_fee_payload

        ratings_svc.enrich_matching_results_with_loader_ratings(db, matching_results)
        _settings = get_settings()
        for m in matching_results:
            m["market_rate"] = load_pricing_svc.suggest_for_open_load(m["load"])
            m["loader_platform_fee"] = loader_platform_fee_payload(m["load"].budget_gbp, _settings)

    if driver_actor is not None:
        haulier = db.get(models.Haulier, driver_actor.haulier_id)
        if not haulier:
            return RedirectResponse(url="/driver-login", status_code=302)
        d = _haulier_scoped_lists(db, haulier, driver_actor)
        vehicles = d["vehicles"]
        jobs = d["jobs"]
        payments = d["payments"]
        loads = d["loads"]
        load_interests = d["load_interests"]
        users = d["users"]
        drivers = d["drivers"]
        hauliers = d["hauliers"]
        planned_loads = d["planned_loads"]
        current_user = _driver_portal_user(driver_actor)
    elif current_user and current_user.loader_id:
        loader = db.get(models.Loader, current_user.loader_id)
        loads = db.query(models.Load).filter(models.Load.loader_id == loader.id).order_by(models.Load.created_at.desc()).all()
        planned_loads = db.query(models.PlannedLoad).filter(models.PlannedLoad.loader_id == loader.id).order_by(models.PlannedLoad.created_at.desc()).all()
        load_ids = [l.id for l in loads]
        planned_ids = [p.id for p in planned_loads]
        load_interests = []
        if load_ids:
            load_interests.extend(db.query(models.LoadInterest).filter(models.LoadInterest.load_id.in_(load_ids)).all())
        if planned_ids:
            load_interests.extend(db.query(models.LoadInterest).filter(models.LoadInterest.planned_load_id.in_(planned_ids)).all())
        jobs = (
            db.query(models.BackhaulJob)
            .filter(
                models.BackhaulJob.load_id.in_(load_ids),
                models.BackhaulJob.haulier_cancelled_at.is_(None),
            )
            .order_by(models.BackhaulJob.matched_at.desc())
            .all()
            if load_ids
            else []
        )
        payments = db.query(models.Payment).filter(models.Payment.backhaul_job_id.in_([j.id for j in jobs])).all() if jobs else []
        hauliers = []
        vehicles = []
        users = db.query(models.User).filter(models.User.loader_id == loader.id).order_by(models.User.email).all()
        drivers = []
    elif current_user and current_user.haulier_id:
        haulier = db.get(models.Haulier, current_user.haulier_id)
        vehicles = db.query(models.Vehicle).filter(models.Vehicle.haulier_id == haulier.id).order_by(models.Vehicle.registration).all()
        vehicle_ids = [v.id for v in vehicles]
        jobs = (
            db.query(models.BackhaulJob)
            .filter(
                models.BackhaulJob.vehicle_id.in_(vehicle_ids),
                models.BackhaulJob.haulier_cancelled_at.is_(None),
            )
            .order_by(models.BackhaulJob.matched_at.desc())
            .all()
            if vehicle_ids
            else []
        )
        payments = db.query(models.Payment).filter(models.Payment.backhaul_job_id.in_([j.id for j in jobs])).all() if jobs else []
        loads = db.query(models.Load).order_by(models.Load.created_at.desc()).all()
        load_interests = db.query(models.LoadInterest).filter(models.LoadInterest.haulier_id == haulier.id).all()
        hauliers = [haulier]
        planned_loads = []
        users = db.query(models.User).filter(models.User.haulier_id == haulier.id).order_by(models.User.email).all()
        drivers = db.query(models.Driver).filter(models.Driver.haulier_id == haulier.id).order_by(models.Driver.name).all()
    elif current_user and (getattr(current_user, "role", None) or "").strip().lower() == "haulier":
        vehicles = []
        loads = db.query(models.Load).order_by(models.Load.created_at.desc()).all()
        load_interests = []
        jobs = []
        payments = []
        planned_loads = []
        hauliers = []
        users = []
        drivers = []
    elif current_user and (getattr(current_user, "role", None) or "").strip().lower() == "admin":
        hauliers = db.query(models.Haulier).order_by(models.Haulier.created_at.desc()).all()
        vehicles = db.query(models.Vehicle).order_by(models.Vehicle.created_at.desc()).all()
        loads = db.query(models.Load).order_by(models.Load.created_at.desc()).all()
        jobs = (
            db.query(models.BackhaulJob)
            .filter(models.BackhaulJob.haulier_cancelled_at.is_(None))
            .order_by(models.BackhaulJob.matched_at.desc())
            .all()
        )
        payments = db.query(models.Payment).order_by(models.Payment.created_at.desc()).all()
        load_interests = db.query(models.LoadInterest).order_by(models.LoadInterest.created_at.desc()).all()
        planned_loads = []
        users = db.query(models.User).order_by(models.User.email).all()
        drivers = db.query(models.Driver).order_by(models.Driver.name).all()
    else:
        vehicles = []
        loads = db.query(models.Load).order_by(models.Load.created_at.desc()).all()
        load_interests = []
        jobs = []
        payments = []
        planned_loads = []
        hauliers = []
        users = []
        drivers = []

    load_interests_display = _load_interests_display(load_interests, db)
    try:
        open_loads_count = db.query(models.Load).filter(models.Load.status == models.LoadStatusEnum.OPEN.value).count()
    except Exception:
        open_loads_count = 0
    try:
        total_payout = float(sum((p.net_payout_gbp or 0) for p in payments))
    except Exception:
        total_payout = 0.0

    haulier_profile = None
    loader_profile = None
    if current_user and getattr(current_user, "role", None) != "driver" and current_user.haulier_id:
        haulier_profile = db.get(models.Haulier, current_user.haulier_id)
    elif current_user and current_user.loader_id:
        loader_profile = db.get(models.Loader, current_user.loader_id)

    _scust, _sconn, _stest = _stripe_dashboard_urls()
    _stripe_ok = _stripe_billing_configured()

    rating_ctx = ratings_svc.build_home_rating_context(db, current_user, loads, vehicles)
    rating_ok = request.query_params.get("rating_ok")
    rating_error = request.query_params.get("rating_error")
    apply_insurance_status_to_vehicles(vehicles)
    vehicle_availability = _vehicle_availability_map(vehicles)
    _sl = (request.query_params.get("load_id") or "").strip()
    try:
        shared_load_id = int(_sl) if _sl else None
    except ValueError:
        shared_load_id = None
    _pub_base = get_settings().public_app_base_url
    find_backhaul_msg = (request.query_params.get("msg") or "").strip() or None
    onboarding_checklist = _build_onboarding_checklist(current_user, haulier_profile, loader_profile, vehicles, loads)

    _cset = get_settings()
    cancellation_ui_settings = {
        "free_h": _cset.free_cancellation_hours,
        "warn_h": _cset.warning_cancellation_hours,
        "pen_h": _cset.penalty_cancellation_hours,
        "fee_warn": _cset.cancellation_fee_warning_gbp,
        "fee_pen": _cset.cancellation_fee_penalty_gbp,
    }
    emergency_evidence_jobs: list = []
    if current_user and getattr(current_user, "role", None) == "haulier" and getattr(current_user, "haulier_id", None):
        _evids = [v.id for v in vehicles] if vehicles else []
        if _evids:
            emergency_evidence_jobs = (
                db.query(models.BackhaulJob)
                .filter(models.BackhaulJob.vehicle_id.in_(_evids))
                .filter(models.BackhaulJob.haulier_cancelled_at.isnot(None))
                .filter(models.BackhaulJob.emergency_cancellation.is_(True))
                .filter(models.BackhaulJob.emergency_evidence_required.is_(True))
                .filter(models.BackhaulJob.emergency_evidence_submitted_at.is_(None))
                .order_by(models.BackhaulJob.haulier_cancelled_at.desc())
                .all()
            )

    job_by_load_id = {}
    for j in jobs:
        prev = job_by_load_id.get(j.load_id)
        if prev is None or j.id > prev.id:
            job_by_load_id[j.load_id] = j

    return templates.TemplateResponse(
        "home.html",
        {
            "request": request,
            "users": users,
            "drivers": drivers,
            "hauliers": hauliers,
            "vehicles": vehicles,
            "loads": loads,
            "jobs": jobs,
            "job_by_load_id": job_by_load_id,
            "payments": payments,
            "load_interests": load_interests,
            "load_interests_display": load_interests_display,
            "haulier_profile": haulier_profile,
            "loader_profile": loader_profile,
            "stripe_dashboard_customers_root": _scust,
            "stripe_dashboard_connect_accounts_root": _sconn,
            "stripe_is_test_mode": _stest,
            "stripe_billing_configured": _stripe_ok,
            "uploaded": None,
            "upload_errors": None,
            "upload_type": "",
            "delete_error": None,
            "deleted": None,
            "driver_error": None,
            "driver_ok": None,
            "team_error": None,
            "team_ok": None,
            "create_login_error": None,
            "create_login_ok": None,
            "open_loads_count": open_loads_count,
            "total_payout": total_payout,
            "matching_results": matching_results,
            "find_vehicle_id": vehicle_id_raw,
            "find_origin_postcode": origin_postcode,
            "find_destination_postcode": destination_postcode,
            "postcode_lookup_failed": postcode_lookup_failed,
            "match_diagnostic": match_diagnostic,
            "find_vehicle_busy": find_vehicle_busy,
            "vehicle_availability": vehicle_availability,
            "platform_fee_percent": get_settings().platform_fee_percent,
            "loader_flat_fee_gbp": get_settings().loader_flat_fee_gbp,
            "loader_fee_minimum_gbp": get_settings().loader_flat_fee_gbp,
            "loader_fee_percent_of_load": get_settings().loader_fee_percent_of_load,
            "pallet_volume_m3": get_settings().pallet_volume_m3,
            "current_user_email": (current_user.email if current_user else ""),
            "current_user": current_user,
            "rating_ok": rating_ok,
            "rating_error": rating_error,
            "public_app_base_url": _pub_base,
            "shared_load_id": shared_load_id,
            "driver_can_find_backhauls": False,
            "driver_pending_approvals": [],
            "haulier_pending_backhaul_approvals": [],
            "approval_confirmation": None,
            "find_backhaul_msg": find_backhaul_msg,
            "onboarding_checklist": onboarding_checklist,
            "emergency_evidence_jobs": emergency_evidence_jobs,
            "cancellation_ui_settings": cancellation_ui_settings,
            **rating_ctx,
        },
    )


@router.post("/hauliers", response_class=HTMLResponse)
async def create_haulier_form(
    request: Request,
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin),
) -> RedirectResponse:
    form = dict(await request.form())
    name = form.get("name") or ""
    email = form.get("contact_email") or ""
    phone = form.get("contact_phone") or ""

    haulier = models.Haulier(name=name, contact_email=email, contact_phone=phone)
    db.add(haulier)
    db.commit()

    return RedirectResponse(url="/?section=vehicles", status_code=303)


@router.post("/vehicles", response_class=HTMLResponse)
async def create_vehicle_form(
    request: Request,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    from app.auth import get_current_user_optional

    current_user = get_current_user_optional(request, db)
    if not current_user:
        return RedirectResponse(url="/login", status_code=302)
    form = await request.form()
    role = (getattr(current_user, "role", None) or "").strip().lower()
    try:
        if role == "admin":
            haulier_id_raw = form.get("haulier_id")
            if haulier_id_raw is None or (isinstance(haulier_id_raw, str) and not str(haulier_id_raw).strip()):
                return RedirectResponse(
                    url="/?section=vehicles&delete_error=Please+pick+a+company",
                    status_code=303,
                )
            haulier_id = int(str(haulier_id_raw).strip())
        elif role == "haulier" and current_user.haulier_id:
            haulier_id = int(current_user.haulier_id)
        elif role == "loader":
            return RedirectResponse(
                url="/?section=vehicles&delete_error=Loaders+cannot+add+vehicles+here",
                status_code=303,
            )
        elif role == "haulier" and not current_user.haulier_id:
            return RedirectResponse(
                url="/?section=vehicles&delete_error=Your+account+is+not+linked+to+a+haulier+company",
                status_code=303,
            )
        else:
            return RedirectResponse(
                url="/?section=vehicles&delete_error=Not+authorized",
                status_code=303,
            )
    except (TypeError, ValueError):
        return RedirectResponse(
            url="/?section=vehicles&delete_error=Please+pick+a+company",
            status_code=303,
        )
    registration = (form.get("registration") or "").strip().upper()
    if not registration:
        return RedirectResponse(
            url="/?section=vehicles&delete_error=Registration+required",
            status_code=303,
        )
    vehicle_type = form.get("vehicle_type") or "rigid"
    trailer_type = (form.get("trailer_type") or "").strip() or None

    if db.query(models.Vehicle).filter(models.Vehicle.registration == registration).first():
        return RedirectResponse(
            url="/?section=vehicles&delete_error=Registration+already+exists",
            status_code=303,
        )
    haulier = db.get(models.Haulier, haulier_id)
    if not haulier:
        return RedirectResponse(
            url="/?section=vehicles&delete_error=Company+not+found",
            status_code=303,
        )

    from datetime import date as date_cls
    from urllib.parse import quote_plus

    from starlette.datastructures import UploadFile

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

    base_postcode = (form.get("base_postcode") or "").strip().upper() or None

    def _optional_str(name: str, maxlen: int) -> Optional[str]:
        s = (form.get(name) or "").strip()
        return s[:maxlen] if s else None

    vy_raw = form.get("vehicle_year")
    vehicle_year: Optional[int] = None
    if vy_raw not in (None, ""):
        try:
            vehicle_year = int(str(vy_raw).strip())
        except ValueError:
            vehicle_year = None

    try:
        vehicle = models.Vehicle(
            haulier_id=haulier_id,
            registration=registration,
            vehicle_type=vehicle_type,
            trailer_type=trailer_type,
            base_postcode=base_postcode,
            make=_optional_str("vehicle_make", 128),
            model=_optional_str("vehicle_model", 128),
            colour=_optional_str("vehicle_colour", 64),
            year=vehicle_year,
            mot_status=_optional_str("vehicle_mot_status", 128),
            tax_status=_optional_str("vehicle_tax_status", 128),
            has_tail_lift=_form_checkbox(form, "has_tail_lift"),
            has_moffett=_form_checkbox(form, "has_moffett"),
            has_temp_control=_form_checkbox(form, "has_temp_control"),
            is_adr_certified=_form_checkbox(form, "is_adr_certified"),
            insurance_expiry_date=insurance_expiry,
            insurance_status=calculate_insurance_status(insurance_expiry),
        )
        db.add(vehicle)
        db.commit()
        db.refresh(vehicle)
    except IntegrityError:
        db.rollback()
        return RedirectResponse(
            url="/?section=vehicles&delete_error=Could+not+save+vehicle",
            status_code=303,
        )

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
                vehicle.id, base_postcode, haulier_id, db, origin_label="base",
            )
        except Exception:
            pass

    return RedirectResponse(url="/?section=vehicles&vehicle_added=1", status_code=303)


def _parse_float(s, default=None):
    if s is None or (isinstance(s, str) and not s.strip()):
        return default
    try:
        return float(s)
    except (TypeError, ValueError):
        return default


def _form_checkbox(form, name: str) -> bool:
    """HTML checkbox: present → on/true."""
    v = form.get(name)
    if v is None:
        return False
    s = str(v).strip().lower()
    return s in ("on", "true", "1", "yes")

@router.post("/upload", response_class=RedirectResponse)
async def upload_file_form(
    request: Request,
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin),
) -> RedirectResponse:
    """Handle CSV/Excel bulk upload from the web form; redirect back with result."""
    form = await request.form()
    file = form.get("file")
    upload_type = (form.get("type") or "hauliers").strip().lower()
    if upload_type not in ("hauliers", "vehicles", "loads"):
        upload_type = "hauliers"

    if not file or not hasattr(file, "read"):
        return RedirectResponse(url="/?uploaded=0&errors=1&upload_type=" + upload_type, status_code=303)

    filename = getattr(file, "filename", "") or "upload.csv"
    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        return RedirectResponse(url="/?uploaded=0&errors=1&upload_type=" + upload_type + "&msg=file_too_large", status_code=303)

    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in (".csv", ".xlsx", ".xls"):
        return RedirectResponse(url="/?uploaded=0&errors=1&upload_type=" + upload_type + "&msg=bad_type", status_code=303)

    from app.services.upload_parser import parse_hauliers, parse_loads, parse_vehicles
    from app.services.bulk_import import import_hauliers, import_loads, import_vehicles

    if upload_type == "hauliers":
        rows = parse_hauliers(content, filename)
        created, errs = import_hauliers(db, rows)
    elif upload_type == "vehicles":
        rows = parse_vehicles(content, filename)
        created, errs = import_vehicles(db, rows)
    else:
        rows = parse_loads(content, filename)
        created, errs = import_loads(db, rows)

    section = "loads" if upload_type == "loads" else "vehicles"
    return RedirectResponse(
        url=f"/?section={section}&uploaded={created}&errors={len(errs)}&upload_type={upload_type}",
        status_code=303,
    )


@router.post("/delete-haulier/{haulier_id}", response_class=RedirectResponse)
def delete_haulier_form(
    haulier_id: int,
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin),
) -> RedirectResponse:
    """Delete a haulier company (no vehicles). Clears users/drivers/trailers linked to this haulier."""
    haulier = db.get(models.Haulier, haulier_id)
    if not haulier:
        return RedirectResponse(url="/?delete_error=Haulier+not+found", status_code=303)
    if db.query(models.Vehicle).filter(models.Vehicle.haulier_id == haulier_id).first():
        return RedirectResponse(url="/?delete_error=Delete+vehicles+first", status_code=303)
    for d in db.query(models.Driver).filter(models.Driver.haulier_id == haulier_id).all():
        if db.query(models.BackhaulJob).filter(models.BackhaulJob.driver_id == d.id).first():
            return RedirectResponse(
                url="/?section=vehicles&delete_error=Resolve+driver+jobs+before+deleting+company",
                status_code=303,
            )
    try:
        db.query(models.HaulierRoute).filter(models.HaulierRoute.haulier_id == haulier_id).delete()
        _li_ids = [
            r[0]
            for r in db.query(models.LoadInterest.id)
            .filter(models.LoadInterest.haulier_id == haulier_id)
            .all()
        ]
        if _li_ids:
            db.query(models.BackhaulApprovalToken).filter(
                models.BackhaulApprovalToken.load_interest_id.in_(_li_ids)
            ).delete(synchronize_session=False)
        db.query(models.LoadInterest).filter(models.LoadInterest.haulier_id == haulier_id).delete()
        db.query(models.Trailer).filter(models.Trailer.haulier_id == haulier_id).delete()
        db.query(models.Driver).filter(models.Driver.haulier_id == haulier_id).delete()
        db.execute(sa_update(models.User).where(models.User.haulier_id == haulier_id).values(haulier_id=None))
        db.delete(haulier)
        db.commit()
    except IntegrityError:
        db.rollback()
        return RedirectResponse(
            url="/?section=vehicles&delete_error=Cannot+delete+company",
            status_code=303,
        )
    return RedirectResponse(url="/?section=vehicles&deleted=haulier", status_code=303)


@router.post("/delete-vehicle/{vehicle_id}", response_class=RedirectResponse)
def delete_vehicle_form(
    vehicle_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Delete a vehicle (only if not used in jobs). Admin or owning haulier."""
    from app.auth import get_current_user_optional

    current_user = get_current_user_optional(request, db)
    if not current_user:
        return RedirectResponse(url="/login", status_code=302)
    vehicle = db.get(models.Vehicle, vehicle_id)
    if not vehicle:
        return RedirectResponse(url="/?delete_error=Vehicle+not+found", status_code=303)
    role = (getattr(current_user, "role", None) or "").strip().lower()
    if role == "haulier":
        if not current_user.haulier_id or vehicle.haulier_id != current_user.haulier_id:
            return RedirectResponse(url="/?section=vehicles&delete_error=Not+authorized", status_code=303)
    elif role != "admin":
        return RedirectResponse(url="/?section=vehicles&delete_error=Not+authorized", status_code=303)
    if db.query(models.BackhaulJob).filter(models.BackhaulJob.vehicle_id == vehicle_id).first():
        return RedirectResponse(url="/?delete_error=Vehicle+has+jobs", status_code=303)
    try:
        db.query(models.HaulierRoute).filter(models.HaulierRoute.vehicle_id == vehicle_id).delete(
            synchronize_session=False
        )
        _v_li = [
            r[0]
            for r in db.query(models.LoadInterest.id)
            .filter(models.LoadInterest.vehicle_id == vehicle_id)
            .all()
        ]
        if _v_li:
            db.query(models.BackhaulApprovalToken).filter(
                models.BackhaulApprovalToken.load_interest_id.in_(_v_li)
            ).delete(synchronize_session=False)
        db.execute(sa_delete(models.LoadInterest).where(models.LoadInterest.vehicle_id == vehicle_id))
        db.delete(vehicle)
        db.commit()
    except IntegrityError:
        db.rollback()
        return RedirectResponse(
            url="/?section=vehicles&delete_error=Cannot+delete+vehicle",
            status_code=303,
        )
    return RedirectResponse(url="/?section=vehicles&deleted=vehicle", status_code=303)

@router.post("/delete-job/{job_id}", response_class=RedirectResponse)
async def delete_job_form(
    job_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Haulier office or admin: cancel a backhaul job. Policy: time windows, strikes, emergency soft-cancel."""
    from app.services.cancellation_emails import (
        notify_loader_emergency_haulier_cancel,
        notify_loader_haulier_cancelled,
        notify_support_emergency_cancellation,
        send_haulier_emergency_evidence_reminder,
        send_haulier_probation_notice,
        send_haulier_suspension_notice,
    )
    from app.services.cancellation_policy import haulier_cancellation_penalty_kind, hours_until_pickup
    from app.services.stripe_loader_charge import try_refund_loader_charge

    current_user = get_current_user_optional(request, db)
    if get_current_driver_optional(request, db) is not None:
        return RedirectResponse(url="/?section=matches&delete_error=Not+authorized", status_code=303)
    if not current_user:
        return RedirectResponse(url="/login", status_code=302)
    if current_user.role not in ("haulier", "admin"):
        return RedirectResponse(url="/?section=matches&delete_error=Not+authorized", status_code=303)

    form = await request.form()
    cancellation_type = (form.get("cancellation_type") or "normal").strip().lower()
    emergency_reason = (form.get("emergency_reason") or "").strip().lower()
    emergency_details = (form.get("emergency_details") or "").strip()[:1000]

    job = db.get(models.BackhaulJob, job_id)
    if not job:
        return RedirectResponse(url="/?section=matches&delete_error=Job+not+found", status_code=303)
    if getattr(job, "haulier_cancelled_at", None):
        return RedirectResponse(url="/?section=matches&delete_error=already_cancelled", status_code=303)

    vehicle_id_for_refresh = job.vehicle_id
    vehicle = db.get(models.Vehicle, job.vehicle_id)
    if current_user.role == "haulier":
        if not current_user.haulier_id or not vehicle or vehicle.haulier_id != current_user.haulier_id:
            return RedirectResponse(url="/?section=matches&delete_error=Not+your+job", status_code=303)

    load = db.get(models.Load, job.load_id)
    if not load:
        return RedirectResponse(url="/?section=matches&delete_error=Load+not+found", status_code=303)

    now = datetime.now(timezone.utc)
    hours = hours_until_pickup(load, job, now)
    settings = get_settings()
    pen_h = float(settings.penalty_cancellation_hours)
    is_emergency = cancellation_type == "emergency" and bool(emergency_reason)

    payment = (
        db.query(models.Payment)
        .filter(models.Payment.backhaul_job_id == job.id)
        .order_by(models.Payment.created_at.asc())
        .first()
    )

    def _refund_loader_if_captured() -> None:
        if payment and (payment.status or "").strip().lower() == models.PaymentStatusEnum.CAPTURED.value:
            try_refund_loader_charge(payment, db, refund_amount_gbp=None)
            db.add(payment)

    def _interest_to_suggested() -> None:
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

    if current_user.role == "admin":
        _refund_loader_if_captured()
        load.status = models.LoadStatusEnum.OPEN.value
        load.reopened_at = now
        db.add(load)
        _interest_to_suggested()
        db.query(models.Payment).filter(models.Payment.backhaul_job_id == job_id).delete()
        db.query(models.POD).filter(models.POD.backhaul_job_id == job_id).delete()
        db.delete(job)
        vehicle_availability_svc.refresh_vehicle_availability(db, vehicle_id_for_refresh)
        db.commit()
        return RedirectResponse(url="/?section=matches&deleted=job", status_code=303)

    haulier = db.get(models.Haulier, current_user.haulier_id)
    if not haulier:
        return RedirectResponse(url="/?section=matches&delete_error=Not+your+job", status_code=303)
    if (haulier.account_status or "").strip().lower() == "suspended":
        return RedirectResponse(url="/?section=matches&delete_error=account_suspended", status_code=303)

    if hours < pen_h and not is_emergency:
        return RedirectResponse(url="/?section=matches&haulier_cancel_blocked=1", status_code=303)

    if is_emergency:
        _refund_loader_if_captured()
        db.query(models.POD).filter(models.POD.backhaul_job_id == job.id).delete()
        db.query(models.Payment).filter(models.Payment.backhaul_job_id == job.id).delete()

        job.haulier_cancelled_at = now
        job.emergency_cancellation = True
        job.emergency_details = emergency_details or None
        job.issue_type = emergency_reason[:50] if emergency_reason else None
        job.emergency_evidence_required = True
        haulier.pending_emergency_reviews = int(haulier.pending_emergency_reviews or 0) + 1
        db.add(haulier)
        db.add(job)

        load.status = models.LoadStatusEnum.OPEN.value
        load.load_priority = "urgent" if hours < 24.0 else "normal"
        load.reopened_at = now
        load.cancellation_reason = "haulier_emergency_cancel"
        db.add(load)

        _interest_to_suggested()

        try:
            notify_loader_emergency_haulier_cancel(
                db, job, emergency_reason, hours, commit_in_app=False
            )
            send_haulier_emergency_evidence_reminder(
                db, haulier, job, emergency_reason, commit_in_app=False
            )
            notify_support_emergency_cancellation(job, emergency_reason, emergency_details)
        except Exception:
            logger.exception("emergency haulier cancel emails")

        vehicle_availability_svc.refresh_vehicle_availability(db, vehicle_id_for_refresh)
        db.commit()
        return RedirectResponse(url="/?section=matches&deleted=job&emergency=1", status_code=303)

    kind = haulier_cancellation_penalty_kind(hours)
    if kind == "blocked":
        return RedirectResponse(url="/?section=matches&haulier_cancel_blocked=1", status_code=303)

    _refund_loader_if_captured()

    if kind == "strike":
        haulier.cancellation_strikes = int(haulier.cancellation_strikes or 0) + 1
        haulier.last_strike_date = now
        thr = settings.suspension_strike_threshold
        prob = settings.probation_strike_threshold
        if haulier.cancellation_strikes >= thr:
            haulier.account_status = "suspended"
            try:
                send_haulier_suspension_notice(haulier)
            except Exception:
                logger.exception("send_haulier_suspension_notice")
        elif haulier.cancellation_strikes >= prob:
            haulier.account_status = "probation"
            try:
                send_haulier_probation_notice(haulier)
            except Exception:
                logger.exception("send_haulier_probation_notice")
        db.add(haulier)

    try:
        notify_loader_haulier_cancelled(db, job, hours, commit_in_app=False)
    except Exception:
        logger.exception("notify_loader_haulier_cancelled")

    load.status = models.LoadStatusEnum.OPEN.value
    if hours < 12.0:
        load.load_priority = "emergency"
    elif hours < 24.0:
        load.load_priority = "urgent"
    else:
        load.load_priority = "normal"
    load.reopened_at = now
    load.cancellation_reason = "haulier_cancel"
    db.add(load)

    _interest_to_suggested()

    db.query(models.POD).filter(models.POD.backhaul_job_id == job_id).delete()
    db.query(models.Payment).filter(models.Payment.backhaul_job_id == job_id).delete()
    db.delete(job)
    vehicle_availability_svc.refresh_vehicle_availability(db, vehicle_id_for_refresh)
    db.commit()
    return RedirectResponse(url="/?section=matches&deleted=job", status_code=303)


@router.post("/assign-job-driver", response_class=RedirectResponse)
async def assign_job_driver(
    request: Request,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Assign/unassign a driver to a confirmed job (haulier office or admin)."""
    current_user = get_current_user_optional(request, db)
    if not current_user:
        return RedirectResponse(url="/login", status_code=302)
    if current_user.role not in ("haulier", "admin"):
        return RedirectResponse(url="/?section=matches&delete_error=Not+authorized", status_code=303)

    form = await request.form()
    try:
        job_id = int(form.get("job_id") or 0)
    except (TypeError, ValueError):
        return RedirectResponse(url="/?section=matches&delete_error=Invalid+job", status_code=303)
    job = db.get(models.BackhaulJob, job_id)
    if not job:
        return RedirectResponse(url="/?section=matches&delete_error=Job+not+found", status_code=303)
    vehicle = db.get(models.Vehicle, job.vehicle_id)
    if not vehicle:
        return RedirectResponse(url="/?section=matches&delete_error=Vehicle+not+found", status_code=303)

    if current_user.role == "haulier" and vehicle.haulier_id != current_user.haulier_id:
        return RedirectResponse(url="/?section=matches&delete_error=Not+your+job", status_code=303)

    driver_id_raw = (form.get("driver_id") or "").strip()
    if not driver_id_raw:
        job.driver_id = None
    else:
        try:
            driver_id = int(driver_id_raw)
        except (TypeError, ValueError):
            return RedirectResponse(url="/?section=matches&delete_error=Invalid+driver", status_code=303)
        driver = db.get(models.Driver, driver_id)
        if not driver or driver.haulier_id != vehicle.haulier_id:
            return RedirectResponse(url="/?section=matches&delete_error=Driver+not+found+for+this+haulier", status_code=303)
        # First-to-act wins: if already assigned to another driver (office or claim), don't overwrite.
        if job.driver_id is not None and job.driver_id != driver.id:
            return RedirectResponse(
                url="/?section=matches&delete_error=Job+already+assigned.+Unassign+first+to+change+driver",
                status_code=303,
            )
        job.driver_id = driver.id
        from app.services.job_groups import propagate_group_driver

        propagate_group_driver(db, job, driver.id)

    db.add(job)
    db.commit()
    return RedirectResponse(url="/?section=matches&deleted=driver_assigned", status_code=303)


def _can_view_job_track(job: models.BackhaulJob, user: Optional[models.User], db: Session) -> bool:
    """Admin, loader who owns the load, or haulier who owns the vehicle can view track."""
    if not user:
        return False
    if user.role == "admin":
        return True
    load = db.get(models.Load, job.load_id)
    if user.role == "loader" and load and getattr(load, "loader_id", None) == user.loader_id:
        return True
    vehicle = db.get(models.Vehicle, job.vehicle_id)
    if user.role == "haulier" and vehicle and vehicle.haulier_id == user.haulier_id:
        return True
    return False


@router.get("/track/{job_id}", response_class=HTMLResponse)
def track_job_page(
    job_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Live track: map + driver status. Admin, loader (owner of load), or haulier (owner of job) only."""
    user = get_current_user_optional(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    job = db.get(models.BackhaulJob, job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    if not _can_view_job_track(job, user, db):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You cannot view this job")
    load = db.get(models.Load, job.load_id)
    return templates.TemplateResponse(
        "track.html",
        {
            "request": request,
            "job": job,
            "load": load,
            "job_id": job_id,
        },
    )


@router.get("/api/track/jobs/{job_id}")
def track_job_api(
    job_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """JSON for live track polling: driver position + status timestamps. Same auth as track page."""
    user = get_current_user_optional(request, db)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not logged in")
    job = db.get(models.BackhaulJob, job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    if not _can_view_job_track(job, user, db):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot view this job")
    load = db.get(models.Load, job.load_id)
    return {
        "job_id": job.id,
        "pickup_postcode": load.pickup_postcode if load else "",
        "delivery_postcode": load.delivery_postcode if load else "",
        "shipper_name": load.shipper_name if load else "",
        "reached_pickup_at": job.reached_pickup_at.isoformat() if job.reached_pickup_at else None,
        "collected_at": job.collected_at.isoformat() if job.collected_at else None,
        "departed_pickup_at": job.departed_pickup_at.isoformat() if job.departed_pickup_at else None,
        "reached_delivery_at": job.reached_delivery_at.isoformat() if job.reached_delivery_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "last_lat": job.last_lat,
        "last_lng": job.last_lng,
        "location_updated_at": job.location_updated_at.isoformat() if job.location_updated_at else None,
    }


@router.post("/delete-load/{load_id}", response_class=RedirectResponse)
def delete_load_form(
    load_id: int,
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin),
) -> RedirectResponse:
    """Delete a load (only if it has no backhaul jobs)."""
    load = db.get(models.Load, load_id)
    if not load:
        return RedirectResponse(url="/?delete_error=Load+not+found", status_code=303)
    if db.query(models.BackhaulJob).filter(models.BackhaulJob.load_id == load_id).first():
        return RedirectResponse(url="/?section=loads&delete_error=Load+has+jobs", status_code=303)
    db.query(models.LoadInterest).filter(models.LoadInterest.load_id == load_id).delete()
    db.delete(load)
    db.commit()
    return RedirectResponse(url="/?section=loads&deleted=load", status_code=303)


@router.post("/delete-user/{user_id}", response_class=RedirectResponse)
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin),
) -> RedirectResponse:
    """Delete a user account (admin only). Cannot delete admin users."""
    user = db.get(models.User, user_id)
    if not user:
        return RedirectResponse(url="/?section=admin&delete_error=User+not+found", status_code=303)
    
    # Prevent deleting admin accounts
    if user.role == "admin":
        return RedirectResponse(url="/?section=admin&delete_error=Cannot+delete+admin+users", status_code=303)
    
    # Delete the user
    db.delete(user)
    db.commit()
    
    return RedirectResponse(url="/?section=admin&deleted=user", status_code=303)


@router.post("/show-interest", response_class=RedirectResponse)
async def show_interest_form(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Haulier office or driver: express interest in a suggested load or planned load."""
    current_user = get_current_user_optional(request, db)
    driver_actor = get_current_driver_optional(request, db) if current_user is None else None
    if current_user is None and driver_actor is None:
        return RedirectResponse(url="/login", status_code=302)

    form = dict(await request.form())
    haulier_id = int(form.get("haulier_id"))
    vehicle_id = int(form.get("vehicle_id"))
    planned_load_id = form.get("planned_load_id")
    load_id = form.get("load_id")
    if planned_load_id:
        planned_load_id = int(planned_load_id)
    else:
        planned_load_id = None
    if load_id:
        load_id = int(load_id)
    else:
        load_id = None
    if not planned_load_id and not load_id:
        return RedirectResponse(url="/", status_code=303)

    v = db.get(models.Vehicle, vehicle_id)
    if not v or v.haulier_id != haulier_id:
        return RedirectResponse(url="/?section=matches", status_code=303)
    if current_user and getattr(current_user, "role", None) == "haulier":
        if not current_user.haulier_id or current_user.haulier_id != haulier_id:
            return RedirectResponse(url="/?section=matches", status_code=303)
    elif current_user and getattr(current_user, "role", None) == "admin":
        pass
    elif driver_actor:
        if driver_actor.haulier_id != haulier_id:
            return RedirectResponse(url="/?section=matches", status_code=303)
        if driver_actor.vehicle_id and driver_actor.vehicle_id != vehicle_id:
            return RedirectResponse(url="/?section=matches", status_code=303)
    else:
        return RedirectResponse(url="/?section=matches", status_code=303)
    existing = (
        db.query(models.LoadInterest)
        .filter(
            models.LoadInterest.haulier_id == haulier_id,
            models.LoadInterest.vehicle_id == vehicle_id,
            models.LoadInterest.planned_load_id == planned_load_id,
            models.LoadInterest.load_id == load_id,
        )
        .first()
    )
    is_new_interest = existing is None
    prev_status = existing.status if existing else None
    if existing:
        existing.status = "expressed"
        if driver_actor:
            existing.expressing_driver_id = driver_actor.id
        elif current_user and getattr(current_user, "role", None) == "haulier":
            existing.expressing_driver_id = None
        db.commit()
        interest = existing
    else:
        interest = models.LoadInterest(
            haulier_id=haulier_id,
            vehicle_id=vehicle_id,
            load_id=load_id,
            planned_load_id=planned_load_id,
            status="expressed",
            expressing_driver_id=driver_actor.id if driver_actor else None,
        )
        db.add(interest)
        db.commit()
    if is_new_interest or prev_status != "expressed":
        try:
            from app.services.in_app_notifications import record_loader_haulier_interest_notifications

            load_row = db.get(models.Load, load_id) if load_id else None
            planned_row = db.get(models.PlannedLoad, planned_load_id) if planned_load_id else None
            record_loader_haulier_interest_notifications(
                db,
                load=load_row,
                planned_load=planned_row,
                haulier_id=haulier_id,
                vehicle_id=vehicle_id,
            )
        except Exception as e:
            print(f"[NOTIFY] record_loader_haulier_interest_notifications failed: {e}")
    try:
        from app.services.email_sender import schedule_loader_interest_email

        schedule_loader_interest_email(background_tasks, interest.id)
    except Exception as e:
        print(f"[EMAIL] schedule_loader_interest_email failed: {e}")
    return RedirectResponse(url="/", status_code=303)


@router.post("/accept-interest", response_class=RedirectResponse)
async def accept_interest(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Loader (or admin): accept haulier/driver interest and create a backhaul job with driver when known."""
    from datetime import datetime, timezone
    from urllib.parse import quote_plus

    redir = require_loader(request, db)
    if redir is not None:
        return redir
    current_user = get_current_user_optional(request, db)
    if not current_user:
        return RedirectResponse(url="/login", status_code=302)

    form = dict(await request.form())
    try:
        interest_id = int(form.get("interest_id"))
    except (TypeError, ValueError):
        return RedirectResponse(
            url="/?section=matches&msg=" + quote_plus("invalid_interest"),
            status_code=303,
        )

    interest = db.get(models.LoadInterest, interest_id)
    if not interest or interest.status not in ("expressed", "pending_haulier_approval"):
        return RedirectResponse(url="/?section=matches", status_code=303)
    if not interest.load_id:
        return RedirectResponse(
            url="/?section=matches&msg=" + quote_plus("use_loader_flow_for_planned"),
            status_code=303,
        )

    load = db.get(models.Load, interest.load_id)
    if not load:
        return RedirectResponse(url="/?section=matches", status_code=303)
    if current_user.role == "loader":
        if not current_user.loader_id or load.loader_id != current_user.loader_id:
            return RedirectResponse(
                url="/?section=matches&msg=" + quote_plus("not_your_load"),
                status_code=303,
            )

    from app.services.job_driver_resolution import resolve_driver_id_for_accepted_interest

    driver_id = resolve_driver_id_for_accepted_interest(db, interest)

    interest.status = "accepted"
    job = models.BackhaulJob(
        vehicle_id=interest.vehicle_id,
        load_id=interest.load_id,
        driver_id=driver_id,
        matched_at=datetime.now(timezone.utc),
    )
    db.add(job)
    load.status = models.LoadStatusEnum.MATCHED.value
    db.add(load)
    from app.services.job_groups import try_link_new_job_pickup_group

    try_link_new_job_pickup_group(db, job)
    db.commit()
    db.refresh(job)
    vehicle_availability_svc.refresh_vehicle_availability(db, job.vehicle_id)
    db.commit()

    try:
        from app.services.email_sender import schedule_haulier_job_email

        schedule_haulier_job_email(background_tasks, job.id)
    except Exception as e:
        print(f"[EMAIL] schedule_haulier_job_email failed: {e}")

    return RedirectResponse(url="/?section=matches", status_code=303)


@router.post("/decline-interest", response_class=RedirectResponse)
async def decline_interest(
    request: Request,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Loader or admin: decline expressed interest (haulier cannot be accepted for this expression)."""
    from urllib.parse import quote_plus

    redir = require_loader(request, db)
    if redir is not None:
        return redir
    current_user = get_current_user_optional(request, db)
    if not current_user:
        return RedirectResponse(url="/login", status_code=302)

    form = dict(await request.form())
    try:
        interest_id = int(form.get("interest_id"))
    except (TypeError, ValueError):
        return RedirectResponse(
            url="/?section=matches&msg=" + quote_plus("invalid_interest"),
            status_code=303,
        )

    interest = db.get(models.LoadInterest, interest_id)
    if not interest or interest.status not in ("expressed", "pending_haulier_approval"):
        return RedirectResponse(url="/?section=matches", status_code=303)

    if interest.load_id:
        load = db.get(models.Load, interest.load_id)
        if not load:
            return RedirectResponse(url="/?section=matches", status_code=303)
        if current_user.role == "loader":
            if not current_user.loader_id or load.loader_id != current_user.loader_id:
                return RedirectResponse(
                    url="/?section=matches&msg=" + quote_plus("not_your_load"),
                    status_code=303,
                )
    elif interest.planned_load_id:
        pl = db.get(models.PlannedLoad, interest.planned_load_id)
        if not pl:
            return RedirectResponse(url="/?section=matches", status_code=303)
        if current_user.role == "loader":
            if not current_user.loader_id or pl.loader_id != current_user.loader_id:
                return RedirectResponse(
                    url="/?section=matches&msg=" + quote_plus("not_your_load"),
                    status_code=303,
                )
    else:
        return RedirectResponse(url="/?section=matches", status_code=303)

    interest.status = "declined"
    db.add(interest)
    db.commit()
    return RedirectResponse(url="/?section=matches&interest_declined=1", status_code=303)


@router.post("/create-haulier-account", response_class=RedirectResponse)
async def create_haulier_account(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin),
) -> RedirectResponse:
    """Admin: create a new haulier (company) and their login in one step. No separate Add company needed."""
    from urllib.parse import quote_plus
    form = await request.form()
    name = (form.get("name") or "").strip()
    email = (form.get("email") or "").strip().lower()
    contact_phone = (form.get("contact_phone") or "").strip() or None
    password = form.get("password") or ""
    base = "/?section=admin"
    def redirect_error(msg: str) -> RedirectResponse:
        return RedirectResponse(url=base + "&create_login_error=" + quote_plus(msg), status_code=303)
    if not name or not email:
        return redirect_error("Company name and email required")
    if len(password) < 6:
        return redirect_error("Password must be at least 6 characters")
    if db.query(models.User).filter(models.User.email == email).first():
        return redirect_error("That login email is already used — use Link login to existing company or another email")
    if db.query(models.Haulier).filter(models.Haulier.contact_email == email).first():
        return redirect_error("A company with this contact email already exists — use Link login to existing company (not New Haulier)")
    try:
        haulier = models.Haulier(name=name, contact_email=email, contact_phone=contact_phone)
        db.add(haulier)
        db.commit()
        db.refresh(haulier)
        user = models.User(
            email=email,
            password_hash=hash_password(password),
            role="haulier",
            haulier_id=haulier.id,
        )
        db.add(user)
        db.commit()
    except IntegrityError:
        db.rollback()
        return redirect_error("Company or email already exists — use Link login to existing company")
    base_url = str(request.base_url).rstrip("/")
    schedule_registration_emails(
        background_tasks,
        user_email=email,
        user_name=name,
        user_type="haulier",
        company_name=name,
        dashboard_link=f"{base_url}/?section=find",
        tutorial_link=f"{base_url}/?section=find",
        vehicle_setup_link=f"{base_url}/?section=vehicles",
        admin_panel_link=f"{base_url}/admin/dashboard",
        registered_at_iso=datetime.now(timezone.utc).isoformat(),
        user_id=user.id,
        contact_phone=contact_phone,
    )
    return RedirectResponse(url=base + "&create_login_ok=" + quote_plus("Company + login created. They can log in and add vehicles."), status_code=303)


@router.post("/create-haulier-login", response_class=RedirectResponse)
async def create_haulier_login(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin),
) -> RedirectResponse:
    """Admin: create a login for an existing haulier."""
    from urllib.parse import quote_plus
    form = await request.form()
    haulier_id = form.get("haulier_id")
    email = (form.get("email") or "").strip().lower()
    password = form.get("password") or ""
    base = "/?section=admin"
    def redirect_error(msg: str) -> RedirectResponse:
        return RedirectResponse(url=base + "&create_login_error=" + quote_plus(msg), status_code=303)
    if not haulier_id or not email or not password:
        return redirect_error("Missing fields")
    try:
        haulier_id = int(haulier_id)
    except (TypeError, ValueError):
        return redirect_error("Invalid haulier")
    if db.query(models.Haulier).filter(models.Haulier.id == haulier_id).first() is None:
        return redirect_error("Haulier not found")
    if db.query(models.User).filter(models.User.email == email).first():
        return redirect_error("Email already used")
    user = models.User(
        email=email,
        password_hash=hash_password(password),
        role="haulier",
        haulier_id=haulier_id,
    )
    db.add(user)
    db.commit()
    haulier = db.get(models.Haulier, haulier_id)
    haulier_name = (haulier.name if haulier else "Haulier") or "Haulier"
    base_url = str(request.base_url).rstrip("/")
    schedule_registration_emails(
        background_tasks,
        user_email=email,
        user_name=haulier_name,
        user_type="haulier",
        company_name=haulier_name,
        dashboard_link=f"{base_url}/?section=find",
        tutorial_link=f"{base_url}/?section=find",
        vehicle_setup_link=f"{base_url}/?section=vehicles",
        admin_panel_link=f"{base_url}/admin/dashboard",
        registered_at_iso=datetime.now(timezone.utc).isoformat(),
        user_id=user.id,
        contact_phone=(haulier.contact_phone if haulier else None),
    )
    return RedirectResponse(url=base + "&create_login_ok=" + quote_plus("Haulier login created"), status_code=303)


@router.post("/create-loader-account", response_class=RedirectResponse)
async def create_loader_account(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin),
) -> RedirectResponse:
    """Admin: create a new loader company and login."""
    form = await request.form()
    name = (form.get("name") or "").strip()
    email = (form.get("email") or "").strip().lower()
    password = form.get("password") or ""
    base = "/?section=admin"
    from urllib.parse import quote_plus
    def redirect_error(msg: str) -> RedirectResponse:
        return RedirectResponse(url=base + "&create_login_error=" + quote_plus(msg), status_code=303)
    if not name or not email or not password:
        return redirect_error("Missing fields")
    if db.query(models.User).filter(models.User.email == email).first():
        return redirect_error("Email already used")
    loader = models.Loader(name=name, contact_email=email, contact_phone=None)
    db.add(loader)
    db.commit()
    db.refresh(loader)
    user = models.User(
        email=email,
        password_hash=hash_password(password),
        role="loader",
        loader_id=loader.id,
    )
    db.add(user)
    db.commit()
    base_url = str(request.base_url).rstrip("/")
    schedule_registration_emails(
        background_tasks,
        user_email=email,
        user_name=name,
        user_type="loader",
        company_name=name,
        dashboard_link=f"{base_url}/?section=find",
        tutorial_link=f"{base_url}/?section=loads",
        vehicle_setup_link=f"{base_url}/?section=vehicles",
        admin_panel_link=f"{base_url}/admin/dashboard",
        registered_at_iso=datetime.now(timezone.utc).isoformat(),
        user_id=user.id,
        contact_phone=None,
    )
    return RedirectResponse(url=base + "&create_login_ok=" + quote_plus("Loader account created"), status_code=303)


@router.post("/create-driver", response_class=RedirectResponse)
async def create_driver_account(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin),
) -> RedirectResponse:
    """Admin: create a driver login linked to a haulier company."""
    from urllib.parse import quote_plus

    form = await request.form()
    haulier_id_raw = form.get("haulier_id")
    name = (form.get("name") or "").strip()
    email = (form.get("email") or "").strip().lower()
    phone = (form.get("phone") or "").strip() or None
    password = form.get("password") or ""
    base = "/?section=admin"

    def redirect_error(msg: str) -> RedirectResponse:
        return RedirectResponse(url=base + "&create_login_error=" + quote_plus(msg), status_code=303)

    if not haulier_id_raw or not name or not email or not password:
        return redirect_error("All driver fields except phone are required")
    if len(password) < 6:
        return redirect_error("Password must be at least 6 characters")
    try:
        haulier_id = int(haulier_id_raw)
    except (TypeError, ValueError):
        return redirect_error("Invalid haulier selected")
    haulier = db.get(models.Haulier, haulier_id)
    if not haulier:
        return redirect_error("Haulier not found")
    if db.query(models.Driver).filter(models.Driver.email == email).first():
        return redirect_error("Driver email already used")

    vehicle_id = None
    vid_raw = form.get("vehicle_id")
    if vid_raw and str(vid_raw).strip():
        try:
            vid = int(str(vid_raw).strip())
        except (TypeError, ValueError):
            return redirect_error("Invalid vehicle")
        vv = db.get(models.Vehicle, vid)
        if not vv or vv.haulier_id != haulier_id:
            return redirect_error("Vehicle must belong to the selected company")
        vehicle_id = vid

    driver = models.Driver(
        haulier_id=haulier_id,
        vehicle_id=vehicle_id,
        name=name,
        email=email,
        phone=phone,
        password_hash=hash_password(password),
    )
    db.add(driver)
    db.commit()
    db.refresh(driver)
    base_url = str(request.base_url).rstrip("/")
    schedule_registration_emails(
        background_tasks,
        user_email=email,
        user_name=name,
        user_type="driver",
        company_name=haulier.name,
        dashboard_link=f"{base_url}/?section=find",
        tutorial_link=f"{base_url}/driver",
        vehicle_setup_link=f"{base_url}/?section=vehicles",
        admin_panel_link=f"{base_url}/admin/dashboard",
        registered_at_iso=datetime.now(timezone.utc).isoformat(),
        user_id=None,
        contact_phone=phone,
        driver_id=driver.id,
    )
    return RedirectResponse(url=base + "&create_login_ok=" + quote_plus("Driver account created"), status_code=303)


@router.post("/my-drivers", response_class=RedirectResponse)
async def create_my_driver_account(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Haulier: create a driver for their own company only."""
    from urllib.parse import quote_plus

    current_user = get_current_user_optional(request, db)
    base = "/?section=my-drivers"

    def redirect_error(msg: str) -> RedirectResponse:
        return RedirectResponse(url=base + "&driver_error=" + quote_plus(msg), status_code=303)

    if not current_user:
        return RedirectResponse(url="/login", status_code=302)
    role = (getattr(current_user, "role", None) or "").strip().lower()
    if role != "haulier":
        return redirect_error("Only haulier accounts can manage drivers")
    if not current_user.haulier_id:
        return redirect_error("Your account is not linked to a haulier company")

    form = await request.form()
    name = (form.get("name") or "").strip()
    email = (form.get("email") or "").strip().lower()
    phone = (form.get("phone") or "").strip() or None
    password = form.get("password") or ""
    if not name or not email or not password:
        return redirect_error("Name, email and password are required")
    if len(password) < 6:
        return redirect_error("Password must be at least 6 characters")
    if db.query(models.Driver).filter(models.Driver.email == email).first():
        return redirect_error("Driver email already used")

    vehicle_id = None
    vid_raw = form.get("vehicle_id")
    if vid_raw and str(vid_raw).strip():
        try:
            vid = int(str(vid_raw).strip())
        except (TypeError, ValueError):
            return redirect_error("Invalid vehicle")
        vv = db.get(models.Vehicle, vid)
        if not vv or vv.haulier_id != current_user.haulier_id:
            return redirect_error("Invalid vehicle for your company")
        vehicle_id = vid

    haulier = db.get(models.Haulier, current_user.haulier_id)
    driver = models.Driver(
        haulier_id=current_user.haulier_id,
        vehicle_id=vehicle_id,
        name=name,
        email=email,
        phone=phone,
        password_hash=hash_password(password),
    )
    db.add(driver)
    db.commit()
    db.refresh(driver)
    base_url = str(request.base_url).rstrip("/")
    schedule_registration_emails(
        background_tasks,
        user_email=email,
        user_name=name,
        user_type="driver",
        company_name=haulier.name if haulier else "Haulier",
        dashboard_link=f"{base_url}/?section=find",
        tutorial_link=f"{base_url}/driver",
        vehicle_setup_link=f"{base_url}/?section=vehicles",
        admin_panel_link=f"{base_url}/admin/dashboard",
        registered_at_iso=datetime.now(timezone.utc).isoformat(),
        user_id=None,
        contact_phone=phone,
        driver_id=driver.id,
    )
    return RedirectResponse(url=base + "&driver_ok=" + quote_plus("Driver account created"), status_code=303)


@router.post("/my-drivers/delete/{driver_id}", response_class=RedirectResponse)
def delete_my_driver_account(
    driver_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Haulier: delete a driver from their own company only."""
    from urllib.parse import quote_plus

    current_user = get_current_user_optional(request, db)
    base = "/?section=my-drivers"

    def redirect_error(msg: str) -> RedirectResponse:
        return RedirectResponse(url=base + "&driver_error=" + quote_plus(msg), status_code=303)

    if not current_user:
        return RedirectResponse(url="/login", status_code=302)
    role = (getattr(current_user, "role", None) or "").strip().lower()
    if role != "haulier":
        return redirect_error("Only haulier accounts can manage drivers")
    if not current_user.haulier_id:
        return redirect_error("Your account is not linked to a haulier company")

    driver = db.get(models.Driver, driver_id)
    if not driver or driver.haulier_id != current_user.haulier_id:
        return redirect_error("Driver not found for your company")
    db.delete(driver)
    db.commit()
    return RedirectResponse(url=base + "&driver_ok=" + quote_plus("Driver deleted"), status_code=303)


@router.post("/my-team", response_class=RedirectResponse)
async def create_company_team_user(
    request: Request,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Haulier/loader: create another office login for the same company."""
    from urllib.parse import quote_plus

    current_user = get_current_user_optional(request, db)
    base = "/?section=my-team"

    def redirect_error(msg: str) -> RedirectResponse:
        return RedirectResponse(url=base + "&team_error=" + quote_plus(msg), status_code=303)

    if not current_user:
        return RedirectResponse(url="/login", status_code=302)
    role = (getattr(current_user, "role", None) or "").strip().lower()
    if role not in ("haulier", "loader"):
        return redirect_error("Only haulier and loader accounts can manage team logins")
    if role == "haulier" and not current_user.haulier_id:
        return redirect_error("Your account is not linked to a haulier company")
    if role == "loader" and not current_user.loader_id:
        return redirect_error("Your account is not linked to a loader company")

    form = await request.form()
    name = (form.get("name") or "").strip()
    email = (form.get("email") or "").strip().lower()
    phone = (form.get("phone") or "").strip() or None
    password = form.get("password") or ""
    if not name or not email or not password:
        return redirect_error("Name, email and password are required")
    if len(password) < 6:
        return redirect_error("Password must be at least 6 characters")
    if db.query(models.User).filter(models.User.email == email).first():
        return redirect_error("That email is already used")

    new_user = models.User(
        email=email,
        password_hash=hash_password(password),
        role=role,
        full_name=name,
        phone=phone,
        haulier_id=current_user.haulier_id if role == "haulier" else None,
        loader_id=current_user.loader_id if role == "loader" else None,
    )
    db.add(new_user)
    db.commit()
    return RedirectResponse(url=base + "&team_ok=" + quote_plus("Team login created"), status_code=303)


@router.post("/my-team/delete/{user_id}", response_class=RedirectResponse)
def delete_company_team_user(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Haulier/loader: delete a team login from their own company."""
    from urllib.parse import quote_plus

    current_user = get_current_user_optional(request, db)
    base = "/?section=my-team"

    def redirect_error(msg: str) -> RedirectResponse:
        return RedirectResponse(url=base + "&team_error=" + quote_plus(msg), status_code=303)

    if not current_user:
        return RedirectResponse(url="/login", status_code=302)
    if current_user.id == user_id:
        return redirect_error("You cannot delete your own login")
    role = (getattr(current_user, "role", None) or "").strip().lower()
    if role not in ("haulier", "loader"):
        return redirect_error("Only haulier and loader accounts can manage team logins")

    target = db.get(models.User, user_id)
    if not target:
        return redirect_error("User not found")
    if (getattr(target, "role", None) or "").strip().lower() != role:
        return redirect_error("User not found in your company")
    if role == "haulier":
        if not current_user.haulier_id or target.haulier_id != current_user.haulier_id:
            return redirect_error("User not found in your company")
    else:
        if not current_user.loader_id or target.loader_id != current_user.loader_id:
            return redirect_error("User not found in your company")

    db.delete(target)
    db.commit()
    return RedirectResponse(url=base + "&team_ok=" + quote_plus("Team login deleted"), status_code=303)


@router.post("/delete-driver/{driver_id}", response_class=RedirectResponse)
def delete_driver_account(
    driver_id: int,
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin),
) -> RedirectResponse:
    """Admin: delete a driver account."""
    driver = db.get(models.Driver, driver_id)
    if not driver:
        return RedirectResponse(url="/?section=admin&create_login_error=Driver+not+found", status_code=303)
    db.delete(driver)
    db.commit()
    return RedirectResponse(url="/?section=admin&create_login_ok=Driver+deleted", status_code=303)

@router.post("/interest", response_class=RedirectResponse)
async def express_interest(
    request: Request,
    background_tasks: BackgroundTasks,
    load_id: Optional[str] = Form(None),
    vehicle_id: Optional[str] = Form(None),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Haulier expresses interest in a load - creates a match suggestion.

    Always returns a redirect (never implicit None → 200 empty). Form fields are
    optional at validation so bad/missing IDs redirect instead of 422 JSON.
    """
    from urllib.parse import quote_plus

    def _redirect_matches(msg: str) -> RedirectResponse:
        return RedirectResponse(
            url="/?section=matches&msg=" + quote_plus(msg),
            status_code=303,
        )

    try:
        lid = int(str(load_id).strip()) if load_id is not None and str(load_id).strip() else None
        vid = int(str(vehicle_id).strip()) if vehicle_id is not None and str(vehicle_id).strip() else None
    except (TypeError, ValueError):
        return _redirect_matches("invalid_interest")

    if lid is None or vid is None:
        return _redirect_matches("invalid_interest")

    current_user = get_current_user_optional(request, db)
    driver_actor = get_current_driver_optional(request, db) if current_user is None else None
    if current_user is None and driver_actor is None:
        return RedirectResponse(url="/login", status_code=303)

    haulier_id: Optional[int] = None
    if current_user and getattr(current_user, "role", None) == "haulier":
        haulier_id = current_user.haulier_id
    elif driver_actor is not None:
        haulier_id = driver_actor.haulier_id
    else:
        return RedirectResponse(url="/?section=find", status_code=303)

    if haulier_id is None:
        return _redirect_matches("invalid_interest")

    vehicle = db.get(models.Vehicle, vid)
    if not vehicle or vehicle.haulier_id != haulier_id:
        return _redirect_matches("not_your_vehicle")

    if driver_actor is not None and driver_actor.vehicle_id and driver_actor.vehicle_id != vid:
        return _redirect_matches("not_your_vehicle")

    if vehicle_availability_svc.vehicle_has_active_job(db, vid):
        return _redirect_matches("vehicle_on_job")

    existing = db.query(models.LoadInterest).filter(
        models.LoadInterest.load_id == lid,
        models.LoadInterest.vehicle_id == vid,
        models.LoadInterest.haulier_id == haulier_id,
    ).first()

    if existing:
        if driver_actor is not None:
            existing.expressing_driver_id = driver_actor.id
            db.add(existing)
            db.commit()
        return RedirectResponse(url="/?section=matches&msg=already_interested", status_code=303)

    interest = models.LoadInterest(
        load_id=lid,
        vehicle_id=vid,
        haulier_id=haulier_id,
        status="expressed",
        expressing_driver_id=driver_actor.id if driver_actor else None,
    )
    db.add(interest)
    db.commit()
    db.refresh(interest)

    try:
        from app.services.in_app_notifications import record_loader_haulier_interest_notifications

        load_row = db.get(models.Load, lid)
        record_loader_haulier_interest_notifications(
            db,
            load=load_row,
            planned_load=None,
            haulier_id=haulier_id,
            vehicle_id=vid,
        )
    except Exception as e:
        print(f"[NOTIFY] record_loader_haulier_interest_notifications failed: {e}")

    try:
        from app.services.email_sender import schedule_loader_interest_email

        schedule_loader_interest_email(background_tasks, interest.id)
    except Exception as e:
        print(f"[EMAIL] schedule_loader_interest_email failed: {e}")

    load_row = db.get(models.Load, lid)
    pct = float(get_settings().platform_fee_percent or 8)
    q_parts = ["section=matches", "interest_ok=1"]
    if load_row:
        q_parts.append("ipickup=" + quote_plus(load_row.pickup_postcode or ""))
        q_parts.append("idelivery=" + quote_plus(load_row.delivery_postcode or ""))
        if load_row.budget_gbp is not None:
            b = float(load_row.budget_gbp)
            fee = round(b * (pct / 100.0), 2)
            net = round(b - fee, 2)
            q_parts.append(f"ibudget={b:.2f}")
            q_parts.append(f"ifee={fee:.2f}")
            q_parts.append(f"inet={net:.2f}")
    return RedirectResponse(url="/?" + "&".join(q_parts), status_code=303)