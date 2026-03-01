from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app import models, schemas
from app.database import get_db


router = APIRouter()


@router.post("/", response_model=schemas.HaulierRead, status_code=status.HTTP_201_CREATED)
def create_haulier(
    haulier_in: schemas.HaulierCreate,
    db: Session = Depends(get_db),
) -> models.Haulier:
    haulier = models.Haulier(
        name=haulier_in.name,
        contact_email=haulier_in.contact_email,
        contact_phone=haulier_in.contact_phone,
        payment_account_id=haulier_in.payment_account_id,
    )
    db.add(haulier)
    db.commit()
    db.refresh(haulier)
    return haulier


@router.get("/{haulier_id}", response_model=schemas.HaulierRead)
def get_haulier(
    haulier_id: int,
    db: Session = Depends(get_db),
) -> models.Haulier:
    haulier = db.get(models.Haulier, haulier_id)
    if not haulier:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Haulier not found")
    return haulier


@router.get("/", response_model=list[schemas.HaulierRead])
def list_hauliers(
    db: Session = Depends(get_db),
) -> list[models.Haulier]:
    return db.query(models.Haulier).order_by(models.Haulier.created_at.desc()).all()

