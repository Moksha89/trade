"""Application settings loaded from environment / .env.

Only the subset of settings needed for Phase 0 is wired up here; the full
contract lives in `.env.example` and will be consumed in later phases.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # App / runtime
    app_env: str = "development"
    execution_mode: str = "paper"  # paper | demo | approval | live
    auto_mode_enabled: bool = False
    hedging_enabled: bool = False
    timezone: str = "Asia/Dubai"

    # Dashboard / API auth
    admin_username: str = "admin"
    admin_password: str = "change_me_strong"
    jwt_secret: str = "change_me_random_long_secret"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 12 * 60
    public_base_url: str = "http://VPS_IP"

    # CORS (comma-separated origins; "*" allows all — fine behind Nginx on VPS IP)
    cors_origins: str = "*"


settings = Settings()
