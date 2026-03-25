from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Form, Query, Request, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import delete as sa_delete, update as sa_update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import models
from app.auth import get_current_admin, get_current_user, get_current_user_optional, hash_password
from app.config import get_settings
from app.database import get_db
from app.services.matching import find_matching_loads


router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

# Folder containing CSV templates (project root / static / templates)
TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent / "static" / "templates"


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
        {"request": request, "last_updated": "March 2026"},
    )


@router.get("/privacy", response_class=HTMLResponse)
def privacy_page(request: Request) -> HTMLResponse:
    """Public Privacy Policy (UK GDPR). No login required."""
    return templates.TemplateResponse(
        "privacy.html",
        {"request": request, "last_updated": "March 2026"},
    )


@router.get("/confidentiality", response_class=HTMLResponse)
def confidentiality_page(request: Request) -> HTMLResponse:
    """Public Confidentiality & Non-Disclosure page. No login required."""
    return templates.TemplateResponse(
        "confidentiality.html",
        {"request": request, "last_updated": "March 2026"},
    )


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
    try:
        data = lookup_vehicle_by_registration(reg)
    except DvlaError as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    if not data:
        return JSONResponse({"error": "Vehicle not found", "vehicle_type": "rigid"}, status_code=200)
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


def _load_interests_display(load_interests_list, db: Session):
    """Build list of {interest, shipper, collection, delivery, label} for template."""
    out = []
    for i in load_interests_list:
        shipper = collection = delivery = label = ""
        if i.load_id:
            load = db.get(models.Load, i.load_id)
            if load:
                shipper = load.shipper_name or ""
                collection = load.pickup_postcode or ""
                delivery = load.delivery_postcode or ""
                label = "Load %d" % load.id
        elif i.planned_load_id:
            pl = db.get(models.PlannedLoad, i.planned_load_id)
            if pl:
                shipper = pl.shipper_name or ""
                collection = pl.pickup_postcode or ""
                delivery = pl.delivery_postcode or ""
                label = "Planned %d" % pl.id
        out.append({
            "interest": i,
            "shipper": shipper,
            "collection": collection,
            "delivery": delivery,
            "label": label or ("Load %s" % (i.load_id or "") if i.load_id else "Planned %s" % (i.planned_load_id or "")),
        })
    return out


def _require_user_or_login(request: Request, db: Session) -> Optional[RedirectResponse]:
    """Any logged-in role may open the dashboard; guests go to /login."""
    from app.auth import get_current_user_optional

    if get_current_user_optional(request, db) is None:
        return RedirectResponse(url="/login", status_code=302)
    return None


