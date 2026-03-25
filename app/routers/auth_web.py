"""
Login, logout, forgot password, and post-login redirects.
"""
import secrets
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app import models
from app.auth import (
    get_current_driver_optional,
    get_current_user_optional,
    hash_password,
    verify_password,
    _hash_reset_token,
)
from app.database import get_db

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

RESET_TOKEN_EXPIRY_HOURS = 1


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    """Show login form. If already logged in, redirect to role dashboard."""
    driver = get_current_driver_optional(request, db)
    if driver:
        return RedirectResponse(url="/driver", status_code=302)
    user = get_current_user_optional(request, db)
    if user:
        if user.role == "haulier":
            return RedirectResponse(url="/?section=find", status_code=302)
        if user.role == "loader":
            return RedirectResponse(url="/?section=find", status_code=302)
        return RedirectResponse(url="/", status_code=302)
    password_reset = request.query_params.get("password_reset")
    logout = request.query_params.get("logout")
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "error": None, "email": "", "password_reset": password_reset, "logout": logout},
    )


@router.get("/driver-login", response_class=HTMLResponse)
def driver_login_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    """Show driver login form."""
    driver = get_current_driver_optional(request, db)
    if driver:
        return RedirectResponse(url="/driver", status_code=302)
    return templates.TemplateResponse(
        "driver_login.html",
        {"request": request, "error": None, "email": ""},
    )


@router.post("/driver-login")
async def driver_login_submit(
    request: Request,
    db: Session = Depends(get_db),
):
    form = await request.form()
    email = (form.get("email") or "").strip().lower()
    password = form.get("password") or ""
    if not email or not password:
        return templates.TemplateResponse(
            "driver_login.html",
            {"request": request, "error": "Email and password required.", "email": email},
            status_code=200,
        )
    driver = db.query(models.Driver).filter(models.Driver.email == email).first()
    if not driver or not verify_password(password, driver.password_hash):
        return templates.TemplateResponse(
            "driver_login.html",
            {"request": request, "error": "Invalid email or password.", "email": email},
            status_code=200,
        )
    request.session.clear()
    request.session["driver_id"] = driver.id
    request.session["haulier_id"] = driver.haulier_id
    request.session["role"] = "driver"
    return RedirectResponse(url="/driver", status_code=302)


@router.post("/login")
async def login_submit(
    request: Request,
    db: Session = Depends(get_db),
):
    form = await request.form()
    email = (form.get("email") or "").strip().lower()
    password = form.get("password") or ""
    if not email or not password:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Email and password required.", "email": email},
            status_code=200,
        )
    user = db.query(models.User).filter(models.User.email == email).first()
    if not user or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Invalid email or password.", "email": email},
            status_code=200,
        )
    request.session["user_id"] = user.id
    request.session["role"] = user.role
    # Go straight to the dashboard. Do not redirect via /haulier or /loader — those URLs
    # only bounce to / and can cause ERR_TOO_MANY_REDIRECTS if session/caching misbehaves.
    if user.role == "haulier":
        return RedirectResponse(url="/?section=find", status_code=302)
    if user.role == "loader":
        return RedirectResponse(url="/?section=find", status_code=302)
    return RedirectResponse(url="/", status_code=302)


@router.post("/logout", response_class=RedirectResponse)
def logout_post(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse(url="/login?logout=1", status_code=303)


@router.get("/logout", response_class=RedirectResponse)
def logout_get(request: Request) -> RedirectResponse:
    """Log out and switch user: clear session and send to login (same as POST)."""
    request.session.clear()
    return RedirectResponse(url="/login?logout=1", status_code=302)


@router.get("/forgot-password", response_class=HTMLResponse)
def forgot_password_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        "forgot_password.html",
        {"request": request, "error": None, "email": ""},
    )


@router.post("/forgot-password")
async def forgot_password_submit(
    request: Request,
    db: Session = Depends(get_db),
):
    form = await request.form()
    email = (form.get("email") or "").strip().lower()
    if not email:
        return templates.TemplateResponse(
            "forgot_password.html",
            {"request": request, "error": "Enter your email.", "email": ""},
            status_code=200,
        )
    user = db.query(models.User).filter(models.User.email == email).first()
    if not user:
        return templates.TemplateResponse(
            "forgot_password.html",
            {"request": request, "error": "No account found with that email.", "email": email},
            status_code=200,
        )
    token = secrets.token_urlsafe(32)
    token_hash = _hash_reset_token(token)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=RESET_TOKEN_EXPIRY_HOURS)
    reset_row = models.PasswordResetToken(
        user_id=user.id,
        token_hash=token_hash,
        expires_at=expires_at,
    )
    db.add(reset_row)
    db.commit()
    base_url = str(request.base_url).rstrip("/")
    reset_link = f"{base_url}/reset-password?token={token}"
    return templates.TemplateResponse(
        "forgot_password_done.html",
        {"request": request, "reset_link": reset_link, "expiry_hours": RESET_TOKEN_EXPIRY_HOURS},
    )


@router.get("/reset-password", response_class=HTMLResponse)
def reset_password_page(request: Request) -> HTMLResponse:
    token = request.query_params.get("token") or ""
    return templates.TemplateResponse(
        "reset_password.html",
        {"request": request, "token": token, "error": None},
    )


