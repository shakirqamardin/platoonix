"""Insurance certificate expiry: status labels for vehicles."""
from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

from starlette.datastructures import UploadFile


def get_insurance_storage_dir() -> Path:
    from app.config import get_settings

    raw = get_settings().insurance_upload_dir
    if raw:
        return Path(raw).expanduser().resolve()
    return Path(__file__).resolve().parent.parent.parent / "data" / "insurance"


ALLOWED_INSURANCE_EXTENSIONS = frozenset({".pdf", ".jpg", ".jpeg", ".png", ".webp"})
MAX_INSURANCE_UPLOAD_BYTES = 10 * 1024 * 1024


def calculate_insurance_status(expiry_date: Optional[date]) -> str:
    if not expiry_date:
        return "unknown"

    today = date.today()
    days_until_expiry = (expiry_date - today).days

    if days_until_expiry >= 30:
        return "valid"
    if days_until_expiry >= 0:
        return "expiring_soon"
    return "expired"


def apply_insurance_status_to_vehicles(vehicles: Optional[list]) -> None:
    """Set each vehicle's insurance_status from insurance_expiry_date (for templates)."""
    if not vehicles:
        return
    for v in vehicles:
        if v is None:
            continue
        v.insurance_status = calculate_insurance_status(getattr(v, "insurance_expiry_date", None))


def save_insurance_bytes_for_vehicle(vehicle_id: int, original_filename: str, data: bytes) -> str:
    """Write certificate bytes to disk. Returns basename for Vehicle.insurance_certificate_path."""
    if not data:
        raise ValueError("Insurance certificate file is empty")

    suffix = Path(original_filename or "").suffix.lower()
    if suffix not in ALLOWED_INSURANCE_EXTENSIONS:
        raise ValueError("Insurance file must be PDF or image (JPG, PNG, WebP)")

    if len(data) > MAX_INSURANCE_UPLOAD_BYTES:
        raise ValueError("Insurance file is too large (max 10 MB)")

    ts = int(datetime.now(timezone.utc).timestamp())
    basename = f"{vehicle_id}_{ts}{suffix}"
    dest_dir = get_insurance_storage_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / basename
    dest_path.write_bytes(data)
    return basename


async def save_insurance_certificate_for_vehicle(vehicle_id: int, upload: UploadFile) -> str:
    """Read upload and store on disk. Returns basename for Vehicle.insurance_certificate_path."""
    if not upload or not getattr(upload, "filename", None):
        raise ValueError("Insurance certificate file is required")
    data = await upload.read()
    return save_insurance_bytes_for_vehicle(vehicle_id, upload.filename or "cert.pdf", data)


async def finalize_vehicle_insurance_upload(db: Any, vehicle: Any, upload: UploadFile) -> None:
    """After Vehicle row exists: save file and update certificate path + status + last_checked."""
    basename = await save_insurance_certificate_for_vehicle(vehicle.id, upload)
    vehicle.insurance_certificate_path = basename
    vehicle.insurance_last_checked = datetime.now(timezone.utc)
    vehicle.insurance_status = calculate_insurance_status(vehicle.insurance_expiry_date)
    db.add(vehicle)
    db.commit()
