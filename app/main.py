from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.routers import alerts, hauliers, vehicles, loads, matches, planned_routes, pods, payments, upload, web

app = FastAPI(
    title="Backhaul Logistics Platform",
    description="API for automated backhaul matching, ULEZ/CAZ-aware routing, and instant payouts.",
    version="0.1.0",
)

# Serve logo and other static assets
static_dir = Path(__file__).resolve().parent.parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


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

