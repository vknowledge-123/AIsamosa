from __future__ import annotations

from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, Field


class TradeAction(str, Enum):
    no_trade = "NO_TRADE"
    enter_call = "ENTER_CALL"
    enter_put = "ENTER_PUT"
    hold = "HOLD"
    exit = "EXIT"
    partial_exit = "PARTIAL_EXIT"
    add_position = "ADD_POSITION"
    exit_pyramid_leg = "EXIT_PYRAMID_LEG"
    update_target = "UPDATE_TARGET"
    update_stop = "UPDATE_STOP"


class OperatingMode(str, Enum):
    heuristic = "heuristic"
    full_ai = "full-ai"


class InstrumentMode(str, Enum):
    nifty = "nifty"
    stock = "stock"


class FullAIProvider(str, Enum):
    openai = "openai"
    deepseek = "deepseek"


class Candle(BaseModel):
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


class HeuristicCandleReference(BaseModel):
    label: str
    index: int | None = None
    candle: Candle


class PreviousDayLevels(BaseModel):
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0


class Zone(BaseModel):
    label: str
    zone_type: str
    price: float
    upper: float
    lower: float
    strength: float = Field(ge=0.0, le=1.0, default=0.5)
    notes: str = ""


class LiquidityLedgerEntry(BaseModel):
    window_label: str
    window_minutes: int | None = None
    side: str
    level_label: str
    level: float
    status: str
    trap_side: str = "none"
    strength: float = Field(ge=0.0, le=1.0, default=0.5)
    candle_count: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None
    notes: str = ""


class SignalEvent(BaseModel):
    timestamp: datetime
    title: str
    sentiment: str
    description: str


class HeuristicTraceEntry(BaseModel):
    timestamp: datetime
    event_type: str
    title: str
    status: str | None = None
    market_state: str | None = None
    action: str | None = None
    direction: str | None = None
    setup_type: str | None = None
    option_type: str | None = None
    confidence: float | None = None
    setup_score: float | None = None
    trigger_price: float | None = None
    invalidation_level: float | None = None
    matched_level_label: str | None = None
    matched_level_price: float | None = None
    candle_refs: list[HeuristicCandleReference] = Field(default_factory=list)
    block_reason: str | None = None
    detail: str = ""


class HeuristicNarrativeEvent(BaseModel):
    timestamp: datetime
    event_type: str
    title: str
    direction: str | None = None
    price: float | None = None
    status: str | None = None
    matched_level_label: str | None = None
    matched_level_price: float | None = None
    candle_refs: list[HeuristicCandleReference] = Field(default_factory=list)
    detail: str = ""


class InstrumentState(BaseModel):
    mode: InstrumentMode = InstrumentMode.nifty
    label: str = "Nifty 50"
    symbol: str = "NIFTY"
    security_id: str = "13"
    exchange_segment: str = "IDX_I"
    instrument_type: str = "INDEX"
    supports_options: bool = True
    lot_size: int = 65


class StockWatchItem(BaseModel):
    symbol: str
    label: str
    security_id: str
    trade_bias: str = "both"
    selected: bool = False
    subscribed: bool = False
    last_ltp: float | None = None
    last_tick_at: datetime | None = None
    ticks_received: int = 0
    history_status: str = "idle"
    previous_day_candles: int = 0
    intraday_candles: int = 0
    total_loaded: int = 0
    last_5m_turnover: float | None = None
    last_5m_turnover_passed: bool | None = None
    last_5m_turnover_start: datetime | None = None
    last_5m_turnover_end: datetime | None = None
    decision_action: str | None = None
    decision_confidence: float | None = None
    decision_reason: str | None = None
    has_active_trade: bool = False
    active_trade_direction: str | None = None
    active_trade_pnl: float | None = None
    trade_count: int = 0
    closed_trade_count: int = 0
    last_trade_status: str | None = None
    realized_pnl: float = 0.0
    live_order_message: str | None = None
    live_order_error: str | None = None
    live_order_updated_at: datetime | None = None


