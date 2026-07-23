from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Market Report Finder Service"
    deployment_profile: str = Field(default="local", validation_alias="SIQ_DEPLOYMENT_PROFILE")
    internal_service_token: str | None = Field(
        default=None,
        validation_alias="SIQ_MARKET_REPORT_FINDER_TOKEN",
    )
    download_dir: Path = Field(default=Path("downloads"), validation_alias="MARKET_REPORT_DOWNLOAD_DIR")
    http_timeout_seconds: float = 30.0
    sec_user_agent: str = Field(
        default="market-report-finder-service/0.1 contact@example.com",
        validation_alias="SEC_USER_AGENT",
    )
    sec_max_requests_per_second: float = Field(default=8.0, validation_alias="SEC_MAX_REQUESTS_PER_SECOND")
    hkex_max_requests_per_second: float = Field(default=4.0, validation_alias="HKEX_MAX_REQUESTS_PER_SECOND")
    dart_api_key: str | None = Field(default=None, validation_alias="DART_API_KEY")
    dart_max_requests_per_second: float = Field(default=3.0, validation_alias="DART_MAX_REQUESTS_PER_SECOND")
    edinet_api_key: str | None = Field(default=None, validation_alias="EDINET_API_KEY")
    edinet_max_requests_per_second: float = Field(default=3.0, validation_alias="EDINET_MAX_REQUESTS_PER_SECOND")
    tdnet_api_key: str | None = Field(default=None, validation_alias="TDNET_API_KEY")
    tdnet_recent_days: int = Field(default=120, validation_alias="TDNET_RECENT_DAYS")
    tdnet_max_pages_per_day: int = Field(default=12, validation_alias="TDNET_MAX_PAGES_PER_DAY")
    tdnet_max_requests_per_second: float = Field(default=2.0, validation_alias="TDNET_MAX_REQUESTS_PER_SECOND")
    krx_kind_enabled: bool = Field(default=False, validation_alias="KRX_KIND_ENABLED")
    eu_user_agent: str = Field(
        default="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36 SIQ-market-report-finder/0.1",
        validation_alias="EU_USER_AGENT",
    )
    eu_max_requests_per_second: float = Field(default=1.0, validation_alias="EU_MAX_REQUESTS_PER_SECOND")
    allow_manual_unverified_downloads: bool = Field(
        default=False,
        validation_alias="MARKET_REPORT_ALLOW_MANUAL_UNVERIFIED_DOWNLOADS",
    )
    download_overwrite: bool = False
    download_index_file: str = ".download_index.json"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
