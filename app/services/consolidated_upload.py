"""
Consolidated CSV upload: Company + related entities in one file.
"""
from typing import Dict, List, Tuple
from sqlalchemy.orm import Session
from app import models
import csv
import io


def import_hauliers_vehicles(db: Session, content: bytes, filename: str) -> Dict:
    """
    Import hauliers and their vehicles from consolidated CSV.
    Columns: company_name, contact_email, contact_phone, contact_name, 
             registration, vehicle_type, trailer_type, capacity_weight_kg, 
             capacity_pallets, base_postcode
    """
    # Parse CSV
    text = content.decode('utf-8-sig')
    reader = csv.DictReader(io.StringIO(text))
    
    companies = {}  # {email: haulier_obj}
    vehicles_data = []
    errors = []
    
    for idx, row in enumerate(reader, start=2):
        try:
            email = (row.get('contact_email') or '').strip()
            company_name = (row.get('company_name') or '').strip()
            
            if not email or not company_name:
                errors.append(f"Row {idx}: Missing company_name or contact_email")
                continue
            
            # Create or get company
            if email not in companies:
                # Check if exists
                existing = db.query(models.Haulier).filter(
                    models.Haulier.contact_email == email
                ).first()
                
                if existing:
                    companies[email] = existing
                else:
                    # Create new
                    haulier = models.Haulier(
                        name=company_name,
                        contact_email=email,
                        contact_phone=(row.get('contact_phone') or '').strip() or None,
                        base_postcode=(row.get('base_postcode') or '').strip() or None,
                        contact_name=(row.get('contact_name') or '').strip() or None,
                    )
                    db.add(haulier)
                    db.flush()  # Get ID
                    companies[email] = haulier
            
            # Add vehicle
            reg = (row.get('registration') or '').strip().upper()
            if reg:
                vehicle_type = (row.get('vehicle_type') or '').strip().lower()
                trailer_type = (row.get('trailer_type') or '').strip().lower()
                
                # Check for duplicate registration
                existing_vehicle = db.query(models.Vehicle).filter(
                    models.Vehicle.registration == reg
                ).first()
                
                if existing_vehicle:
                    errors.append(f"Row {idx}: Vehicle {reg} already exists")
                    continue
                
                capacity_pallets = row.get('capacity_pallets', '').strip()
                capacity_weight = row.get('capacity_weight_kg', '').strip()
                
                vehicle = models.Vehicle(
                    haulier_id=companies[email].id,
                    registration=reg,
                    vehicle_type=vehicle_type or None,
                    trailer_type=trailer_type or None,
                    capacity_pallets=int(capacity_pallets) if capacity_pallets else None,
                    capacity_weight_kg=int(capacity_weight) if capacity_weight else None,
                )
                db.add(vehicle)
                vehicles_data.append(reg)
        
        except Exception as e:
            errors.append(f"Row {idx}: {str(e)}")
    
    db.commit()
    
    return {
        "companies_created": len(companies),
        "vehicles_created": len(vehicles_data),
        "errors": errors,
        "company_names": [h.name for h in companies.values()],
        "vehicle_registrations": vehicles_data,
    }


def import_loaders_loads(db: Session, content: bytes, filename: str) -> Dict:
    """
    Import loaders and their loads from consolidated CSV.
    Columns: company_name, contact_email, contact_phone, contact_name,
             pickup_postcode, delivery_postcode, weight_kg, pallets, 
             pickup_date, vehicle_type_required, trailer_type_required
    """
    text = content.decode('utf-8-sig')
    reader = csv.DictReader(io.StringIO(text))
    
    companies = {}  # {email: loader_obj}
    loads_data = []
    errors = []
    
    for idx, row in enumerate(reader, start=2):
        try:
            email = (row.get('contact_email') or '').strip()
            company_name = (row.get('company_name') or '').strip()
            
            if not email or not company_name:
                errors.append(f"Row {idx}: Missing company_name or contact_email")
                continue
            
            # Create or get company
            if email not in companies:
                existing = db.query(models.Loader).filter(
                    models.Loader.contact_email == email
                ).first()
                
                if existing:
                    companies[email] = existing
                else:
                    loader = models.Loader(
                        name=company_name,
                        contact_email=email,
                        contact_phone=(row.get('contact_phone') or '').strip() or None,
                        contact_name=(row.get('contact_name') or '').strip() or None,
                    )
                    db.add(loader)
                    db.flush()
                    companies[email] = loader
            
            # Add load
            pickup = (row.get('pickup_postcode') or '').strip().upper()
            delivery = (row.get('delivery_postcode') or '').strip().upper()
            
            if pickup and delivery:
                weight_kg = row.get('weight_kg', '').strip()
                pallets = row.get('pallets', '').strip()
                
                # Build requirements dict
                requirements = {}
                veh_type = (row.get('vehicle_type_required') or '').strip().lower()
                trailer_type = (row.get('trailer_type_required') or '').strip().lower()
                
                if veh_type and veh_type != 'any':
                    requirements['vehicle_type'] = veh_type
                if trailer_type and trailer_type != 'any':
                    requirements['trailer_type'] = trailer_type
                
                load = models.Load(
                    loader_id=companies[email].id,
                    pickup_postcode=pickup,
                    delivery_postcode=delivery,
                    weight_kg=int(weight_kg) if weight_kg else None,
                    pallets=int(pallets) if pallets else None,
                    requirements=requirements if requirements else None,
                    status=models.LoadStatusEnum.OPEN.value,
                )
                db.add(load)
                loads_data.append(f"{pickup} → {delivery}")
        
        except Exception as e:
            errors.append(f"Row {idx}: {str(e)}")
    
    db.commit()
    
    return {
        "companies_created": len(companies),
        "loads_created": len(loads_data),
        "errors": errors,
        "company_names": [l.name for l in companies.values()],
        "load_routes": loads_data,
    }
