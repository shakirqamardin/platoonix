from functools import lru_cache
from typing import Optional

from pydantic import AnyUrl, field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "Backhaul Logistics Platform"
    database_url: AnyUrl

    @field_validator("database_url")
    @classmethod
    def database_url_must_be_postgres(cls, v: AnyUrl) -> AnyUrl:
        s = str(v)
        if "postgresql" not in s and "postgres" not in s:
            raise ValueError(
                f"DATABASE_URL must be a PostgreSQL URL (postgresql://...). "
                f"Check Render Environment: you may have set the wrong value. Got: {s[:80]!r}"
            )
        return v

    # External integrations (placeholders for now)
    dvla_api_key: Optional[str] = None
    dvla_base_url: str = "https://driver-vehicle-licensing.api.gov.uk"

    # Matching defaults
    default_backhaul_radius_miles: int = 25

    # Platform fee: single 8% of job value (deducted from haulier payout)
    platform_fee_percent: float = 8.0

    # Auth: session secret (set in production)
    session_secret_key: str = "change-me-in-production-platoonix"
    admin_email: str = "admin@platoonix.local"
    admin_password: str = "change-me"

    # Email (optional): leave unset to skip sending
    smtp_host: Optional[str] = None
    smtp_port: int = 587
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_from_email: str = "noreply@platoonix.com"

    # Stripe (optional): for payouts to hauliers via Connect. Leave unset to skip.
    stripe_secret_key: Optional[str] = None

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache
def get_settings() -> Settings:
    return Settings()

