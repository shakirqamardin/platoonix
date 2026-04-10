"""QR codes for delivery verification (Tier 1)."""
from __future__ import annotations

import base64
import secrets
from io import BytesIO
from typing import TYPE_CHECKING, Optional, Tuple

import qrcode
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from app import models


def generate_qr_code_for_load(load_id: int) -> Tuple[str, str]:
    """
    Generate unique payload for a load. Returns (code_string, qr_image_base64_png).
    """
    code = f"PLTNX-{load_id}-{secrets.token_urlsafe(16)}"
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(code)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buffered = BytesIO()
    img.save(buffered, format="PNG")
    img_str = base64.b64encode(buffered.getvalue()).decode()
    return code, img_str


def qr_png_bytes(code: str) -> bytes:
    """Render QR payload to PNG bytes."""
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(code)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buffered = BytesIO()
    img.save(buffered, format="PNG")
    return buffered.getvalue()


def verify_qr_code(scanned_code: str, load_id: int, db: Session) -> bool:
    """Verify QR payload matches load and has not been used."""
    from app import models

    load = db.get(models.Load, load_id)
    if not load:
        return False
    if not load.qr_code or (load.qr_code or "").strip() != (scanned_code or "").strip():
        return False
    if load.qr_code_used:
        return False
    return True


def ensure_qr_for_load(db: Session, load: "models.Load") -> None:
    """Assign a unique qr_code when missing (call after load has an id)."""
    if load.qr_code:
        return
    code, _img = generate_qr_code_for_load(load.id)
    load.qr_code = code
    db.add(load)
