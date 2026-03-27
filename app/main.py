import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
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


@app.get("/sw.js", include_in_schema=False)
def service_worker() -> FileResponse:
    """Serve service worker from root so it can control the whole app scope."""
    return FileResponse(static_dir / "sw.js", media_type="application/javascript")


@app.get("/manifest.webmanifest", include_in_schema=False)
def web_manifest() -> FileResponse:
    """Serve PWA manifest from root."""
    return FileResponse(static_dir / "manifest.webmanifest", media_type="application/manifest+json")


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
            try:
                conn.execute(text(
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS loader_id INTEGER REFERENCES loaders(id)"
                ))
                conn.commit()
            except Exception as e:
                conn.rollback()
                if "already exists" not in str(e).lower() and "duplicate" not in str(e).lower():
                    print(f"Migration users.loader_id: {e!r}", file=sys.stderr)
            for col_sql in (
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS full_name VARCHAR(255)",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS phone VARCHAR(50)",
            ):
                try:
                    conn.execute(text(col_sql))
                    conn.commit()
                except Exception as e:
                    conn.rollback()
                    if "already exists" not in str(e).lower() and "duplicate" not in str(e).lower():
                        print(f"Migration users column: {e!r}", file=sys.stderr)
            try:
                conn.execute(text(
                    "ALTER TABLE backhaul_jobs ADD COLUMN IF NOT EXISTS driver_id INTEGER REFERENCES drivers(id)"
                ))
                conn.commit()
            except Exception as e:
                conn.rollback()
                if "already exists" not in str(e).lower() and "duplicate" not in str(e).lower():
                    print(f"Migration backhaul_jobs.driver_id: {e!r}", file=sys.stderr)
            # drivers table existed before final schema; ensure required columns are present
            for col_sql in (
                "ALTER TABLE drivers ADD COLUMN IF NOT EXISTS email VARCHAR(255)",
                "ALTER TABLE drivers ADD COLUMN IF NOT EXISTS phone VARCHAR(50)",
                "ALTER TABLE drivers ADD COLUMN IF NOT EXISTS password_hash VARCHAR(255)",
                "ALTER TABLE drivers ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITH TIME ZONE",
            ):
                try:
                    conn.execute(text(col_sql))
                    conn.commit()
                except Exception as e:
                    conn.rollback()
                    if "already exists" not in str(e).lower() and "duplicate" not in str(e).lower():
                        print(f"Migration drivers columns: {e!r}", file=sys.stderr)
            try:
                conn.execute(text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_drivers_email ON drivers(email)"
                ))
                conn.commit()
            except Exception as e:
                conn.rollback()
                if "already exists" not in str(e).lower() and "duplicate" not in str(e).lower():
                    print(f"Migration drivers email index: {e!r}", file=sys.stderr)
            try:
                conn.execute(text(
                    "ALTER TABLE drivers ADD COLUMN IF NOT EXISTS vehicle_id INTEGER REFERENCES vehicles(id)"
                ))
                conn.commit()
            except Exception as e:
                conn.rollback()
                if "already exists" not in str(e).lower() and "duplicate" not in str(e).lower():
                    print(f"Migration drivers.vehicle_id: {e!r}", file=sys.stderr)
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
            for col_sql in (
                "ALTER TABLE loads ADD COLUMN IF NOT EXISTS booking_ref VARCHAR(255)",
                "ALTER TABLE loads ADD COLUMN IF NOT EXISTS booking_name VARCHAR(255)",
            ):
                try:
                    conn.execute(text(col_sql))
                    conn.commit()
                except Exception as e:
                    conn.rollback()
                    if "already exists" not in str(e).lower() and "duplicate" not in str(e).lower():
                        print(f"Migration loads booking columns: {e!r}", file=sys.stderr)
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
            # loaders: Stripe Customer for charging the loader when a job completes
            try:
                conn.execute(text(
                    "ALTER TABLE loaders ADD COLUMN IF NOT EXISTS stripe_customer_id VARCHAR(255)"
                ))
                conn.commit()
            except Exception as e:
                conn.rollback()
                if "already exists" not in str(e).lower() and "duplicate" not in str(e).lower():
                    print(f"Migration loaders.stripe_customer_id: {e!r}", file=sys.stderr)
            for col_sql in (
                "ALTER TABLE payments ADD COLUMN IF NOT EXISTS flat_fee_gbp DOUBLE PRECISION DEFAULT 0",
                "ALTER TABLE payments ADD COLUMN IF NOT EXISTS loader_stripe_payment_intent_id VARCHAR(255)",
            ):
                try:
                    conn.execute(text(col_sql))
                    conn.commit()
                except Exception as e:
                    conn.rollback()
                    if "already exists" not in str(e).lower() and "duplicate" not in str(e).lower():
                        print(f"Migration payments column: {e!r}", file=sys.stderr)
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
# Force rebuild Thu 19 Mar 2026 11:33:35 GMT
# Rebuild Thu 19 Mar 2026 12:21:49 GMT
