"""
AutoBet Configuration Settings
"""
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class OddsAPISettings(BaseSettings):
    """The Odds API configuration."""
    model_config = SettingsConfigDict(env_prefix="ODDS_API_")

    api_key: str = Field(default="", description="The Odds API key")
    base_url: str = Field(default="https://api.the-odds-api.com/v4")
    enabled: bool = Field(default=True)

    # Bookmakers to fetch (empty = all available)
    bookmakers: list[str] = Field(default_factory=lambda: [
        "pinnacle", "bet365", "1xbet", "unibet", "williamhill",
        "betfair_ex_eu", "smarkets", "matchbook", "betdaq",
        "marathonbet", "betvictor", "ladbrokes", "coral",
        "paddypower", "betway", "888sport", "bwin"
    ])


class ExchangeSettings(BaseSettings):
    """Exchange API settings for execution."""
    model_config = SettingsConfigDict(env_prefix="")

    # Betfair
    betfair_enabled: bool = Field(default=False)
    betfair_app_key: str = Field(default="")
    betfair_username: str = Field(default="")
    betfair_password: str = Field(default="")
    betfair_cert_path: str = Field(default="")
    betfair_commission: float = Field(default=0.05)

    # Smarkets
    smarkets_enabled: bool = Field(default=False)
    smarkets_api_key: str = Field(default="")
    smarkets_commission: float = Field(default=0.02)

    # Matchbook
    matchbook_enabled: bool = Field(default=False)
    matchbook_username: str = Field(default="")
    matchbook_password: str = Field(default="")
    matchbook_commission: float = Field(default=0.02)


class TelegramSettings(BaseSettings):
    """Telegram bot configuration."""
    model_config = SettingsConfigDict(env_prefix="TELEGRAM_")

    bot_token: str = Field(default="")
    chat_id: str = Field(default="")
    enabled: bool = Field(default=True)

    # Alert settings
    alert_min_edge: float = Field(default=0.5, description="Min edge % to alert")
    alert_on_execution: bool = Field(default=True)
    alert_on_error: bool = Field(default=True)


class RiskSettings(BaseSettings):
    """Risk management configuration."""
    model_config = SettingsConfigDict(env_prefix="RISK_")

    # Bankroll management
    initial_bankroll: float = Field(default=1000.0)
    max_stake_percent: float = Field(default=0.02, description="Max 2% per bet")
    max_daily_stake_percent: float = Field(default=0.10, description="Max 10% per day")
    max_daily_drawdown_percent: float = Field(default=0.05, description="Kill switch at 5%")

    # Arbitrage settings
    min_edge_percent: float = Field(default=0.8, description="Min 0.8% edge")
    max_odds_age_seconds: float = Field(default=5.0, description="Max 5s staleness")
    min_liquidity: float = Field(default=50.0, description="Min liquidity in EUR")

    # Execution settings
    max_slippage_percent: float = Field(default=0.5, description="Max 0.5% slippage")
    order_timeout_seconds: float = Field(default=10.0)

    # Kill switch
    kill_switch_enabled: bool = Field(default=False)


class DatabaseSettings(BaseSettings):
    """Database configuration."""
    model_config = SettingsConfigDict(env_prefix="DB_")

    url: str = Field(default="sqlite+aiosqlite:///./autobet.db")
    echo: bool = Field(default=False)


class Settings(BaseSettings):
    """Main application settings."""
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # App settings
    app_name: str = Field(default="AutoBet Scanner")
    debug: bool = Field(default=False)
    mode: str = Field(default="dry", description="dry, semi-auto, auto")

    # Scan settings
    scan_interval_seconds: int = Field(default=60)
    sports: list[str] = Field(default_factory=lambda: ["tennis", "soccer"])

    # Sub-settings
    odds_api: OddsAPISettings = Field(default_factory=OddsAPISettings)
    exchanges: ExchangeSettings = Field(default_factory=ExchangeSettings)
    telegram: TelegramSettings = Field(default_factory=TelegramSettings)
    risk: RiskSettings = Field(default_factory=RiskSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)


# Global settings instance
settings = Settings()
