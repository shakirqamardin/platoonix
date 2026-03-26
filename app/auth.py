"""
Login, session, and permission helpers.
"""
from typing import Optional, Union

from fastapi import Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app import models
from app.database import get_db


# Bcrypt limit; use bytes to avoid passlib/bcrypt 4.1+ compatibility issues
_BCRYPT_MAX_PASSWORD = 72


def hash_password(plain: str) -> str:
    import bcrypt
    raw = (plain or "").encode("utf-8")[: _BCRYPT_MAX_PASSWORD]
    return bcrypt.hashpw(raw, bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    import bcrypt
    raw = (plain or "").encode("utf-8")[: _BCRYPT_MAX_PASSWORD]
    return bcrypt.checkpw(raw, (hashed or "").encode("utf-8"))


def _hash_reset_token(token: str) -> str:
    import hashlib
    return hashlib.sha256(token.encode()).hexdigest()


def get_session_user_id(request: Request) -> Optional[int]:
    """Return user_id from session or None."""
    return request.session.get("user_id")


def get_session_driver_id(request: Request) -> Optional[int]:
    """Return driver_id from session or None."""
    return request.session.get("driver_id")


def _maybe_relink_orphan_haulier(db: Session, user: models.User) -> models.User:
    """If a haulier login has no haulier_id but exactly one Haulier row matches their email, link them.

    Recovers after company-delete (which clears users.haulier_id) or bad data, without needing the Admin tab.
    If zero or multiple companies match the email, leave unchanged (admin must resolve).
    """
    from sqlalchemy import func

    role = (getattr(user, "role", None) or "").strip().lower()
    if role != "haulier" or user.haulier_id is not None:
        return user
    email = (user.email or "").strip().lower()
    if not email:
        return user
    matches = (
        db.query(models.Haulier)
        .filter(func.lower(models.Haulier.contact_email) == email)
        .all()
    )
    if len(matches) != 1:
        return user
    user.haulier_id = matches[0].id
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def get_current_user_optional(
    request: Request,
    db: Session,
) -> Optional[models.User]:
    """Load current user from session; return None if not logged in."""
    user_id = get_session_user_id(request)
    if not user_id:
        return None
    user = db.get(models.User, user_id)
    if user is None:
        return None
    return _maybe_relink_orphan_haulier(db, user)


def get_current_driver_optional(
    request: Request,
    db: Session,
) -> Optional[models.Driver]:
    """Load current driver from session; return None if not logged in as driver."""
    driver_id = get_session_driver_id(request)
    if not driver_id:
        return None
    return db.get(models.Driver, driver_id)


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
) -> Union[models.User, RedirectResponse]:
    """Dependency: require login; redirect to /login if not authenticated."""
    user = get_current_user_optional(request, db)
    if user is None:
        return RedirectResponse(url="/login", status_code=302)
    return user


def require_admin(request: Request, db: Session) -> Optional[RedirectResponse]:
    """If not admin, redirect to role dashboard or login. Returns None if admin."""
    user = get_current_user_optional(request, db)
    if user is None:
        return RedirectResponse(url="/login", status_code=302)
    if user.role != "admin":
        if user.role == "haulier":
            return RedirectResponse(url="/?section=find", status_code=302)
        if user.role == "loader":
            return RedirectResponse(url="/?section=find", status_code=302)
        return RedirectResponse(url="/login", status_code=302)
    return None


def require_haulier(request: Request, db: Session) -> Optional[RedirectResponse]:
    """If not haulier (or admin), redirect. Returns None if haulier or admin."""
    user = get_current_user_optional(request, db)
    if user is None:
        return RedirectResponse(url="/login", status_code=302)
    if user.role not in ("haulier", "admin"):
        if user.role == "loader":
            return RedirectResponse(url="/?section=find", status_code=302)
        return RedirectResponse(url="/login", status_code=302)
    return None


def require_loader(request: Request, db: Session) -> Optional[RedirectResponse]:
    """If not loader (or admin), redirect. Returns None if loader or admin."""
    user = get_current_user_optional(request, db)
    if user is None:
        return RedirectResponse(url="/login", status_code=302)
    if user.role not in ("loader", "admin"):
        if user.role == "haulier":
            return RedirectResponse(url="/?section=find", status_code=302)
        return RedirectResponse(url="/login", status_code=302)
    return None


def get_current_admin(
    request: Request,
    db: Session = Depends(get_db),
) -> Union[models.User, RedirectResponse]:
    """Dependency: require admin; redirect to /login or role dashboard otherwise."""
    redirect = require_admin(request, db)
    if redirect is not None:
        return redirect
    user_id = get_session_user_id(request)
    user = db.get(models.User, user_id)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return user
