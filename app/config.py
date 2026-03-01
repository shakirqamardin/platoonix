from functools import lru_cache
from typing import Optional

from pydantic import AnyUrl
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "Backhaul Logistics Platform"
    database_url: AnyUrl

    # External integrations (placeholders for now)
    dvla_api_key: Optional[str] = None
    dvla_base_url: str = "https://driver-vehicle-licensing.api.gov.uk"

    # Matching defaults
    default_backhaul_radius_miles: int = 25

    # Platform fee: single 8% of job value (deducted from haulier payout)
    platform_fee_percent: float = 8.0

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache
def get_settings() -> Settings:
    return Settings()

