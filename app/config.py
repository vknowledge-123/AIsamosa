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
    broker_provider: str = "dhan"
    zerodha_api_key: str | None = None
    zerodha_api_secret: str | None = None
    zerodha_access_token: str | None = None
    dhan_live_security_id: str = "13"
    simulation_lot_size: int = 65
    nifty_order_lots: int = 1
    stock_trade_capital: float = 25000.0
    stock_execution_mode: str = "cash"
    stock_future_lots: int = 1
    stock_option_lots: int = 1
    heuristic_advance_timeframe_minutes: int = 3
    nifty_expiry_preference: str = "current-weekly"
    nifty_trailing_stop_enabled: bool = True
    nifty_heuristic_early_exit_enabled: bool = True
    nifty_cost_sl_enabled: bool = False
    nifty_cost_sl_points: float = 35.0
    nifty_min_sl_points: float = 40.0
    nifty_max_sl_points: float = 60.0
    nifty_target_enabled: bool = False
    nifty_target_points: float = 90.0
    nifty_daily_max_loss_enabled: bool = False
    nifty_daily_max_loss: float = 100.0
    pyramiding_enabled: bool = False
    intelligent_pyramiding_enabled: bool = False
    stock_percent_pyramiding_enabled: bool = False
    stock_percent_pyramiding_step: float = 1.0
    stock_cost_sl_after_pyramid_enabled: bool = False
    nifty_point_pyramiding_enabled: bool = False
    nifty_point_pyramiding_points: float = 50.0
    nifty_trade_bias: str = "both"
    nifty_option_trade_mode: str = "selling"
    simulation_starting_balance: float = 250000.0
    simulation_max_risk_per_trade: float = 0.01
    simulation_max_open_trades: int = 1
    session_candle_limit: int = 90
    stock_sync_max_workers: int = 4
    stock_min_5m_turnover: float = 30000000.0
    global_mtm_square_off_enabled: bool = False
    global_mtm_square_off_threshold: float = 0.0

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
