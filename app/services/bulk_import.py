"""Shared bulk import logic for CSV/Excel uploads (API and web)."""
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

from sqlalchemy.orm import Session

from app import models
from app.services.upload_parser import parse_datetime_optional


def import_hauliers(db: Session, rows: List[Dict[str, Any]]) -> Tuple[int, List[str]]:
    created = 0
    errors = []
    for i, row in enumerate(rows):
        if not row.get("name") or not row.get("contact_email"):
            errors.append(f"Row {i + 2}: missing name or contact_email")
            continue
        try:
            haulier = models.Haulier(
                name=str(row["name"]).strip(),
                contact_email=str(row["contact_email"]).strip(),
                contact_phone=str(row["contact_phone"]).strip() if row.get("contact_phone") else None,
            )
            db.add(haulier)
            db.commit()
            created += 1
        except Exception as e:
            db.rollback()
            errors.append(f"Row {i + 2}: {e}")
    return created, errors


def import_vehicles(db: Session, rows: List[Dict[str, Any]]) -> Tuple[int, List[str]]:
    created = 0
    errors = []
    for i, row in enumerate(rows):
        if not row.get("haulier_id") or not row.get("registration") or not row.get("vehicle_type"):
            errors.append(f"Row {i + 2}: missing haulier_id, registration or vehicle_type")
            continue
        try:
            haulier_id = int(row["haulier_id"])
            reg = str(row["registration"]).strip().upper()
            vehicle_type = str(row["vehicle_type"]).strip() or "rigid"
            trailer_type = str(row["trailer_type"]).strip() if row.get("trailer_type") else None
            if trailer_type == "":
                trailer_type = None
            if db.query(models.Haulier).filter(models.Haulier.id == haulier_id).first() is None:
                errors.append(f"Row {i + 2}: haulier_id {haulier_id} not found")
                continue
            if db.query(models.Vehicle).filter(models.Vehicle.registration == reg).first():
                errors.append(f"Row {i + 2}: registration {reg} already exists")
                continue
            kw = row.get("capacity_weight_kg")
            try:
                kw = float(kw) if kw is not None and str(kw).strip() else None
            except (TypeError, ValueError):
                kw = None
            vol = row.get("capacity_volume_m3")
            try:
                vol = float(vol) if vol is not None and str(vol).strip() else None
            except (TypeError, ValueError):
                vol = None
            vehicle = models.Vehicle(
                haulier_id=haulier_id,
                registration=reg,
                vehicle_type=vehicle_type,
                trailer_type=trailer_type,
                capacity_weight_kg=kw,
                capacity_volume_m3=vol,
            )
            db.add(vehicle)
            db.commit()
            created += 1
        except Exception as e:
            db.rollback()
            errors.append(f"Row {i + 2}: {e}")
    return created, errors


def import_loads(db: Session, rows: List[Dict[str, Any]]) -> Tuple[int, List[str]]:
    created = 0
    errors = []
    now = datetime.now(timezone.utc)
    for i, row in enumerate(rows):
        if not row.get("shipper_name") or not row.get("pickup_postcode") or not row.get("delivery_postcode"):
            errors.append(f"Row {i + 2}: missing shipper_name, pickup_postcode or delivery_postcode")
            continue
        try:
            pickup_start = parse_datetime_optional(row.get("pickup_window_start")) or now
            pickup_end = parse_datetime_optional(row.get("pickup_window_end")) or now
            delivery_start = parse_datetime_optional(row.get("delivery_window_start"))
            delivery_end = parse_datetime_optional(row.get("delivery_window_end"))
            weight = row.get("weight_kg")
            weight = float(weight) if weight is not None and str(weight).strip() else None
            volume = row.get("volume_m3")
            volume = float(volume) if volume is not None and str(volume).strip() else None
            budget = row.get("budget_gbp")
            budget = float(budget) if budget is not None and str(budget).strip() else None
            req_vehicle = (str(row.get("required_vehicle_type") or "").strip().lower()) or None
            req_trailer = (str(row.get("required_trailer_type") or "").strip().lower()) or None
            if req_vehicle == "any":
                req_vehicle = None
            if req_trailer == "any":
                req_trailer = None
            requirements = {}
            if req_vehicle:
                requirements["vehicle_type"] = req_vehicle
            if req_trailer:
                requirements["trailer_type"] = req_trailer
            requirements = requirements if requirements else None
            load = models.Load(
                shipper_name=str(row["shipper_name"]).strip(),
                pickup_postcode=str(row["pickup_postcode"]).strip().upper(),
                delivery_postcode=str(row["delivery_postcode"]).strip().upper(),
                pickup_window_start=pickup_start,
                pickup_window_end=pickup_end,
                delivery_window_start=delivery_start,
                delivery_window_end=delivery_end,
                weight_kg=weight,
                volume_m3=volume,
                budget_gbp=budget,
                requirements=requirements,
            )
            db.add(load)
            db.commit()
            db.refresh(load)
            try:
                from app.services.alert_stream import notify_new_load
                notify_new_load(load, db)
            except Exception:
                pass
            created += 1
        except Exception as e:
            db.rollback()
            errors.append(f"Row {i + 2}: {e}")
    return created, errors