@router.post("/reset-password")
async def reset_password_submit(
    request: Request,
    db: Session = Depends(get_db),
):
    form = await request.form()
    token = (form.get("token") or "").strip()
    password = form.get("password") or ""
    password_confirm = form.get("password_confirm") or ""
    if not token:
        return templates.TemplateResponse(
            "reset_password.html",
            {"request": request, "token": "", "error": "Invalid or expired link. Request a new one from Forgot password."},
            status_code=200,
        )
    if not password or len(password) < 6:
        return templates.TemplateResponse(
            "reset_password.html",
            {"request": request, "token": token, "error": "Password must be at least 6 characters."},
            status_code=200,
        )
    if password != password_confirm:
        return templates.TemplateResponse(
            "reset_password.html",
            {"request": request, "token": token, "error": "Passwords do not match."},
            status_code=200,
        )
    token_hash = _hash_reset_token(token)
    now = datetime.now(timezone.utc)
    row = (
        db.query(models.PasswordResetToken)
        .filter(
            models.PasswordResetToken.token_hash == token_hash,
            models.PasswordResetToken.expires_at > now,
        )
        .first()
    )
    if not row:
        return templates.TemplateResponse(
            "reset_password.html",
            {"request": request, "token": "", "error": "Link expired or already used. Request a new one from Forgot password."},
            status_code=200,
        )
    user = db.get(models.User, row.user_id)
    if not user:
        db.delete(row)
        db.commit()
        return templates.TemplateResponse(
            "reset_password.html",
            {"request": request, "token": "", "error": "Invalid link."},
            status_code=200,
        )
    user.password_hash = hash_password(password)
    db.delete(row)
    db.commit()
    return RedirectResponse(url="/login?password_reset=1", status_code=303)


@router.get("/register", response_class=HTMLResponse)
def register_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    """Show registration form (haulier or loader). If logged in, redirect to dashboard."""
    user = get_current_user_optional(request, db)
    if user:
        if user.role == "haulier":
            return RedirectResponse(url="/?section=find", status_code=302)
        if user.role == "loader":
            return RedirectResponse(url="/?section=find", status_code=302)
        return RedirectResponse(url="/", status_code=302)
    error = request.query_params.get("error")
    return templates.TemplateResponse(
        "register.html",
        {"request": request, "error": error, "success": None},
    )


@router.post("/register-haulier", response_class=RedirectResponse)
async def register_haulier_submit(
    request: Request,
    db: Session = Depends(get_db),
):
    """Create Haulier + User (self-service)."""
    form = await request.form()
    name = (form.get("company_name") or "").strip()
    email = (form.get("email") or "").strip().lower()
    phone = (form.get("contact_phone") or "").strip() or None
    password = form.get("password") or ""
    password_confirm = form.get("password_confirm") or ""
    if not name or not email:
        return _register_redirect("Company name and email required.")
    if len(password) < 6:
        return _register_redirect("Password must be at least 6 characters.")
    if password != password_confirm:
        return _register_redirect("Passwords do not match.")
    if db.query(models.User).filter(models.User.email == email).first():
        return _register_redirect("That email is already registered. Log in or use another email.")
    haulier = models.Haulier(name=name, contact_email=email, contact_phone=phone)
    db.add(haulier)
    db.flush()
    user = models.User(
        email=email,
        password_hash=hash_password(password),
        role="haulier",
        haulier_id=haulier.id,
    )
    db.add(user)
    db.flush()
    # Read id/role before commit(): after commit, instances expire; lazy refresh in async routes can raise MissingGreenlet.
    user_id, user_role = user.id, user.role
    db.commit()
    request.session["user_id"] = user_id
    request.session["role"] = user_role
    return RedirectResponse(url="/?section=find", status_code=303)


@router.post("/register-loader", response_class=RedirectResponse)
async def register_loader_submit(
    request: Request,
    db: Session = Depends(get_db),
):
    """Create Loader + User (self-service)."""
    form = await request.form()
    name = (form.get("company_name") or "").strip()
    email = (form.get("email") or "").strip().lower()
    phone = (form.get("contact_phone") or "").strip() or None
    password = form.get("password") or ""
    password_confirm = form.get("password_confirm") or ""
    if not name or not email:
        return _register_redirect("Company name and email required.")
    if len(password) < 6:
        return _register_redirect("Password must be at least 6 characters.")
    if password != password_confirm:
        return _register_redirect("Passwords do not match.")
    if db.query(models.User).filter(models.User.email == email).first():
        return _register_redirect("That email is already registered. Log in or use another email.")
    loader = models.Loader(name=name, contact_email=email, contact_phone=phone)
    db.add(loader)
    db.flush()
    user = models.User(
        email=email,
        password_hash=hash_password(password),
        role="loader",
        loader_id=loader.id,
    )
    db.add(user)
    db.flush()
    user_id, user_role = user.id, user.role
    db.commit()
    request.session["user_id"] = user_id
    request.session["role"] = user_role
    return RedirectResponse(url="/?section=find", status_code=303)


def _register_redirect(error: str):
    """Redirect back to register page with error in query string."""
    from urllib.parse import quote
    return RedirectResponse(url="/register?error=" + quote(error), status_code=303)