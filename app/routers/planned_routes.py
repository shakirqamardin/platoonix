"""
Planned weekly/monthly routes: loaders post planned loads, hauliers post empty legs.
System matches and alerts hauliers to show interest.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app import models, schemas
from app.database import get_db
from app.services.alert_stream import notify_route_match
from app.services.matching import find_route_matches, planned_load_matches_route

router = APIRouter()

DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _run_matching_for_new_planned_load(planned_load: models.PlannedLoad, db: Session) -> None:
    """Find haulier routes that match this planned load; notify and create suggested interests."""
    routes = db.query(models.HaulierRoute).all()
    for route in routes:
        if planned_load_matches_route(planned_load, route, db):
            notify_route_match(planned_load, route, db)


def _run_matching_for_new_route(route: models.HaulierRoute, db: Session) -> None:
    """Find planned loads that match this route; notify and create suggested interests."""
    planned_loads = db.query(models.PlannedLoad).all()
    for pl in planned_loads:
        if planned_load_matches_route(pl, route, db):
            notify_route_match(pl, route, db)


# ---- Planned loads (loaders) ----

@router.post("/planned-loads", response_model=schemas.PlannedLoadRead, status_code=status.HTTP_201_CREATED)
def create_planned_load(
    body: schemas.PlannedLoadCreate,
    db: Session = Depends(get_db),
) -> models.PlannedLoad:
    """Loader adds a recurring/planned load (e.g. every Tuesday). Matching runs automatically."""
    pl = models.PlannedLoad(
        shipper_name=body.shipper_name,
        pickup_postcode=body.pickup_postcode.strip().upper(),
        delivery_postcode=body.delivery_postcode.strip().upper(),
        day_of_week=body.day_of_week,
        weight_kg=body.weight_kg,
        volume_m3=body.volume_m3,
        requirements=body.requirements,
        budget_gbp=body.budget_gbp,
        recurrence=body.recurrence or "weekly",
    )
    db.add(pl)
    db.commit()
    db.refresh(pl)
    _run_matching_for_new_planned_load(pl, db)
    return pl


@router.get("/planned-loads", response_model=list[schemas.PlannedLoadRead])
def list_planned_loads(db: Session = Depends(get_db)) -> list[models.PlannedLoad]:
    return db.query(models.PlannedLoad).order_by(models.PlannedLoad.created_at.desc()).all()


@router.get("/planned-loads/{id}", response_model=schemas.PlannedLoadRead)
def get_planned_load(id: int, db: Session = Depends(get_db)) -> models.PlannedLoad:
    pl = db.get(models.PlannedLoad, id)
    if not pl:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Planned load not found")
    return pl


# ---- Haulier routes (empty legs) ----

@router.post("/haulier-routes", response_model=schemas.HaulierRouteRead, status_code=status.HTTP_201_CREATED)
def create_haulier_route(
    body: schemas.HaulierRouteCreate,
    db: Session = Depends(get_db),
) -> models.HaulierRoute:
    """Haulier adds a recurring empty leg (e.g. every Tuesday I'm empty at X). Matching runs automatically."""
    vehicle = db.get(models.Vehicle, body.vehicle_id)
    if not vehicle:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Vehicle not found")
    if vehicle.haulier_id != body.haulier_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Vehicle does not belong to this haulier")
    route = models.HaulierRoute(
        haulier_id=body.haulier_id,
        vehicle_id=body.vehicle_id,
        empty_at_postcode=body.empty_at_postcode.strip().upper(),
        day_of_week=body.day_of_week,
        recurrence=body.recurrence or "weekly",
    )
    db.add(route)
    db.commit()
    db.refresh(route)
    _run_matching_for_new_route(route, db)
    return route


@router.get("/haulier-routes", response_model=list[schemas.HaulierRouteRead])
def list_haulier_routes(
    haulier_id: Optional[int] = None,
    db: Session = Depends(get_db),
) -> list[models.HaulierRoute]:
    q = db.query(models.HaulierRoute).order_by(models.HaulierRoute.created_at.desc())
    if haulier_id is not None:
        q = q.filter(models.HaulierRoute.haulier_id == haulier_id)
    return q.all()


@router.get("/haulier-routes/{id}", response_model=schemas.HaulierRouteRead)
def get_haulier_route(id: int, db: Session = Depends(get_db)) -> models.HaulierRoute:
    route = db.get(models.HaulierRoute, id)
    if not route:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Haulier route not found")
    return route


# ---- Show interest ----

@router.post("/load-interests", response_model=schemas.LoadInterestRead, status_code=status.HTTP_201_CREATED)
def show_interest(
    body: schemas.LoadInterestCreate,
    db: Session = Depends(get_db),
) -> models.LoadInterest:
    """Haulier expresses interest in a load or planned load. Use status='expressed'."""
    if body.load_id is None and body.planned_load_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Provide load_id or planned_load_id")
    existing = (
        db.query(models.LoadInterest)
        .filter(
            models.LoadInterest.haulier_id == body.haulier_id,
            models.LoadInterest.vehicle_id == body.vehicle_id,
            models.LoadInterest.load_id == body.load_id,
            models.LoadInterest.planned_load_id == body.planned_load_id,
        )
        .first()
    )
    if existing:
        prev_status = existing.status
        existing.status = body.status
        db.commit()
        db.refresh(existing)
        result = existing
    else:
        prev_status = None
        interest = models.LoadInterest(
            haulier_id=body.haulier_id,
            vehicle_id=body.vehicle_id,
            load_id=body.load_id,
            planned_load_id=body.planned_load_id,
            expressing_driver_id=body.expressing_driver_id,
            status=body.status or "expressed",
        )
        db.add(interest)
        db.commit()
        db.refresh(interest)
        result = interest

    new_status = result.status
    if new_status == "expressed" and (prev_status is None or prev_status != "expressed"):
        try:
            from app.services.in_app_notifications import record_loader_haulier_interest_notifications

            load_row = db.get(models.Load, result.load_id) if result.load_id else None
            planned_row = (
                db.get(models.PlannedLoad, result.planned_load_id) if result.planned_load_id else None
            )
            record_loader_haulier_interest_notifications(
                db,
                load=load_row,
                planned_load=planned_row,
                haulier_id=body.haulier_id,
                vehicle_id=body.vehicle_id,
            )
        except Exception as e:
            print(f"[NOTIFY] record_loader_haulier_interest_notifications failed: {e}")

    return result


@router.get("/load-interests", response_model=list[schemas.LoadInterestRead])
def list_load_interests(
    haulier_id: Optional[int] = None,
    planned_load_id: Optional[int] = None,
    load_id: Optional[int] = None,
    db: Session = Depends(get_db),
) -> list[models.LoadInterest]:
    q = db.query(models.LoadInterest).order_by(models.LoadInterest.created_at.desc())
    if haulier_id is not None:
        q = q.filter(models.LoadInterest.haulier_id == haulier_id)
    if planned_load_id is not None:
        q = q.filter(models.LoadInterest.planned_load_id == planned_load_id)
    if load_id is not None:
        q = q.filter(models.LoadInterest.load_id == load_id)
    return q.all()


# ---- Run matching (optional; also runs automatically on create) ----

@router.post("/run-route-matching")
def run_route_matching(db: Session = Depends(get_db)) -> dict:
    """Run route matching for all planned loads and haulier routes; notify matches. Idempotent for suggested interests."""
    pairs = find_route_matches(db)
    for planned_load, route in pairs:
        notify_route_match(planned_load, route, db)
    return {"matched_pairs": len(pairs), "message": "Matching complete; hauliers with live alerts will be notified."}
