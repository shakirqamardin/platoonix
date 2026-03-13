
"""
GPS tracking endpoints for driver location updates and public tracking page.
"""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from datetime import datetime

from app.database import get_db
from app import models
from app.auth import get_current_admin

router = APIRouter()


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


@router.get("/track/{job_id}", response_class=HTMLResponse)
async def public_tracking_page(
    job_id: int,
    db: Session = Depends(get_db),
):
    """Public tracking page for loaders to see driver progress."""
    # TODO: Create tracking HTML template
    return "<h1>Tracking page - coming soon!</h1>"
