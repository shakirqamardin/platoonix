import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import get_settings
from app.routers import alerts, hauliers, vehicles, loads, matches, planned_routes, pods, payments, upload, web, auth_web, haulier_web, loader_web, driver, tracking
app = FastAPI(
    title="Backhaul Logistics Platform",
    description="API for automated backhaul matching, ULEZ/CAZ-aware routing, and instant payouts.",
    version="0.1.0",
)

app.add_middleware(
    SessionMiddleware,
    secret_key=get_settings().session_secret_key,
    session_cookie="platoonix_session",
    max_age=86400 * 7,  # 7 days
)

# Serve logo and other static assets
static_dir = Path(__file__).resolve().parent.parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.on_event("startup")
def check_db_and_create_tables():
    """Check DB connection and create tables if missing (no Shell needed on free tier)."""
    try:
        from app.database import Base, engine, SessionLocal
        from app import models  # noqa: F401 - register models with Base
        from app.auth import hash_password, verify_password
        from app.config import get_settings
        engine.connect().close()
        Base.metadata.create_all(bind=engine)
        # Add new columns to existing tables (no Alembic migration run)
        from sqlalchemy import text
        with engine.connect() as conn:
            for table, col in (("loads", "loader_id"), ("planned_loads", "loader_id")):
                try:
                    conn.execute(text(
                        f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} INTEGER REFERENCES loaders(id)"
                    ))
                    conn.commit()
                except Exception as e:
                    conn.rollback()
                    if "already exists" not in str(e).lower() and "duplicate" not in str(e).lower():
                        print(f"Migration {table}.{col}: {e!r}", file=sys.stderr)
            # backhaul_jobs.collected_at: confirmed collection (captures pay)
            try:
                conn.execute(text(
                    "ALTER TABLE backhaul_jobs ADD COLUMN IF NOT EXISTS collected_at TIMESTAMP WITH TIME ZONE"
                ))
                conn.commit()
            except Exception as e:
                conn.rollback()
                if "already exists" not in str(e).lower() and "duplicate" not in str(e).lower():
                    print("Migration backhaul_jobs.collected_at: {!r}".format(e), file=sys.stderr)
            # vehicles.base_postcode: for automatic matching when loads are added
            try:
                conn.execute(text(
                    "ALTER TABLE vehicles ADD COLUMN IF NOT EXISTS base_postcode VARCHAR(20)"
                ))
                conn.commit()
            except Exception as e:
                conn.rollback()
                if "already exists" not in str(e).lower() and "duplicate" not in str(e).lower():
                    print("Migration vehicles.base_postcode: {!r}".format(e), file=sys.stderr)
            # hauliers.base_postcode: company default base (route home); vehicle can override
            try:
                conn.execute(text(
                    "ALTER TABLE hauliers ADD COLUMN IF NOT EXISTS base_postcode VARCHAR(20)"
                ))
                conn.commit()
            except Exception as e:
                conn.rollback()
                if "already exists" not in str(e).lower() and "duplicate" not in str(e).lower():
                    print("Migration hauliers.base_postcode: {!r}".format(e), file=sys.stderr)
            # hauliers bank details (captured once in My company)
            for col, typ in (("bank_account_name", "VARCHAR(255)"), ("sort_code", "VARCHAR(20)"), ("account_number", "VARCHAR(20)")):
                try:
                    conn.execute(text(
                        f"ALTER TABLE hauliers ADD COLUMN IF NOT EXISTS {col} {typ}"
                    ))
                    conn.commit()
                except Exception as e:
                    conn.rollback()
                    if "already exists" not in str(e).lower() and "duplicate" not in str(e).lower():
                        print(f"Migration hauliers.{col}: {e!r}", file=sys.stderr)
            # loads.pallets: optional; when set, volume_m3 = pallets * 1.2 for display & matching
            try:
                conn.execute(text(
                    "ALTER TABLE loads ADD COLUMN IF NOT EXISTS pallets DOUBLE PRECISION"
                ))
                conn.commit()
            except Exception as e:
                conn.rollback()
                if "already exists" not in str(e).lower() and "duplicate" not in str(e).lower():
                    print(f"Migration loads.pallets: {e!r}", file=sys.stderr)
            # backhaul_jobs: driver timeline + live GPS
            for col, typ in (
                ("reached_pickup_at", "TIMESTAMP WITH TIME ZONE"),
                ("departed_pickup_at", "TIMESTAMP WITH TIME ZONE"),
                ("reached_delivery_at", "TIMESTAMP WITH TIME ZONE"),
                ("last_lat", "DOUBLE PRECISION"),
                ("last_lng", "DOUBLE PRECISION"),
                ("location_updated_at", "TIMESTAMP WITH TIME ZONE"),
            ):
                try:
                    conn.execute(text(
                        f"ALTER TABLE backhaul_jobs ADD COLUMN IF NOT EXISTS {col} {typ}"
                    ))
                    conn.commit()
                except Exception as e:
                    conn.rollback()
                    if "already exists" not in str(e).lower() and "duplicate" not in str(e).lower():
                        print(f"Migration backhaul_jobs.{col}: {e!r}", file=sys.stderr)
        # Create or sync admin from ADMIN_EMAIL / ADMIN_PASSWORD
        db = SessionLocal()
        try:
            settings = get_settings()
            admin_user = db.query(models.User).filter(models.User.role == "admin").first()
            if db.query(models.User).count() == 0:
                admin = models.User(
                    email=settings.admin_email,
                    password_hash=hash_password(settings.admin_password),
                    role="admin",
                    haulier_id=None,
                    loader_id=None,
                )
                db.add(admin)
                db.commit()
                print(f"Created default admin: {settings.admin_email}", file=sys.stderr)
            elif admin_user and (admin_user.email != settings.admin_email or not verify_password(settings.admin_password, admin_user.password_hash)):
                # Sync existing admin to env vars so Render ADMIN_EMAIL/ADMIN_PASSWORD work
                admin_user.email = settings.admin_email
                admin_user.password_hash = hash_password(settings.admin_password)
                db.commit()
                print(f"Synced admin to: {settings.admin_email}", file=sys.stderr)
        finally:
            db.close()
    except Exception as e:
        print(f"Startup error: {e!r}", file=sys.stderr)
        raise


@app.get("/health", tags=["meta"])
def health_check() -> dict:
    return {"status": "ok"}


app.include_router(hauliers.router, prefix="/api/hauliers", tags=["hauliers"])
app.include_router(vehicles.router, prefix="/api/vehicles", tags=["vehicles"])
app.include_router(loads.router, prefix="/api/loads", tags=["loads"])
app.include_router(matches.router, prefix="/api/matches", tags=["matches"])
app.include_router(alerts.router, prefix="/api/alerts", tags=["alerts"])
app.include_router(planned_routes.router, prefix="/api", tags=["planned-routes"])
app.include_router(pods.router, prefix="/api/pods", tags=["pods"])
app.include_router(payments.router, prefix="/api/payments", tags=["payments"])
app.include_router(upload.router, tags=["upload"])
app.include_router(web.router, tags=["web"])
app.include_router(auth_web.router, tags=["auth"])
app.include_router(haulier_web.router, tags=["haulier-web"])
app.include_router(loader_web.router, tags=["loader-web"])
app.include_router(driver.router)
app.include_router(tracking.router)
