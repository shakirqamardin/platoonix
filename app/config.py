from typing import Optional

from pydantic import AnyUrl, field_validator
from pydantic_settings import BaseSettings


def _strip_empty_optional_str(v: Optional[str]) -> Optional[str]:
    if v is None or not isinstance(v, str):
        return v
    s = v.strip()
    return s if s else None


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

    @field_validator("stripe_secret_key", mode="before")
    @classmethod
    def strip_stripe_secret_key(cls, v: Optional[str]) -> Optional[str]:
        return _strip_empty_optional_str(v)

    # Road routing (matching, pricing, distances): at least one recommended.
    # OpenRouteService: free tier at https://openrouteservice.org/ — matrix + HGV profile.
    openrouteservice_api_key: Optional[str] = None
    # Mapbox Matrix API — good Google alternative (https://www.mapbox.com/pricing/).
    mapbox_access_token: Optional[str] = None
    # Google Distance Matrix (optional last resort if ORS and Mapbox unset or fail).
    google_maps_api_key: Optional[str] = None

    @field_validator("openrouteservice_api_key", "mapbox_access_token", "google_maps_api_key", mode="before")
    @classmethod
    def strip_routing_api_keys(cls, v: Optional[str]) -> Optional[str]:
        """Avoid blank / whitespace-only env values breaking auth (Railway copy-paste)."""
        return _strip_empty_optional_str(v)

    # Public site URL for share links (WhatsApp, etc.). Override via PUBLIC_APP_BASE_URL in env.
    public_app_base_url: str = "https://web-production-7ca42.up.railway.app"

    # Optional: Platoonix support WhatsApp (digits only, country code included, e.g. 447123456789).
    # When set, driver "Get help" opens wa.me to this number with a prefilled job context message.
    support_whatsapp_e164: Optional[str] = None

    # Vehicle insurance certificate files (PDF/images). Default: <project>/data/insurance
    insurance_upload_dir: Optional[str] = None

    # Cancellation policy (hours / GBP) — see Terms §9 and cancellation_policy service
    free_cancellation_hours: int = 24
    warning_cancellation_hours: int = 12
    penalty_cancellation_hours: int = 2
    cancellation_fee_warning_gbp: float = 25.0
    cancellation_fee_penalty_gbp: float = 50.0
    # Loader matched loads (backhaul): two tiers — free ≥6h before pickup; £25 if 0<h<6; blocked after pickup
    loader_matched_free_cancellation_hours: int = 6
    loader_matched_penalty_fee_gbp: float = 25.0
    no_show_penalty_gbp: float = 100.0
    no_show_compensation_loader_gbp: float = 50.0
    suspension_strike_threshold: int = 3
    probation_strike_threshold: int = 2

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


def get_settings() -> Settings:
    """New Settings() each call so env changes apply after redeploy without stale @lru_cache."""
    return Settings()