@router.get("/", response_class=HTMLResponse)
def home(
    request: Request,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    from app.auth import get_current_user_optional

    login_redir = _require_user_or_login(request, db)
    if login_redir is not None:
        return login_redir
    current_user = get_current_user_optional(request, db)
    
    # Role-based filtering
    if current_user and current_user.loader_id:
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
        
        # Only jobs for their loads
        jobs = db.query(models.BackhaulJob).filter(models.BackhaulJob.load_id.in_(load_ids)).order_by(models.BackhaulJob.matched_at.desc()).all() if load_ids else []
        payments = db.query(models.Payment).filter(models.Payment.backhaul_job_id.in_([j.id for j in jobs])).all() if jobs else []
        
        # No vehicles, hauliers, or routes for loaders
        hauliers = []
        vehicles = []
        haulier_routes = []
        users = []
        drivers = []
    elif current_user and current_user.haulier_id:
        # HAULIER VIEW - only their vehicles and jobs
        haulier = db.get(models.Haulier, current_user.haulier_id)
        vehicles = db.query(models.Vehicle).filter(models.Vehicle.haulier_id == haulier.id).order_by(models.Vehicle.registration).all()
        haulier_routes = db.query(models.HaulierRoute).filter(models.HaulierRoute.haulier_id == haulier.id).all()
        
        vehicle_ids = [v.id for v in vehicles]
        jobs = db.query(models.BackhaulJob).filter(models.BackhaulJob.vehicle_id.in_(vehicle_ids)).order_by(models.BackhaulJob.matched_at.desc()).all() if vehicle_ids else []
        payments = db.query(models.Payment).filter(models.Payment.backhaul_job_id.in_([j.id for j in jobs])).all() if jobs else []
        
        # Show all loads (for searching)
        loads = db.query(models.Load).order_by(models.Load.created_at.desc()).all()
        
        # Show interests they've expressed
        load_interests = db.query(models.LoadInterest).filter(models.LoadInterest.haulier_id == haulier.id).all()
        
        # No loader-specific data
        hauliers = [haulier]  # Just their own company
        planned_loads = []
        users = []
        drivers = db.query(models.Driver).filter(models.Driver.haulier_id == haulier.id).order_by(models.Driver.name).all()
    else:
        # ADMIN VIEW - see everything
        hauliers = db.query(models.Haulier).order_by(models.Haulier.created_at.desc()).all()
        vehicles = db.query(models.Vehicle).order_by(models.Vehicle.created_at.desc()).all()
        loads = db.query(models.Load).order_by(models.Load.created_at.desc()).all()
        jobs = db.query(models.BackhaulJob).order_by(models.BackhaulJob.matched_at.desc()).all()
        payments = db.query(models.Payment).order_by(models.Payment.created_at.desc()).all()
        planned_loads = db.query(models.PlannedLoad).order_by(models.PlannedLoad.created_at.desc()).all()
        haulier_routes = db.query(models.HaulierRoute).order_by(models.HaulierRoute.created_at.desc()).all()
        load_interests = db.query(models.LoadInterest).order_by(models.LoadInterest.created_at.desc()).all()
        
        users = db.query(models.User).order_by(models.User.email).all()
        drivers = db.query(models.Driver).order_by(models.Driver.name).all()

    load_interests_display = _load_interests_display(load_interests, db)

    uploaded = request.query_params.get("uploaded")
    errors_count = request.query_params.get("errors")
    upload_type = request.query_params.get("upload_type")
    delete_error = request.query_params.get("delete_error")
    deleted = request.query_params.get("deleted")
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
    if current_user and current_user.haulier_id:
        haulier_profile = db.get(models.Haulier, current_user.haulier_id)
    elif current_user and current_user.loader_id:
        loader_profile = db.get(models.Loader, current_user.loader_id)

    _scust, _sconn, _stest = _stripe_dashboard_urls()

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
            "vehicles": vehicles,
            "loads": loads,
            "jobs": jobs,
            "payments": payments,
            "planned_loads": planned_loads,
            "haulier_routes": haulier_routes,
            "load_interests": load_interests,
            "load_interests_display": load_interests_display,
            "uploaded": int(uploaded) if uploaded and uploaded.isdigit() else None,
            "upload_errors": int(errors_count) if errors_count and errors_count.isdigit() else None,
            "upload_type": upload_type or "",
            "delete_error": delete_error,
            "deleted": deleted,
            "create_login_error": create_login_error,
            "create_login_ok": create_login_ok,
            "open_loads_count": open_loads_count,
            "total_payout": total_payout,
            "matching_results": None,
            "find_vehicle_id": "",
            "find_origin_postcode": "",
            "postcode_lookup_failed": False,
            "match_diagnostic": None,
            "platform_fee_percent": get_settings().platform_fee_percent,
            "loader_flat_fee_gbp": get_settings().loader_flat_fee_gbp,
            "pallet_volume_m3": get_settings().pallet_volume_m3,
            "current_user_email": (current_user.email if current_user else ""),
            "current_user": current_user,
        },
    )
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
        status=models.LoadStatusEnum.OPEN.value,
        loader_id=loader_id,
    )
    db.add(load)
    db.commit()
    
    return RedirectResponse(url="/?section=loads&load_added=1", status_code=303)


