"""Referral program: capped signups, 50% platform fee discount for referrers (3 months, no extension)."""
from __future__ import annotations

import logging
import secrets
import string
from datetime import date, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app import models

logger = logging.getLogger(__name__)

REFERRAL_CAP = 100
REFERRAL_DISCOUNT_DAYS = 90
CODE_PREFIX = "PLTNX-"


def count_successful_referrals(db: Session) -> int:
    return (
        db.query(models.User)
        .filter(models.User.referred_by_code.isnot(None))
        .filter(models.User.referred_by_code != "")
        .count()
    )


def generate_unique_referral_code(db: Session) -> str:
    chars = string.ascii_uppercase + string.digits
    for _ in range(80):
        random_part = "".join(secrets.choice(chars) for _ in range(4))
        code = f"{CODE_PREFIX}{random_part}"
        exists = db.query(models.User.id).filter(models.User.referral_code == code).first()
        if not exists:
            return code
    raise RuntimeError("Could not allocate a unique referral code")


def ensure_user_referral_code(db: Session, user: models.User) -> None:
    if (user.referral_code or "").strip():
        return
    user.referral_code = generate_unique_referral_code(db)
    db.add(user)


def user_has_active_referral_discount(user: Optional[models.User], today: date) -> bool:
    if not user:
        return False
    u = getattr(user, "referral_discount_until", None)
    return u is not None and u >= today


def haulier_referral_fee_multiplier(db: Session, haulier_id: Optional[int], today: date) -> float:
    if not haulier_id:
        return 1.0
    row = (
        db.query(models.User)
        .filter(models.User.haulier_id == haulier_id)
        .filter(models.User.referral_discount_until.isnot(None))
        .filter(models.User.referral_discount_until >= today)
        .first()
    )
    return 0.5 if row else 1.0


def loader_referral_fee_multiplier(db: Session, loader_id: Optional[int], today: date) -> float:
    if not loader_id:
        return 1.0
    row = (
        db.query(models.User)
        .filter(models.User.loader_id == loader_id)
        .filter(models.User.referral_discount_until.isnot(None))
        .filter(models.User.referral_discount_until >= today)
        .first()
    )
    return 0.5 if row else 1.0


def count_active_referral_discounts(db: Session, today: date) -> int:
    return (
        db.query(models.User)
        .filter(models.User.referral_discount_until.isnot(None))
        .filter(models.User.referral_discount_until >= today)
        .count()
    )


def process_referral_for_new_user(
    db: Session,
    new_user: models.User,
    referral_code_raw: str,
) -> None:
    """
    Apply referral if code valid and program not full. Mutates referrer and new_user (referred_by_code);
    caller commits. New user always pays full price — only referrer gets fee discounts.
    """
    from app.services.email_sender import send_email
    from app.services.in_app_notifications import record_user_notifications

    code = (referral_code_raw or "").strip().upper()
    if not code:
        return

    if count_successful_referrals(db) >= REFERRAL_CAP:
        logger.info("Referral program cap reached; ignoring code for new user %s", new_user.email)
        return

    referrer = db.query(models.User).filter(models.User.referral_code == code).first()
    if not referrer or referrer.id == new_user.id:
        return

    new_user.referred_by_code = code
    db.add(new_user)

    today = date.today()
    discount_active = (
        referrer.referral_discount_until is not None and referrer.referral_discount_until >= today
    )

    referrer.referral_count = int(getattr(referrer, "referral_count", 0) or 0) + 1

    if not discount_active:
        referrer.referral_discount_until = today + timedelta(days=REFERRAL_DISCOUNT_DAYS)
        db.add(referrer)
        record_user_notifications(
            db,
            [referrer.id],
            title="50% off platform fees for 3 months",
            body=(
                f"{new_user.email} signed up with your code. Your 50% platform fee discount runs until "
                f"{referrer.referral_discount_until.strftime('%d %b %Y')}."
            ),
            link_url="/?section=find",
            kind="referral_success",
            priority="important",
            commit=False,
        )
        try:
            send_email(
                referrer.email,
                "You earned 50% off Platoonix platform fees",
                f"""Hi,

{new_user.email} just registered using your referral code {code}.

You now have 50% off platform fees for 3 months (until {referrer.referral_discount_until.strftime('%d %b %Y')}).

Thank you for spreading the word.

— Platoonix
""",
            )
        except Exception as exc:
            logger.warning("referral success email failed: %s", exc)
    else:
        db.add(referrer)
        until = referrer.referral_discount_until
        record_user_notifications(
            db,
            [referrer.id],
            title="Thanks for another referral",
            body=(
                f"{new_user.email} used your code. Your discount stays active until "
                f"{until.strftime('%d %b %Y')} (no extension)."
            ),
            link_url="/?section=find",
            kind="referral_thankyou",
            priority="normal",
            commit=False,
        )
        try:
            send_email(
                referrer.email,
                "Someone used your Platoonix referral code",
                f"""Hi,

{new_user.email} registered with your referral code. Your current 50% fee discount is unchanged and runs until {until.strftime('%d %b %Y')}.

— Platoonix
""",
            )
        except Exception as exc:
            logger.warning("referral thankyou email failed: %s", exc)


def backfill_missing_referral_codes(db: Session) -> int:
    """Assign referral_code to users that don't have one. Returns number updated."""
    from app import models

    n = 0
    rows = db.query(models.User).filter(models.User.referral_code.is_(None)).all()
    for u in rows:
        u.referral_code = generate_unique_referral_code(db)
        db.add(u)
        n += 1
    if n:
        db.commit()
    return n
