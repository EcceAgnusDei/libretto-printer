from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Monorepo root: api/app/config.py → ../../.env
ROOT_ENV = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_ENV,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    gemini_api_key: str = ""
    cors_origins: str = "http://localhost:3000"
    data_dir: Path = Path(__file__).resolve().parents[2] / "data"
    ingest_batch_size: int = 5
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
