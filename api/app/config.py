from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_ENV = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_ENV,
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.7-flash"
    cors_origins: str = "http://localhost:3000"
    data_dir: Path = Path(__file__).resolve().parents[2] / "data"
    batch_size: int = Field(
        default=5,
        validation_alias=AliasChoices(
            "BATCH_SIZE",
            "batch_size",
            "INGEST_BATCH_SIZE",
            "ingest_batch_size",
            "EXTRACT_BATCH_SIZE",
            "extract_batch_size",
        ),
    )
    extract_max_retries: int = 2
    extract_retry_backoff_seconds: float = 2.0
    extract_resume_cooldown_seconds: float = 5.0
    gemini_min_interval_seconds: float = 5.0
    gemini_quota_timezone: str = "America/Los_Angeles"
    gemini_max_batches_per_day: int = Field(
        default=250,
        validation_alias=AliasChoices(
            "GEMINI_MAX_BATCHES_PER_DAY",
            "gemini_max_batches_per_day",
            "GEMINI_MAX_REQUESTS_PER_DAY",
            "gemini_max_requests_per_day",
        ),
    )
    max_upload_bytes: int = 50 * 1024 * 1024
    max_pages_per_session: int = 500
    render_dpi: int = 150
    max_image_edge: int = 2048

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def gemini_configured(self) -> bool:
        return bool(self.gemini_api_key.strip())

    @property
    def sessions_dir(self) -> Path:
        return self.data_dir / "sessions"


settings = Settings()
