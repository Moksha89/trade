"""Application settings loaded from environment / .env.

These are process-level / infrastructure settings. Mutable trading configuration
(risk limits, strategy toggles, AI options) lives in the database so it can be
edited from the dashboard at runtime — see `app.models.SettingRow` and
`app.services.settings_store`.
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

    cors_origins: str = "*"

    # Database — defaults to a local SQLite file for the prototype; set
    # DATABASE_URL to a postgresql+psycopg://… URL in production.
    database_url: str = "sqlite:///./trading.db"

    # Redis (reserved for later phases)
    redis_url: str = "redis://redis:6379/0"

    # Capital.com broker
    capital_environment: str = "demo"  # demo | live
    capital_api_key: str = ""
    capital_identifier: str = ""
    capital_password: str = ""
    capital_demo_base_url: str = "https://demo-api-capital.backend-capital.com"
    capital_live_base_url: str = "https://api-capital.backend-capital.com"
    capital_ws_url: str = "wss://api-streaming-capital.backend-capital.com/connect"

    # AI providers
    ai_provider: str = "claude"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-5-20250929"
    openai_api_key: str = ""
    ai_min_confidence: int = 70

    # Local model (Ollama) — used for shadow comparison against Claude. The
    # containers reach the host's Ollama via the Docker bridge gateway.
    ollama_base_url: str = "http://172.18.0.1:11434"
    ollama_model: str = "qwen2.5:7b-instruct"
    ollama_timeout_seconds: float = 240.0

    # Risk engine defaults (AED). These seed the DB-backed risk settings on first run.
    account_start_capital: float = 5000
    max_active_trades: int = 2
    max_risk_per_trade: float = 50
    max_combined_open_risk: float = 100
    max_trades_per_day: int = 3
    daily_max_loss: float = 150
    weekly_max_loss: float = 400
    stop_after_n_losses: int = 2
    min_risk_reward: float = 2.0

    # News / calendar
    news_api_key: str = ""

    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    @property
    def capital_base_url(self) -> str:
        if self.capital_environment.lower() == "live":
            return self.capital_live_base_url
        return self.capital_demo_base_url


settings = Settings()
