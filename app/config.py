from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: str = "development"
    app_port: int = 8011
    log_level: str = "INFO"

    database_url: str = "sqlite+aiosqlite:///./data/fireworkrouter.db"
    redis_url: str | None = None

    admin_token: str = Field(min_length=8)
    admin_password_hash: str = Field(min_length=20, description="bcrypt hash of admin password")
    upstream_key_fernet_key: str = Field(min_length=32)

    session_token_secret: str = Field(default="please-change-me-please-change-me-32+chars", min_length=32)
    session_token_ttl_hours: int = 24

    gateway_max_retry_credentials: int = 3
    gateway_max_retry_interval_s: int = 30

    fireworks_inference_base_url: str = "https://api.fireworks.ai/inference/v1"
    fireworks_admin_base_url: str = "https://api.fireworks.ai/v1"
    gateway_default_timeout_s: int = 120
    gateway_max_retry: int = 3

    probe_interval_minutes: int = 15
    probe_concurrency: int = 20
    probe_min_balance_usd: float = 0.5
    probe_on_startup: bool = True

    http_proxy: str | None = None
    https_proxy: str | None = None

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def data_dir(self) -> Path:
        return Path(__file__).resolve().parent.parent / "data"

    @property
    def proxy_url(self) -> str | None:
        return self.https_proxy or self.http_proxy


@lru_cache
def get_settings() -> Settings:
    settings = Settings()  # type: ignore[call-arg]
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings
