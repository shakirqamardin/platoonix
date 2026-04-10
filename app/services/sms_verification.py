"""SMS delivery verification codes (Tier 2) — provider integration TODO."""
from __future__ import annotations

import logging
import random
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from app import models

logger = logging.getLogger(__name__)


def generate_sms_code() -> str:
    return str(random.randint(100000, 999999))


def send_verification_sms(phone_number: str, code: str) -> None:
    """Send SMS with verification code (logs until Twilio/SNS wired)."""
    logger.info("SMS verification (stub) to %s: code %s", phone_number[-4:] if phone_number else "?", code)


def verify_sms_code(entered_code: str, load_id: int, db: Session) -> bool:
    from app import models

    load = db.get(models.Load, load_id)
    if not load or not load.sms_verification_code:
        return False
    if load.sms_code_used:
        return False
    if (entered_code or "").strip() != (load.sms_verification_code or "").strip():
        return False
    exp = load.sms_code_expires_at
    if exp is None:
        return False
    now = datetime.now(timezone.utc)
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if now > exp:
        return False
    return True


def mark_sms_code_used(load: "models.Load", db: Session) -> None:
    load.sms_code_used = True
    db.add(load)
