"""Insurance certificate expiry: status labels for vehicles; upload validation and verification workflow."""
from __future__ import annotations

import io
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

from starlette.datastructures import UploadFile

logger = logging.getLogger(__name__)


def get_insurance_storage_dir() -> Path:
    from app.config import get_settings

    raw = get_settings().insurance_upload_dir
    if raw:
        return Path(raw).expanduser().resolve()
    return Path(__file__).resolve().parent.parent.parent / "data" / "insurance"


ALLOWED_INSURANCE_EXTENSIONS = frozenset({".pdf", ".jpg", ".jpeg", ".png", ".webp"})
MAX_INSURANCE_UPLOAD_BYTES = 5 * 1024 * 1024
MIN_IMAGE_DIMENSION = 200


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


def vehicle_may_accept_loads(vehicle: Any) -> bool:
    """True when this vehicle may express interest or be matched (verified cert, not expired)."""
    if vehicle is None:
        return False
    path = getattr(vehicle, "insurance_certificate_path", None) or ""
    if not str(path).strip():
        return False
    if not getattr(vehicle, "insurance_certificate_verified", False):
        return False
    return calculate_insurance_status(getattr(vehicle, "insurance_expiry_date", None)) != "expired"


def haulier_has_pending_insurance_review(vehicles: Optional[list]) -> bool:
    if not vehicles:
        return False
    for v in vehicles:
        if v is None:
            continue
        path = getattr(v, "insurance_certificate_path", None) or ""
        if str(path).strip() and not getattr(v, "insurance_certificate_verified", False):
            return True
    return False


def validate_insurance_upload_bytes(
    data: bytes,
    original_filename: str,
    content_type: Optional[str] = None,
) -> None:
    """Structural checks on certificate bytes. Raises ValueError with a user-facing message."""
    if not data:
        raise ValueError("Insurance certificate file is empty")

    suffix = Path(original_filename or "").suffix.lower()
    if suffix not in ALLOWED_INSURANCE_EXTENSIONS:
        raise ValueError("Insurance file must be PDF or image (JPG, PNG, WebP)")

    if len(data) > MAX_INSURANCE_UPLOAD_BYTES:
        raise ValueError("Insurance file is too large (max 5 MB)")

    ct = (content_type or "").split(";")[0].strip().lower()
    if ct and ct not in (
        "application/octet-stream",
        "binary/octet-stream",
        "",
    ):
        disallowed = ("video/", "audio/", "text/html")
        if any(ct.startswith(p) for p in disallowed):
            raise ValueError("Invalid file type for insurance certificate")

    if suffix == ".pdf":
        if not data.startswith(b"%PDF"):
            raise ValueError("Invalid or corrupted PDF file")
        return

    from PIL import Image

    try:
        buf = io.BytesIO(data)
        with Image.open(buf) as img:
            img.verify()
        buf2 = io.BytesIO(data)
        with Image.open(buf2) as img2:
            img2.load()
            w, h = img2.size
            if w < MIN_IMAGE_DIMENSION or h < MIN_IMAGE_DIMENSION:
                raise ValueError(
                    "Insurance certificate image is too small; upload a clear photo or scan "
                    f"(minimum {MIN_IMAGE_DIMENSION}×{MIN_IMAGE_DIMENSION} pixels)."
                )
    except ValueError:
        raise
    except Exception as exc:
        logger.warning("Insurance image validation failed: %s", exc)
        raise ValueError("Invalid or corrupted image file") from exc


def save_insurance_bytes_for_vehicle(
    vehicle_id: int,
    original_filename: str,
    data: bytes,
    *,
    content_type: Optional[str] = None,
) -> str:
    """Write certificate bytes to disk. Returns basename for Vehicle.insurance_certificate_path."""
    validate_insurance_upload_bytes(data, original_filename, content_type)

    suffix = Path(original_filename or "").suffix.lower()
    ts = int(datetime.now(timezone.utc).timestamp())
    basename = f"{vehicle_id}_{ts}{suffix}"
    dest_dir = get_insurance_storage_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / basename
    dest_path.write_bytes(data)
    return basename


async def save_insurance_certificate_for_vehicle(
    vehicle_id: int,
    upload: UploadFile,
) -> str:
    """Read upload and store on disk. Returns basename for Vehicle.insurance_certificate_path."""
    if not upload or not getattr(upload, "filename", None):
        raise ValueError("Insurance certificate file is required")
    data = await upload.read()
    ct = getattr(upload, "content_type", None)
    return save_insurance_bytes_for_vehicle(vehicle_id, upload.filename or "cert.pdf", data, content_type=ct)


def _unlink_insurance_basename(basename: Optional[str]) -> None:
    if not basename or not str(basename).strip():
        return
    try:
        p = get_insurance_storage_dir() / basename
        if p.is_file():
            p.unlink()
    except OSError:
        pass


def remove_insurance_file_if_exists(basename: Optional[str]) -> None:
    """Delete stored certificate file by basename (safe no-op if missing)."""
    _unlink_insurance_basename(basename)


def _notify_admins_insurance_pending(db: Any, vehicle_id: int, haulier_id: int) -> None:
    from app import models
    from app.services.in_app_notifications import record_user_notifications

    admin_ids = [
        int(r[0])
        for r in db.query(models.User.id).filter(models.User.role == "admin").all()
    ]
    if not admin_ids:
        return
    haulier = db.get(models.Haulier, haulier_id)
    label = (haulier.name or "Haulier") if haulier else "Haulier"
    record_user_notifications(
        db,
        admin_ids,
        title="Insurance certificate needs verification",
        body=f"{label} uploaded a certificate for vehicle ID {vehicle_id}. Review before they can accept loads.",
        link_url=f"/admin/verify-insurance/{vehicle_id}",
        kind="insurance_verification",
        priority="important",
    )


async def finalize_vehicle_insurance_upload(db: Any, vehicle: Any, upload: UploadFile) -> None:
    """After Vehicle row exists: save file, set pending verification, notify admins."""
    old_basename = getattr(vehicle, "insurance_certificate_path", None)
    basename = await save_insurance_certificate_for_vehicle(vehicle.id, upload)
    if old_basename and old_basename != basename:
        _unlink_insurance_basename(old_basename)

    now = datetime.now(timezone.utc)
    vehicle.insurance_certificate_path = basename
    vehicle.insurance_last_checked = now
    vehicle.insurance_uploaded_at = now
    vehicle.insurance_certificate_verified = False
    vehicle.insurance_verified_at = None
    vehicle.insurance_verified_by = None
    vehicle.insurance_rejection_reason = None
    vehicle.insurance_status = calculate_insurance_status(vehicle.insurance_expiry_date)
    db.add(vehicle)
    db.commit()

    try:
        _notify_admins_insurance_pending(db, vehicle.id, vehicle.haulier_id)
    except Exception as exc:
        logger.warning("notify_admins_insurance_pending failed: %s", exc)
