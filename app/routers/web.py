from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app import models
from app.config import get_settings
from app.database import get_db
from app.services.matching import find_matching_loads


router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

# Folder containing CSV templates (project root / static / templates)
TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent / "static" / "templates"


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


@router.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    hauliers = db.query(models.Haulier).order_by(models.Haulier.created_at.desc()).all()
    vehicles = db.query(models.Vehicle).order_by(models.Vehicle.created_at.desc()).all()
    loads = db.query(models.Load).order_by(models.Load.created_at.desc()).all()
    jobs = db.query(models.BackhaulJob).order_by(models.BackhaulJob.matched_at.desc()).all()
    payments = db.query(models.Payment).order_by(models.Payment.created_at.desc()).all()
    planned_loads = db.query(models.PlannedLoad).order_by(models.PlannedLoad.created_at.desc()).all()
    haulier_routes = db.query(models.HaulierRoute).order_by(models.HaulierRoute.created_at.desc()).all()
    load_interests = db.query(models.LoadInterest).order_by(models.LoadInterest.created_at.desc()).all()

    uploaded = request.query_params.get("uploaded")
    errors_count = request.query_params.get("errors")
    upload_type = request.query_params.get("upload_type")

    return templates.TemplateResponse(
        "home.html",
        {
            "request": request,
            "hauliers": hauliers,
            "vehicles": vehicles,
            "loads": loads,
            "jobs": jobs,
            "payments": payments,
            "planned_loads": planned_loads,
            "haulier_routes": haulier_routes,
            "load_interests": load_interests,
            "uploaded": int(uploaded) if uploaded and uploaded.isdigit() else None,
            "upload_errors": int(errors_count) if errors_count and errors_count.isdigit() else None,
            "upload_type": upload_type or "",
            "matching_results": None,
            "find_vehicle_id": "",
            "find_origin_postcode": "",
            "platform_fee_percent": get_settings().platform_fee_percent,
        },
    )


@router.get("/find-backhaul", response_class=HTMLResponse)
def find_backhaul_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    """Run smart matching and render home with matching_results (vehicle_id + origin_postcode from query)."""
    vehicle_id_raw = request.query_params.get("vehicle_id", "").strip()
    origin_postcode = (request.query_params.get("origin_postcode") or "").strip()

    matching_results = None
    if vehicle_id_raw and origin_postcode:
        try:
            vehicle_id = int(vehicle_id_raw)
            pairs = find_matching_loads(vehicle_id, origin_postcode, db)
            matching_results = [{"load": load, "distance_miles": dist} for load, dist in pairs]
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

    return templates.TemplateResponse(
        "home.html",
        {
            "request": request,
            "hauliers": hauliers,
            "vehicles": vehicles,
            "loads": loads,
            "jobs": jobs,
            "payments": payments,
            "planned_loads": planned_loads,
            "haulier_routes": haulier_routes,
            "load_interests": load_interests,
            "uploaded": None,
            "upload_errors": None,
            "upload_type": "",
            "matching_results": matching_results,
            "find_vehicle_id": vehicle_id_raw,
            "find_origin_postcode": origin_postcode,
            "platform_fee_percent": get_settings().platform_fee_percent,
        },
    )


@router.post("/hauliers", response_class=HTMLResponse)
async def create_haulier_form(
    request: Request,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    form = dict(await request.form())
    name = form.get("name") or ""
    email = form.get("contact_email") or ""
    phone = form.get("contact_phone") or ""

    haulier = models.Haulier(name=name, contact_email=email, contact_phone=phone)
    db.add(haulier)
    db.commit()

    return RedirectResponse(url="/", status_code=303)


@router.post("/vehicles", response_class=HTMLResponse)
async def create_vehicle_form(
    request: Request,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    form = dict(await request.form())
    haulier_id = int(form.get("haulier_id"))
    registration = (form.get("registration") or "").upper()
    vehicle_type = form.get("vehicle_type") or "rigid"
    trailer_type = (form.get("trailer_type") or "").strip() or None

    vehicle = models.Vehicle(
        haulier_id=haulier_id,
        registration=registration,
        vehicle_type=vehicle_type,
        trailer_type=trailer_type,
    )
    db.add(vehicle)
    db.commit()

    return RedirectResponse(url="/", status_code=303)


@router.post("/loads", response_class=HTMLResponse)
async def create_load_form(
    request: Request,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    form = dict(await request.form())
    shipper_name = form.get("shipper_name") or ""
    pickup_postcode = (form.get("pickup_postcode") or "").upper()
    delivery_postcode = (form.get("delivery_postcode") or "").upper()

    load = models.Load(
        shipper_name=shipper_name,
        pickup_postcode=pickup_postcode,
        delivery_postcode=delivery_postcode,
        pickup_window_start=datetime.utcnow(),
        pickup_window_end=datetime.utcnow(),
    )
    db.add(load)
    db.commit()

    return RedirectResponse(url="/", status_code=303)


@router.post("/upload", response_class=RedirectResponse)
async def upload_file_form(
    request: Request,
    db: Session = Depends(get_db),
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

    return RedirectResponse(
        url=f"/?uploaded={created}&errors={len(errs)}&upload_type={upload_type}",
        status_code=303,
    )


@router.post("/planned-loads", response_class=RedirectResponse)
async def create_planned_load_form(
    request: Request,
    db: Session = Depends(get_db),
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
    from app.services.alert_stream import notify_route_match
    from app.services.matching import planned_load_matches_route
    for pl in db.query(models.PlannedLoad).all():
        if planned_load_matches_route(pl, route, db):
            notify_route_match(pl, route, db)
    return RedirectResponse(url="/", status_code=303)


@router.post("/show-interest", response_class=RedirectResponse)
async def show_interest_form(
    request: Request,
    db: Session = Depends(get_db),
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
    return RedirectResponse(url="/", status_code=303)