class StrategyContext(BaseModel):
    instrument: InstrumentState
    current_candle: Candle
    live_current_candle: Candle | None = None
    recent_candles: list[Candle] = Field(default_factory=list)
    session_candles: list[Candle] = Field(default_factory=list)
    previous_day_candles: list[Candle] = Field(default_factory=list)
    previous_day: PreviousDayLevels
    liquidity_zones: list[Zone]
    liquidity_ledger: list[LiquidityLedgerEntry] = Field(default_factory=list)
    operator_zones: list[Zone]
    signal_events: list[SignalEvent]
    market_structure: str = ""
    companion_symbol: str | None = None
    companion_current_candle: Candle | None = None
    companion_recent_candles: list[Candle] = Field(default_factory=list)
    companion_session_candles: list[Candle] = Field(default_factory=list)
    companion_previous_day_candles: list[Candle] = Field(default_factory=list)
    companion_previous_day: PreviousDayLevels = Field(default_factory=PreviousDayLevels)
    pending_setup: "PendingSetup | None" = None
    active_trade: "SimulatedTrade | None" = None
    recent_closed_trades: list["SimulatedTrade"] = Field(default_factory=list)
    portfolio_order_count_estimate: int = 0
    rulebook_markdown: str
    stock_partial_profit_enabled: bool = True
    stock_trailing_stop_enabled: bool = True
    stock_heuristic_early_exit_enabled: bool = True
    nifty_trailing_stop_enabled: bool = True
    nifty_heuristic_early_exit_enabled: bool = True
    nifty_cost_sl_enabled: bool = False
    nifty_cost_sl_points: float = 35.0
    nifty_min_sl_points: float = 40.0
    nifty_max_sl_points: float = 60.0
    nifty_target_enabled: bool = False
    nifty_target_points: float = 90.0
    pyramiding_enabled: bool = False
    intelligent_pyramiding_enabled: bool = False
    nifty_point_pyramiding_enabled: bool = False
    nifty_point_pyramiding_points: float = 50.0
    nifty_trade_bias: str = "both"
    nifty_option_trade_mode: str = "selling"
    stock_trade_bias: str = "both"


class PendingSetup(BaseModel):
    setup_id: str
    status: str = "armed"
    setup_type: str
    direction: str
    option_type: str
    strike: int | None = None
    trigger_price: float
    invalidation_level: float | None = None
    target_spot_price: float | None = None
    first_target_price: float | None = None
    setup_score: float | None = None
    market_state: str | None = None
    trigger_basis: str = "close_above"
    created_at: datetime
    updated_at: datetime
    last_evaluated_at: datetime | None = None
    source: str = "full-ai"
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    notes: str = ""
    replacement_reason: str | None = None
    triggered_at: datetime | None = None
    consumed_at: datetime | None = None
    invalidated_at: datetime | None = None
    status_reason: str | None = None
    executed_trade_id: str | None = None


class TradeDecision(BaseModel):
    action: TradeAction
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    reason: str = ""
    decision_source: str = "heuristic"
    strike: int | None = None
    option_type: str | None = None
    target_option_price: float | None = None
    stop_option_price: float | None = None
    invalidation_level: float | None = None
    target_spot_price: float | None = None
    first_target_price: float | None = None
    partial_exit_quantity: int | None = None
    add_quantity: int | None = None
    pyramid_leg_ids: list[str] = Field(default_factory=list)
    market_state: str | None = None
    setup_score: float | None = None
    setup_type: str | None = None
    rule_ids_used: list[str] = Field(default_factory=list)
    pending_setup_action: str = "NONE"
    pending_setup_type: str | None = None
    pending_setup_direction: str | None = None
    pending_setup_trigger_price: float | None = None
    pending_setup_invalidation_level: float | None = None
    pending_setup_trigger_basis: str | None = None
    pending_setup_notes: str | None = None
    pending_setup_strike: int | None = None
    pending_setup_option_type: str | None = None


