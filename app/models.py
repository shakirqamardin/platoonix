from enum import Enum
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Haulier(Base):
    __tablename__ = "hauliers"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    contact_email: Mapped[str] = mapped_column(String(255), nullable=False)
    contact_phone: Mapped[Optional[str]] = mapped_column(String(50))
    payment_account_id: Mapped[Optional[str]] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )

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
    shipper_name: Mapped[str] = mapped_column(String(255), nullable=False)
    pickup_postcode: Mapped[str] = mapped_column(String(20), nullable=False)
    delivery_postcode: Mapped[str] = mapped_column(String(20), nullable=False)
    pickup_window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    pickup_window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    delivery_window_start: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    delivery_window_end: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    weight_kg: Mapped[Optional[float]] = mapped_column(Float)
    volume_m3: Mapped[Optional[float]] = mapped_column(Float)
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
    __tablename__ = "backhaul_jobs"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id"), nullable=False)
    load_id: Mapped[int] = mapped_column(ForeignKey("loads.id"), nullable=False)

    matched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )
    accepted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

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