def _match_diagnostic(vehicle_id: int, origin_postcode: str, db: Session):
    """Explain why each open load did or didn't match (for 'no matches' debugging)."""
    """Explain why each open load did or didn't match (for 'no matches' debugging)."""
    from app.services.geocode import get_lat_lon
    from app.services.distance import haversine_miles
    from app.config import get_settings

    vehicle = db.get(models.Vehicle, vehicle_id)
    if not vehicle:
        return {"origin_ok": False, "origin_reason": "Vehicle not found", "loads": []}
    origin_ll = get_lat_lon(origin_postcode)
    if not origin_ll:
        return {"origin_ok": False, "origin_reason": "Postcode lookup failed", "loads": []}
    radius = get_settings().default_backhaul_radius_miles
    open_loads = (
        db.query(models.Load)
        .filter(models.Load.status == models.LoadStatusEnum.OPEN.value)
        .all()
    )
    rows = []
    for load in open_loads:
        pickup_ll = get_lat_lon(load.pickup_postcode)
        if not pickup_ll:
            rows.append({"load": load, "reason": "Pickup postcode lookup failed", "distance_miles": None})
            continue
        dist = round(haversine_miles(origin_ll[0], origin_ll[1], pickup_ll[0], pickup_ll[1]), 1)
        if dist > radius:
            rows.append({"load": load, "reason": f"{dist} mi (over {radius} mi limit)", "distance_miles": dist})
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
    from app.auth import get_current_user_optional

    login_redir = _require_user_or_login(request, db)
    if login_redir is not None:
        return login_redir
    from app.services.geocode import get_lat_lon
    from app.services.matching import find_matching_loads_along_route
    current_user = get_current_user_optional(request, db)

    vehicle_id_raw = request.query_params.get("vehicle_id", "").strip()
    raw_origin = (request.query_params.get("origin_postcode") or "").strip()
    raw_dest = (request.query_params.get("destination_postcode") or "").strip()
    origin_postcode = " ".join(raw_origin.split()).strip() if raw_origin else ""  # collapse spaces
    destination_postcode = " ".join(raw_dest.split()).strip() if raw_dest else ""  # collapse spaces

    matching_results = None
    postcode_lookup_failed = False
    match_diagnostic = None
    if vehicle_id_raw and origin_postcode:
        try:
            vehicle_id = int(vehicle_id_raw)
            # UNIFIED SMART SEARCH: Find loads near pickup + loads along route home
            if destination_postcode:
                # Route search: finds loads along entire journey corridor
                route_pairs = find_matching_loads_along_route(vehicle_id, origin_postcode, destination_postcode, db)
                # Also find loads near origin (might catch some the route missed)
                origin_pairs = find_matching_loads(vehicle_id, origin_postcode, db)
                # Merge and deduplicate by load_id
                all_pairs = route_pairs + origin_pairs
                seen_load_ids = set()
                unique_pairs = []
                for load, dist, is_perfect, reasons in all_pairs:
                    if load.id not in seen_load_ids:
                        seen_load_ids.add(load.id)
                        unique_pairs.append((load, dist, is_perfect, reasons))
                pairs = unique_pairs
            else:
                # Just origin search if no destination
                pairs = find_matching_loads(vehicle_id, origin_postcode, db)
            
            matching_results = [{"load": load, "distance_miles": dist, "is_perfect_match": is_perfect, "mismatch_reasons": reasons} for load, dist, is_perfect, reasons in pairs]
            if not matching_results:
                open_count = db.query(models.Load).filter(models.Load.status == models.LoadStatusEnum.OPEN.value).count()
                if open_count > 0:
                    if get_lat_lon(origin_postcode) is None:
                        postcode_lookup_failed = True
                    match_diagnostic = _match_diagnostic(vehicle_id, origin_postcode, db)
        except ValueError:
            matching_results = []

    hauliers = db.query(models.Haulier).order_by(models.Haulier.created_at.desc()).all()
    vehicles = db.query(models.Vehicle).order_by(models.Vehicle.created_at.desc()).all()
    loads = db.query(models.Load).order_by(models.Load.created_at.desc()).all()
    jobs = db.query(models.BackhaulJob).order_by(models.BackhaulJob.matched_at.desc()).all()
    payments = db.query(models.Payment).order_by(models.Payment.created_at.desc()).all()
    planned_loads = db.query(models.PlannedLoad).order_by(models.PlannedLoad.created_at.desc()).all()
    haulier_routes = db.query(models.HaulierRoute).order_by(models.HaulierRoute.created_at.desc()).all()
    load_interests = db.query(models.LoadInterest).order_by(models.LoadInterest.created_at.desc()).all()
    load_interests_display = _load_interests_display(load_interests, db)
    users = db.query(models.User).order_by(models.User.email).all()
    drivers = db.query(models.Driver).order_by(models.Driver.name).all()
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
    if current_user and current_user.haulier_id:
        haulier_profile = db.get(models.Haulier, current_user.haulier_id)
    elif current_user and current_user.loader_id:
        loader_profile = db.get(models.Loader, current_user.loader_id)

    _scust, _sconn, _stest = _stripe_dashboard_urls()

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
            "payments": payments,
            "planned_loads": planned_loads,
            "haulier_routes": haulier_routes,
            "load_interests": load_interests,
            "load_interests_display": load_interests_display,
            "haulier_profile": haulier_profile,
            "loader_profile": loader_profile,
            "stripe_dashboard_customers_root": _scust,
            "stripe_dashboard_connect_accounts_root": _sconn,
            "stripe_is_test_mode": _stest,
            "uploaded": None,
            "upload_errors": None,
            "upload_type": "",
            "delete_error": None,
            "deleted": None,
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
            "platform_fee_percent": get_settings().platform_fee_percent,
            "loader_flat_fee_gbp": get_settings().loader_flat_fee_gbp,
            "pallet_volume_m3": get_settings().pallet_volume_m3,
            "current_user_email": (current_user.email if current_user else ""),
            "current_user": current_user,
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
    form = dict(await request.form())
    try:
        if current_user.role == "admin":
            haulier_id_raw = form.get("haulier_id")
            if not haulier_id_raw:
                return RedirectResponse(
                    url="/?section=vehicles&delete_error=Please+pick+a+company",
                    status_code=303,
                )
            haulier_id = int(haulier_id_raw)
        elif current_user.role == "haulier" and current_user.haulier_id:
            haulier_id = int(current_user.haulier_id)
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

    base_postcode = (form.get("base_postcode") or "").strip().upper() or None
    try:
        vehicle = models.Vehicle(
            haulier_id=haulier_id,
            registration=registration,
            vehicle_type=vehicle_type,
            trailer_type=trailer_type,
            base_postcode=base_postcode,
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
    if base_postcode:
        try:
            from app.services.alert_stream import notify_matching_loads_for_vehicle
            notify_matching_loads_for_vehicle(
                vehicle.id, base_postcode, haulier_id, db, origin_label="base",
            )
        except Exception:
            pass

    return RedirectResponse(url="/?section=vehicles", status_code=303)


def _parse_float(s, default=None):
    if s is None or (isinstance(s, str) and not s.strip()):
        return default
    try:
        return float(s)
    except (TypeError, ValueError):
        return default

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


@router.post("/planned-loads", response_class=RedirectResponse)
async def create_planned_load_form(
    request: Request,
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin),
) -> RedirectResponse:
    """Loader: add a weekly/monthly planned load. Matching runs automatically."""
    form = dict(await request.form())
    try:
        day = int(form.get("day_of_week", 0))
    except (TypeError, ValueError):
        day = 0
    pl = models.PlannedLoad(
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
    return RedirectResponse(url="/", status_code=303)


@router.post("/haulier-routes", response_class=RedirectResponse)
async def create_haulier_route_form(
    request: Request,
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin),
) -> RedirectResponse:
    """Haulier: add a weekly/monthly empty leg. Matching runs automatically."""
    form = dict(await request.form())
    haulier_id = int(form.get("haulier_id"))
    vehicle_id = int(form.get("vehicle_id"))
    try:
        day = int(form.get("day_of_week", 0))
    except (TypeError, ValueError):
        day = 0
    route = models.HaulierRoute(
        haulier_id=haulier_id,
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
    return RedirectResponse(url="/", status_code=303)


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
    """Delete a vehicle (only if not used in jobs or planned routes). Admin or owning haulier."""
    from app.auth import get_current_user_optional

    current_user = get_current_user_optional(request, db)
    if not current_user:
        return RedirectResponse(url="/login", status_code=302)
    vehicle = db.get(models.Vehicle, vehicle_id)
    if not vehicle:
        return RedirectResponse(url="/?delete_error=Vehicle+not+found", status_code=303)
    if current_user.role == "haulier":
        if not current_user.haulier_id or vehicle.haulier_id != current_user.haulier_id:
            return RedirectResponse(url="/?section=vehicles&delete_error=Not+authorized", status_code=303)
    elif current_user.role != "admin":
        return RedirectResponse(url="/?section=vehicles&delete_error=Not+authorized", status_code=303)
    if db.query(models.BackhaulJob).filter(models.BackhaulJob.vehicle_id == vehicle_id).first():
        return RedirectResponse(url="/?delete_error=Vehicle+has+jobs", status_code=303)
    if db.query(models.HaulierRoute).filter(models.HaulierRoute.vehicle_id == vehicle_id).first():
        return RedirectResponse(url="/?delete_error=Remove+from+planned+routes+first", status_code=303)
    try:
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
def delete_job_form(
    job_id: int,
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin),
) -> RedirectResponse:
    """Admin: cancel/delete a backhaul job. Resets load to open and interest to suggested."""
    job = db.get(models.BackhaulJob, job_id)
    if not job:
        return RedirectResponse(url="/?section=matches&delete_error=Job+not+found", status_code=303)
    
    # Reset load status to open
    if job.load_id:
        load = db.get(models.Load, job.load_id)
        if load:
            load.status = models.LoadStatusEnum.OPEN.value
    
    # Reset interest status to suggested (so haulier can try again)
    interest = db.query(models.LoadInterest).filter(
        models.LoadInterest.load_id == job.load_id,
        models.LoadInterest.vehicle_id == job.vehicle_id,
        models.LoadInterest.status == "accepted"
    ).first()
    if interest:
        interest.status = "suggested"
    
    # Delete related records
    db.query(models.Payment).filter(models.Payment.backhaul_job_id == job_id).delete()
    db.query(models.POD).filter(models.POD.backhaul_job_id == job_id).delete()
    db.delete(job)
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
        job.driver_id = driver.id

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
    _admin=Depends(get_current_admin),
) -> RedirectResponse:
    """Haulier: express interest in a suggested load or planned load."""
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
    if existing:
        existing.status = "expressed"
        db.commit()
        interest = existing
    else:
        interest = models.LoadInterest(
            haulier_id=haulier_id,
            vehicle_id=vehicle_id,
            load_id=load_id,
            planned_load_id=planned_load_id,
            status="expressed",
        )
        db.add(interest)
        db.commit()
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
    _admin=Depends(get_current_admin),
) -> RedirectResponse:
    """Loader: accept haulier's interest and create a backhaul job."""
    form = dict(await request.form())
    interest_id = int(form.get("interest_id"))
    
    # Get the interest record
    interest = db.get(models.LoadInterest, interest_id)
    if not interest:
        return RedirectResponse(url="/?section=matches", status_code=303)
    
    # Update status to accepted
    interest.status = "accepted"
    db.commit()
    
    # Create the BackhaulJob
    from datetime import datetime, timezone
    job = models.BackhaulJob(
        vehicle_id=interest.vehicle_id,
        load_id=interest.load_id,
        matched_at=datetime.now(timezone.utc),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    
    # Update load status to matched
    if interest.load_id:
        load = db.get(models.Load, interest.load_id)
        if load:
            load.status = models.LoadStatusEnum.MATCHED.value
            db.commit()
    
    try:
        from app.services.email_sender import schedule_haulier_job_email

        schedule_haulier_job_email(background_tasks, job.id)
    except Exception as e:
        print(f"[EMAIL] schedule_haulier_job_email failed: {e}")

    return RedirectResponse(url="/?section=matches", status_code=303)


@router.post("/create-haulier-account", response_class=RedirectResponse)
async def create_haulier_account(
    request: Request,
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
    return RedirectResponse(url=base + "&create_login_ok=" + quote_plus("Company + login created. They can log in and add vehicles."), status_code=303)


@router.post("/create-haulier-login", response_class=RedirectResponse)
async def create_haulier_login(
    request: Request,
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
    return RedirectResponse(url=base + "&create_login_ok=" + quote_plus("Haulier login created"), status_code=303)


@router.post("/create-loader-account", response_class=RedirectResponse)
async def create_loader_account(
    request: Request,
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
    return RedirectResponse(url=base + "&create_login_ok=" + quote_plus("Loader account created"), status_code=303)


@router.post("/create-driver", response_class=RedirectResponse)
async def create_driver_account(
    request: Request,
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

    driver = models.Driver(
        haulier_id=haulier_id,
        name=name,
        email=email,
        phone=phone,
        password_hash=hash_password(password),
    )
    db.add(driver)
    db.commit()
    return RedirectResponse(url=base + "&create_login_ok=" + quote_plus("Driver account created"), status_code=303)


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
    if not current_user or current_user.role != "haulier":
        return RedirectResponse(url="/login", status_code=303)

    vehicle = db.get(models.Vehicle, vid)
    if not vehicle or vehicle.haulier_id != current_user.haulier_id:
        return _redirect_matches("not_your_vehicle")
    
    existing = db.query(models.LoadInterest).filter(
        models.LoadInterest.load_id == lid,
        models.LoadInterest.vehicle_id == vid,
        models.LoadInterest.haulier_id == current_user.haulier_id
    ).first()

    if existing:
        return RedirectResponse(url="/?section=matches&msg=already_interested", status_code=303)

    interest = models.LoadInterest(
        load_id=lid,
        vehicle_id=vid,
        haulier_id=current_user.haulier_id,
        status="expressed",
    )
    db.add(interest)
    db.commit()
    db.refresh(interest)

    try:
        from app.services.email_sender import schedule_loader_interest_email

        schedule_loader_interest_email(background_tasks, interest.id)
    except Exception as e:
        print(f"[EMAIL] schedule_loader_interest_email failed: {e}")

    return RedirectResponse(url="/?section=matches", status_code=303)