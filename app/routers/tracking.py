
"""
GPS tracking endpoints for driver location updates and public tracking page.
"""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from datetime import datetime

from app.database import get_db
from app import models
from app.auth import get_current_admin

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.post("/api/tracking/update")
async def update_location(
    request: Request,
    job_id: int,
    latitude: float,
    longitude: float,
    status: Optional[str] = "en_route_to_pickup",
    db: Session = Depends(get_db),
):
    """Driver updates their GPS location for an active job."""
    data = await request.json()
    
    job_id = data.get('job_id')
    latitude = data.get('latitude')
    longitude = data.get('longitude')
    status = data.get('status', 'en_route_to_pickup')
    
    if not all([job_id, latitude, longitude]):
        raise HTTPException(status_code=400, detail="Missing required fields")
    
    # Get the job
    job = db.get(models.BackhaulJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    # Create or update location
    latest = (
        db.query(models.DriverLocation)
        .filter(models.DriverLocation.job_id == job_id)
        .order_by(models.DriverLocation.updated_at.desc())
        .first()
    )
    
    if latest:
        # Update existing
        latest.latitude = latitude
        latest.longitude = longitude
        latest.status = status
        latest.updated_at = datetime.utcnow()
    else:
        # Create new
        location = models.DriverLocation(
            job_id=job_id,
            driver_id=job.vehicle.haulier_id,
            latitude=latitude,
            longitude=longitude,
            status=status,
        )
        db.add(location)
    
    # Update job tracking status
    job.tracking_active = True
    if not job.tracking_started_at:
        job.tracking_started_at = datetime.utcnow()
    
    db.commit()
    
    return {"success": True, "status": status}


@router.get("/api/tracking/location/{job_id}")
async def get_location(
    job_id: int,
    db: Session = Depends(get_db),
):
    """Public endpoint: get driver's latest location for a job."""
    location = (
        db.query(models.DriverLocation)
        .filter(models.DriverLocation.job_id == job_id)
        .order_by(models.DriverLocation.updated_at.desc())
        .first()
    )
    
    if not location:
        raise HTTPException(status_code=404, detail="No tracking data yet")
    
    return {
        "latitude": float(location.latitude),
        "longitude": float(location.longitude),
        "status": location.status,
        "updated_at": location.updated_at.isoformat(),
    }


@router.get("/driver/track/{job_id}", response_class=HTMLResponse)
async def driver_tracking_page(
    request: Request,
    job_id: int,
    db: Session = Depends(get_db),
):
    """Driver tracking page - share GPS location."""
    job = db.get(models.BackhaulJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    load = db.get(models.Load, job.load_id)
    loader = db.get(models.Loader, load.loader_id) if load else None
    
    return templates.TemplateResponse("driver_track.html", {
        "request": request,
        "job": job,
        "load": load,
        "loader_contact": loader.contact_name if loader else None,
        "loader_phone": loader.contact_phone if loader else None,
    })


@router.get("/track/{job_id}", response_class=HTMLResponse)
async def public_tracking_page(
    request: Request,
    job_id: int,
    db: Session = Depends(get_db),
):
    """Public tracking page for loaders to see driver progress."""
    job = db.get(models.BackhaulJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    load = db.get(models.Load, job.load_id)
    vehicle = db.get(models.Vehicle, job.vehicle_id)
    haulier = db.get(models.Haulier, vehicle.haulier_id) if vehicle else None
    
    return templates.TemplateResponse("public_track.html", {
        "request": request,
        "job": job,
        "load": load,
        "vehicle": vehicle,
        "driver_name": haulier.contact_name if haulier else None,
        "driver_phone": haulier.contact_phone if haulier else None,
    })