class PyramidLeg(BaseModel):
    leg_id: str
    add_number: int
    status: str = "OPEN"
    quantity: int
    open_quantity: int
    entry_time: datetime
    entry_price: float
    entry_spot_price: float
    invalidation_level: float
    exit_time: datetime | None = None
    exit_price: float | None = None
    exit_spot_price: float | None = None
    broker_order_id: str | None = None
    broker_exit_order_id: str | None = None
    broker_entry_correlation_id: str | None = None
    broker_exit_correlation_id: str | None = None
    broker_status: str | None = None
    broker_status_message: str | None = None


class SimulatedTrade(BaseModel):
    trade_id: str
    status: str
    direction: str
    instrument_mode: InstrumentMode = InstrumentMode.nifty
    instrument_label: str = "Nifty 50"
    price_mode: str = "option"
    trade_security_id: str | None = None
    quote_exchange_segment: str | None = None
    option_type: str
    strike: int
    symbol: str
    option_security_id: str | None = None
    quantity: int
    base_quantity: int | None = None
    open_quantity: int | None = None
    closed_quantity: int = 0
    entry_time: datetime
    entry_price: float
    entry_spot_price: float
    entry_option_price: float
    execution_source: str = "simulated"
    entry_quote_source: str = "simulated"
    entry_quote_time: datetime | None = None
    current_price: float
    current_option_price: float
    current_quote_source: str = "simulated"
    current_quote_time: datetime | None = None
    stop_price: float
    stop_option_price: float
    target_price: float
    target_option_price: float
    invalidation_level: float | None = None
    target_spot_price: float | None = None
    first_target_price: float | None = None
    setup_type: str | None = None
    setup_score: float | None = None
    market_state: str | None = None
    exit_time: datetime | None = None
    exit_price: float | None = None
    exit_option_price: float | None = None
    exit_quote_source: str | None = None
    exit_quote_time: datetime | None = None
    booked_pnl: float = 0.0
    partial_exit_count: int = 0
    last_partial_exit_time: datetime | None = None
    pyramid_count: int = 0
    last_pyramid_time: datetime | None = None
    last_pyramid_price: float | None = None
    pyramid_legs: list[PyramidLeg] = Field(default_factory=list)
    pnl: float = 0.0
    entry_notes: str = ""
    exit_notes: str | None = None
    notes: str = ""
    broker_product_type: str | None = None
    broker_order_id: str | None = None
    broker_exit_order_id: str | None = None
    broker_entry_correlation_id: str | None = None
    broker_exit_correlation_id: str | None = None
    broker_status: str | None = None
    broker_status_message: str | None = None


class RulebookUpdate(BaseModel):
    summary: str
    proposed_markdown: str
    extracted_rules: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)


class LiveFeedState(BaseModel):
    connected: bool = False
    status: str = "disconnected"
    source: str = "sample"
    security_id: str = "13"
    instrument_label: str = "Nifty 50"
    ticks_received: int = 0
    last_packet_type: str | None = None
    last_tick_at: datetime | None = None
    last_ltp: float | None = None
    status_message: str | None = None
    error: str | None = None
    retry_attempt: int = 0
    next_retry_at: datetime | None = None
    current_candle: Candle | None = None


class CredentialSummary(BaseModel):
    client_id: str | None = None
    resolved_client_id: str | None = None
    dhan_access_token_saved: bool = False
    openai_api_key_saved: bool = False
    openai_model: str = "gpt-5.4-mini"
    deepseek_api_key_saved: bool = False
    deepseek_model: str = "deepseek-v4-flash"
    full_ai_provider: FullAIProvider = FullAIProvider.openai
    operating_mode: OperatingMode = OperatingMode.full_ai
    nifty_order_lots: int = 1
    stock_trade_capital: float = 25000.0
    nifty_expiry_preference: str = "current-weekly"
    stock_partial_profit_enabled: bool = True
    stock_trailing_stop_enabled: bool = True
    stock_heuristic_early_exit_enabled: bool = True
    nifty_trailing_stop_enabled: bool = True
    nifty_heuristic_early_exit_enabled: bool = True
    nifty_cost_sl_enabled: bool = False
    nifty_cost_sl_points: float = 35.0
    nifty_min_sl_points: float = 40.0
    nifty_max_sl_points: float = 60.0
    nifty_target_enabled: bool = False
    nifty_target_points: float = 90.0
    pyramiding_enabled: bool = False
    intelligent_pyramiding_enabled: bool = False
    nifty_point_pyramiding_enabled: bool = False
    nifty_point_pyramiding_points: float = 50.0
    nifty_trade_bias: str = "both"
    nifty_option_trade_mode: str = "selling"
    dhan_credential_message: str | None = None
    storage_path: str
    last_updated: datetime | None = None


