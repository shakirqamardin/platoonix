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

    # DVLA Vehicle Enquiry API — set env var DVLA_API_KEY (e.g. Railway / Render)
    dvla_api_key: Optional[str] = None
    dvla_base_url: str = "https://driver-vehicle-licensing.api.gov.uk"

    # Matching defaults
    default_backhaul_radius_miles: int = 25

    # Loader platform fee (charged at collection, on top of load value): max(minimum, percent of load)
    loader_flat_fee_gbp: float = 5.0  # minimum £ when 2% would be lower
    loader_fee_percent_of_load: float = 2.0  # percentage of load value when that is >= minimum

    # Pallets → volume: 1 pallet = this many m³ (euro pallet ~1.2)
    pallet_volume_m3: float = 1.2

    # Platform fee: single 8% of job value (deducted from haulier payout)
    platform_fee_percent: float = 8.0

    # Auth: session secret (set in production)
    session_secret_key: str = "change-me-in-production-platoonix"
    admin_email: str = "admin@platoonix.local"
    admin_password: str = "change-me"

    # Email (optional): SendGrid API key, or SMTP below. Set SMTP_FROM_EMAIL to a verified SendGrid sender.
    smtp_host: Optional[str] = None
    smtp_port: int = 587
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_from_email: str = "noreply@platoonix.com"
    sendgrid_api_key: Optional[str] = None
    email_send_timeout_seconds: int = 15
    email_retry_count: int = 2

    # Stripe (optional): for payouts to hauliers via Connect. Leave unset to skip.
    stripe_secret_key: Optional[str] = None

    # Road routing (matching, pricing, distances): at least one recommended.
    # OpenRouteService: free tier at https://openrouteservice.org/ — matrix + HGV profile.
    openrouteservice_api_key: Optional[str] = None
    # Mapbox Matrix API — good Google alternative (https://www.mapbox.com/pricing/).
    mapbox_access_token: Optional[str] = None
    # Google Distance Matrix (optional last resort if ORS and Mapbox unset or fail).
    google_maps_api_key: Optional[str] = None

    # Public site URL for share links (WhatsApp, etc.). Override via PUBLIC_APP_BASE_URL in env.
    public_app_base_url: str = "https://web-production-7ca42.up.railway.app"

    # Vehicle insurance certificate files (PDF/images). Default: <project>/data/insurance
    insurance_upload_dir: Optional[str] = None

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache
def get_settings() -> Settings:
    return Settings()

