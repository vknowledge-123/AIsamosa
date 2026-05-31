from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_title: str = "SL Hunting Paper Trader"
    openai_api_key: str | None = None
    openai_model: str = "gpt-5.4-mini"
    deepseek_api_key: str | None = None
    deepseek_model: str = "deepseek-v4-flash"
    full_ai_provider: str = "openai"
    operating_mode: str = "full-ai"
    dhan_client_id: str | None = None
    dhan_access_token: str | None = None
    dhan_live_security_id: str = "13"
    simulation_lot_size: int = 65
    nifty_order_lots: int = 1
    stock_trade_capital: float = 25000.0
    nifty_expiry_preference: str = "current-weekly"
    nifty_trailing_stop_enabled: bool = True
    nifty_heuristic_early_exit_enabled: bool = True
    pyramiding_enabled: bool = False
    intelligent_pyramiding_enabled: bool = False
    nifty_option_trade_mode: str = "selling"
    simulation_starting_balance: float = 250000.0
    simulation_max_risk_per_trade: float = 0.01
    simulation_max_open_trades: int = 1
    session_candle_limit: int = 90
    stock_sync_max_workers: int = 4
    stock_min_5m_turnover: float = 30000000.0

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
