from enum import Enum
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class UserRoleEnum(str, Enum):
    HAULIER = "haulier"
    LOADER = "loader"
    ADMIN = "admin"


class User(Base):
    """Login account: links to one Haulier or one Loader, or is admin (both null)."""
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # haulier, loader, admin
    haulier_id: Mapped[Optional[int]] = mapped_column(ForeignKey("hauliers.id"), nullable=True)
    loader_id: Mapped[Optional[int]] = mapped_column(ForeignKey("loaders.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )


class PasswordResetToken(Base):
    """One-time token for forgot-password flow. Token stored as SHA-256 hash."""
    __tablename__ = "password_reset_tokens"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)  # sha256 hex
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )

class Loader(Base):
    """Loader/shipper company: owns loads and planned loads."""
    __tablename__ = "loaders"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    contact_email: Mapped[str] = mapped_column(String(255), nullable=False)
    contact_phone: Mapped[Optional[str]] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )
    contact_name: Mapped[Optional[str]] = mapped_column(String(255))

    loads: Mapped[list["Load"]] = relationship("Load", back_populates="loader")
    planned_loads: Mapped[list["PlannedLoad"]] = relationship("PlannedLoad", back_populates="loader")
    

class Haulier(Base):
    __tablename__ = "hauliers"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    contact_email: Mapped[str] = mapped_column(String(255), nullable=False)
    contact_phone: Mapped[Optional[str]] = mapped_column(String(50))
    payment_account_id: Mapped[Optional[str]] = mapped_column(String(255))
    base_postcode: Mapped[Optional[str]] = mapped_column(String(20))
    # Bank details (for payouts if not using Stripe Connect)
    bank_account_name: Mapped[Optional[str]] = mapped_column(String(255))
    sort_code: Mapped[Optional[str]] = mapped_column(String(20))
    account_number: Mapped[Optional[str]] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )
    contact_name: Mapped[Optional[str]] = mapped_column(String(255))
    driver_photo_url: Mapped[Optional[str]] = mapped_column(String(500))
    
    vehicles: Mapped[list["Vehicle"]] = relationship("Vehicle", back_populates="haulier")


class Vehicle(Base):
    __tablename__ = "vehicles"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    haulier_id: Mapped[int] = mapped_column(ForeignKey("hauliers.id"), nullable=False)
    registration: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    vehicle_type: Mapped[str] = mapped_column(String(50), nullable=False)  # artic, rigid, van
    trailer_type: Mapped[Optional[str]] = mapped_column(String(50))  # curtain_sider, box, flatbed, refrigerated, tautliner, tanker, tipper, low_loader
    capacity_weight_kg: Mapped[Optional[float]] = mapped_column(Float)
    capacity_volume_m3: Mapped[Optional[float]] = mapped_column(Float)
    base_postcode: Mapped[Optional[str]] = mapped_column(String(20))  # default empty location for automatic matching

    # DVLA / emissions
    euro_status: Mapped[Optional[str]] = mapped_column(String(20))
    fuel_type: Mapped[Optional[str]] = mapped_column(String(50))
    dvla_raw: Mapped[Optional[dict]] = mapped_column(JSON)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )

    haulier: Mapped["Haulier"] = relationship("Haulier", back_populates="vehicles")


class Trailer(Base):
    __tablename__ = "trailers"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    haulier_id: Mapped[int] = mapped_column(ForeignKey("hauliers.id"), nullable=False)
    trailer_type: Mapped[str] = mapped_column(String(50), nullable=False)  # curtain, box, flat
    description: Mapped[Optional[str]] = mapped_column(String(255))
    capacity_weight_kg: Mapped[Optional[float]] = mapped_column(Float)
    capacity_volume_m3: Mapped[Optional[float]] = mapped_column(Float)
    features: Mapped[Optional[dict]] = mapped_column(JSON)  # tail_lift, double_deck, etc.

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )


class LoadStatusEnum(str, Enum):
    OPEN = "open"
    MATCHED = "matched"
    IN_TRANSIT = "in_transit"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class Load(Base):
    __tablename__ = "loads"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    loader_id: Mapped[Optional[int]] = mapped_column(ForeignKey("loaders.id"), nullable=True)  # who posted this load
    shipper_name: Mapped[str] = mapped_column(String(255), nullable=False)
    pickup_postcode: Mapped[str] = mapped_column(String(20), nullable=False)
    delivery_postcode: Mapped[str] = mapped_column(String(20), nullable=False)
    pickup_window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    pickup_window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    delivery_window_start: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    delivery_window_end: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    weight_kg: Mapped[Optional[float]] = mapped_column(Float)
    volume_m3: Mapped[Optional[float]] = mapped_column(Float)
    pallets: Mapped[Optional[float]] = mapped_column(Float)  # if set, volume_m3 = pallets * 1.2 (display both)
    requirements: Mapped[Optional[dict]] = mapped_column(JSON)
    budget_gbp: Mapped[Optional[float]] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20), default=LoadStatusEnum.OPEN.value)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )


# ---- Planned routes: loaders and hauliers enter weekly/monthly patterns ----

class PlannedLoad(Base):
    """Loader's recurring or planned load (e.g. every Tuesday pickup at X, deliver to Y)."""
    __tablename__ = "planned_loads"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    loader_id: Mapped[Optional[int]] = mapped_column(ForeignKey("loaders.id"), nullable=True)
    shipper_name: Mapped[str] = mapped_column(String(255), nullable=False)
    pickup_postcode: Mapped[str] = mapped_column(String(20), nullable=False)
    delivery_postcode: Mapped[str] = mapped_column(String(20), nullable=False)
    day_of_week: Mapped[int] = mapped_column(nullable=False)  # 0=Mon .. 6=Sun
    weight_kg: Mapped[Optional[float]] = mapped_column(Float)
    volume_m3: Mapped[Optional[float]] = mapped_column(Float)
    requirements: Mapped[Optional[dict]] = mapped_column(JSON)  # e.g. trailer_type
    budget_gbp: Mapped[Optional[float]] = mapped_column(Float)
    recurrence: Mapped[str] = mapped_column(String(20), default="weekly")  # weekly, monthly
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )


class HaulierRoute(Base):
    """Haulier's recurring empty leg (e.g. every Tuesday I'm empty at this postcode with this vehicle)."""
    __tablename__ = "haulier_routes"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    haulier_id: Mapped[int] = mapped_column(ForeignKey("hauliers.id"), nullable=False)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id"), nullable=False)
    empty_at_postcode: Mapped[str] = mapped_column(String(20), nullable=False)
    day_of_week: Mapped[int] = mapped_column(nullable=False)  # 0=Mon .. 6=Sun
    recurrence: Mapped[str] = mapped_column(String(20), default="weekly")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )


class LoadInterest(Base):
    """Haulier has expressed interest in a load (one-off or planned); loader can accept."""
    __tablename__ = "load_interests"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    haulier_id: Mapped[int] = mapped_column(ForeignKey("hauliers.id"), nullable=False)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id"), nullable=False)
    load_id: Mapped[Optional[int]] = mapped_column(ForeignKey("loads.id"))
    planned_load_id: Mapped[Optional[int]] = mapped_column(ForeignKey("planned_loads.id"))
    status: Mapped[str] = mapped_column(String(20), default="expressed")  # expressed, accepted, declined
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )


class BackhaulJob(Base):
    """
    Driver-led timeline: reached_pickup -> collected -> departed_pickup -> reached_delivery -> completed (ePOD).
    Payment: RESERVED until collected (then CAPTURED), PAID_OUT when delivery confirmed.
    """
    __tablename__ = "backhaul_jobs"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id"), nullable=False)
    load_id: Mapped[int] = mapped_column(ForeignKey("loads.id"), nullable=False)

    matched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )
    accepted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    reached_pickup_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    collected_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    departed_pickup_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    reached_delivery_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Live GPS (driver shares location; visible to admin/loader)
    last_lat: Mapped[Optional[float]] = mapped_column(Float)
    last_lng: Mapped[Optional[float]] = mapped_column(Float)
    location_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    route_geometry: Mapped[Optional[dict]] = mapped_column(JSON)
    ulez_caz_status: Mapped[Optional[str]] = mapped_column(String(50))


class PODStatusEnum(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class POD(Base):
    __tablename__ = "pods"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    backhaul_job_id: Mapped[int] = mapped_column(
        ForeignKey("backhaul_jobs.id"), nullable=False
    )
    file_url: Mapped[str] = mapped_column(String(512), nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default=PODStatusEnum.PENDING.value)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class PaymentStatusEnum(str, Enum):
    RESERVED = "reserved"
    CAPTURED = "captured"
    PAID_OUT = "paid_out"
    FAILED = "failed"


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    backhaul_job_id: Mapped[int] = mapped_column(
        ForeignKey("backhaul_jobs.id"), nullable=False
    )
    amount_gbp: Mapped[float] = mapped_column(Float, nullable=False)
    fee_gbp: Mapped[float] = mapped_column(Float, default=0.0)
    net_payout_gbp: Mapped[float] = mapped_column(Float, nullable=False)
    provider_payment_id: Mapped[Optional[str]] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), default=PaymentStatusEnum.RESERVED.value)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )
class DriverLocation(Base):
    __tablename__ = "driver_locations"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("backhaul_jobs.id"), nullable=False)
    driver_id: Mapped[int] = mapped_column(nullable=False)
    latitude: Mapped[float] = mapped_column(Numeric(10, 7), nullable=False)
    longitude: Mapped[float] = mapped_column(Numeric(10, 7), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="en_route_to_pickup")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )
