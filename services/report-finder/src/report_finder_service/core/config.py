from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_DOWNLOAD_DIR = PROJECT_ROOT / "data" / "report-finder" / "downloads"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="REPORT_FINDER_",
        env_file=".env",
        extra="ignore",
    )

    env: str = "dev"
    allowed_sources: str = Field(default="cninfo")
    enable_company_mapping_agent: bool = False
    company_mapping_base_url: str = "https://api.openai.com/v1"
    company_mapping_model: str = "gpt-4.1-mini"
    company_mapping_api_key: str | None = None
    http_timeout_seconds: float = 20.0
    sec_user_agent: str = "ReportFinderService contact@example.com"
    download_dir: str = str(DEFAULT_DOWNLOAD_DIR)
    download_overwrite: bool = False
    download_index_file: str = ".download_index.json"

    @property
    def allowed_source_list(self) -> list[str]:
        return [item.strip() for item in self.allowed_sources.split(",") if item.strip()]


settings = Settings()