class ExecutionState(BaseModel):
    live_trading_enabled: bool = False
    order_updates_connected: bool = False
    order_updates_status: str = "disconnected"
    order_updates_message: str | None = None
    last_order_update_at: datetime | None = None
    last_order_message: str | None = None
    last_order_symbol: str | None = None
    last_order_error: str | None = None
    last_order_error_at: datetime | None = None


class DataSyncState(BaseModel):
    status: str = "idle"
    source: str = "sample"
    message: str | None = None
    last_synced_at: datetime | None = None
    replay_session_day: date | None = None
    previous_context_day: date | None = None
    previous_day_candles: int = 0
    intraday_candles: int = 0
    total_loaded: int = 0
    has_live_open_candle: bool = False


class RulebookJobState(BaseModel):
    job_id: str | None = None
    status: str = "idle"
    source_name: str | None = None
    message: str = "No rulebook learning job has run yet."
    started_at: datetime | None = None
    completed_at: datetime | None = None
    used_fallback: bool = False


class OperationJobState(BaseModel):
    job_id: str | None = None
    job_type: str = "idle"
    status: str = "idle"
    message: str = "No background sync or replay job is running."
    started_at: datetime | None = None
    completed_at: datetime | None = None


class IntegratedPnlState(BaseModel):
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    total_pnl: float = 0.0
    max_total_pnl: float = 0.0
    max_total_pnl_at: datetime | None = None
    min_total_pnl: float = 0.0
    min_total_pnl_at: datetime | None = None


class DashboardState(BaseModel):
    state_revision: int = 0
    mode: str
    instrument: InstrumentState
    operating_mode: OperatingMode
    current_index: int
    total_candles: int
    latest_candle: Candle | None = None
    recent_candles: list[Candle] = Field(default_factory=list)
    previous_day: PreviousDayLevels
    liquidity_zones: list[Zone]
    liquidity_ledger: list[LiquidityLedgerEntry] = Field(default_factory=list)
    operator_zones: list[Zone]
    signal_events: list[SignalEvent]
    signal_history: list[SignalEvent] = Field(default_factory=list)
    heuristic_trace: list[HeuristicTraceEntry] = Field(default_factory=list)
    heuristic_narrative: list[HeuristicNarrativeEvent] = Field(default_factory=list)
    market_structure: str = ""
    nifty_market_mechanics: dict[str, object] = Field(default_factory=dict)
    pending_setup: PendingSetup | None = None
    decision: TradeDecision | None = None
    active_trade: SimulatedTrade | None = None
    trade_history: list[SimulatedTrade]
    rulebook: str
    learning_log: list[str]
    balance: float
    realized_pnl: float
    unrealized_pnl: float
    integrated_pnl: IntegratedPnlState = Field(default_factory=IntegratedPnlState)
    ai_enabled: bool
    live_feed: LiveFeedState
    execution: ExecutionState = Field(default_factory=ExecutionState)
    data_sync: DataSyncState
    operation_job: OperationJobState = Field(default_factory=OperationJobState)
    rulebook_job: RulebookJobState
    credentials: CredentialSummary
    stock_watchlist: list[StockWatchItem] = Field(default_factory=list)


StrategyContext.model_rebuild()
