from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_ENV = Path(__file__).resolve().parents[2] / ".env"

ThinkingLevelName = Literal["low", "medium"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_ENV,
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.8-flash"
    gemini_thinking_level: ThinkingLevelName = Field(
        default="low",
        validation_alias=AliasChoices(
            "GEMINI_THINKING_LEVEL",
            "gemini_thinking_level",
        ),
    )
    devlog: bool = Field(
        default=False,
        validation_alias=AliasChoices("DEVLOG", "devlog"),
    )
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
    extract_supervisor_interval_seconds: float = 5.0
    extract_max_concurrent_workers: int = Field(
        default=1,
        validation_alias=AliasChoices(
            "EXTRACT_MAX_CONCURRENT_WORKERS",
            "extract_max_concurrent_workers",
        ),
    )
    gemini_min_interval_seconds: float = 5.0
    gemini_request_timeout_seconds: float = 300.0
    extract_stall_grace_seconds: float = 60.0
    gemini_quota_timezone: str = "America/Los_Angeles"
    gemini_max_batches_per_day: int = Field(
        default=300,
        validation_alias=AliasChoices(
            "GEMINI_MAX_BATCHES_PER_DAY",
            "gemini_max_batches_per_day",
            "GEMINI_MAX_REQUESTS_PER_DAY",
            "gemini_max_requests_per_day",
        ),
    )
    allow_unlimited_gemini_quota: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "ALLOW_UNLIMITED_GEMINI_QUOTA",
            "allow_unlimited_gemini_quota",
        ),
    )
    rate_limit_window_seconds: float = Field(
        default=60.0,
        validation_alias=AliasChoices(
            "RATE_LIMIT_WINDOW_SECONDS",
            "rate_limit_window_seconds",
        ),
    )
    rate_limit_upload_max: int = Field(
        default=20,
        validation_alias=AliasChoices(
            "RATE_LIMIT_UPLOAD_MAX",
            "rate_limit_upload_max",
        ),
    )
    rate_limit_extract_start_max: int = Field(
        default=30,
        validation_alias=AliasChoices(
            "RATE_LIMIT_EXTRACT_START_MAX",
            "rate_limit_extract_start_max",
        ),
    )
    trust_proxy_headers: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "TRUST_PROXY_HEADERS",
            "trust_proxy_headers",
        ),
    )
    force_https: bool = Field(
        default=False,
        validation_alias=AliasChoices("FORCE_HTTPS", "force_https"),
    )
    session_ttl_hours: float = Field(
        default=72.0,
        validation_alias=AliasChoices(
            "SESSION_TTL_HOURS",
            "session_ttl_hours",
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
