"""Bulk upload via CSV or Excel for companies without API integration."""
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.bulk_import import import_hauliers, import_loads, import_vehicles
from app.services.upload_parser import parse_hauliers, parse_loads, parse_vehicles

router = APIRouter()

ALLOWED_EXTENSIONS = {".csv", ".xlsx", ".xls"}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB


def _check_file(file: UploadFile) -> None:
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename")
    ext = "." + file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"File type not allowed. Use CSV or Excel (.csv, .xlsx, .xls)",
        )


@router.post("/api/upload/hauliers")
async def upload_hauliers(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Upload CSV or Excel to create hauliers in bulk. Columns: name, contact_email, contact_phone (or email, phone)."""
    _check_file(file)
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="File too large (max 5 MB)")
    rows = parse_hauliers(content, file.filename or "")
    created, errors = import_hauliers(db, rows)
    return {"created": created, "errors": errors, "total_rows": len(rows)}


@router.post("/api/upload/vehicles")
async def upload_vehicles(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Upload CSV or Excel to create vehicles. Columns: haulier_id, registration, vehicle_type; optional: capacity_weight_kg, capacity_volume_m3."""
    _check_file(file)
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="File too large (max 5 MB)")
    rows = parse_vehicles(content, file.filename or "")
    created, errors = import_vehicles(db, rows)
    return {"created": created, "errors": errors, "total_rows": len(rows)}


@router.post("/api/upload/loads")
async def upload_loads(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Upload CSV or Excel to create loads. Columns: shipper_name, pickup_postcode, delivery_postcode; optional: pickup/delivery windows, weight_kg, pallets, volume_m3, budget_gbp. If pallets is set, volume_m3 is calculated (1.2 m³ per pallet)."""
    _check_file(file)
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="File too large (max 5 MB)")
    rows = parse_loads(content, file.filename or "")
    created, errors = import_loads(db, rows)
    return {"created": created, "errors": errors, "total_rows": len(rows)}
@router.post("/api/upload/hauliers-vehicles")
async def upload_hauliers_vehicles(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Upload consolidated CSV: creates companies + vehicles in one go.
    Columns: company_name, contact_email, contact_phone, contact_name, registration, 
             vehicle_type, trailer_type, capacity_weight_kg, capacity_pallets, base_postcode"""
    from app.services.consolidated_upload import import_hauliers_vehicles
    
    _check_file(file)
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="File too large (max 5 MB)")
    
    result = import_hauliers_vehicles(db, content, file.filename or "")
    return result


@router.post("/api/upload/loaders-loads")
async def upload_loaders_loads(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Upload consolidated CSV: creates loader companies + loads in one go.
    Columns: company_name, contact_email, contact_phone, contact_name, pickup_postcode,
             delivery_postcode, weight_kg, pallets, pickup_date, vehicle_type_required, trailer_type_required"""
    from app.services.consolidated_upload import import_loaders_loads
    
    _check_file(file)
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="File too large (max 5 MB)")
    
    result = import_loaders_loads(db, content, file.filename or "")
    return result