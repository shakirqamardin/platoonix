from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, EmailStr, Field


class HaulierBase(BaseModel):
    name: str = Field(..., example="ABC Transport Ltd")
    contact_email: EmailStr
    contact_phone: Optional[str] = None


class HaulierCreate(HaulierBase):
    payment_account_id: Optional[str] = Field(
        default=None, description="External payment provider account ID (e.g. Stripe account ID)."
    )


class HaulierRead(HaulierBase):
    id: int
    payment_account_id: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


# Common trailer/body types for matching (curtain sider, box, etc.)
TRAILER_TYPE_VALUES = [
    "curtain_sider", "box", "flatbed", "refrigerated", "tautliner",
    "tanker", "tipper", "low_loader", "other",
]


class VehicleBase(BaseModel):
    registration: str = Field(..., example="AB12CDE")
    vehicle_type: str = Field(..., example="artic", description="e.g. artic, rigid, van")
    trailer_type: Optional[str] = Field(
        default=None,
        description="Body/trailer type for matching: curtain_sider, box, flatbed, refrigerated, tautliner, tanker, tipper, low_loader",
    )
    capacity_weight_kg: Optional[float] = None
    capacity_volume_m3: Optional[float] = None


class VehicleCreate(VehicleBase):
    haulier_id: int


class VehicleRead(VehicleBase):
    id: int
    haulier_id: int
    euro_status: Optional[str]
    fuel_type: Optional[str]
    dvla_raw: Optional[dict]

    class Config:
        from_attributes = True


class LoadBase(BaseModel):
    shipper_name: str
    pickup_postcode: str
    delivery_postcode: str
    pickup_window_start: datetime
    pickup_window_end: datetime
    delivery_window_start: Optional[datetime] = None
    delivery_window_end: Optional[datetime] = None
    weight_kg: Optional[float] = None
    volume_m3: Optional[float] = None
    pallets: Optional[float] = None
    requirements: Optional[dict[str, Any]] = None
    budget_gbp: Optional[float] = None


class LoadCreate(LoadBase):
    pass


class LoadRead(LoadBase):
    id: int
    status: str
    created_at: datetime

    class Config:
        from_attributes = True


class BackhaulJobRead(BaseModel):
    id: int
    vehicle_id: int
    load_id: int
    matched_at: datetime
    accepted_at: Optional[datetime]
    collected_at: Optional[datetime]
    completed_at: Optional[datetime]
    ulez_caz_status: Optional[str]

    class Config:
        from_attributes = True


class DriverJobRead(BaseModel):
    """Job for driver app: timeline + load details + live GPS."""
    id: int
    vehicle_id: int
    load_id: int
    pickup_postcode: str
    delivery_postcode: str
    shipper_name: str
    matched_at: datetime
    reached_pickup_at: Optional[datetime] = None
    collected_at: Optional[datetime] = None
    departed_pickup_at: Optional[datetime] = None
    reached_delivery_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    last_lat: Optional[float] = None
    last_lng: Optional[float] = None
    location_updated_at: Optional[datetime] = None
    payment_status: Optional[str] = None


class DriverLocationUpdate(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)


class DriverStatusUpdate(BaseModel):
    status: str = Field(
        ...,
        description="One of: reached_pickup, collected, departed_pickup, reached_delivery",
    )


class LoadMatchResult(BaseModel):
    """A load suggested for backhaul with distance from search origin."""
    load: LoadRead
    distance_miles: float


class BackhaulAssignRequest(BaseModel):
    vehicle_id: int
    load_id: int
    amount_gbp: float = Field(..., description="Job value (what loader pays). Platform takes platform_fee_percent; haulier gets the rest.")
    fee_gbp: Optional[float] = Field(
        default=None,
        description="Optional override. If omitted, fee = amount_gbp * platform_fee_percent (e.g. 8%).",
    )


class PODCreate(BaseModel):
    backhaul_job_id: int
    file_url: str
    notes: Optional[str] = None


class PODRead(BaseModel):
    id: int
    backhaul_job_id: int
    file_url: str
    notes: Optional[str]
    status: str
    created_at: datetime
    confirmed_at: Optional[datetime]

    class Config:
        from_attributes = True


class PaymentRead(BaseModel):
    id: int
    backhaul_job_id: int
    amount_gbp: float
    fee_gbp: float
    net_payout_gbp: float
    provider_payment_id: Optional[str]
    status: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ---- Planned routes (weekly/monthly) and show interest ----

class PlannedLoadBase(BaseModel):
    shipper_name: str
    pickup_postcode: str
    delivery_postcode: str
    day_of_week: int = Field(..., ge=0, le=6, description="0=Monday .. 6=Sunday")
    weight_kg: Optional[float] = None
    volume_m3: Optional[float] = None
    requirements: Optional[dict[str, Any]] = None
    budget_gbp: Optional[float] = None
    recurrence: str = Field(default="weekly", description="weekly or monthly")


class PlannedLoadCreate(PlannedLoadBase):
    pass


class PlannedLoadRead(PlannedLoadBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True


class HaulierRouteBase(BaseModel):
    haulier_id: int
    vehicle_id: int
    empty_at_postcode: str
    day_of_week: int = Field(..., ge=0, le=6, description="0=Monday .. 6=Sunday")
    recurrence: str = Field(default="weekly", description="weekly or monthly")


class HaulierRouteCreate(HaulierRouteBase):
    pass


class HaulierRouteRead(HaulierRouteBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True


class LoadInterestCreate(BaseModel):
    haulier_id: int
    vehicle_id: int
    load_id: Optional[int] = None
    planned_load_id: Optional[int] = None
    status: str = Field(default="expressed", description="expressed, accepted, declined")


class LoadInterestRead(BaseModel):
    id: int
    haulier_id: int
    vehicle_id: int
    load_id: Optional[int]
    planned_load_id: Optional[int]
    status: str
    created_at: datetime

    class Config:
        from_attributes = True

