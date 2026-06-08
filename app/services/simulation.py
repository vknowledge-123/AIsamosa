from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import math
import queue
import re
import threading
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from app.config import Settings
from app.schemas import (
    Candle,
    CredentialSummary,
    DataSyncState,
    DashboardState,
    ExecutionState,
    FullAIProvider,
    IntegratedPnlState,
    InstrumentMode,
    LiveFeedState,
    LiquidityLedgerEntry,
    OperationJobState,
    OperatingMode,
    PendingSetup,
    PreviousDayLevels,
    PyramidLeg,
    ReplayDailyPnlState,
    ReplayPnlSummaryState,
    RulebookJobState,
    SignalEvent,
    SimulatedTrade,
    StockWatchItem,
    StrategyContext,
    TradeAction,
    TradeDecision,
    Zone,
)
from app.services.ai_service import AIDecisionService
from app.services.credential_store import CredentialStore
from app.services.dhan_adapter import DhanMarketFeedAdapter, resolve_quote_subscription
from app.services.dhan_execution import DhanExecutionError, DhanExecutionService
from app.services.dhan_history import DhanChartEmptyDataError, DhanChartService, DhanSessionBundle
from app.services.dhan_order_updates import DhanOrderUpdateAdapter
from app.services.instruments import BANKNIFTY_INSTRUMENT, NIFTY_INSTRUMENT, InstrumentSpec, build_stock_instrument, get_instrument_spec
from app.services.dhan_options import DhanOptionQuoteError, DhanOptionQuoteService, OptionContract, OptionQuote
from app.services.heuristic_engine import HeuristicDecisionEngine
from app.services.market_data import (
    calculate_previous_day_levels,
    calculate_previous_day_levels_for_timestamp,
    generate_sample_candles,
    get_previous_day_candles,
    get_session_candles_up_to_index,
    parse_candle_csv,
)
from app.services.rulebook import RulebookService
from app.services.stock_universe import StockFutureContract, StockUniverseService
from app.services.zerodha_adapter import ZerodhaMarketFeedAdapter
from app.services.zerodha_execution import ZerodhaExecutionError, ZerodhaExecutionService
from app.services.zerodha_history import ZerodhaChartService


@dataclass
class StockRuntimeSession:
    spec: InstrumentSpec
    candles: list[Candle] = field(default_factory=list)
    current_index: int = -1
    live_current_candle: Candle | None = None
    signal_history: list[SignalEvent] = field(default_factory=list)
    signal_history_keys: set[tuple[str, str, str]] = field(default_factory=set)
    pending_setup: PendingSetup | None = None
    active_trade: SimulatedTrade | None = None
    trade_history: list[SimulatedTrade] = field(default_factory=list)
    decision: TradeDecision | None = None
    realized_pnl: float = 0.0
    balance: float = 0.0
    data_sync: DataSyncState = field(default_factory=DataSyncState)
    heuristic_engine: HeuristicDecisionEngine = field(default_factory=HeuristicDecisionEngine)


@dataclass(frozen=True)
class StockTurnoverSnapshot:
    window_start: datetime
    window_end: datetime
    close: float
    volume: float
    turnover: float
    passed: bool


class SimulationEngine:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.lock = threading.RLock()
        self.rulebook_service = RulebookService()
        self.credential_store = CredentialStore()
        self._credential_summary_cache = self.credential_store.summary(settings)
        self.ai_service = AIDecisionService(settings)
        self.chart_service = DhanChartService()
        self.option_quote_service = DhanOptionQuoteService()
        self.execution_service = DhanExecutionService()
        self.zerodha_execution_service = ZerodhaExecutionService()
        self.zerodha_chart_service = ZerodhaChartService(self.zerodha_execution_service)
        self.stock_universe = StockUniverseService()
        self.heuristic_engine = HeuristicDecisionEngine()
        self._configure_ai_service()
        self.operating_mode = self.credential_store.get_operating_mode(settings)
        self.instrument_mode = InstrumentMode.nifty
        self.instrument_spec = get_instrument_spec(self.instrument_mode)
        self.companion_instrument_spec = BANKNIFTY_INSTRUMENT
        self.live_feed_adapter: DhanMarketFeedAdapter | None = None
        self.order_update_adapter: DhanOrderUpdateAdapter | None = None
        self.live_feed = self._build_live_feed_state()
        self.execution_state = ExecutionState()
        self._stock_execution_feedback: dict[str, dict] = {}
        self.live_current_candle: Candle | None = None
        self.companion_candles: list[Candle] = []
        self.companion_live_current_candle: Candle | None = None
        self.data_sync = DataSyncState()
        self.operation_job = OperationJobState()
        self.replay_pnl_summary = ReplayPnlSummaryState()
        self.rulebook_job = RulebookJobState()
        self._operation_job_token = ""
        self._rulebook_job_token = ""
        self._evaluation_lock = threading.Lock()
        self._state_wait_condition = threading.Condition(self.lock)
        self._state_cache_lock = threading.Lock()
        self._state_revision = 0
        self._cached_state_revision = -1
        self._cached_state: DashboardState | None = None
        self._live_packet_queue: queue.Queue[str | None] = queue.Queue()
        self._pending_live_packets: dict[str, dict] = {}
        self._queued_live_packet_keys: set[str] = set()
        self._replay_simulation_depth = 0
        self._live_packet_worker = threading.Thread(
            target=self._run_live_packet_worker,
            name="live-packet-worker",
            daemon=True,
        )
        self._live_packet_worker.start()
        self._live_evaluation_queue: queue.Queue[tuple[str | None, int] | None] = queue.Queue()
        self._live_evaluation_worker = threading.Thread(
            target=self._run_live_evaluation_worker,
            name="live-evaluation-worker",
            daemon=True,
        )
        self._live_evaluation_worker.start()
        self._watchlist_subscription_refresh_event = threading.Event()
        self._watchlist_subscription_worker = threading.Thread(
            target=self._run_watchlist_subscription_worker,
            name="watchlist-subscription-worker",
            daemon=True,
        )
        self._watchlist_subscription_worker.start()
        self.signal_history: list[SignalEvent] = []
        self._signal_history_keys: set[tuple[str, str, str]] = set()
        self._active_option_subscription: tuple | None = None
        self._stock_quote_subscriptions: dict[str, tuple] = {}
        self._stock_symbol_by_security_id: dict[str, str] = {}
        self._live_cumulative_volume_by_security_id: dict[str, tuple[date, float]] = {}
        self.stock_watchlist: dict[str, InstrumentSpec] = {}
        self.stock_watch_meta: dict[str, dict] = {}
        self.stock_sessions: dict[str, StockRuntimeSession] = {}
        self.selected_stock_symbol: str | None = None
        self._bulk_replay_trade_history: list[SimulatedTrade] = []
        self._runtime_dhan_client_id = ""
        self._runtime_dhan_access_token = ""
        self.live_trading_enabled = False
        self._global_mtm_square_off_triggered = False
        self.pending_setup: PendingSetup | None = None
        self._default_heuristic_engine = self.heuristic_engine
        self._integrated_pnl_peak: float | None = None
        self._integrated_pnl_peak_at: datetime | None = None
        self._integrated_pnl_trough: float | None = None
        self._integrated_pnl_trough_at: datetime | None = None
        self._restore_persisted_ui_preferences_locked()
        if not self.stock_watchlist and not self._has_saved_stock_watchlist_preferences():
            self._ensure_default_stock_watchlist()
        self.reset_with_candles(generate_sample_candles())

    def _mark_state_dirty_locked(self) -> None:
        acquired_here = False
        if not getattr(self.lock, "_is_owned", lambda: False)():
            self.lock.acquire()
            acquired_here = True
        try:
            self._state_revision += 1
            with self._state_cache_lock:
                self._cached_state = None
                self._cached_state_revision = -1
            self._state_wait_condition.notify_all()
        finally:
            if acquired_here:
                self.lock.release()

    def get_state_revision(self) -> int:
        with self.lock:
            return self._state_revision

    def wait_for_state_revision(self, after_revision: int, timeout: float = 15.0) -> int:
        with self._state_wait_condition:
            self._state_wait_condition.wait_for(lambda: self._state_revision > after_revision, timeout=timeout)
            return self._state_revision

    def _set_operation_job_locked(
        self,
        *,
        job_id: str | None,
        job_type: str,
        status: str,
        message: str,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
    ) -> None:
        self.operation_job = OperationJobState(
            job_id=job_id,
            job_type=job_type,
            status=status,
            message=message,
            started_at=started_at,
            completed_at=completed_at,
        )

    def _ensure_no_running_operation_locked(self) -> None:
        if self.operation_job.status == "running":
            raise ValueError(f"{self.operation_job.job_type} is already running in the background.")

    def _run_operation_job(
        self,
        *,
        job_id: str,
        job_type: str,
        target,
        success_message: str,
        error_prefix: str,
    ) -> None:
        try:
            target()
            with self.lock:
                if self._operation_job_token != job_id:
                    return
                self._set_operation_job_locked(
                    job_id=job_id,
                    job_type=job_type,
                    status="success",
                    message=success_message,
                    started_at=self.operation_job.started_at,
                    completed_at=datetime.now(),
                )
                self._mark_state_dirty_locked()
        except Exception as exc:
            with self.lock:
                if self._operation_job_token != job_id:
                    return
                self._set_operation_job_locked(
                    job_id=job_id,
                    job_type=job_type,
                    status="error",
                    message=f"{error_prefix}: {exc}",
                    started_at=self.operation_job.started_at,
                    completed_at=datetime.now(),
                )
                self.data_sync = DataSyncState(
                    status="error",
                    source=self.data_sync.source,
                    message=f"{error_prefix}: {exc}",
                    last_synced_at=self.data_sync.last_synced_at,
                    replay_session_day=self.data_sync.replay_session_day,
                    previous_context_day=self.data_sync.previous_context_day,
                    previous_day_candles=self.data_sync.previous_day_candles,
                    intraday_candles=self.data_sync.intraday_candles,
                    total_loaded=self.data_sync.total_loaded,
                    has_live_open_candle=self.data_sync.has_live_open_candle,
                )
                self.rulebook_service.learning_log.insert(0, f"{error_prefix}: {exc}")
                self._mark_state_dirty_locked()

    def _build_live_feed_state(
        self,
        *,
        connected: bool = False,
        status: str = "disconnected",
        source: str = "sample",
        status_message: str | None = None,
        retry_attempt: int = 0,
        next_retry_at: datetime | None = None,
    ) -> LiveFeedState:
        return LiveFeedState(
            connected=connected,
            status=status,
            source=source,
            security_id=self.instrument_spec.security_id,
            instrument_label=self.instrument_spec.label,
            status_message=status_message,
            retry_attempt=retry_attempt,
            next_retry_at=next_retry_at,
        )

    def instrument_state(self):
        return self.instrument_spec.to_state(self._display_lot_size())

    def _display_lot_size(self) -> int:
        if self.instrument_spec.supports_options:
            return self.settings.simulation_lot_size * self.credential_store.get_nifty_order_lots(self.settings)
        return self.settings.simulation_lot_size

    def _nifty_order_lots(self) -> int:
        return self.credential_store.get_nifty_order_lots(self.settings)

    def _stock_trade_capital(self) -> float:
        return self.credential_store.get_stock_trade_capital(self.settings)

    def _stock_execution_mode(self) -> str:
        return self.credential_store.get_stock_execution_mode(self.settings)

    def _stock_future_lots(self) -> int:
        return self.credential_store.get_stock_future_lots(self.settings)

    def _stock_option_lots(self) -> int:
        return self.credential_store.get_stock_option_lots(self.settings)

    def _stock_future_mode_enabled(self) -> bool:
        return self.instrument_mode == InstrumentMode.stock and self._stock_execution_mode() == "future"

    def _stock_option_mode_enabled(self) -> bool:
        return self.instrument_mode == InstrumentMode.stock and self._stock_execution_mode() == "option"

    def _stock_derivative_execution_mode_enabled(self) -> bool:
        return self.instrument_mode == InstrumentMode.stock and self._stock_execution_mode() in {"future", "option"}

    def _stock_min_5m_turnover(self) -> float:
        return max(float(getattr(self.settings, "stock_min_5m_turnover", 30000000.0)), 0.0)

    def _stock_percent_pyramiding_enabled(self) -> bool:
        return self.credential_store.get_stock_percent_pyramiding_enabled(self.settings)

    def _stock_percent_pyramiding_step(self) -> float:
        return self.credential_store.get_stock_percent_pyramiding_step(self.settings)

    def _nifty_expiry_preference(self) -> str:
        return self.credential_store.get_nifty_expiry_preference(self.settings)

    def _nifty_cost_sl_enabled(self) -> bool:
        return self.credential_store.get_nifty_cost_sl_enabled(self.settings)

    def _nifty_cost_sl_points(self) -> float:
        return self.credential_store.get_nifty_cost_sl_points(self.settings)

    def _nifty_min_sl_points(self) -> float:
        return self.credential_store.get_nifty_min_sl_points(self.settings)

    def _nifty_max_sl_points(self) -> float:
        min_points = self._nifty_min_sl_points()
        return max(self.credential_store.get_nifty_max_sl_points(self.settings), min_points)

    def _nifty_target_enabled(self) -> bool:
        return self.credential_store.get_nifty_target_enabled(self.settings)

    def _nifty_target_points(self) -> float:
        return self.credential_store.get_nifty_target_points(self.settings)

    def _nifty_daily_max_loss_enabled(self) -> bool:
        return self.credential_store.get_nifty_daily_max_loss_enabled(self.settings)

    def _nifty_daily_max_loss(self) -> float:
        return self.credential_store.get_nifty_daily_max_loss(self.settings)

    def _nifty_point_pyramiding_enabled(self) -> bool:
        return self.credential_store.get_nifty_point_pyramiding_enabled(self.settings)

    def _nifty_point_pyramiding_points(self) -> float:
        return self.credential_store.get_nifty_point_pyramiding_points(self.settings)

    def _nifty_trade_bias(self) -> str:
        return self.credential_store.get_nifty_trade_bias(self.settings)

    def _global_mtm_square_off_enabled(self) -> bool:
        return self.credential_store.get_global_mtm_square_off_enabled(self.settings)

    def _global_mtm_square_off_threshold(self) -> float:
        return self.credential_store.get_global_mtm_square_off_threshold(self.settings)

    def _use_banknifty_companion(self) -> bool:
        return self.instrument_mode == InstrumentMode.nifty and self.instrument_spec.symbol == "NIFTY"

    def _use_stock_nifty_companion(self) -> bool:
        return self.instrument_mode == InstrumentMode.stock

    def _active_companion_instrument_spec(self) -> InstrumentSpec | None:
        if self._use_banknifty_companion():
            return self.companion_instrument_spec
        if self._use_stock_nifty_companion():
            return NIFTY_INSTRUMENT
        return None

    def _clear_companion_context_locked(self) -> None:
        self.companion_candles = []
        self.companion_live_current_candle = None

    def _load_companion_bundle_locked(self, bundle, replay_from_session_start: bool = False) -> None:
        self.companion_candles = list(bundle.previous_day_candles) + list(bundle.intraday_candles)
        self.companion_live_current_candle = None if replay_from_session_start else bundle.live_open_candle

    def _build_companion_snapshot_locked(
        self,
        *,
        evaluation_index: int | None = None,
        replay_decision_duration_minutes: int = 1,
        source: str = "manual",
    ) -> tuple[list[Candle], list[Candle], list[Candle], Candle | None, PreviousDayLevels]:
        if self._active_companion_instrument_spec() is None or not self.companion_candles:
            return [], [], [], None, PreviousDayLevels()
        companion_index = len(self.companion_candles) - 1 if evaluation_index is None else min(evaluation_index, len(self.companion_candles) - 1)
        if companion_index < 0:
            return [], [], [], None, PreviousDayLevels()
        if source == "replay" and replay_decision_duration_minutes > 1:
            session_candles = self._aggregate_candles(
                get_session_candles_up_to_index(self.companion_candles, companion_index),
                replay_decision_duration_minutes,
            )
            previous_day_candles = self._aggregate_candles(
                get_previous_day_candles(self.companion_candles, companion_index),
                replay_decision_duration_minutes,
            )
        else:
            session_candles = get_session_candles_up_to_index(self.companion_candles, companion_index)
            previous_day_candles = get_previous_day_candles(self.companion_candles, companion_index)
        recent_candles = session_candles[-20:]
        current_candle = session_candles[-1] if session_candles else None
        if self.companion_live_current_candle is not None and source != "replay":
            if not recent_candles or recent_candles[-1].timestamp != self.companion_live_current_candle.timestamp:
                recent_candles = list(recent_candles) + [self.companion_live_current_candle]
            else:
                recent_candles = list(recent_candles)
                recent_candles[-1] = self.companion_live_current_candle
            if not session_candles or session_candles[-1].timestamp != self.companion_live_current_candle.timestamp:
                session_candles = list(session_candles) + [self.companion_live_current_candle]
            else:
                session_candles = list(session_candles)
                session_candles[-1] = self.companion_live_current_candle
            current_candle = self.companion_live_current_candle
        previous_day = calculate_previous_day_levels(self.companion_candles, companion_index) if session_candles else PreviousDayLevels()
        return session_candles, previous_day_candles, recent_candles, current_candle, previous_day

    def _fetch_nifty_and_banknifty_bundles(
        self,
        *,
        client_id: str,
        access_token: str,
        provider: str = "dhan",
    ) -> tuple[object, object]:
        def fetch(spec: InstrumentSpec):
            return self._fetch_market_context_for_spec(
                provider,
                client_id,
                access_token,
                spec,
                prefer_last_closed_session_before_open=True,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            nifty_future = executor.submit(fetch, self.instrument_spec)
            banknifty_future = executor.submit(fetch, self.companion_instrument_spec)
            return nifty_future.result(), banknifty_future.result()

    def _fetch_companion_market_context_bundle(
        self,
        *,
        client_id: str,
        access_token: str,
        provider: str = "dhan",
    ):
        companion_spec = self._active_companion_instrument_spec()
        if companion_spec is None:
            return None
        return self._fetch_market_context_for_spec(
            provider,
            client_id,
            access_token,
            companion_spec,
            prefer_last_closed_session_before_open=True,
        )

    def _fetch_nifty_and_banknifty_historical_bundles(
        self,
        *,
        client_id: str,
        access_token: str,
        replay_session_day: date,
        previous_day: date,
        provider: str = "dhan",
    ) -> tuple[object, object]:
        def fetch(spec: InstrumentSpec):
            return self._fetch_market_context_for_spec_days(
                provider,
                client_id,
                access_token,
                spec,
                session_day=replay_session_day,
                previous_context_day=previous_day,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            nifty_future = executor.submit(fetch, self.instrument_spec)
            banknifty_future = executor.submit(fetch, self.companion_instrument_spec)
            return nifty_future.result(), banknifty_future.result()

    def _fetch_historical_bundle_with_previous_fallback(
        self,
        *,
        client_id: str,
        access_token: str,
        replay_session_day: date,
        spec: InstrumentSpec,
        provider: str = "dhan",
    ) -> DhanSessionBundle:
        replay_candles, replay_source = self._fetch_session_day_candles_for_spec(
            provider,
            client_id,
            access_token,
            spec,
            replay_session_day,
        )
        previous_candidate = self.chart_service._previous_trading_day(replay_session_day)
        previous_candles, previous_source, previous_context_day = self._fetch_latest_session_day_candles_for_spec(
            provider,
            client_id,
            access_token,
            spec,
            previous_candidate,
        )
        return DhanSessionBundle(
            previous_day_candles=previous_candles,
            intraday_candles=replay_candles,
            live_open_candle=None,
            previous_day_source=previous_source,
            replay_session_day=replay_session_day,
            intraday_source=replay_source,
            previous_context_day=previous_context_day,
        )

    def _fetch_nifty_and_banknifty_historical_bundles_with_previous_fallback(
        self,
        *,
        client_id: str,
        access_token: str,
        replay_session_day: date,
        provider: str = "dhan",
    ) -> tuple[DhanSessionBundle, DhanSessionBundle]:
        def fetch(spec: InstrumentSpec) -> DhanSessionBundle:
            return self._fetch_historical_bundle_with_previous_fallback(
                client_id=client_id,
                access_token=access_token,
                replay_session_day=replay_session_day,
                spec=spec,
                provider=provider,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            nifty_future = executor.submit(fetch, self.instrument_spec)
            banknifty_future = executor.submit(fetch, self.companion_instrument_spec)
            return nifty_future.result(), banknifty_future.result()

    def _fetch_companion_historical_bundle(
        self,
        *,
        client_id: str,
        access_token: str,
        replay_session_day: date,
        previous_day: date,
        provider: str = "dhan",
    ):
        companion_spec = self._active_companion_instrument_spec()
        if companion_spec is None:
            return None
        return self._fetch_market_context_for_spec_days(
            provider,
            client_id,
            access_token,
            companion_spec,
            session_day=replay_session_day,
            previous_context_day=previous_day,
        )

    def _remember_dhan_credentials(self, client_id: str, access_token: str) -> None:
        self._runtime_dhan_client_id = (client_id or "").strip()
        self._runtime_dhan_access_token = (access_token or "").strip()

    def _available_dhan_credentials(self, client_id: str | None = None, access_token: str | None = None) -> tuple[str, str]:
        stored_client, stored_token = self.credential_store.get_dhan_credentials(self.settings)
        client = (client_id or self._runtime_dhan_client_id or stored_client or "").strip()
        token = (access_token or self._runtime_dhan_access_token or stored_token or "").strip()
        client, token, message = self.credential_store.resolve_dhan_credentials(client, token)
        if message:
            if not self.rulebook_service.learning_log or self.rulebook_service.learning_log[0] != message:
                self.rulebook_service.learning_log.insert(0, message)
        return client, token

    def _selected_broker_provider(self) -> str:
        return self.credential_store.get_broker_provider(self.settings)

    def _available_zerodha_credentials(self) -> tuple[str, str, str]:
        api_key, api_secret, access_token = self.credential_store.get_zerodha_credentials(self.settings)
        return (api_key or "").strip(), (api_secret or "").strip(), (access_token or "").strip()

    def _history_provider_credentials(
        self,
        client_id: str | None = None,
        access_token: str | None = None,
    ) -> tuple[str, str, str]:
        broker = self._selected_broker_provider()
        if broker == "zerodha":
            api_key, _, zerodha_token = self._available_zerodha_credentials()
            if not api_key or not zerodha_token:
                raise ValueError("Zerodha API key and access token are required to fetch Zerodha historical candles.")
            return "zerodha", api_key, zerodha_token
        client, token = self._available_dhan_credentials(client_id, access_token)
        if not client or not token:
            raise ValueError("Dhan client ID and access token are required to fetch Dhan historical candles.")
        self._remember_dhan_credentials(client, token)
        return "dhan", client, token

    def _history_source_label(self, provider: str) -> str:
        return "zerodha-rest" if provider == "zerodha" else "dhan-rest"

    def _history_broker_label(self, provider: str) -> str:
        return "Zerodha" if provider == "zerodha" else "Dhan"

    def _fetch_market_context_for_spec(
        self,
        provider: str,
        key: str,
        token: str,
        spec: InstrumentSpec,
        *,
        prefer_last_closed_session_before_open: bool = False,
    ) -> DhanSessionBundle:
        if provider == "zerodha":
            return self.zerodha_chart_service.fetch_market_context(
                key,
                token,
                symbol=spec.symbol,
                tradingsymbol=spec.symbol,
                exchange_segment=spec.exchange_segment,
                prefer_last_closed_session_before_open=prefer_last_closed_session_before_open,
            )
        return self.chart_service.fetch_market_context(
            client_id=key,
            access_token=token,
            security_id=spec.security_id,
            exchange_segment=spec.exchange_segment,
            instrument_type=spec.instrument_type,
            prefer_last_closed_session_before_open=prefer_last_closed_session_before_open,
        )

    def _fetch_market_context_for_spec_days(
        self,
        provider: str,
        key: str,
        token: str,
        spec: InstrumentSpec,
        *,
        session_day: date,
        previous_context_day: date,
    ) -> DhanSessionBundle:
        if provider == "zerodha":
            return self.zerodha_chart_service.fetch_market_context_for_days(
                key,
                token,
                session_day=session_day,
                previous_context_day=previous_context_day,
                symbol=spec.symbol,
                tradingsymbol=spec.symbol,
                exchange_segment=spec.exchange_segment,
            )
        return self.chart_service.fetch_market_context_for_days(
            client_id=key,
            access_token=token,
            session_day=session_day,
            previous_context_day=previous_context_day,
            security_id=spec.security_id,
            exchange_segment=spec.exchange_segment,
            instrument_type=spec.instrument_type,
        )

    def _fetch_session_day_candles_for_spec(
        self,
        provider: str,
        key: str,
        token: str,
        spec: InstrumentSpec,
        session_day: date,
    ) -> tuple[list[Candle], str]:
        if provider == "zerodha":
            return self.zerodha_chart_service.fetch_session_day_candles(
                key,
                token,
                symbol=spec.symbol,
                tradingsymbol=spec.symbol,
                exchange_segment=spec.exchange_segment,
                session_day=session_day,
            )
        return self.chart_service.fetch_session_day_candles(
            key,
            token,
            spec.security_id,
            session_day,
            spec.exchange_segment,
            spec.instrument_type,
        )

    def _fetch_latest_session_day_candles_for_spec(
        self,
        provider: str,
        key: str,
        token: str,
        spec: InstrumentSpec,
        session_day: date,
    ) -> tuple[list[Candle], str, date]:
        if provider == "zerodha":
            return self.zerodha_chart_service.fetch_latest_available_session_day_candles(
                key,
                token,
                symbol=spec.symbol,
                tradingsymbol=spec.symbol,
                exchange_segment=spec.exchange_segment,
                session_day=session_day,
            )
        return self.chart_service.fetch_latest_available_session_day_candles(
            key,
            token,
            spec.security_id,
            session_day,
            spec.exchange_segment,
            spec.instrument_type,
        )

    def zerodha_login_url(self) -> str:
        api_key, _, _ = self._available_zerodha_credentials()
        if not api_key:
            raise ValueError("Save Zerodha API key before opening the Kite login URL.")
        return self.zerodha_execution_service.login_url(api_key)

    def generate_zerodha_session(self, request_token: str) -> DashboardState:
        api_key, api_secret, _ = self._available_zerodha_credentials()
        if not api_key or not api_secret:
            raise ValueError("Save Zerodha API key and API secret before generating access token.")
        if not request_token.strip():
            raise ValueError("Zerodha request token is required.")
        data = self.zerodha_execution_service.generate_session(
            api_key=api_key,
            api_secret=api_secret,
            request_token=request_token,
        )
        access_token = str(data.get("access_token") or "").strip()
        if not access_token:
            raise ValueError("Zerodha did not return an access token.")
        with self.lock:
            self.credential_store.save_zerodha_access_token(access_token)
            self._credential_summary_cache = self.credential_store.summary(self.settings)
            self.rulebook_service.learning_log.insert(0, "Zerodha Kite access token saved for current session.")
            self._mark_state_dirty_locked()
        return self.get_state()

    def _normalize_replay_decision_duration_minutes(self, minutes: int | None) -> int:
        try:
            normalized = int(minutes or 1)
        except (TypeError, ValueError):
            normalized = 1
        return min(max(normalized, 1), 60)

    def _minutes_since_session_open(self, candle_time: datetime) -> int:
        session_open = candle_time.replace(hour=9, minute=15, second=0, microsecond=0)
        elapsed = candle_time - session_open
        return max(int(elapsed.total_seconds() // 60), 0)

    def _is_replay_decision_boundary(self, evaluation_index: int, interval_minutes: int) -> bool:
        if interval_minutes <= 1:
            return True
        candle = self.candles[evaluation_index]
        return (self._minutes_since_session_open(candle.timestamp) + 1) % interval_minutes == 0

    def _aggregate_candles(self, candles: list[Candle], interval_minutes: int) -> list[Candle]:
        if interval_minutes <= 1 or len(candles) <= 1:
            return [candle.model_copy(deep=True) for candle in candles]
        aggregated: list[Candle] = []
        current_bucket: Candle | None = None
        current_bucket_key: tuple[date, int] | None = None
        for candle in candles:
            bucket_key = (candle.timestamp.date(), self._minutes_since_session_open(candle.timestamp) // interval_minutes)
            if current_bucket is None or bucket_key != current_bucket_key:
                if current_bucket is not None:
                    aggregated.append(current_bucket)
                current_bucket = Candle(
                    timestamp=candle.timestamp,
                    open=candle.open,
                    high=candle.high,
                    low=candle.low,
                    close=candle.close,
                    volume=candle.volume,
                )
                current_bucket_key = bucket_key
                continue
            current_bucket.high = max(current_bucket.high, candle.high)
            current_bucket.low = min(current_bucket.low, candle.low)
            current_bucket.close = candle.close
            current_bucket.volume += candle.volume
            current_bucket.timestamp = candle.timestamp
        if current_bucket is not None:
            aggregated.append(current_bucket)
        return aggregated

    def _build_evaluation_context_locked(
        self,
        evaluation_index: int,
        *,
        source: str,
        replay_decision_duration_minutes: int,
    ) -> StrategyContext:
        if source != "replay" or replay_decision_duration_minutes <= 1:
            return self.build_context()
        session_candles = self._aggregate_candles(
            get_session_candles_up_to_index(self.candles, evaluation_index),
            replay_decision_duration_minutes,
        )
        previous_day_candles = self._aggregate_candles(
            get_previous_day_candles(self.candles, evaluation_index),
            replay_decision_duration_minutes,
        )
        (
            companion_session_candles,
            companion_previous_day_candles,
            companion_recent_candles,
            companion_current_candle,
            companion_previous_day,
        ) = self._build_companion_snapshot_locked(
            evaluation_index=evaluation_index,
            replay_decision_duration_minutes=replay_decision_duration_minutes,
            source=source,
        )
        current_candle = session_candles[-1] if session_candles else self.candles[evaluation_index]
        recent_candles = session_candles[-20:]
        previous_day = calculate_previous_day_levels(self.candles, evaluation_index)
        liquidity_zones = self.find_liquidity_zones(session_candles, previous_day, previous_day_candles)
        liquidity_ledger = self.build_liquidity_ledger(session_candles, previous_day, previous_day_candles)
        operator_zones = self.find_operator_zones(session_candles)
        signal_events = self.detect_signal_events(current_candle, liquidity_zones, previous_day)
        recent_closed_trades = [
            trade.model_copy(deep=True)
            for trade in reversed(self.trade_history)
            if trade.status == "CLOSED"
            and (
                trade.trade_security_id == self.instrument_spec.security_id
                or trade.instrument_label == self.instrument_spec.label
                or trade.symbol.startswith(self.instrument_spec.symbol)
            )
        ][:6]
        return StrategyContext(
            instrument=self.instrument_state(),
            current_candle=current_candle,
            live_current_candle=None,
            recent_candles=recent_candles,
            session_candles=session_candles,
            previous_day_candles=previous_day_candles,
            previous_day=previous_day,
            liquidity_zones=liquidity_zones,
            liquidity_ledger=liquidity_ledger,
            operator_zones=operator_zones,
            signal_events=signal_events,
            market_structure=self.describe_market_structure(
                session_candles=session_candles,
                previous_day_candles=previous_day_candles,
                previous_day=previous_day,
                live_current_candle=None,
                liquidity_ledger=liquidity_ledger,
            ),
            companion_symbol=self.companion_instrument_spec.symbol if companion_session_candles else None,
            companion_current_candle=companion_current_candle,
            companion_recent_candles=companion_recent_candles,
            companion_session_candles=companion_session_candles,
            companion_previous_day_candles=companion_previous_day_candles,
            companion_previous_day=companion_previous_day,
            pending_setup=self.pending_setup,
            active_trade=self.active_trade,
            recent_closed_trades=recent_closed_trades,
            portfolio_order_count_estimate=self._portfolio_order_count_estimate_locked(),
            rulebook_markdown=self.rulebook_service.get_rulebook(),
            stock_partial_profit_enabled=self.credential_store.get_stock_partial_profit_enabled(self.settings),
            stock_trailing_stop_enabled=self.credential_store.get_stock_trailing_stop_enabled(self.settings),
            stock_heuristic_early_exit_enabled=self.credential_store.get_stock_heuristic_early_exit_enabled(self.settings),
            nifty_trailing_stop_enabled=self.credential_store.get_nifty_trailing_stop_enabled(self.settings),
            nifty_heuristic_early_exit_enabled=self.credential_store.get_nifty_heuristic_early_exit_enabled(self.settings),
            nifty_cost_sl_enabled=self._nifty_cost_sl_enabled(),
            nifty_cost_sl_points=self._nifty_cost_sl_points(),
            nifty_min_sl_points=self._nifty_min_sl_points(),
            nifty_max_sl_points=self._nifty_max_sl_points(),
            nifty_target_enabled=self._nifty_target_enabled(),
            nifty_target_points=self._nifty_target_points(),
            nifty_daily_max_loss_enabled=self._nifty_daily_max_loss_enabled(),
            nifty_daily_max_loss=self._nifty_daily_max_loss(),
            pyramiding_enabled=False if self._stock_derivative_execution_mode_enabled() else self.credential_store.get_pyramiding_enabled(self.settings),
            intelligent_pyramiding_enabled=False if self._stock_derivative_execution_mode_enabled() else self.credential_store.get_intelligent_pyramiding_enabled(self.settings),
            stock_percent_pyramiding_enabled=False if self._stock_derivative_execution_mode_enabled() else self._stock_percent_pyramiding_enabled(),
            stock_percent_pyramiding_step=self._stock_percent_pyramiding_step(),
            nifty_point_pyramiding_enabled=self._nifty_point_pyramiding_enabled(),
            nifty_point_pyramiding_points=self._nifty_point_pyramiding_points(),
            nifty_trade_bias=self._nifty_trade_bias(),
            nifty_option_trade_mode=self.credential_store.get_nifty_option_trade_mode(self.settings),
            stock_trade_bias=self.stock_watch_meta.get(self.instrument_spec.symbol, {}).get("trade_bias", "both"),
        )

    def _ensure_default_stock_watchlist(self) -> None:
        self.stock_watchlist["SBIN"] = get_instrument_spec(InstrumentMode.stock)
        self.selected_stock_symbol = self.selected_stock_symbol or "SBIN"
        self.stock_watch_meta.setdefault("SBIN", {})
        self.stock_sessions.setdefault("SBIN", self._build_stock_runtime_session(self.stock_watchlist["SBIN"]))
        if self.stock_watchlist["SBIN"].security_id:
            self._stock_symbol_by_security_id[self.stock_watchlist["SBIN"].security_id] = "SBIN"

    def _has_saved_stock_watchlist_preferences(self) -> bool:
        payload = self.credential_store.load()
        return any(
            key in payload
            for key in ("stock_watchlist_symbols", "selected_stock_symbol", "instrument_mode")
        )

    def _stock_watchlist_placeholder_spec(self) -> InstrumentSpec:
        return build_stock_instrument("STOCKS", "", label="Stock Watchlist")

    def _restore_persisted_ui_preferences_locked(self) -> None:
        instrument_mode, selected_symbol, persisted_watchlist = self.credential_store.get_ui_preferences()
        for symbol in persisted_watchlist:
            if symbol in self.stock_watchlist:
                continue
            try:
                spec = self._add_stock_to_watchlist_locked(symbol, make_selected=False)
            except ValueError:
                continue
            self.stock_sessions.setdefault(spec.symbol, self._build_stock_runtime_session(spec))
        if selected_symbol and selected_symbol not in self.stock_watchlist:
            try:
                self._add_stock_to_watchlist_locked(selected_symbol, make_selected=False)
            except ValueError:
                selected_symbol = None
        if selected_symbol and selected_symbol in self.stock_watchlist:
            self.selected_stock_symbol = selected_symbol
        if instrument_mode == InstrumentMode.stock:
            self.instrument_mode = InstrumentMode.stock
            if self.selected_stock_symbol and self.selected_stock_symbol in self.stock_watchlist:
                self.instrument_spec = self.stock_watchlist[self.selected_stock_symbol]
            else:
                self.instrument_spec = self._stock_watchlist_placeholder_spec()

    def _persist_ui_preferences_locked(self) -> None:
        self.credential_store.save_ui_preferences(
            instrument_mode=self.instrument_mode,
            selected_stock_symbol=self.selected_stock_symbol or "",
            stock_watchlist_symbols=list(self.stock_watchlist.keys()),
        )

    def _build_stock_runtime_session(self, spec: InstrumentSpec) -> StockRuntimeSession:
        return StockRuntimeSession(
            spec=spec,
            balance=self.settings.simulation_starting_balance,
        )

    def _selected_runtime_session_view_locked(self) -> StockRuntimeSession | None:
        if self.instrument_mode != InstrumentMode.stock or not self.selected_stock_symbol:
            return None
        return StockRuntimeSession(
            spec=self.instrument_spec,
            candles=list(self.candles),
            current_index=self.current_index,
            live_current_candle=self.live_current_candle.model_copy(deep=True) if self.live_current_candle is not None else None,
            signal_history=[event.model_copy(deep=True) for event in self.signal_history],
            signal_history_keys=set(self._signal_history_keys),
            pending_setup=self.pending_setup.model_copy(deep=True) if self.pending_setup is not None else None,
            active_trade=self.active_trade.model_copy(deep=True) if self.active_trade is not None else None,
            trade_history=[trade.model_copy(deep=True) for trade in self.trade_history],
            decision=self.decision.model_copy(deep=True) if self.decision is not None else None,
            realized_pnl=self.realized_pnl,
            balance=self.balance,
            data_sync=self.data_sync.model_copy(deep=True),
            heuristic_engine=self.heuristic_engine,
        )

    def _session_mark_price_locked(self, session: StockRuntimeSession) -> float | None:
        trade = session.active_trade
        if trade is None:
            return None
        if trade.price_mode != "cash":
            return trade.current_price
        reference_candle = None
        if session.live_current_candle is not None:
            reference_candle = session.live_current_candle
        elif 0 <= session.current_index < len(session.candles):
            reference_candle = session.candles[session.current_index]
        elif session.candles:
            reference_candle = session.candles[-1]
        if reference_candle is None:
            return trade.current_price
        return self.current_trade_market_price(reference_candle.close, trade)

    def _trade_pnl_for_session_locked(self, session: StockRuntimeSession) -> float | None:
        trade = session.active_trade
        if trade is None:
            return None
        if trade.price_mode == "cash":
            mark_price = self._session_mark_price_locked(session)
            if mark_price is not None:
                return self.calculate_trade_pnl(trade, mark_price)
        return trade.pnl

    def _estimated_order_count_for_trade(self, trade: SimulatedTrade) -> int:
        count = 1 + max(int(trade.pyramid_count or 0), 0) + max(int(trade.partial_exit_count or 0), 0)
        if (
            trade.status == "CLOSED"
            or trade.exit_time is not None
            or trade.closed_quantity >= trade.quantity
            or ((trade.open_quantity or 0) <= 0 and trade.quantity > 0)
        ):
            count += 1
        return count

    def _portfolio_order_count_estimate_locked(self) -> int:
        selected_runtime_view = self._selected_runtime_session_view_locked()
        total = 0
        if self.stock_watchlist:
            for symbol in self.stock_watchlist:
                session = (
                    selected_runtime_view
                    if symbol == self.selected_stock_symbol and selected_runtime_view is not None
                    else self.stock_sessions.get(symbol)
                )
                if session is None:
                    continue
                total += sum(self._estimated_order_count_for_trade(trade) for trade in session.trade_history)
            return total
        return sum(self._estimated_order_count_for_trade(trade) for trade in self.trade_history)

    def _build_integrated_pnl_state_locked(self, reference_time: datetime | None = None) -> IntegratedPnlState:
        realized_component = 0.0
        unrealized_component = 0.0
        selected_runtime_view = self._selected_runtime_session_view_locked()
        if self.stock_watchlist:
            for symbol in self.stock_watchlist:
                session = (
                    selected_runtime_view
                    if symbol == self.selected_stock_symbol and selected_runtime_view is not None
                    else self.stock_sessions.get(symbol)
                )
                if session is None:
                    continue
                realized_component += session.realized_pnl
                session_trade_pnl = self._trade_pnl_for_session_locked(session)
                if session_trade_pnl is not None:
                    unrealized_component += session_trade_pnl
        else:
            realized_component = self.realized_pnl
            if self.active_trade is not None:
                mark_price = self.current_trade_market_price(
                    (self.live_current_candle or self.candles[self.current_index]).close,
                    self.active_trade,
                ) if self.active_trade.price_mode == "cash" and (
                    self.live_current_candle is not None or 0 <= self.current_index < len(self.candles)
                ) else self.active_trade.current_price
                unrealized_component = self.calculate_trade_pnl(self.active_trade, mark_price)

        realized_component = round(realized_component, 2)
        unrealized_component = round(unrealized_component, 2)
        total_pnl = round(realized_component + unrealized_component, 2)
        stamp = reference_time or datetime.now()
        if self._integrated_pnl_peak is None or total_pnl > self._integrated_pnl_peak:
            self._integrated_pnl_peak = total_pnl
            self._integrated_pnl_peak_at = stamp
        if self._integrated_pnl_trough is None or total_pnl < self._integrated_pnl_trough:
            self._integrated_pnl_trough = total_pnl
            self._integrated_pnl_trough_at = stamp
        return IntegratedPnlState(
            realized_pnl=realized_component,
            unrealized_pnl=unrealized_component,
            total_pnl=total_pnl,
            max_total_pnl=self._integrated_pnl_peak or 0.0,
            max_total_pnl_at=self._integrated_pnl_peak_at,
            min_total_pnl=self._integrated_pnl_trough or 0.0,
            min_total_pnl_at=self._integrated_pnl_trough_at,
        )

    def _normalize_stock_trade_bias(self, trade_bias: str | None) -> str:
        normalized = (trade_bias or "both").strip().lower()
        if normalized in {"long", "long-only", "long_only", "buy"}:
            return "long"
        if normalized in {"short", "short-only", "short_only", "sell"}:
            return "short"
        return "both"

    def _stock_trade_bias_label(self, trade_bias: str | None) -> str:
        bias = self._normalize_stock_trade_bias(trade_bias)
        if bias == "long":
            return "long-only"
        if bias == "short":
            return "short-only"
        return "both-side"

    def _add_stock_to_watchlist_locked(
        self,
        symbol: str,
        *,
        make_selected: bool = False,
        trade_bias: str | None = None,
    ) -> InstrumentSpec:
        entry = self.stock_universe.preview(symbol)
        spec = build_stock_instrument(
            entry.symbol,
            entry.security_id,
            label=entry.label,
            exchange_segment=entry.exchange_segment,
            instrument_type=entry.instrument_type,
        )
        self.stock_watchlist[spec.symbol] = spec
        if spec.security_id:
            self._stock_symbol_by_security_id[spec.security_id] = spec.symbol
        meta = self.stock_watch_meta.setdefault(spec.symbol, {})
        if trade_bias is not None:
            meta["trade_bias"] = self._normalize_stock_trade_bias(trade_bias)
        else:
            meta.setdefault("trade_bias", "both")
        if not spec.security_id:
            meta["history_status"] = "resolving"
        self.stock_sessions.setdefault(spec.symbol, self._build_stock_runtime_session(spec))
        if make_selected or self.selected_stock_symbol is None:
            self.selected_stock_symbol = spec.symbol
        return spec

    def search_stocks(self, query: str, limit: int = 20) -> list[dict[str, str]]:
        matches = self.stock_universe.search(query, limit=limit)
        return [
            {
                "symbol": match.symbol,
                "label": match.label,
                "security_id": match.security_id,
            }
            for match in matches
        ]

    def add_stock_to_watchlist(self, symbol: str) -> DashboardState:
        with self.lock:
            previous_mode = self.instrument_mode
            had_live_feed = self.live_feed_adapter is not None
        with self.lock:
            if self.instrument_mode == InstrumentMode.stock and self.selected_stock_symbol:
                self._capture_stock_session_locked(self.selected_stock_symbol)
            spec = self._add_stock_to_watchlist_locked(symbol, make_selected=True)
            self._load_stock_session_locked(spec.symbol)
        if had_live_feed and previous_mode != InstrumentMode.stock:
            client, token = self._available_dhan_credentials()
            self.disconnect_live_feed()
            if client and token:
                self.connect_live_feed(client_id=client, access_token=token)
                with self.lock:
                    self.rulebook_service.learning_log.insert(
                        0,
                        f"Moved the live feed from {previous_mode.value} mode into the stock watchlist feed.",
                    )
                    self._mark_state_dirty_locked()
                    return self.get_state()
        self._schedule_watchlist_subscription_refresh()
        self._auto_prepare_watchlist_symbols_async([spec.symbol])
        with self.lock:
            self.rulebook_service.learning_log.insert(
                0,
                f"Added {spec.symbol} ({spec.security_id}) to the stock watchlist and made it active.",
            )
            self._persist_ui_preferences_locked()
            self._mark_state_dirty_locked()
            return self.get_state()

    def extract_bulk_stock_symbols(self, raw_text: str) -> tuple[list[str], list[str]]:
        seen: set[str] = set()
        added: list[str] = []
        skipped: list[str] = []
        for raw_line in (raw_text or "").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.lower().startswith("symbol"):
                continue
            candidate = re.split(r"\t+|\s{2,}|,", line, maxsplit=1)[0].strip().upper()
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            try:
                self.stock_universe.preview(candidate)
            except ValueError:
                skipped.append(candidate)
                continue
            added.append(candidate)
        return added, skipped

    def add_bulk_stocks_to_watchlist(
        self,
        raw_text: str,
        trade_bias: str | None = "both",
    ) -> tuple[DashboardState, list[str], list[str]]:
        symbols, skipped = self.extract_bulk_stock_symbols(raw_text)
        if not symbols:
            raise ValueError("No valid stock symbols were found in the pasted text.")
        normalized_bias = self._normalize_stock_trade_bias(trade_bias)
        with self.lock:
            previous_mode = self.instrument_mode
            had_live_feed = self.live_feed_adapter is not None
        with self.lock:
            if self.instrument_mode == InstrumentMode.stock and self.selected_stock_symbol:
                self._capture_stock_session_locked(self.selected_stock_symbol)
            first_selected = self.selected_stock_symbol is None or previous_mode != InstrumentMode.stock
            selected_symbol = self.selected_stock_symbol
            added_symbols: list[str] = []
            for symbol in symbols:
                spec = self._add_stock_to_watchlist_locked(
                    symbol,
                    make_selected=False,
                    trade_bias=normalized_bias,
                )
                added_symbols.append(spec.symbol)
                if first_selected and selected_symbol is None:
                    selected_symbol = spec.symbol
            if selected_symbol:
                self.selected_stock_symbol = selected_symbol
                self._load_stock_session_locked(selected_symbol)
        if had_live_feed and previous_mode != InstrumentMode.stock:
            client, token = self._available_dhan_credentials()
            self.disconnect_live_feed()
            if client and token:
                self.connect_live_feed(client_id=client, access_token=token)
                with self.lock:
                    self.rulebook_service.learning_log.insert(
                        0,
                        f"Moved the live feed from {previous_mode.value} mode into the stock watchlist feed.",
                    )
                    self._mark_state_dirty_locked()
                    return self.get_state(), added_symbols, skipped
        self._schedule_watchlist_subscription_refresh()
        self._auto_prepare_watchlist_symbols_async(added_symbols)
        with self.lock:
            skipped_note = f" Skipped {', '.join(skipped)}." if skipped else ""
            self.rulebook_service.learning_log.insert(
                0,
                f"Bulk-added {len(added_symbols)} {self._stock_trade_bias_label(normalized_bias)} stock(s) "
                f"to the watchlist: {', '.join(added_symbols)}.{skipped_note}",
            )
            self._persist_ui_preferences_locked()
            self._mark_state_dirty_locked()
            return self.get_state(), added_symbols, skipped

    def remove_stock_from_watchlist(self, symbol: str) -> DashboardState:
        normalized = (symbol or "").strip().upper()
        with self.lock:
            if not normalized:
                raise ValueError("Stock symbol is required.")
            if normalized not in self.stock_watchlist:
                raise ValueError(f"{normalized} is not in the stock watchlist.")
            if self.instrument_mode == InstrumentMode.stock and self.selected_stock_symbol:
                self._capture_stock_session_locked(self.selected_stock_symbol)
            removed_spec = self.stock_watchlist.pop(normalized)
            if removed_spec.security_id:
                self._stock_symbol_by_security_id.pop(removed_spec.security_id, None)
            self.stock_watch_meta.pop(normalized, None)
            self._stock_execution_feedback.pop(normalized, None)
            self.stock_sessions.pop(normalized, None)
            if self.selected_stock_symbol == normalized:
                self.selected_stock_symbol = next(iter(self.stock_watchlist), None)
                if self.selected_stock_symbol:
                    self._load_stock_session_locked(self.selected_stock_symbol)
                elif self.instrument_mode == InstrumentMode.stock:
                    self._clear_active_session_locked()
                    self.instrument_spec = self._stock_watchlist_placeholder_spec()
                    self.live_feed.instrument_label = self.instrument_spec.label
                    self.live_feed.security_id = self.instrument_spec.security_id
                    self.live_feed.current_candle = None
                    self.live_feed.last_ltp = None
                    self.live_feed.last_tick_at = None
                    self.data_sync = DataSyncState(
                        status="idle",
                        source="stock-watchlist",
                        message="No stock in the watchlist. Search and add a stock to start a new chart.",
                    )
            self.rulebook_service.learning_log.insert(
                0,
                f"Removed {removed_spec.symbol} ({removed_spec.security_id or 'unresolved'}) from the stock watchlist.",
            )
            self._persist_ui_preferences_locked()
            self._mark_state_dirty_locked()
        self._schedule_watchlist_subscription_refresh()
        with self.lock:
            return self.get_state()

    def select_stock(self, symbol: str) -> DashboardState:
        normalized = (symbol or "").strip().upper()
        with self.lock:
            previous_mode = self.instrument_mode
            had_live_feed = self.live_feed_adapter is not None
        with self.lock:
            if self.instrument_mode == InstrumentMode.stock and self.selected_stock_symbol:
                self._capture_stock_session_locked(self.selected_stock_symbol)
            spec = self.stock_watchlist.get(normalized)
            if spec is None:
                spec = self._add_stock_to_watchlist_locked(normalized, make_selected=True)
            self.selected_stock_symbol = spec.symbol
            self._load_stock_session_locked(spec.symbol)
        if had_live_feed and previous_mode != InstrumentMode.stock:
            client, token = self._available_dhan_credentials()
            self.disconnect_live_feed()
            if client and token:
                self.connect_live_feed(client_id=client, access_token=token)
                with self.lock:
                    self.rulebook_service.learning_log.insert(
                        0,
                        f"Moved the live feed from {previous_mode.value} mode into the stock watchlist feed.",
                    )
                    self._mark_state_dirty_locked()
                    return self.get_state()
        self._schedule_watchlist_subscription_refresh()
        self._auto_prepare_watchlist_symbols_async([spec.symbol])
        with self.lock:
            self.rulebook_service.learning_log.insert(0, f"Selected {spec.symbol} as the active stock chart.")
            self._persist_ui_preferences_locked()
            self._mark_state_dirty_locked()
            return self.get_state()

    def _auto_sync_selected_stock_async(self) -> None:
        self._auto_prepare_watchlist_symbols_async([self.selected_stock_symbol] if self.selected_stock_symbol else [])

    def _auto_prepare_watchlist_symbols_async(self, symbols: list[str]) -> None:
        broker = self._selected_broker_provider()
        try:
            provider, client, token = self._history_provider_credentials()
        except ValueError:
            provider, client, token = broker, "", ""
        with self.lock:
            unique_symbols = [symbol for symbol in dict.fromkeys(symbols) if symbol]
            selected_symbol = self.selected_stock_symbol
            if selected_symbol:
                selected_spec = self.stock_watchlist.get(selected_symbol)
                status_text = (
                    "syncing"
                    if selected_spec and (provider == "zerodha" or selected_spec.security_id) and client and token
                    else ("queued" if selected_spec and (provider == "zerodha" or selected_spec.security_id) else "resolving")
                )
                self.stock_watch_meta.setdefault(selected_symbol, {})["history_status"] = status_text
                if not client or not token:
                    self._clear_active_session_locked()
                    self.data_sync = DataSyncState(
                        status="idle",
                        source="stock-watchlist",
                        message=(
                            f"{self.instrument_spec.label} is active now. Save {self._history_broker_label(provider)} "
                            "credentials or press sync/connect to backfill 1-minute context."
                        ),
                    )
                else:
                    self.data_sync = DataSyncState(
                        status="syncing",
                        source="stock-watchlist",
                        message=(
                            f"Preparing {self.instrument_spec.label} in the background and syncing "
                            f"{self._history_broker_label(provider)} 1-minute context."
                        ),
                    )
                self._mark_state_dirty_locked()
        if not unique_symbols:
            return
        worker = threading.Thread(
            target=self._run_watchlist_prepare_batch,
            args=(unique_symbols, client or None, token or None, provider),
            name=f"watchlist-prepare-{(selected_symbol or unique_symbols[0]).lower()}",
            daemon=True,
        )
        worker.start()

    def _run_watchlist_prepare_batch(self, symbols: list[str], client_id: str | None, access_token: str | None, provider: str = "dhan") -> None:
        unique_symbols = [symbol for symbol in dict.fromkeys(symbols) if symbol]
        if not unique_symbols:
            return
        with ThreadPoolExecutor(max_workers=self._max_stock_sync_workers(len(unique_symbols))) as executor:
            futures = [
                executor.submit(self._run_selected_stock_prepare, symbol, client_id, access_token, provider)
                for symbol in unique_symbols
            ]
            for future in as_completed(futures):
                future.result()

    def _run_selected_stock_prepare(self, symbol: str, client_id: str | None, access_token: str | None, provider: str = "dhan") -> None:
        try:
            self._resolve_watchlist_symbol_if_needed(symbol)
            self._schedule_watchlist_subscription_refresh()
            if client_id and access_token:
                self._sync_stock_symbol_now(symbol, client_id=client_id, access_token=access_token, provider=provider)
            else:
                with self.lock:
                    spec = self.stock_watchlist.get(symbol)
                    if spec and spec.security_id:
                        self.stock_watch_meta.setdefault(symbol, {})["history_status"] = "queued"
                        if symbol == self.selected_stock_symbol:
                            self.data_sync = DataSyncState(
                                status="idle",
                                source="stock-watchlist",
                                message=(
                                    f"Resolved {spec.label} (security {spec.security_id}). Save credentials or press sync/connect "
                                    "to load 1-minute context."
                                ),
                            )
                        self._mark_state_dirty_locked()
        except Exception as exc:
            with self.lock:
                meta = self.stock_watch_meta.setdefault(symbol, {})
                meta["history_status"] = "error"
                if symbol == self.selected_stock_symbol:
                    self.data_sync = DataSyncState(
                        status="error",
                        source="stock-watchlist",
                        message=f"Background sync failed for {symbol}: {exc}",
                    )
                self.rulebook_service.learning_log.insert(0, f"Background sync failed for {symbol}: {exc}")
                self._mark_state_dirty_locked()

    def _resolve_watchlist_symbol_if_needed(self, symbol: str) -> InstrumentSpec:
        with self.lock:
            spec = self.stock_watchlist[symbol]
            if spec.security_id:
                return spec
        resolved = self.stock_universe.resolve(symbol)
        updated_spec = build_stock_instrument(
            resolved.symbol,
            resolved.security_id,
            label=resolved.label,
            exchange_segment=resolved.exchange_segment,
            instrument_type=resolved.instrument_type,
        )
        with self.lock:
            previous_spec = self.stock_watchlist.get(symbol)
            if previous_spec and previous_spec.security_id and previous_spec.security_id != updated_spec.security_id:
                self._stock_symbol_by_security_id.pop(previous_spec.security_id, None)
            self.stock_watchlist[symbol] = updated_spec
            if updated_spec.security_id:
                self._stock_symbol_by_security_id[updated_spec.security_id] = symbol
            session = self.stock_sessions.setdefault(symbol, self._build_stock_runtime_session(updated_spec))
            session.spec = updated_spec
            if symbol == self.selected_stock_symbol and self.instrument_mode == InstrumentMode.stock:
                self.instrument_spec = updated_spec
                self.live_feed.instrument_label = updated_spec.label
                self.live_feed.security_id = updated_spec.security_id
            self.stock_watch_meta.setdefault(symbol, {})["history_status"] = "queued"
            self._mark_state_dirty_locked()
            return updated_spec

    def _sync_stock_symbol_now(self, symbol: str, *, client_id: str, access_token: str, provider: str = "dhan") -> None:
        spec = self._resolve_watchlist_symbol_if_needed(symbol)
        if provider != "zerodha" and not spec.security_id:
            raise ValueError(f"Could not resolve a Dhan NSE cash security id for {symbol}.")
        bundle = self._fetch_market_context_for_spec(
            provider,
            client_id,
            access_token,
            spec,
            prefer_last_closed_session_before_open=True,
        )
        self._apply_bundle_to_stock_session(symbol, bundle, replay_from_session_start=False)
        with self.lock:
            self.stock_watch_meta.setdefault(symbol, {}).update(
                {
                    "history_status": "ready",
                    "previous_day_candles": len(bundle.previous_day_candles),
                    "intraday_candles": len(bundle.intraday_candles),
                    "total_loaded": len(bundle.previous_day_candles) + len(bundle.intraday_candles),
                }
            )
            if symbol == self.selected_stock_symbol:
                self.data_sync = self.stock_sessions[symbol].data_sync.model_copy(deep=True)
            self.rulebook_service.learning_log.insert(
                0,
                (
                    f"Synced {self._history_broker_label(provider)} chart history for {spec.label}: "
                    f"{len(bundle.previous_day_candles)} previous-day candles via {bundle.previous_day_source}, "
                    f"{len(bundle.intraday_candles)} intraday closed candles, "
                    f"open candle {'loaded' if bundle.live_open_candle else 'not present'}."
                ),
            )
            self._mark_state_dirty_locked()

    def _clear_active_session_locked(self) -> None:
        if self.live_feed_adapter is not None and self._active_option_subscription is not None:
            self.live_feed_adapter.unsubscribe_symbols([self._active_option_subscription])
        self.candles = []
        self.current_index = -1
        self.active_trade = None
        self.trade_history = []
        self._bulk_replay_trade_history = []
        self.decision = None
        self.realized_pnl = 0.0
        self.balance = self.settings.simulation_starting_balance
        self.live_current_candle = None
        self.signal_history = []
        self._signal_history_keys = set()
        self._active_option_subscription = None
        self.pending_setup = None
        self.heuristic_engine.reset_session()
        self._live_cumulative_volume_by_security_id.clear()

    def _capture_stock_session_locked(self, symbol: str) -> None:
        session = self.stock_sessions.setdefault(symbol, self._build_stock_runtime_session(self.stock_watchlist[symbol]))
        session.spec = self.stock_watchlist[symbol]
        session.candles = list(self.candles)
        session.current_index = self.current_index
        session.live_current_candle = self.live_current_candle.model_copy(deep=True) if self.live_current_candle is not None else None
        session.signal_history = [event.model_copy(deep=True) for event in self.signal_history]
        session.signal_history_keys = set(self._signal_history_keys)
        session.pending_setup = self.pending_setup.model_copy(deep=True) if self.pending_setup is not None else None
        session.active_trade = self.active_trade.model_copy(deep=True) if self.active_trade is not None else None
        session.trade_history = [trade.model_copy(deep=True) for trade in self.trade_history]
        session.decision = self.decision.model_copy(deep=True) if self.decision is not None else None
        session.realized_pnl = self.realized_pnl
        session.balance = self.balance
        session.data_sync = self.data_sync.model_copy(deep=True)
        session.heuristic_engine = self.heuristic_engine

    def _load_stock_session_locked(self, symbol: str) -> None:
        session = self.stock_sessions.setdefault(symbol, self._build_stock_runtime_session(self.stock_watchlist[symbol]))
        self.instrument_mode = InstrumentMode.stock
        self.instrument_spec = session.spec
        self.candles = list(session.candles)
        self.current_index = session.current_index
        self.live_current_candle = session.live_current_candle.model_copy(deep=True) if session.live_current_candle is not None else None
        self.signal_history = [event.model_copy(deep=True) for event in session.signal_history]
        self._signal_history_keys = set(session.signal_history_keys)
        self.pending_setup = session.pending_setup.model_copy(deep=True) if session.pending_setup is not None else None
        self.active_trade = session.active_trade.model_copy(deep=True) if session.active_trade is not None else None
        self.trade_history = [trade.model_copy(deep=True) for trade in session.trade_history]
        self._bulk_replay_trade_history = []
        self.decision = session.decision.model_copy(deep=True) if session.decision is not None else None
        self.realized_pnl = session.realized_pnl
        self.balance = session.balance or self.settings.simulation_starting_balance
        self.data_sync = session.data_sync.model_copy(deep=True)
        self.heuristic_engine = session.heuristic_engine
        self.live_feed.instrument_label = self.instrument_spec.label
        self.live_feed.security_id = self.instrument_spec.security_id
        self.live_feed.current_candle = self.live_current_candle
        self.live_feed.last_ltp = self.stock_watch_meta.get(symbol, {}).get("last_ltp")
        self.live_feed.last_tick_at = self.stock_watch_meta.get(symbol, {}).get("last_tick_at")

    def _persist_selected_stock_session_locked(self) -> None:
        if self.instrument_mode == InstrumentMode.stock and self.selected_stock_symbol:
            self._capture_stock_session_locked(self.selected_stock_symbol)

    def _run_in_stock_session(self, symbol: str, callback):
        with self.lock:
            selected_symbol = self.selected_stock_symbol
            if symbol == selected_symbol:
                result = callback()
                self._capture_stock_session_locked(symbol)
                return result
            if selected_symbol:
                self._capture_stock_session_locked(selected_symbol)
            self._load_stock_session_locked(symbol)
        try:
            return callback()
        finally:
            with self.lock:
                self._capture_stock_session_locked(symbol)
                if selected_symbol:
                    self._load_stock_session_locked(selected_symbol)

    def _apply_bundle_to_stock_session(
        self,
        symbol: str,
        bundle,
        *,
        replay_from_session_start: bool,
        replay_decision_duration_minutes: int = 1,
        companion_bundle=None,
    ) -> None:
        def operation():
            if replay_from_session_start:
                self._begin_replay_simulation()
            try:
                with self.lock:
                    result = self._load_dhan_bundle(bundle, replay_from_session_start=replay_from_session_start)
                    if companion_bundle is not None:
                        self._load_companion_bundle_locked(
                            companion_bundle,
                            replay_from_session_start=replay_from_session_start,
                        )
                if replay_from_session_start:
                    start_index, end_index = result
                    for evaluation_index in range(start_index, end_index + 1):
                        self._evaluate_index(
                            evaluation_index,
                            source="replay",
                            replay_decision_duration_minutes=replay_decision_duration_minutes,
                        )
                    with self.lock:
                        last_candle = self.candles[end_index] if 0 <= end_index < len(self.candles) else None
                        if self.active_trade and last_candle and bundle.live_open_candle is None:
                            self.close_active_trade(last_candle, "Replay completed at the final available session candle.")
                    return None
                evaluation_index = result
                if evaluation_index is not None:
                    self._evaluate_index(evaluation_index, source="sync")
                return None
            finally:
                if replay_from_session_start:
                    self._end_replay_simulation()

        self._run_in_stock_session(symbol, operation)

    def _build_stock_watchlist_state_locked(self) -> list[StockWatchItem]:
        items: list[StockWatchItem] = []
        selected_runtime_view = self._selected_runtime_session_view_locked()
        broker = self._selected_broker_provider()
        for symbol, spec in self.stock_watchlist.items():
            meta = self.stock_watch_meta.get(symbol, {})
            feedback = self._stock_execution_feedback.get(symbol, {})
            session = selected_runtime_view if symbol == self.selected_stock_symbol and selected_runtime_view is not None else self.stock_sessions.get(symbol)
            active_trade_pnl = self._trade_pnl_for_session_locked(session) if session is not None else None
            turnover_snapshot = self._stock_turnover_snapshot_for_session_locked(session)
            feed_security_id = self._live_feed_security_id_for_spec(spec, broker)
            trade_count = len(session.trade_history) if session is not None else 0
            closed_trade_count = (
                sum(1 for trade in session.trade_history if trade.status == "CLOSED") if session is not None else 0
            )
            last_trade_status = session.trade_history[-1].status if session is not None and session.trade_history else None
            items.append(
                StockWatchItem(
                    symbol=symbol,
                    label=spec.label,
                    security_id=spec.security_id,
                    trade_bias=meta.get("trade_bias", "both"),
                    selected=symbol == self.selected_stock_symbol,
                    subscribed=bool(feed_security_id and feed_security_id in self._stock_quote_subscriptions),
                    last_ltp=meta.get("last_ltp"),
                    last_tick_at=meta.get("last_tick_at"),
                    ticks_received=meta.get("ticks_received", 0),
                    history_status=meta.get("history_status", "idle"),
                    previous_day_candles=meta.get("previous_day_candles", 0),
                    intraday_candles=meta.get("intraday_candles", 0),
                    total_loaded=meta.get("total_loaded", 0),
                    last_5m_turnover=turnover_snapshot.turnover if turnover_snapshot else None,
                    last_5m_turnover_passed=turnover_snapshot.passed if turnover_snapshot else None,
                    last_5m_turnover_start=turnover_snapshot.window_start if turnover_snapshot else None,
                    last_5m_turnover_end=turnover_snapshot.window_end if turnover_snapshot else None,
                    decision_action=session.decision.action.value if session and session.decision is not None else None,
                    decision_confidence=session.decision.confidence if session and session.decision is not None else None,
                    decision_reason=session.decision.reason if session and session.decision is not None else None,
                    has_active_trade=session.active_trade is not None if session else False,
                    active_trade_direction=session.active_trade.direction if session and session.active_trade is not None else None,
                    active_trade_pnl=active_trade_pnl,
                    trade_count=trade_count,
                    closed_trade_count=closed_trade_count,
                    last_trade_status=last_trade_status,
                    realized_pnl=session.realized_pnl if session else 0.0,
                    live_order_message=feedback.get("message"),
                    live_order_error=feedback.get("error"),
                    live_order_updated_at=feedback.get("updated_at"),
                )
            )
        items.sort(key=lambda item: (not item.selected, item.symbol))
        return items

    def _last_completed_5m_window(self, reference_time: datetime) -> tuple[datetime, datetime]:
        minute_floor = reference_time.replace(second=0, microsecond=0)
        completed_end_minute = (minute_floor.minute // 5) * 5
        window_end = minute_floor.replace(minute=completed_end_minute)
        window_start = window_end - timedelta(minutes=5)
        return window_start, window_end

    def _stock_turnover_snapshot_from_candles(
        self,
        candles: list[Candle],
        reference_time: datetime,
    ) -> StockTurnoverSnapshot | None:
        window_start, window_end = self._last_completed_5m_window(reference_time)
        market_open = reference_time.replace(hour=9, minute=15, second=0, microsecond=0)
        if window_start < market_open:
            return None
        window_candles = [
            candle
            for candle in candles
            if candle.timestamp.date() == reference_time.date()
            and window_start <= candle.timestamp < window_end
        ]
        if not window_candles:
            return None
        close = window_candles[-1].close
        volume = sum(max(candle.volume, 0.0) for candle in window_candles)
        turnover = round(close * volume, 2)
        return StockTurnoverSnapshot(
            window_start=window_start,
            window_end=window_end,
            close=close,
            volume=volume,
            turnover=turnover,
            passed=turnover >= self._stock_min_5m_turnover(),
        )

    def _stock_turnover_snapshot_for_session_locked(
        self,
        session: StockRuntimeSession | None,
    ) -> StockTurnoverSnapshot | None:
        if session is None:
            return None
        reference_candle = session.live_current_candle
        if reference_candle is None and 0 <= session.current_index < len(session.candles):
            reference_candle = session.candles[session.current_index]
        if reference_candle is None:
            return None
        return self._stock_turnover_snapshot_from_candles(session.candles, reference_candle.timestamp)

    def _stock_turnover_snapshot_for_current_context_locked(
        self,
        current_candle: Candle,
    ) -> StockTurnoverSnapshot | None:
        return self._stock_turnover_snapshot_from_candles(self.candles, current_candle.timestamp)

    def _format_crore(self, value: float) -> str:
        return f"{value / 10000000:.2f} crore"

    def _normalize_stock_replay_scope(self, stock_replay_scope: str | None) -> str:
        scope = (stock_replay_scope or "all").strip().lower().replace("-", "_")
        if scope in {"active", "selected", "current", "single"}:
            return "active"
        if scope in {"all", "watchlist", "all_watchlist", "select_all"}:
            return "all"
        raise ValueError("Stock replay scope must be active or all.")

    def _stock_replay_symbols_for_scope_locked(self, stock_replay_scope: str | None) -> tuple[list[str], str | None, str]:
        if not self.stock_watchlist:
            raise ValueError("No stocks are in the watchlist. Add a stock before starting stock replay.")
        scope = self._normalize_stock_replay_scope(stock_replay_scope)
        if scope == "all":
            symbols = list(self.stock_watchlist.keys())
            selected = self.selected_stock_symbol if self.selected_stock_symbol in self.stock_watchlist else symbols[0]
            return symbols, selected, scope
        selected = self.selected_stock_symbol
        if not selected or selected not in self.stock_watchlist:
            raise ValueError("Select a stock from the watchlist before starting active-stock replay.")
        return [selected], selected, scope

    def _stock_replay_scope_label(self, scope: str) -> str:
        return "all watchlist stocks" if scope == "all" else "the active stock"

    def _apply_stock_turnover_filter_locked(
        self,
        current_candle: Candle,
        decision: TradeDecision,
    ) -> TradeDecision:
        if (
            self.operating_mode != OperatingMode.heuristic
            or self.instrument_mode != InstrumentMode.stock
            or self.instrument_spec.supports_options
            or decision.action not in {TradeAction.enter_call, TradeAction.enter_put}
        ):
            return decision

        threshold = self._stock_min_5m_turnover()
        snapshot = self._stock_turnover_snapshot_for_current_context_locked(current_candle)
        side = "long" if decision.action == TradeAction.enter_call else "short"
        if snapshot is not None and snapshot.passed:
            passed = decision.model_copy(deep=True)
            turnover_note = (
                f"5-minute turnover gate passed: {snapshot.window_start.strftime('%H:%M')}-"
                f"{snapshot.window_end.strftime('%H:%M')} turnover was {self._format_crore(snapshot.turnover)} "
                f"(close {snapshot.close:.2f} x volume {snapshot.volume:.0f}), above required "
                f"{self._format_crore(threshold)}."
            )
            passed.reason = f"{passed.reason} {turnover_note}".strip()
            return passed

        blocked = decision.model_copy(deep=True)
        blocked.action = TradeAction.no_trade
        blocked.confidence = min(blocked.confidence, 0.49)
        blocked.decision_source = f"{decision.decision_source}-turnover-filter"
        if decision.decision_source == "pending-setup-trigger":
            blocked.pending_setup_action = "INVALIDATE"
            blocked.pending_setup_notes = "Pending setup trigger failed the stock 5-minute turnover gate."
        else:
            blocked.pending_setup_action = "NONE"

        if snapshot is None:
            window_start, window_end = self._last_completed_5m_window(current_candle.timestamp)
            blocked.reason = (
                f"Stock turnover gate blocked {side} entry because no completed 5-minute candle was available "
                f"for {window_start.strftime('%H:%M')}-{window_end.strftime('%H:%M')}. "
                f"Required turnover is at least {self._format_crore(threshold)}."
            )
            return blocked

        blocked.reason = (
            f"Stock turnover gate blocked {side} entry: last completed 5-minute candle "
            f"{snapshot.window_start.strftime('%H:%M')}-{snapshot.window_end.strftime('%H:%M')} "
            f"turnover was {self._format_crore(snapshot.turnover)} "
            f"(close {snapshot.close:.2f} x volume {snapshot.volume:.0f}), below required "
            f"{self._format_crore(threshold)}."
        )
        return blocked

    def _apply_stock_trade_bias_filter_locked(self, decision: TradeDecision) -> TradeDecision:
        if self.instrument_spec.supports_options:
            return decision
        bias = self._normalize_stock_trade_bias(
            self.stock_watch_meta.get(self.instrument_spec.symbol, {}).get("trade_bias", "both")
        )
        if bias == "both":
            return decision
        blocked_side = "short" if bias == "long" else "long"
        entry_blocked = (
            (bias == "long" and decision.action == TradeAction.enter_put)
            or (bias == "short" and decision.action == TradeAction.enter_call)
        )
        pending_option = self.normalize_option_type(decision.pending_setup_option_type)
        pending_blocked = (
            decision.pending_setup_action in {"ARM", "REPLACE", "KEEP"}
            and ((bias == "long" and pending_option == "PE") or (bias == "short" and pending_option == "CE"))
        )
        if not entry_blocked and not pending_blocked:
            return decision
        blocked = decision.model_copy(deep=True)
        blocked.action = TradeAction.no_trade
        blocked.confidence = min(blocked.confidence, 0.49)
        blocked.pending_setup_action = "INVALIDATE" if decision.decision_source == "pending-setup-trigger" else "NONE"
        blocked.pending_setup_notes = f"Pending setup invalidated by {self._stock_trade_bias_label(bias)} stock bias."
        blocked.pending_setup_option_type = None
        blocked.pending_setup_direction = None
        blocked.pending_setup_trigger_price = None
        blocked.reason = (
            f"Stock is in the {self._stock_trade_bias_label(bias)} bulk list, so {blocked_side} setups are ignored. "
            f"Original setup: {decision.reason}"
        )
        return blocked

    def _apply_nifty_trade_bias_filter_locked(self, decision: TradeDecision) -> TradeDecision:
        if (
            self.instrument_mode != InstrumentMode.nifty
            or self.instrument_spec.symbol != "NIFTY"
            or not self.instrument_spec.supports_options
        ):
            return decision
        bias = self._nifty_trade_bias()
        if bias == "both":
            return decision
        blocked_side = "short" if bias == "long" else "long"
        entry_blocked = (
            (bias == "long" and decision.action == TradeAction.enter_put)
            or (bias == "short" and decision.action == TradeAction.enter_call)
        )
        pending_option = self.normalize_option_type(decision.pending_setup_option_type)
        pending_blocked = (
            decision.pending_setup_action in {"ARM", "REPLACE", "KEEP"}
            and ((bias == "long" and pending_option == "PE") or (bias == "short" and pending_option == "CE"))
        )
        if not entry_blocked and not pending_blocked:
            return decision
        blocked = decision.model_copy(deep=True)
        blocked.action = TradeAction.no_trade
        blocked.confidence = min(blocked.confidence, 0.49)
        blocked.pending_setup_action = (
            "INVALIDATE" if pending_blocked or decision.decision_source == "pending-setup-trigger" else "NONE"
        )
        blocked.pending_setup_notes = f"Pending setup invalidated by NIFTY {bias} bias."
        blocked.pending_setup_option_type = None
        blocked.pending_setup_direction = None
        blocked.pending_setup_trigger_price = None
        blocked.reason = (
            f"NIFTY bias is set to {bias}, so {blocked_side} setups are ignored. "
            f"Original setup: {decision.reason}"
        )
        return blocked

    def _apply_nifty_daily_loss_cap_filter_locked(
        self,
        current_candle: Candle,
        decision: TradeDecision,
        source: str,
    ) -> TradeDecision:
        if (
            source != "live"
            or self.instrument_mode != InstrumentMode.nifty
            or self.instrument_spec.symbol != "NIFTY"
            or not self._nifty_daily_max_loss_enabled()
            or self._nifty_daily_max_loss() <= 0
        ):
            return decision
        cap_hit = self._nifty_daily_loss_cap_hit_locked(current_candle.timestamp.date(), include_active=False)
        if not cap_hit:
            return decision
        is_entry = decision.action in {TradeAction.enter_call, TradeAction.enter_put}
        is_pending = self.normalize_pending_setup_action(decision.pending_setup_action) in {"ARM", "REPLACE", "KEEP"}
        if not is_entry and not is_pending:
            return decision
        blocked = decision.model_copy(deep=True)
        blocked.action = TradeAction.no_trade
        blocked.confidence = min(blocked.confidence, 0.2)
        blocked.pending_setup_action = "INVALIDATE" if decision.decision_source == "pending-setup-trigger" else "NONE"
        blocked.pending_setup_option_type = None
        blocked.pending_setup_direction = None
        blocked.pending_setup_trigger_price = None
        blocked.pending_setup_notes = self._nifty_daily_loss_cap_note_locked(
            current_candle.timestamp.date(),
            include_active=False,
        )
        blocked.reason = blocked.pending_setup_notes
        return blocked

    def _live_feed_adapter_running_locked(self) -> bool:
        if self.live_feed_adapter is None:
            return False
        is_running = getattr(self.live_feed_adapter, "is_running", None)
        if callable(is_running):
            try:
                return bool(is_running())
            except Exception:
                return False
        return self.live_feed.status in {"connecting", "connected", "reconnecting"}

    def _sync_watchlist_subscriptions(self) -> None:
        plan = self._build_watchlist_subscription_plan()
        if plan is None:
            return
        adapter, current, desired = plan
        changed = False
        for security_id, subscription in current.items():
            if security_id not in desired:
                adapter.unsubscribe_symbols([subscription])
                with self.lock:
                    if self.live_feed_adapter is adapter:
                        self._stock_quote_subscriptions.pop(security_id, None)
                        changed = True
        for security_id, subscription in desired.items():
            if security_id in current:
                continue
            adapter.subscribe_symbols([subscription])
            with self.lock:
                if self.live_feed_adapter is adapter:
                    self._stock_quote_subscriptions[security_id] = subscription
                    changed = True
        if changed:
            with self.lock:
                self._mark_state_dirty_locked()

    def _build_watchlist_subscription_plan(self):
        with self.lock:
            if self.live_feed_adapter is None or self.instrument_mode != InstrumentMode.stock:
                return None
            adapter = self.live_feed_adapter
            broker = self._selected_broker_provider()
            if broker == "zerodha":
                desired = {
                    self._live_feed_security_id_for_spec(spec, broker): self._zerodha_quote_subscription_for_spec(spec)
                    for spec in self.stock_watchlist.values()
                    if self._live_feed_security_id_for_spec(spec, broker)
                }
            else:
                desired = {
                    spec.security_id: resolve_quote_subscription(spec.security_id, spec.exchange_segment)
                    for spec in self.stock_watchlist.values()
                    if spec.security_id
                }
            current = dict(self._stock_quote_subscriptions)
        return adapter, current, desired

    def _schedule_watchlist_subscription_refresh(self) -> None:
        self._watchlist_subscription_refresh_event.set()

    def _run_watchlist_subscription_worker(self) -> None:
        while True:
            self._watchlist_subscription_refresh_event.wait()
            self._watchlist_subscription_refresh_event.clear()
            while True:
                try:
                    self._sync_watchlist_subscriptions()
                except Exception as exc:
                    with self.lock:
                        self.rulebook_service.learning_log.insert(
                            0,
                            f"Watchlist subscription refresh failed: {exc}",
                        )
                        self._mark_state_dirty_locked()
                if not self._watchlist_subscription_refresh_event.is_set():
                    break
                self._watchlist_subscription_refresh_event.clear()

    def set_instrument_mode(self, mode: InstrumentMode | str) -> DashboardState:
        normalized_mode = InstrumentMode(mode)
        adapter = None
        with self.lock:
            if self.instrument_mode == InstrumentMode.stock and self.selected_stock_symbol:
                self._capture_stock_session_locked(self.selected_stock_symbol)
            if normalized_mode == self.instrument_mode:
                return self.get_state()
            if self.live_feed_adapter is not None:
                adapter = self.live_feed_adapter
                self.live_feed_adapter = None
            self.instrument_mode = normalized_mode
            if normalized_mode == InstrumentMode.stock:
                if self.selected_stock_symbol and self.selected_stock_symbol in self.stock_watchlist:
                    self.instrument_spec = self.stock_watchlist[self.selected_stock_symbol]
                else:
                    self.instrument_spec = self._stock_watchlist_placeholder_spec()
            else:
                self.instrument_spec = get_instrument_spec(normalized_mode)
            self.live_feed = self._build_live_feed_state()
        if adapter is not None:
            adapter.stop()
        self._clear_live_packet_queue()
        if normalized_mode == InstrumentMode.stock:
            with self.lock:
                self._clear_companion_context_locked()
                if self.selected_stock_symbol and self.selected_stock_symbol in self.stock_watchlist:
                    self._load_stock_session_locked(self.selected_stock_symbol)
                else:
                    self._clear_active_session_locked()
                    self.instrument_spec = self._stock_watchlist_placeholder_spec()
                    self.data_sync = DataSyncState(
                        status="idle",
                        source="stock-watchlist",
                        message="No stock in the watchlist. Search and add a stock to start a new chart.",
                    )
                    self.live_feed.instrument_label = self.instrument_spec.label
                    self.live_feed.security_id = self.instrument_spec.security_id
                self.rulebook_service.learning_log.insert(
                    0,
                    f"Switched active instrument to {self.instrument_spec.label} ({self.instrument_spec.security_id or 'no security selected'}).",
                )
                self._persist_ui_preferences_locked()
                self._mark_state_dirty_locked()
                return self.get_state()
        self.heuristic_engine = self._default_heuristic_engine
        self.heuristic_engine.reset_session()
        self.reset_with_candles(generate_sample_candles())
        with self.lock:
            self.rulebook_service.learning_log.insert(
                0,
                f"Switched active instrument to {self.instrument_spec.label} ({self.instrument_spec.security_id}).",
            )
            self._persist_ui_preferences_locked()
            self._mark_state_dirty_locked()
        return self.get_state()

    def reset_with_candles(self, candles: list[Candle]) -> None:
        self._clear_live_packet_queue()
        with self.lock:
            if self.live_feed_adapter is not None and self._active_option_subscription is not None:
                self.live_feed_adapter.unsubscribe_symbols([self._active_option_subscription])
            self.candles = candles
            self.current_index = -1
            self.active_trade: SimulatedTrade | None = None
            self.trade_history: list[SimulatedTrade] = []
            self.decision: TradeDecision | None = None
            self.realized_pnl = 0.0
            self.balance = self.settings.simulation_starting_balance
            self.live_current_candle = None
            self._clear_companion_context_locked()
            self.signal_history = []
            self._signal_history_keys = set()
            self._active_option_subscription = None
            self.pending_setup = None
            self.heuristic_engine.reset_session()
            self.live_feed = self._build_live_feed_state(source="sample")
            self.data_sync = DataSyncState(
                status="ready",
                source="sample",
                message=f"Loaded built-in sample candles for {self.instrument_spec.label}.",
                last_synced_at=datetime.now(),
                total_loaded=len(candles),
            )
            if self.instrument_mode == InstrumentMode.stock and self.selected_stock_symbol:
                self._capture_stock_session_locked(self.selected_stock_symbol)
            self._mark_state_dirty_locked()

    def load_csv(self, content: bytes) -> None:
        candles = parse_candle_csv(content)
        if not candles:
            raise ValueError("No candles were found in the uploaded CSV")
        self.reset_with_candles(candles)
        with self.lock:
            self.data_sync = DataSyncState(
                status="ready",
                source="uploaded-csv",
                message="Loaded uploaded candle CSV into the simulator.",
                last_synced_at=datetime.now(),
                total_loaded=len(candles),
            )
            self._mark_state_dirty_locked()

    def update_rulebook_from_text(self, source_name: str, source_text: str) -> str:
        update = self.ai_service.propose_rulebook_update(
            current_rulebook=self.rulebook_service.get_rulebook(),
            source_text=source_text,
        )
        return self.rulebook_service.update_rulebook(update, source_name)

    def start_rulebook_job(self, source_name: str, source_text: str) -> RulebookJobState:
        if not source_text.strip():
            raise ValueError("The uploaded document did not contain readable text.")

        job_id = uuid.uuid4().hex[:10]
        with self.lock:
            self._rulebook_job_token = job_id
            self.rulebook_job = RulebookJobState(
                job_id=job_id,
                status="running",
                source_name=source_name,
                message=f"Queued rulebook learning for {source_name}.",
                started_at=datetime.now(),
                completed_at=None,
                used_fallback=False,
            )

        worker = threading.Thread(
            target=self._run_rulebook_job,
            args=(job_id, source_name, source_text),
            name=f"rulebook-job-{job_id}",
            daemon=True,
        )
        worker.start()
        return self.rulebook_job

    def _run_rulebook_job(self, job_id: str, source_name: str, source_text: str) -> None:
        try:
            message = self.update_rulebook_from_text(source_name, source_text)
            with self.lock:
                if self._rulebook_job_token != job_id:
                    return
                latest_summary = self.rulebook_service.learning_log[0] if self.rulebook_service.learning_log else message
                self.rulebook_job = RulebookJobState(
                    job_id=job_id,
                    status="success",
                    source_name=source_name,
                    message=latest_summary,
                    started_at=self.rulebook_job.started_at,
                    completed_at=datetime.now(),
                    used_fallback="fallback" in message.lower(),
                )
        except Exception as exc:
            with self.lock:
                if self._rulebook_job_token != job_id:
                    return
                self.rulebook_job = RulebookJobState(
                    job_id=job_id,
                    status="error",
                    source_name=source_name,
                    message=str(exc),
                    started_at=self.rulebook_job.started_at,
                    completed_at=datetime.now(),
                    used_fallback=False,
                )

    def step(self, steps: int = 1) -> DashboardState:
        for _ in range(steps):
            with self.lock:
                if self.current_index >= len(self.candles) - 1:
                    break
                self.current_index += 1
                evaluation_index = self.current_index
            self._evaluate_index(evaluation_index, source="manual")
        return self.get_state()

    def connect_live_feed(self, client_id: str | None = None, access_token: str | None = None) -> DashboardState:
        broker = self._selected_broker_provider()
        client, token = self._available_dhan_credentials(client_id, access_token)
        zerodha_api_key, _, zerodha_token = self._available_zerodha_credentials()
        if broker == "zerodha":
            if not zerodha_api_key or not zerodha_token:
                raise ValueError("Zerodha API key and access token are required to start the Kite websocket.")
        else:
            if not client or not token:
                raise ValueError("Dhan client ID and access token are required to start the live feed")
        if client and token:
            self._remember_dhan_credentials(client, token)
        stock_symbols_to_prepare: list[str] = []

        stale_adapter = None
        with self.lock:
            if self.live_feed_adapter is not None and self._live_feed_adapter_running_locked():
                self._schedule_watchlist_subscription_refresh()
                if self.instrument_mode == InstrumentMode.stock:
                    stock_symbols_to_prepare = list(self.stock_watchlist.keys())
                self.live_feed.status_message = self.live_feed.status_message or "Live feed connection already in progress."
                self._mark_state_dirty_locked()
                state = self.get_state()
            elif self.live_feed_adapter is not None:
                stale_adapter = self.live_feed_adapter
                self.live_feed_adapter = None
                state = None
            else:
                state = None
        if state is not None:
            self._auto_prepare_watchlist_symbols_async(stock_symbols_to_prepare)
            return state

        if stale_adapter is not None:
            stale_adapter.stop()

        if self.instrument_mode != InstrumentMode.stock:
            self.sync_dhan_context(client_id=client, access_token=token)
        else:
            with self.lock:
                stock_symbols_to_prepare = list(self.stock_watchlist.keys())
        with self.lock:
            feed_source = "zerodha-websocket" if broker == "zerodha" else "dhan-websocket"
            feed_message = "Connecting to Zerodha Kite websocket." if broker == "zerodha" else "Connecting to Dhan market feed."
            self.live_feed = self._build_live_feed_state(
                connected=False,
                status="connecting",
                source=feed_source,
                status_message=feed_message,
            )
            self._live_cumulative_volume_by_security_id.clear()
            if self.instrument_mode == InstrumentMode.stock:
                resolved_specs = [
                    spec
                    for spec in self.stock_watchlist.values()
                    if broker == "zerodha" or spec.security_id
                ]
                instruments = [
                    (
                        self._zerodha_quote_subscription_for_spec(spec)
                        if broker == "zerodha"
                        else resolve_quote_subscription(spec.security_id, spec.exchange_segment)
                    )
                    for spec in resolved_specs
                ]
                companion_spec = self._active_companion_instrument_spec()
                if companion_spec is not None and companion_spec.security_id:
                    instruments.append(
                        self._zerodha_quote_subscription_for_spec(companion_spec)
                        if broker == "zerodha"
                        else resolve_quote_subscription(companion_spec.security_id, companion_spec.exchange_segment)
                    )
                if not instruments:
                    raise ValueError("No stock in the watchlist has a resolved security id yet. Search once or wait a moment, then retry.")
            else:
                instruments = [
                    (
                        self._zerodha_quote_subscription_for_spec(self.instrument_spec)
                        if broker == "zerodha"
                        else resolve_quote_subscription(self.instrument_spec.security_id, self.instrument_spec.exchange_segment)
                    )
                ]
                if self._use_banknifty_companion():
                    instruments.append(
                        (
                            self._zerodha_quote_subscription_for_spec(self.companion_instrument_spec)
                            if broker == "zerodha"
                            else resolve_quote_subscription(
                                self.companion_instrument_spec.security_id,
                                self.companion_instrument_spec.exchange_segment,
                            )
                        )
                    )
            self.live_feed_adapter = (
                ZerodhaMarketFeedAdapter(
                    zerodha_api_key,
                    zerodha_token,
                    instruments,
                    order_update_callback=self.handle_order_update_packet,
                )
                if broker == "zerodha"
                else DhanMarketFeedAdapter(client, token, instruments)
            )
            self._stock_quote_subscriptions = {}
            if self.instrument_mode == InstrumentMode.stock:
                for spec, subscription in zip(resolved_specs, instruments):
                    feed_security_id = self._live_feed_security_id_for_spec(spec, broker)
                    if feed_security_id:
                        self._stock_quote_subscriptions[feed_security_id] = subscription
                        self._stock_symbol_by_security_id[feed_security_id] = spec.symbol
            self.live_feed_adapter.start(self.handle_live_packet, self.handle_live_status)
            self._sync_active_trade_subscription_locked()
            self.rulebook_service.learning_log.insert(
                0,
                (
                    f"Started {'Zerodha Kite' if broker == 'zerodha' else 'Dhan'} live feed for "
                    f"{self.instrument_spec.label} (security {self.instrument_spec.security_id}) with live-paper analysis enabled."
                ),
            )
            self._mark_state_dirty_locked()
            state = self.get_state()
        if stock_symbols_to_prepare:
            self._auto_prepare_watchlist_symbols_async(stock_symbols_to_prepare)
        return state

    def start_live_trading(self) -> DashboardState:
        if self.operating_mode != OperatingMode.heuristic:
            raise ValueError("Switch Trading Decision Mode to Heuristic before starting real order automation.")
        broker = self._selected_broker_provider()
        client_id, access_token = self._available_dhan_credentials()
        if broker == "zerodha":
            api_key, _, zerodha_token = self._available_zerodha_credentials()
            if not api_key or not zerodha_token:
                raise ValueError("Saved Zerodha API key and current access token are required before starting live trading.")
        elif not client_id or not access_token:
            raise ValueError("Saved or runtime Dhan credentials are required before starting live trading.")
        with self.lock:
            if self.live_feed_adapter is None:
                raise ValueError("Connect the selected live feed before starting live trading.")
            cleared_symbols: list[str] = []
            if self.instrument_mode == InstrumentMode.stock:
                selected_symbol = self.selected_stock_symbol
                if selected_symbol:
                    self._capture_stock_session_locked(selected_symbol)
                for symbol, session in self.stock_sessions.items():
                    if self._clear_simulated_active_trade_from_session(session):
                        cleared_symbols.append(symbol)
                if selected_symbol and selected_symbol in self.stock_sessions:
                    self._load_stock_session_locked(selected_symbol)
            self.live_trading_enabled = True
            self._global_mtm_square_off_triggered = False
            self.execution_state.live_trading_enabled = True
            self.execution_state.last_order_message = "Live heuristic execution is armed."
            if broker == "zerodha":
                self.execution_state.order_updates_connected = True
                self.execution_state.order_updates_status = "kite-feed"
                self.execution_state.order_updates_message = "Zerodha order updates are received through the Kite websocket."
            self._mark_state_dirty_locked()
        if broker == "dhan":
            self._start_order_updates(client_id, access_token)
        with self.lock:
            if cleared_symbols:
                cleared_list = ", ".join(sorted(cleared_symbols))
                self.rulebook_service.learning_log.insert(
                    0,
                    f"Cleared simulated open stock trades before arming live execution: {cleared_list}.",
                )
            self.rulebook_service.learning_log.insert(
                0,
                (
                    "Started live heuristic execution. New real orders will only be placed from realtime "
                    "Dhan or Zerodha websocket evaluations, not from sync or replay actions."
                ),
            )
            self._mark_state_dirty_locked()
            return self.get_state()

    def square_off_all_trades(self) -> DashboardState:
        return self._square_off_all_trades_with_reason("Manual square off button pressed.")

    def _square_off_all_trades_with_reason(self, reason: str) -> DashboardState:
        client_id, access_token = self._available_dhan_credentials()
        broker = self._selected_broker_provider()
        with self.lock:
            self.live_trading_enabled = False
            self.execution_state.live_trading_enabled = False
            self.execution_state.last_order_message = f"{reason} Live heuristic execution is disarmed."
            self._mark_state_dirty_locked()
        squared_off = 0
        if broker == "zerodha" or (client_id and access_token):
            if self.instrument_mode == InstrumentMode.stock:
                watched_symbols = list(self.stock_watchlist.keys())
                selected = self.selected_stock_symbol
                for symbol in watched_symbols:
                    def operation():
                        return self._square_off_active_trade_locked(client_id, access_token, reason=reason)
                    if self._run_in_stock_session(symbol, operation):
                        squared_off += 1
                if selected:
                    with self.lock:
                        self._load_stock_session_locked(selected)
            else:
                if self._square_off_active_trade_locked(client_id, access_token, reason=reason):
                    squared_off += 1
        self._stop_order_updates()
        with self.lock:
            self.rulebook_service.learning_log.insert(
                0,
                (
                    f"{reason} Closed {squared_off} tracked trade(s) and stopped live heuristic execution."
                ),
            )
            self._mark_state_dirty_locked()
            return self.get_state()

    def _start_order_updates(self, client_id: str, access_token: str) -> None:
        with self.lock:
            if self.order_update_adapter is not None:
                return
            self.order_update_adapter = DhanOrderUpdateAdapter(client_id, access_token)
            adapter = self.order_update_adapter
        adapter.start(self.handle_order_update_packet, self.handle_order_update_status)

    def _stop_order_updates(self) -> None:
        adapter = None
        with self.lock:
            if self.order_update_adapter is not None:
                adapter = self.order_update_adapter
                self.order_update_adapter = None
        if adapter is not None:
            adapter.stop()
        with self.lock:
            self.execution_state.order_updates_connected = False
            self.execution_state.order_updates_status = "disconnected"
            self.execution_state.order_updates_message = None
            self._mark_state_dirty_locked()

    def handle_order_update_status(self, status: str, message: str | None) -> None:
        with self.lock:
            self.execution_state.order_updates_status = status
            self.execution_state.order_updates_connected = status == "connected"
            self.execution_state.order_updates_message = message
            if status == "connected":
                self.execution_state.last_order_error = None
                self.execution_state.last_order_error_at = None
            if status == "error" and message:
                self.execution_state.last_order_error = message
                self.execution_state.last_order_error_at = datetime.now()
            self._mark_state_dirty_locked()

    def handle_order_update_packet(self, packet: dict) -> None:
        payload = packet.get("Data", {}) if isinstance(packet, dict) else {}
        with self.lock:
            self.execution_state.last_order_update_at = datetime.now()
            self.execution_state.last_order_message = self._format_order_update_message(payload)
            self._apply_order_update_to_all_trades_locked(payload)
            self._mark_state_dirty_locked()

    def _record_execution_feedback_locked(
        self,
        *,
        symbol: str | None,
        message: str,
        error: str | None = None,
    ) -> None:
        now = datetime.now()
        self.execution_state.last_order_message = message
        self.execution_state.last_order_symbol = symbol
        self.execution_state.last_order_update_at = now
        self.execution_state.last_order_error = error
        self.execution_state.last_order_error_at = now if error else None
        normalized_symbol = ((symbol or "").strip().upper().split()[0] if symbol else "")
        if normalized_symbol and normalized_symbol in self.stock_watchlist:
            self._stock_execution_feedback[normalized_symbol] = {
                "message": message,
                "error": error,
                "updated_at": now,
            }
        self._mark_state_dirty_locked()

    def _format_order_update_message(self, payload: dict) -> str:
        order_id = payload.get("OrderNo") or payload.get("orderNo") or payload.get("orderId") or "-"
        status = payload.get("Status") or payload.get("status") or payload.get("orderStatus") or "UNKNOWN"
        traded_price = payload.get("AvgTradedPrice") or payload.get("averageTradedPrice") or payload.get("TradedPrice") or payload.get("tradedPrice")
        if traded_price not in (None, ""):
            return f"Order {order_id} is {status} at average traded price {traded_price}."
        return f"Order {order_id} is {status}."

    def _max_stock_sync_workers(self, total: int) -> int:
        return max(1, min(total, self.settings.stock_sync_max_workers))

    def _fetch_stock_market_context_bundles(
        self,
        symbols: list[str],
        *,
        client_id: str,
        access_token: str,
        provider: str = "dhan",
    ) -> dict[str, object]:
        def fetch(symbol: str):
            spec = self._resolve_watchlist_symbol_if_needed(symbol)
            bundle = self._fetch_market_context_for_spec(
                provider,
                client_id,
                access_token,
                spec,
                prefer_last_closed_session_before_open=True,
            )
            return symbol, spec, bundle

        bundles: dict[str, object] = {}
        with ThreadPoolExecutor(max_workers=self._max_stock_sync_workers(len(symbols))) as executor:
            futures = [executor.submit(fetch, symbol) for symbol in symbols]
            for future in as_completed(futures):
                symbol, spec, bundle = future.result()
                bundles[symbol] = (spec, bundle)
        return bundles

    def _fetch_stock_historical_bundles(
        self,
        symbols: list[str],
        *,
        client_id: str,
        access_token: str,
        replay_session_day: date,
        previous_day: date,
        provider: str = "dhan",
    ) -> dict[str, object]:
        def fetch(symbol: str):
            spec = self._resolve_watchlist_symbol_if_needed(symbol)
            bundle = self._fetch_market_context_for_spec_days(
                provider,
                client_id,
                access_token,
                spec,
                session_day=replay_session_day,
                previous_context_day=previous_day,
            )
            return symbol, spec, bundle

        bundles: dict[str, object] = {}
        with ThreadPoolExecutor(max_workers=self._max_stock_sync_workers(len(symbols))) as executor:
            futures = [executor.submit(fetch, symbol) for symbol in symbols]
            for future in as_completed(futures):
                symbol, spec, bundle = future.result()
                bundles[symbol] = (spec, bundle)
        return bundles

    def _fetch_stock_historical_bundles_with_previous_fallback(
        self,
        symbols: list[str],
        *,
        client_id: str,
        access_token: str,
        replay_session_day: date,
        provider: str = "dhan",
    ) -> tuple[dict[str, object], list[str]]:
        def fetch(symbol: str):
            spec = self._resolve_watchlist_symbol_if_needed(symbol)
            bundle = self._fetch_historical_bundle_with_previous_fallback(
                client_id=client_id,
                access_token=access_token,
                replay_session_day=replay_session_day,
                spec=spec,
                provider=provider,
            )
            return symbol, spec, bundle

        bundles: dict[str, object] = {}
        skipped: list[str] = []
        with ThreadPoolExecutor(max_workers=self._max_stock_sync_workers(len(symbols))) as executor:
            future_symbols = {executor.submit(fetch, symbol): symbol for symbol in symbols}
            for future in as_completed(future_symbols):
                try:
                    symbol, spec, bundle = future.result()
                except DhanChartEmptyDataError as exc:
                    skipped.append(f"{replay_session_day.isoformat()} {future_symbols[future]} no candles ({exc})")
                    continue
                bundles[symbol] = (spec, bundle)
        return bundles, skipped

    def _apply_order_update_to_trade_locked(self, trade: SimulatedTrade | None, payload: dict) -> None:
        if trade is None:
            return
        order_id = str(payload.get("OrderNo") or payload.get("orderNo") or payload.get("orderId") or "").strip()
        if not order_id:
            return
        if order_id not in {trade.broker_order_id, trade.broker_exit_order_id}:
            matched_leg = next(
                (
                    leg
                    for leg in trade.pyramid_legs
                    if order_id in {leg.broker_order_id, leg.broker_exit_order_id}
                ),
                None,
            )
            if matched_leg is None:
                return
            matched_leg.broker_status = str(payload.get("Status") or payload.get("status") or payload.get("orderStatus") or "").strip() or matched_leg.broker_status
            matched_leg.broker_status_message = self._format_order_update_message(payload)
            trade.broker_status = matched_leg.broker_status
            trade.broker_status_message = matched_leg.broker_status_message
            avg_traded_price = payload.get("AvgTradedPrice") or payload.get("averageTradedPrice")
            if avg_traded_price not in (None, ""):
                try:
                    traded_price = round(float(avg_traded_price), 2)
                except (TypeError, ValueError):
                    return
                if order_id == matched_leg.broker_order_id and matched_leg.status == "OPEN":
                    old_price = matched_leg.entry_price
                    matched_leg.entry_price = traded_price
                    if (trade.open_quantity or 0) > 0:
                        trade.entry_price = round(
                            trade.entry_price + ((traded_price - old_price) * matched_leg.open_quantity / (trade.open_quantity or 1)),
                            2,
                        )
                        trade.entry_option_price = trade.entry_price
                    trade.current_price = traded_price
                    trade.current_option_price = traded_price
                    trade.current_quote_source = "dhan-order-update"
                elif order_id == matched_leg.broker_exit_order_id:
                    matched_leg.exit_price = traded_price
            return
        trade.broker_status = str(payload.get("Status") or payload.get("status") or payload.get("orderStatus") or "").strip() or trade.broker_status
        trade.broker_status_message = self._format_order_update_message(payload)
        avg_traded_price = payload.get("AvgTradedPrice") or payload.get("averageTradedPrice")
        if avg_traded_price not in (None, ""):
            try:
                traded_price = round(float(avg_traded_price), 2)
            except (TypeError, ValueError):
                return
            if order_id == trade.broker_order_id:
                trade.entry_price = traded_price
                trade.entry_option_price = traded_price
                trade.current_price = traded_price
                trade.current_option_price = traded_price
                trade.current_quote_source = "dhan-order-update"
            elif order_id == trade.broker_exit_order_id:
                trade.exit_price = traded_price
                trade.exit_option_price = traded_price

    def _apply_order_update_to_all_trades_locked(self, payload: dict) -> None:
        seen: set[int] = set()

        def apply(trade: SimulatedTrade | None) -> None:
            if trade is None:
                return
            trade_key = id(trade)
            if trade_key in seen:
                return
            seen.add(trade_key)
            self._apply_order_update_to_trade_locked(trade, payload)

        apply(self.active_trade)
        for trade in self.trade_history:
            apply(trade)
        for session in self.stock_sessions.values():
            apply(session.active_trade)
            for trade in session.trade_history:
                apply(trade)

    def start_sync_dhan_context_async(self, client_id: str | None = None, access_token: str | None = None) -> DashboardState:
        provider, client, token = self._history_provider_credentials(client_id, access_token)
        provider_label = self._history_broker_label(provider)
        source_label = self._history_source_label(provider)
        started_at = datetime.now()
        job_id = uuid.uuid4().hex
        with self.lock:
            self._ensure_no_running_operation_locked()
            self._operation_job_token = job_id
            self._set_operation_job_locked(
                job_id=job_id,
                job_type="sync-history",
                status="running",
                message=f"Syncing {provider_label} 1-minute context for {self.instrument_spec.label} in the background.",
                started_at=started_at,
            )
            self.data_sync = DataSyncState(
                status="syncing",
                source=source_label,
                message=f"Background {provider_label} sync started for {self.instrument_spec.label}.",
                last_synced_at=self.data_sync.last_synced_at,
                replay_session_day=self.data_sync.replay_session_day,
                previous_context_day=self.data_sync.previous_context_day,
                previous_day_candles=self.data_sync.previous_day_candles,
                intraday_candles=self.data_sync.intraday_candles,
                total_loaded=self.data_sync.total_loaded,
                has_live_open_candle=self.data_sync.has_live_open_candle,
            )
            self._mark_state_dirty_locked()
        worker = threading.Thread(
            target=self._run_operation_job,
            kwargs={
                "job_id": job_id,
                "job_type": "sync-history",
                "target": lambda: self.sync_dhan_context(client_id=client, access_token=token),
                "success_message": f"Background {provider_label} sync completed for {self.instrument_spec.label}.",
                "error_prefix": f"Background {provider_label} sync failed",
            },
            name="sync-history-job",
            daemon=True,
        )
        worker.start()
        return self.get_state()

    def start_simulate_today_session_async(
        self,
        client_id: str | None = None,
        access_token: str | None = None,
        replay_decision_duration_minutes: int = 1,
        stock_replay_scope: str | None = "all",
    ) -> DashboardState:
        provider, client, token = self._history_provider_credentials(client_id, access_token)
        provider_label = self._history_broker_label(provider)
        source_label = self._history_source_label(provider)
        started_at = datetime.now()
        job_id = uuid.uuid4().hex
        with self.lock:
            scope_label = self.instrument_spec.label
            if self.instrument_mode == InstrumentMode.stock:
                _, _, normalized_scope = self._stock_replay_symbols_for_scope_locked(stock_replay_scope)
                scope_label = self._stock_replay_scope_label(normalized_scope)
            self._ensure_no_running_operation_locked()
            self._operation_job_token = job_id
            self._set_operation_job_locked(
                job_id=job_id,
                job_type="simulate-today",
                status="running",
                message=f"Replaying today's session for {scope_label} in the background.",
                started_at=started_at,
            )
            self.data_sync = DataSyncState(
                status="syncing",
                source=source_label,
                message=f"Background {provider_label} today replay started for {scope_label}.",
                last_synced_at=self.data_sync.last_synced_at,
                replay_session_day=self.data_sync.replay_session_day,
                previous_context_day=self.data_sync.previous_context_day,
                previous_day_candles=self.data_sync.previous_day_candles,
                intraday_candles=self.data_sync.intraday_candles,
                total_loaded=self.data_sync.total_loaded,
                has_live_open_candle=self.data_sync.has_live_open_candle,
            )
            self._mark_state_dirty_locked()
        worker = threading.Thread(
            target=self._run_operation_job,
            kwargs={
                "job_id": job_id,
                "job_type": "simulate-today",
                "target": lambda: self.simulate_today_session(
                    client_id=client,
                    access_token=token,
                    replay_decision_duration_minutes=replay_decision_duration_minutes,
                    stock_replay_scope=stock_replay_scope,
                ),
                "success_message": f"Background {provider_label} today replay completed for {scope_label}.",
                "error_prefix": f"Background {provider_label} today replay failed",
            },
            name="simulate-today-job",
            daemon=True,
        )
        worker.start()
        return self.get_state()

    def start_simulate_historical_session_async(
        self,
        *,
        client_id: str | None = None,
        access_token: str | None = None,
        replay_date: str,
        previous_context_date: str,
        replay_decision_duration_minutes: int = 1,
        stock_replay_scope: str | None = "all",
    ) -> DashboardState:
        provider, client, token = self._history_provider_credentials(client_id, access_token)
        provider_label = self._history_broker_label(provider)
        source_label = self._history_source_label(provider)
        replay_session_day = self._parse_replay_date(replay_date, field_name="replay_date")
        previous_day = self._parse_replay_date(previous_context_date, field_name="previous_context_date")
        if previous_day >= replay_session_day:
            raise ValueError("Previous context day must be earlier than the replay session day.")
        started_at = datetime.now()
        job_id = uuid.uuid4().hex
        with self.lock:
            scope_label = self.instrument_spec.label
            if self.instrument_mode == InstrumentMode.stock:
                _, _, normalized_scope = self._stock_replay_symbols_for_scope_locked(stock_replay_scope)
                scope_label = self._stock_replay_scope_label(normalized_scope)
            self._ensure_no_running_operation_locked()
            self._operation_job_token = job_id
            self._set_operation_job_locked(
                job_id=job_id,
                job_type="simulate-historical",
                status="running",
                message=(
                    f"Replaying {scope_label} for {replay_session_day.isoformat()} "
                    f"with previous context {previous_day.isoformat()} in the background."
                ),
                started_at=started_at,
            )
            self.data_sync = DataSyncState(
                status="syncing",
                source=source_label,
                message=(
                    f"Background {provider_label} historical replay started for {scope_label}: "
                    f"{replay_session_day.isoformat()} with previous context {previous_day.isoformat()}."
                ),
                last_synced_at=self.data_sync.last_synced_at,
                replay_session_day=replay_session_day,
                previous_context_day=previous_day,
                previous_day_candles=self.data_sync.previous_day_candles,
                intraday_candles=self.data_sync.intraday_candles,
                total_loaded=self.data_sync.total_loaded,
                has_live_open_candle=self.data_sync.has_live_open_candle,
            )
            self._mark_state_dirty_locked()
        worker = threading.Thread(
            target=self._run_operation_job,
            kwargs={
                "job_id": job_id,
                "job_type": "simulate-historical",
                "target": lambda: self.simulate_historical_session(
                    client_id=client,
                    access_token=token,
                    replay_date=replay_date,
                    previous_context_date=previous_context_date,
                    replay_decision_duration_minutes=replay_decision_duration_minutes,
                    stock_replay_scope=stock_replay_scope,
                ),
                "success_message": (
                    f"Background {provider_label} historical replay completed for {scope_label}: "
                    f"{replay_session_day.isoformat()}."
                ),
                "error_prefix": f"Background {provider_label} historical replay failed",
            },
            name="simulate-historical-job",
            daemon=True,
        )
        worker.start()
        return self.get_state()

    def start_simulate_historical_range_async(
        self,
        *,
        client_id: str | None = None,
        access_token: str | None = None,
        replay_start_date: str,
        replay_end_date: str,
        replay_decision_duration_minutes: int = 1,
        stock_replay_scope: str | None = "all",
    ) -> DashboardState:
        provider, client, token = self._history_provider_credentials(client_id, access_token)
        provider_label = self._history_broker_label(provider)
        source_label = self._history_source_label(provider)
        start_day = self._parse_replay_date(replay_start_date, field_name="replay_start_date")
        end_day = self._parse_replay_date(replay_end_date, field_name="replay_end_date")
        if end_day < start_day:
            raise ValueError("Replay end date must be on or after replay start date.")
        if (end_day - start_day).days > 90:
            raise ValueError("Bulk historical replay range cannot exceed 90 calendar days.")
        started_at = datetime.now()
        job_id = uuid.uuid4().hex
        with self.lock:
            self._ensure_no_running_operation_locked()
            if self.instrument_mode == InstrumentMode.stock:
                _, _, normalized_scope = self._stock_replay_symbols_for_scope_locked(stock_replay_scope)
                replay_label = f"stock {self._stock_replay_scope_label(normalized_scope)}"
            elif self.instrument_mode == InstrumentMode.nifty and self.instrument_spec.symbol == "NIFTY":
                replay_label = "NIFTY"
            else:
                raise ValueError("Bulk historical replay is available for NIFTY mode or stock mode.")
            self._operation_job_token = job_id
            self._set_operation_job_locked(
                job_id=job_id,
                job_type="simulate-historical-range",
                status="running",
                message=(
                    f"Bulk {replay_label} replay started for {start_day.isoformat()} to {end_day.isoformat()} "
                    "with automatic previous-trading-day context."
                ),
                started_at=started_at,
            )
            self.data_sync = DataSyncState(
                status="syncing",
                source=source_label,
                message=(
                    f"Background {provider_label} bulk {replay_label} replay started for {start_day.isoformat()} to {end_day.isoformat()}."
                ),
                last_synced_at=self.data_sync.last_synced_at,
                replay_session_day=start_day,
                previous_context_day=None,
                previous_day_candles=self.data_sync.previous_day_candles,
                intraday_candles=self.data_sync.intraday_candles,
                total_loaded=self.data_sync.total_loaded,
                has_live_open_candle=False,
            )
            self._mark_state_dirty_locked()
        worker = threading.Thread(
            target=self._run_operation_job,
            kwargs={
                "job_id": job_id,
                "job_type": "simulate-historical-range",
                "target": lambda: self.simulate_historical_range_session(
                    client_id=client,
                    access_token=token,
                    replay_start_date=replay_start_date,
                    replay_end_date=replay_end_date,
                    replay_decision_duration_minutes=replay_decision_duration_minutes,
                    stock_replay_scope=stock_replay_scope,
                ),
                "success_message": (
                    f"Background {provider_label} bulk {replay_label} replay completed for {start_day.isoformat()} to {end_day.isoformat()}."
                ),
                "error_prefix": f"Background {provider_label} bulk {replay_label} replay failed",
            },
            name="simulate-historical-range-job",
            daemon=True,
        )
        worker.start()
        return self.get_state()

    def sync_dhan_context(self, client_id: str | None = None, access_token: str | None = None) -> DashboardState:
        provider, client, token = self._history_provider_credentials(client_id, access_token)
        provider_label = self._history_broker_label(provider)

        if self.instrument_mode == InstrumentMode.stock:
            watched_symbols = list(self.stock_watchlist.keys())
            selected = self.selected_stock_symbol or (watched_symbols[0] if watched_symbols else None)
            bundles = self._fetch_stock_market_context_bundles(
                watched_symbols,
                client_id=client,
                access_token=token,
                provider=provider,
            )
            companion_bundle = self._fetch_companion_market_context_bundle(
                client_id=client,
                access_token=token,
                provider=provider,
            )
            for symbol in watched_symbols:
                spec, bundle = bundles[symbol]
                self._apply_bundle_to_stock_session(symbol, bundle, replay_from_session_start=False)
                with self.lock:
                    self.stock_watch_meta.setdefault(symbol, {}).update(
                        {
                            "history_status": "ready",
                            "previous_day_candles": len(bundle.previous_day_candles),
                            "intraday_candles": len(bundle.intraday_candles),
                            "total_loaded": len(bundle.previous_day_candles) + len(bundle.intraday_candles),
                        }
                    )
                    self.rulebook_service.learning_log.insert(
                        0,
                        (
                            f"Synced {provider_label} chart history for {spec.label}: "
                            f"{len(bundle.previous_day_candles)} previous-day candles via {bundle.previous_day_source}, "
                            f"{len(bundle.intraday_candles)} intraday closed candles, "
                            f"open candle {'loaded' if bundle.live_open_candle else 'not present'}."
                        ),
                    )
                    self._mark_state_dirty_locked()
            if selected:
                with self.lock:
                    if companion_bundle is not None:
                        self._load_companion_bundle_locked(companion_bundle)
                    self.selected_stock_symbol = selected
                    self._load_stock_session_locked(selected)
                    self._mark_state_dirty_locked()
            return self.get_state()

        if self._use_banknifty_companion():
            bundle, companion_bundle = self._fetch_nifty_and_banknifty_bundles(
                client_id=client,
                access_token=token,
                provider=provider,
            )
            with self.lock:
                evaluation_index = self._load_dhan_bundle(bundle)
                self._load_companion_bundle_locked(companion_bundle)
                self.rulebook_service.learning_log.insert(
                        0,
                        (
                            f"Synced {provider_label} chart history for {self.instrument_spec.label} with Bank Nifty confirmation: "
                        f"{len(bundle.previous_day_candles)} Nifty previous-day candles, {len(bundle.intraday_candles)} Nifty intraday candles, "
                        f"{len(companion_bundle.previous_day_candles)} Bank Nifty previous-day candles, and "
                        f"{len(companion_bundle.intraday_candles)} Bank Nifty intraday candles."
                    ),
                )
                self._mark_state_dirty_locked()
            if evaluation_index is not None:
                self._evaluate_index(evaluation_index, source="sync")
            return self.get_state()

        bundle = self._fetch_market_context_for_spec(
            provider,
            client,
            token,
            self.instrument_spec,
            prefer_last_closed_session_before_open=True,
        )
        with self.lock:
            evaluation_index = self._load_dhan_bundle(bundle)
            self.rulebook_service.learning_log.insert(
                0,
                (
                    f"Synced {provider_label} chart history for {self.instrument_spec.label}: "
                    f"{len(bundle.previous_day_candles)} previous-day candles via {bundle.previous_day_source}, "
                    f"{len(bundle.intraday_candles)} intraday closed candles, "
                    f"open candle {'loaded' if bundle.live_open_candle else 'not present'}."
                ),
            )
            self._mark_state_dirty_locked()
            if self.instrument_mode == InstrumentMode.stock:
                self.stock_watch_meta.setdefault(self.instrument_spec.symbol, {}).update(
                    {
                        "history_status": "ready",
                        "previous_day_candles": len(bundle.previous_day_candles),
                        "intraday_candles": len(bundle.intraday_candles),
                        "total_loaded": len(bundle.previous_day_candles) + len(bundle.intraday_candles),
                    }
                )
                self._mark_state_dirty_locked()
        if evaluation_index is not None:
            self._evaluate_index(evaluation_index, source="sync")
        return self.get_state()

    def simulate_today_session(
        self,
        client_id: str | None = None,
        access_token: str | None = None,
        replay_decision_duration_minutes: int = 1,
        stock_replay_scope: str | None = "all",
    ) -> DashboardState:
        provider, client, token = self._history_provider_credentials(client_id, access_token)
        replay_decision_duration_minutes = self._normalize_replay_decision_duration_minutes(
            replay_decision_duration_minutes
        )

        if self.instrument_mode == InstrumentMode.stock:
            with self.lock:
                watched_symbols, selected, normalized_scope = self._stock_replay_symbols_for_scope_locked(stock_replay_scope)
            bundles = self._fetch_stock_market_context_bundles(
                watched_symbols,
                client_id=client,
                access_token=token,
                provider=provider,
            )
            companion_bundle = self._fetch_companion_market_context_bundle(
                client_id=client,
                access_token=token,
                provider=provider,
            )
            replayed_symbols: list[str] = []
            for symbol in watched_symbols:
                spec, bundle = bundles[symbol]
                intraday_count = len(bundle.intraday_candles)
                if intraday_count == 0:
                    continue
                replayed_symbols.append(symbol)
                self._apply_bundle_to_stock_session(
                    symbol,
                    bundle,
                    replay_from_session_start=True,
                    replay_decision_duration_minutes=replay_decision_duration_minutes,
                )
                with self.lock:
                    self.stock_watch_meta.setdefault(symbol, {}).update(
                        {
                            "history_status": "ready",
                            "previous_day_candles": len(bundle.previous_day_candles),
                            "intraday_candles": intraday_count,
                            "total_loaded": len(bundle.previous_day_candles) + intraday_count,
                        }
                    )
                    self.rulebook_service.learning_log.insert(
                        0,
                        (
                            f"Simulated today session for {spec.label}: "
                            f"{intraday_count} intraday candles replayed with quantity {self.settings.simulation_lot_size} "
                            f"under {self._stock_replay_scope_label(normalized_scope)} scope."
                        ),
                    )
            if not replayed_symbols:
                raise ValueError(f"No closed intraday candles were returned for {self._stock_replay_scope_label(normalized_scope)}.")
            if selected:
                with self.lock:
                    if companion_bundle is not None:
                        self._load_companion_bundle_locked(companion_bundle, replay_from_session_start=True)
                    self.selected_stock_symbol = selected
                    self._load_stock_session_locked(selected)
                    self._mark_state_dirty_locked()
            return self.get_state()

        if self._use_banknifty_companion():
            bundle, companion_bundle = self._fetch_nifty_and_banknifty_bundles(
                client_id=client,
                access_token=token,
                provider=provider,
            )
            self._begin_replay_simulation()
            try:
                with self.lock:
                    intraday_count = len(bundle.intraday_candles)
                    if intraday_count == 0:
                        raise ValueError("No closed intraday candles were returned for today yet.")
                    start_index, end_index = self._load_dhan_bundle(bundle, replay_from_session_start=True)
                    self._load_companion_bundle_locked(companion_bundle, replay_from_session_start=True)
                for evaluation_index in range(start_index, end_index + 1):
                    self._evaluate_index(
                        evaluation_index,
                        source="replay",
                        replay_decision_duration_minutes=replay_decision_duration_minutes,
                    )
                with self.lock:
                    last_candle = self.candles[end_index] if 0 <= end_index < len(self.candles) else None
                    if self.active_trade and last_candle and bundle.live_open_candle is None:
                        self.close_active_trade(last_candle, "Replay completed at the final available session candle.")
                    self.rulebook_service.learning_log.insert(
                        0,
                        (
                            f"Simulated today session for {self.instrument_spec.label} with Bank Nifty confirmation: "
                            f"{intraday_count} Nifty intraday candles replayed."
                        ),
                    )
                    self._mark_state_dirty_locked()
            finally:
                self._end_replay_simulation()
            return self.get_state()

        bundle = self._fetch_market_context_for_spec(
            provider,
            client,
            token,
            self.instrument_spec,
            prefer_last_closed_session_before_open=True,
        )
        self._begin_replay_simulation()
        try:
            with self.lock:
                intraday_count = len(bundle.intraday_candles)
                if intraday_count == 0:
                    raise ValueError("No closed intraday candles were returned for today yet.")
                start_index, end_index = self._load_dhan_bundle(bundle, replay_from_session_start=True)
            for evaluation_index in range(start_index, end_index + 1):
                self._evaluate_index(
                    evaluation_index,
                    source="replay",
                    replay_decision_duration_minutes=replay_decision_duration_minutes,
                )
            with self.lock:
                last_candle = self.candles[end_index] if 0 <= end_index < len(self.candles) else None
                if self.active_trade and last_candle and bundle.live_open_candle is None:
                    self.close_active_trade(last_candle, "Replay completed at the final available session candle.")
                self.rulebook_service.learning_log.insert(
                    0,
                    (
                        f"Simulated today session for {self.instrument_spec.label}: "
                        f"{intraday_count} intraday candles replayed with quantity {self.settings.simulation_lot_size}."
                    ),
                )
                self._mark_state_dirty_locked()
        finally:
            self._end_replay_simulation()
        return self.get_state()

    def simulate_historical_session(
        self,
        *,
        client_id: str | None = None,
        access_token: str | None = None,
        replay_date: str,
        previous_context_date: str,
        replay_decision_duration_minutes: int = 1,
        stock_replay_scope: str | None = "all",
    ) -> DashboardState:
        provider, client, token = self._history_provider_credentials(client_id, access_token)
        replay_session_day = self._parse_replay_date(replay_date, field_name="replay_date")
        previous_day = self._parse_replay_date(previous_context_date, field_name="previous_context_date")
        if previous_day >= replay_session_day:
            raise ValueError("Previous context day must be earlier than the replay session day.")
        replay_decision_duration_minutes = self._normalize_replay_decision_duration_minutes(
            replay_decision_duration_minutes
        )

        if self.instrument_mode == InstrumentMode.stock:
            with self.lock:
                watched_symbols, selected, normalized_scope = self._stock_replay_symbols_for_scope_locked(stock_replay_scope)
            bundles = self._fetch_stock_historical_bundles(
                watched_symbols,
                client_id=client,
                access_token=token,
                replay_session_day=replay_session_day,
                previous_day=previous_day,
                provider=provider,
            )
            companion_bundle = self._fetch_companion_historical_bundle(
                client_id=client,
                access_token=token,
                replay_session_day=replay_session_day,
                previous_day=previous_day,
                provider=provider,
            )
            replayed_symbols: list[str] = []
            for symbol in watched_symbols:
                spec, bundle = bundles[symbol]
                if not bundle.intraday_candles:
                    continue
                replayed_symbols.append(symbol)
                self._apply_bundle_to_stock_session(
                    symbol,
                    bundle,
                    replay_from_session_start=True,
                    replay_decision_duration_minutes=replay_decision_duration_minutes,
                )
                with self.lock:
                    self.stock_watch_meta.setdefault(symbol, {}).update(
                        {
                            "history_status": "ready",
                            "previous_day_candles": len(bundle.previous_day_candles),
                            "intraday_candles": len(bundle.intraday_candles),
                            "total_loaded": len(bundle.previous_day_candles) + len(bundle.intraday_candles),
                        }
                    )
                    self.rulebook_service.learning_log.insert(
                        0,
                        (
                            f"Simulated historical session for {spec.label}: replayed {replay_session_day.isoformat()} "
                            f"with previous-day context from {previous_day.isoformat()} "
                            f"under {self._stock_replay_scope_label(normalized_scope)} scope."
                        ),
                    )
            if not replayed_symbols:
                raise ValueError(
                    f"No candles were returned for {self._stock_replay_scope_label(normalized_scope)} "
                    f"on the selected historical replay day."
                )
            if selected:
                with self.lock:
                    if companion_bundle is not None:
                        self._load_companion_bundle_locked(companion_bundle, replay_from_session_start=True)
                    self.selected_stock_symbol = selected
                    self._load_stock_session_locked(selected)
                    self._mark_state_dirty_locked()
            return self.get_state()

        if self._use_banknifty_companion():
            bundle, companion_bundle = self._fetch_nifty_and_banknifty_historical_bundles(
                client_id=client,
                access_token=token,
                replay_session_day=replay_session_day,
                previous_day=previous_day,
                provider=provider,
            )
            self._begin_replay_simulation()
            try:
                with self.lock:
                    intraday_count = len(bundle.intraday_candles)
                    if intraday_count == 0:
                        raise ValueError("No candles were returned for the selected historical replay day.")
                    start_index, end_index = self._load_dhan_bundle(bundle, replay_from_session_start=True)
                    self._load_companion_bundle_locked(companion_bundle, replay_from_session_start=True)
                for evaluation_index in range(start_index, end_index + 1):
                    self._evaluate_index(
                        evaluation_index,
                        source="replay",
                        replay_decision_duration_minutes=replay_decision_duration_minutes,
                    )
                with self.lock:
                    last_candle = self.candles[end_index] if 0 <= end_index < len(self.candles) else None
                    if self.active_trade and last_candle and bundle.live_open_candle is None:
                        self.close_active_trade(last_candle, "Replay completed at the final available session candle.")
                    self.rulebook_service.learning_log.insert(
                        0,
                        (
                            f"Simulated historical session for {self.instrument_spec.label} with Bank Nifty confirmation: replayed "
                            f"{replay_session_day.isoformat()} with previous-day context from {previous_day.isoformat()}."
                        ),
                    )
                    self._mark_state_dirty_locked()
            finally:
                self._end_replay_simulation()
            return self.get_state()

        bundle = self._fetch_market_context_for_spec_days(
            provider,
            client,
            token,
            self.instrument_spec,
            session_day=replay_session_day,
            previous_context_day=previous_day,
        )
        self._begin_replay_simulation()
        try:
            with self.lock:
                intraday_count = len(bundle.intraday_candles)
                if intraday_count == 0:
                    raise ValueError("No candles were returned for the selected historical replay day.")
                start_index, end_index = self._load_dhan_bundle(bundle, replay_from_session_start=True)
            for evaluation_index in range(start_index, end_index + 1):
                self._evaluate_index(
                    evaluation_index,
                    source="replay",
                    replay_decision_duration_minutes=replay_decision_duration_minutes,
                )
            with self.lock:
                last_candle = self.candles[end_index] if 0 <= end_index < len(self.candles) else None
                if self.active_trade and last_candle and bundle.live_open_candle is None:
                    self.close_active_trade(last_candle, "Replay completed at the final available session candle.")
                self.rulebook_service.learning_log.insert(
                    0,
                    (
                        f"Simulated historical session for {self.instrument_spec.label}: replayed "
                        f"{replay_session_day.isoformat()} with previous-day context from {previous_day.isoformat()}."
                    ),
                )
                self._mark_state_dirty_locked()
        finally:
            self._end_replay_simulation()
        return self.get_state()

    def _simulate_stock_historical_range_session(
        self,
        *,
        client_id: str,
        access_token: str,
        provider: str,
        replay_days: list[date],
        replay_decision_duration_minutes: int,
        stock_replay_scope: str | None,
    ) -> DashboardState:
        with self.lock:
            watched_symbols, selected, normalized_scope = self._stock_replay_symbols_for_scope_locked(stock_replay_scope)
        scope_label = self._stock_replay_scope_label(normalized_scope)

        aggregate_trades: list[SimulatedTrade] = []
        replayed_days: list[date] = []
        replayed_stock_sessions = 0
        skipped_days: list[str] = []
        last_bundle: DhanSessionBundle | None = None
        last_symbol: str | None = None

        self._begin_replay_simulation()
        try:
            for replay_day in replay_days:
                try:
                    bundles, skipped_symbols = self._fetch_stock_historical_bundles_with_previous_fallback(
                        watched_symbols,
                        client_id=client_id,
                        access_token=access_token,
                        replay_session_day=replay_day,
                        provider=provider,
                    )
                except DhanChartEmptyDataError as exc:
                    skipped_days.append(f"{replay_day.isoformat()} no stock candles ({exc})")
                    continue

                if skipped_symbols:
                    skipped_days.extend(skipped_symbols[:4])

                companion_bundle = None
                try:
                    companion_bundle = self._fetch_historical_bundle_with_previous_fallback(
                        client_id=client_id,
                        access_token=access_token,
                        replay_session_day=replay_day,
                        spec=NIFTY_INSTRUMENT,
                        provider=provider,
                    )
                except DhanChartEmptyDataError as exc:
                    skipped_days.append(f"{replay_day.isoformat()} NIFTY companion no candles ({exc})")

                replayed_symbols_for_day: list[str] = []
                for symbol in watched_symbols:
                    fetched = bundles.get(symbol)
                    if fetched is None:
                        continue
                    spec, bundle = fetched
                    intraday_count = len(bundle.intraday_candles)
                    if intraday_count == 0:
                        skipped_days.append(f"{replay_day.isoformat()} {symbol} no intraday candles")
                        continue

                    with self.lock:
                        self.data_sync = DataSyncState(
                            status="syncing",
                            source=self._history_source_label(provider),
                            message=(
                                f"Bulk stock replay running: {replay_day.isoformat()} {symbol} "
                                f"with previous context {bundle.previous_context_day.isoformat() if bundle.previous_context_day else '-'}."
                            ),
                            last_synced_at=datetime.now(),
                            replay_session_day=replay_day,
                            previous_context_day=bundle.previous_context_day,
                            previous_day_candles=len(bundle.previous_day_candles),
                            intraday_candles=intraday_count,
                            total_loaded=len(bundle.previous_day_candles) + intraday_count,
                            has_live_open_candle=False,
                        )
                        self.stock_watch_meta.setdefault(symbol, {}).update(
                            {
                                "history_status": "syncing",
                                "previous_day_candles": len(bundle.previous_day_candles),
                                "intraday_candles": intraday_count,
                                "total_loaded": len(bundle.previous_day_candles) + intraday_count,
                            }
                        )
                        self._mark_state_dirty_locked()

                    self._apply_bundle_to_stock_session(
                        symbol,
                        bundle,
                        replay_from_session_start=True,
                        replay_decision_duration_minutes=replay_decision_duration_minutes,
                        companion_bundle=companion_bundle,
                    )

                    with self.lock:
                        session = self.stock_sessions.get(symbol)
                        if session is None:
                            continue
                        day_trades = [trade.model_copy(deep=True) for trade in session.trade_history]
                        aggregate_trades.extend(day_trades)
                        self.stock_watch_meta.setdefault(symbol, {}).update(
                            {
                                "history_status": "ready",
                                "previous_day_candles": len(bundle.previous_day_candles),
                                "intraday_candles": intraday_count,
                                "total_loaded": len(bundle.previous_day_candles) + intraday_count,
                            }
                        )
                        self.rulebook_service.learning_log.insert(
                            0,
                            (
                                f"Bulk stock replay completed {replay_day.isoformat()} for {spec.label}: "
                                f"{len(day_trades)} trade(s), previous context "
                                f"{bundle.previous_context_day.isoformat() if bundle.previous_context_day else '-'}."
                            ),
                        )
                        self._mark_state_dirty_locked()
                    replayed_symbols_for_day.append(symbol)
                    replayed_stock_sessions += 1
                    last_bundle = bundle
                    last_symbol = symbol

                if replayed_symbols_for_day:
                    replayed_days.append(replay_day)
                else:
                    skipped_days.append(f"{replay_day.isoformat()} no replayable stocks under {scope_label} scope")
        finally:
            self._end_replay_simulation()

        if not replayed_days:
            skipped_text = "; ".join(skipped_days[:4]) if skipped_days else "No replayable stock candles found."
            raise ValueError(f"No stock trading days were replayed in the selected range. {skipped_text}")

        aggregate_trades.sort(key=lambda trade: trade.entry_time)
        with self.lock:
            if selected:
                self.selected_stock_symbol = selected
                self._load_stock_session_locked(selected)
            self._bulk_replay_trade_history = [trade.model_copy(deep=True) for trade in aggregate_trades]
            self.active_trade = None
            aggregate_realized_pnl = round(sum(float(trade.booked_pnl or 0.0) for trade in aggregate_trades), 2)
            self.replay_pnl_summary = self._build_replay_pnl_summary(
                aggregate_trades,
                replayed_days=replayed_days,
                skipped_count=len(skipped_days),
                use_daily_cap=False,
            )
            skipped_suffix = f" Skipped {len(skipped_days)} holiday/no-data stock item(s)." if skipped_days else ""
            previous_context_day = last_bundle.previous_context_day if last_bundle is not None else None
            previous_count = len(last_bundle.previous_day_candles) if last_bundle is not None else 0
            intraday_count = len(last_bundle.intraday_candles) if last_bundle is not None else 0
            self.data_sync = DataSyncState(
                status="ready",
                source=self._history_source_label(provider),
                message=(
                    f"Bulk stock replay completed for {len(replayed_days)} trading day(s), "
                    f"{replayed_stock_sessions} stock-session(s), {len(aggregate_trades)} trade(s): "
                    f"{replayed_days[0].isoformat()} to {replayed_days[-1].isoformat()} under {scope_label} scope."
                    f"{skipped_suffix}"
                ),
                last_synced_at=datetime.now(),
                replay_session_day=replayed_days[-1],
                previous_context_day=previous_context_day,
                previous_day_candles=previous_count,
                intraday_candles=intraday_count,
                total_loaded=previous_count + intraday_count,
                has_live_open_candle=False,
            )
            if last_symbol:
                self.stock_watch_meta.setdefault(last_symbol, {}).update({"history_status": "ready"})
            self.rulebook_service.learning_log.insert(
                0,
                (
                    f"Bulk stock replay finished: {len(replayed_days)} trading day(s), "
                    f"{replayed_stock_sessions} stock-session(s), {len(aggregate_trades)} total trade(s), "
                    f"P&L {aggregate_realized_pnl:.2f}."
                    f"{skipped_suffix}"
                ),
            )
            self._mark_state_dirty_locked()
        return self.get_state()

    def simulate_historical_range_session(
        self,
        *,
        client_id: str | None = None,
        access_token: str | None = None,
        replay_start_date: str,
        replay_end_date: str,
        replay_decision_duration_minutes: int = 1,
        stock_replay_scope: str | None = "all",
    ) -> DashboardState:
        provider, client, token = self._history_provider_credentials(client_id, access_token)
        start_day = self._parse_replay_date(replay_start_date, field_name="replay_start_date")
        end_day = self._parse_replay_date(replay_end_date, field_name="replay_end_date")
        if end_day < start_day:
            raise ValueError("Replay end date must be on or after replay start date.")
        if (end_day - start_day).days > 90:
            raise ValueError("Bulk historical replay range cannot exceed 90 calendar days.")

        replay_decision_duration_minutes = self._normalize_replay_decision_duration_minutes(
            replay_decision_duration_minutes
        )
        replay_days = self._weekday_range(start_day, end_day)
        if not replay_days:
            raise ValueError("Selected replay range has no weekday trading sessions.")

        if self.instrument_mode == InstrumentMode.stock:
            return self._simulate_stock_historical_range_session(
                client_id=client,
                access_token=token,
                provider=provider,
                replay_days=replay_days,
                replay_decision_duration_minutes=replay_decision_duration_minutes,
                stock_replay_scope=stock_replay_scope,
            )

        if self.instrument_mode != InstrumentMode.nifty or self.instrument_spec.symbol != "NIFTY":
            raise ValueError("Bulk historical replay is available for NIFTY mode or stock mode.")

        aggregate_trades: list[SimulatedTrade] = []
        replayed_days: list[date] = []
        skipped_days: list[str] = []
        last_bundle: DhanSessionBundle | None = None
        last_end_index: int | None = None
        self._begin_replay_simulation()
        try:
            for replay_day in replay_days:
                try:
                    if self._use_banknifty_companion():
                        bundle, companion_bundle = self._fetch_nifty_and_banknifty_historical_bundles_with_previous_fallback(
                            client_id=client,
                            access_token=token,
                            replay_session_day=replay_day,
                            provider=provider,
                        )
                    else:
                        bundle = self._fetch_historical_bundle_with_previous_fallback(
                            client_id=client,
                            access_token=token,
                            replay_session_day=replay_day,
                            spec=self.instrument_spec,
                            provider=provider,
                        )
                        companion_bundle = None
                except DhanChartEmptyDataError as exc:
                    skipped_days.append(f"{replay_day.isoformat()} no candles ({exc})")
                    continue

                with self.lock:
                    intraday_count = len(bundle.intraday_candles)
                    if intraday_count == 0:
                        skipped_days.append(f"{replay_day.isoformat()} no intraday candles")
                        continue
                    start_index, end_index = self._load_dhan_bundle(bundle, replay_from_session_start=True)
                    if companion_bundle is not None:
                        self._load_companion_bundle_locked(companion_bundle, replay_from_session_start=True)
                    self.data_sync = DataSyncState(
                        status="syncing",
                        source=self._history_source_label(provider),
                        message=(
                            f"Bulk NIFTY replay running: {replay_day.isoformat()} "
                            f"with previous context {bundle.previous_context_day.isoformat() if bundle.previous_context_day else '-'}."
                        ),
                        last_synced_at=datetime.now(),
                        replay_session_day=replay_day,
                        previous_context_day=bundle.previous_context_day,
                        previous_day_candles=len(bundle.previous_day_candles),
                        intraday_candles=intraday_count,
                        total_loaded=len(bundle.previous_day_candles) + intraday_count,
                        has_live_open_candle=False,
                    )
                    self._mark_state_dirty_locked()

                for evaluation_index in range(start_index, end_index + 1):
                    self._evaluate_index(
                        evaluation_index,
                        source="replay",
                        replay_decision_duration_minutes=replay_decision_duration_minutes,
                    )

                with self.lock:
                    last_candle = self.candles[end_index] if 0 <= end_index < len(self.candles) else None
                    if self.active_trade and last_candle:
                        self.close_active_trade(last_candle, "Replay completed at the final available session candle.")
                    aggregate_trades.extend(trade.model_copy(deep=True) for trade in self.trade_history)
                    replayed_days.append(replay_day)
                    last_bundle = bundle
                    last_end_index = end_index
                    self.rulebook_service.learning_log.insert(
                        0,
                        (
                            f"Bulk NIFTY replay completed {replay_day.isoformat()} with previous context "
                            f"{bundle.previous_context_day.isoformat() if bundle.previous_context_day else '-'}: "
                            f"{len(self.trade_history)} trade(s)."
                        ),
                    )
                    self._mark_state_dirty_locked()
        finally:
            self._end_replay_simulation()

        if not replayed_days:
            skipped_text = "; ".join(skipped_days[:4]) if skipped_days else "No replayable candles found."
            raise ValueError(f"No NIFTY trading days were replayed in the selected range. {skipped_text}")

        with self.lock:
            self.trade_history = aggregate_trades
            self.active_trade = None
            self.realized_pnl = round(sum(float(trade.booked_pnl or 0.0) for trade in self.trade_history), 2)
            self.balance = round(self.settings.simulation_starting_balance + self.realized_pnl, 2)
            self.replay_pnl_summary = self._build_replay_pnl_summary(
                aggregate_trades,
                replayed_days=replayed_days,
                skipped_count=len(skipped_days),
            )
            skipped_suffix = f" Skipped {len(skipped_days)} holiday/no-data day(s)." if skipped_days else ""
            previous_context_day = last_bundle.previous_context_day if last_bundle is not None else None
            replay_session_day = replayed_days[-1]
            previous_count = len(last_bundle.previous_day_candles) if last_bundle is not None else 0
            intraday_count = len(last_bundle.intraday_candles) if last_bundle is not None else 0
            self.data_sync = DataSyncState(
                status="ready",
                source=self._history_source_label(provider),
                message=(
                    f"Bulk NIFTY replay completed for {len(replayed_days)} trading day(s): "
                    f"{replayed_days[0].isoformat()} to {replayed_days[-1].isoformat()}."
                    f"{skipped_suffix}"
                ),
                last_synced_at=datetime.now(),
                replay_session_day=replay_session_day,
                previous_context_day=previous_context_day,
                previous_day_candles=previous_count,
                intraday_candles=intraday_count,
                total_loaded=previous_count + intraday_count,
                has_live_open_candle=False,
            )
            self.rulebook_service.learning_log.insert(
                0,
                (
                    f"Bulk NIFTY replay finished: {len(replayed_days)} trading day(s), "
                    f"{len(self.trade_history)} total trade(s), uncapped P&L "
                    f"{self.replay_pnl_summary.uncapped_total_pnl:.2f}, capped P&L "
                    f"{self.replay_pnl_summary.capped_total_pnl:.2f}."
                    f"{skipped_suffix}"
                ),
            )
            if last_end_index is not None:
                self.current_index = last_end_index
            self._mark_state_dirty_locked()
        return self.get_state()

    def _weekday_range(self, start_day: date, end_day: date) -> list[date]:
        days: list[date] = []
        current = start_day
        while current <= end_day:
            if current.weekday() < 5:
                days.append(current)
            current += timedelta(days=1)
        return days

    def _build_replay_pnl_summary(
        self,
        trades: list[SimulatedTrade],
        *,
        replayed_days: list[date],
        skipped_count: int,
        use_daily_cap: bool | None = None,
    ) -> ReplayPnlSummaryState:
        cap_enabled = self._nifty_daily_max_loss_enabled() if use_daily_cap is None else bool(use_daily_cap)
        daily_cap = round(max(self._nifty_daily_max_loss(), 0.0), 2)
        trades_by_day: dict[date, list[SimulatedTrade]] = {day: [] for day in replayed_days}
        for trade in trades:
            trade_day = trade.entry_time.date()
            trades_by_day.setdefault(trade_day, []).append(trade)

        daily_rows: list[ReplayDailyPnlState] = []
        uncapped_total = 0.0
        capped_total = 0.0
        for replay_day in replayed_days:
            day_trades = sorted(trades_by_day.get(replay_day, []), key=lambda item: item.entry_time)
            day_uncapped = round(sum(float(trade.booked_pnl or 0.0) for trade in day_trades), 2)
            uncapped_total = round(uncapped_total + day_uncapped, 2)
            day_capped = 0.0
            cap_triggered = False
            stopped_after_trade = None
            for trade_index, trade in enumerate(day_trades, start=1):
                if cap_triggered:
                    break
                day_capped = round(day_capped + float(trade.booked_pnl or 0.0), 2)
                if cap_enabled and daily_cap > 0 and day_capped <= -daily_cap:
                    day_capped = round(-daily_cap, 2)
                    cap_triggered = True
                    stopped_after_trade = trade_index
            capped_total = round(capped_total + day_capped, 2)
            daily_rows.append(
                ReplayDailyPnlState(
                    replay_day=replay_day,
                    trade_count=len(day_trades),
                    uncapped_pnl=day_uncapped,
                    capped_pnl=day_capped,
                    cap_triggered=cap_triggered,
                    stopped_after_trade=stopped_after_trade,
                )
            )

        return ReplayPnlSummaryState(
            start_day=replayed_days[0] if replayed_days else None,
            end_day=replayed_days[-1] if replayed_days else None,
            replayed_days=len(replayed_days),
            skipped_days=skipped_count,
            total_trades=len(trades),
            uncapped_total_pnl=round(uncapped_total, 2),
            capped_total_pnl=round(capped_total, 2),
            daily_max_loss_enabled=cap_enabled,
            daily_max_loss=daily_cap,
            daily=daily_rows,
        )

    def _parse_replay_date(self, raw_value: str, *, field_name: str) -> date:
        value = (raw_value or "").strip()
        if not value:
            raise ValueError(f"{field_name} is required.")
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"{field_name} must be a valid YYYY-MM-DD date.") from exc

    def save_credentials(
        self,
        *,
        client_id: str | None = None,
        access_token: str | None = None,
        broker_provider: str | None = None,
        zerodha_api_key: str | None = None,
        zerodha_api_secret: str | None = None,
        zerodha_access_token: str | None = None,
        openai_api_key: str | None = None,
        openai_model: str | None = None,
        deepseek_api_key: str | None = None,
        deepseek_model: str | None = None,
        full_ai_provider: str | None = None,
        operating_mode: str | None = None,
        nifty_order_lots: int | None = None,
        stock_trade_capital: float | None = None,
        stock_execution_mode: str | None = None,
        stock_future_lots: int | None = None,
        stock_option_lots: int | None = None,
        nifty_expiry_preference: str | None = None,
        stock_partial_profit_enabled: bool | None = None,
        stock_trailing_stop_enabled: bool | None = None,
        stock_heuristic_early_exit_enabled: bool | None = None,
        nifty_trailing_stop_enabled: bool | None = None,
        nifty_heuristic_early_exit_enabled: bool | None = None,
        nifty_cost_sl_enabled: bool | None = None,
        nifty_cost_sl_points: float | None = None,
        nifty_min_sl_points: float | None = None,
        nifty_max_sl_points: float | None = None,
        nifty_target_enabled: bool | None = None,
        nifty_target_points: float | None = None,
        nifty_daily_max_loss_enabled: bool | None = None,
        nifty_daily_max_loss: float | None = None,
        pyramiding_enabled: bool | None = None,
        intelligent_pyramiding_enabled: bool | None = None,
        stock_percent_pyramiding_enabled: bool | None = None,
        stock_percent_pyramiding_step: float | None = None,
        nifty_point_pyramiding_enabled: bool | None = None,
        nifty_point_pyramiding_points: float | None = None,
        nifty_trade_bias: str | None = None,
        nifty_option_trade_mode: str | None = None,
        global_mtm_square_off_enabled: bool | None = None,
        global_mtm_square_off_threshold: float | None = None,
    ) -> DashboardState:
        with self.lock:
            self.credential_store.save(
                client_id=client_id,
                access_token=access_token,
                broker_provider=broker_provider,
                zerodha_api_key=zerodha_api_key,
                zerodha_api_secret=zerodha_api_secret,
                zerodha_access_token=zerodha_access_token,
                openai_api_key=openai_api_key,
                openai_model=openai_model,
                deepseek_api_key=deepseek_api_key,
                deepseek_model=deepseek_model,
                full_ai_provider=full_ai_provider,
                operating_mode=operating_mode,
                nifty_order_lots=nifty_order_lots,
                stock_trade_capital=stock_trade_capital,
                stock_execution_mode=stock_execution_mode,
                stock_future_lots=stock_future_lots,
                stock_option_lots=stock_option_lots,
                nifty_expiry_preference=nifty_expiry_preference,
                stock_partial_profit_enabled=stock_partial_profit_enabled,
                stock_trailing_stop_enabled=stock_trailing_stop_enabled,
                stock_heuristic_early_exit_enabled=stock_heuristic_early_exit_enabled,
                nifty_trailing_stop_enabled=nifty_trailing_stop_enabled,
                nifty_heuristic_early_exit_enabled=nifty_heuristic_early_exit_enabled,
                nifty_cost_sl_enabled=nifty_cost_sl_enabled,
                nifty_cost_sl_points=nifty_cost_sl_points,
                nifty_min_sl_points=nifty_min_sl_points,
                nifty_max_sl_points=nifty_max_sl_points,
                nifty_target_enabled=nifty_target_enabled,
                nifty_target_points=nifty_target_points,
                nifty_daily_max_loss_enabled=nifty_daily_max_loss_enabled,
                nifty_daily_max_loss=nifty_daily_max_loss,
                pyramiding_enabled=pyramiding_enabled,
                intelligent_pyramiding_enabled=intelligent_pyramiding_enabled,
                stock_percent_pyramiding_enabled=stock_percent_pyramiding_enabled,
                stock_percent_pyramiding_step=stock_percent_pyramiding_step,
                nifty_point_pyramiding_enabled=nifty_point_pyramiding_enabled,
                nifty_point_pyramiding_points=nifty_point_pyramiding_points,
                nifty_trade_bias=nifty_trade_bias,
                nifty_option_trade_mode=nifty_option_trade_mode,
                global_mtm_square_off_enabled=global_mtm_square_off_enabled,
                global_mtm_square_off_threshold=global_mtm_square_off_threshold,
            )
            self._credential_summary_cache = self.credential_store.summary(self.settings)
            self._configure_ai_service()
            self.operating_mode = self.credential_store.get_operating_mode(self.settings)
            self.rulebook_service.learning_log.insert(
                0,
                (
                    "Saved Dhan, AI provider, execution sizing, and operating-mode settings locally. "
                    f"Active trading mode: {self.operating_mode.value}. "
                    f"Full AI provider: {self.credential_store.get_full_ai_provider(self.settings).value}."
                ),
            )
            self._mark_state_dirty_locked()
            return self.get_state()

    def _configure_ai_service(self) -> None:
        provider = self.credential_store.get_full_ai_provider(self.settings)
        if provider == FullAIProvider.deepseek:
            api_key, model = self.credential_store.get_deepseek_settings(self.settings)
        else:
            api_key, model = self.credential_store.get_openai_settings(self.settings)
        self.ai_service.configure(provider=provider, api_key=api_key, model=model)

    def _load_dhan_bundle(self, bundle, replay_from_session_start: bool = False):
        if self.live_feed_adapter is not None and self._active_option_subscription is not None:
            self.live_feed_adapter.unsubscribe_symbols([self._active_option_subscription])
        self.candles = list(bundle.previous_day_candles) + list(bundle.intraday_candles)
        previous_day_count = len(bundle.previous_day_candles)
        self.current_index = previous_day_count - 1 if replay_from_session_start and previous_day_count else -1
        self.active_trade = None
        self.trade_history = []
        self._bulk_replay_trade_history = []
        self.decision = None
        self.realized_pnl = 0.0
        self.balance = self.settings.simulation_starting_balance
        self.replay_pnl_summary = ReplayPnlSummaryState()
        self.live_current_candle = None if replay_from_session_start else bundle.live_open_candle
        self.signal_history = []
        self._signal_history_keys = set()
        self._active_option_subscription = None
        self.pending_setup = None
        self.heuristic_engine.reset_session()
        evaluation_index = len(self.candles) - 1 if self.candles else None
        if not replay_from_session_start and evaluation_index is not None:
            self.current_index = evaluation_index
            self._rebuild_signal_history_up_to_index_locked(evaluation_index)

        replay_day_text = bundle.replay_session_day.strftime("%d %b %Y") if bundle.replay_session_day else "the session day"
        previous_context_text = (
            bundle.previous_context_day.strftime("%d %b %Y") if bundle.previous_context_day else "the previous session"
        )
        intraday_source_text = bundle.intraday_source or "intraday"
        provider_label = "Zerodha" if "zerodha" in f"{intraday_source_text} {bundle.previous_day_source}".lower() else "Dhan"
        source_label = "zerodha-rest" if provider_label == "Zerodha" else "dhan-rest"
        self.data_sync = DataSyncState(
            status="ready",
            source=source_label,
            message=(
                f"Loaded {provider_label} 1-minute context for {self.instrument_spec.label} using {intraday_source_text} data "
                f"for {replay_day_text} and {bundle.previous_day_source} data for {previous_context_text}."
            ),
            last_synced_at=datetime.now(),
            replay_session_day=bundle.replay_session_day,
            previous_context_day=bundle.previous_context_day,
            previous_day_candles=len(bundle.previous_day_candles),
            intraday_candles=len(bundle.intraday_candles),
            total_loaded=len(self.candles),
            has_live_open_candle=(bundle.live_open_candle is not None and not replay_from_session_start),
        )
        self._mark_state_dirty_locked()
        if replay_from_session_start:
            return previous_day_count, len(self.candles) - 1
        return evaluation_index

    def get_credential_summary(self) -> CredentialSummary:
        return self._credential_summary_cache

    def disconnect_live_feed(self) -> DashboardState:
        adapter = None
        with self.lock:
            if self.live_feed_adapter is not None:
                adapter = self.live_feed_adapter
                self.live_feed_adapter = None
        if adapter is not None:
            adapter.stop()
        self._stop_order_updates()
        self._clear_live_packet_queue()
        with self.lock:
            self.live_trading_enabled = False
            self.live_feed.connected = False
            self.live_feed.status = "disconnected"
            self.live_feed.status_message = None
            self.live_feed.error = None
            self.live_feed.retry_attempt = 0
            self.live_feed.next_retry_at = None
            self.live_feed.current_candle = self.live_current_candle
            self._active_option_subscription = None
            self._stock_quote_subscriptions = {}
            self._live_cumulative_volume_by_security_id.clear()
            self.execution_state.live_trading_enabled = False
            self._mark_state_dirty_locked()
            return self.get_state()

    def evaluate_current_candle(self) -> None:
        with self.lock:
            if self.current_index < 0:
                return
            evaluation_index = self.current_index
        self._evaluate_index(evaluation_index, source="manual")

    def build_context(self) -> StrategyContext:
        current_candle = self.candles[self.current_index]
        session_candles = get_session_candles_up_to_index(self.candles, self.current_index)
        previous_day_candles = get_previous_day_candles(self.candles, self.current_index)
        (
            companion_session_candles,
            companion_previous_day_candles,
            companion_recent_candles,
            companion_current_candle,
            companion_previous_day,
        ) = self._build_companion_snapshot_locked(evaluation_index=self.current_index)
        recent_candles = session_candles[-20:]
        previous_day = calculate_previous_day_levels(self.candles, self.current_index)
        liquidity_zones = self.find_liquidity_zones(session_candles, previous_day, previous_day_candles)
        liquidity_ledger = self.build_liquidity_ledger(session_candles, previous_day, previous_day_candles)
        operator_zones = self.find_operator_zones(session_candles)
        signal_events = self.detect_signal_events(current_candle, liquidity_zones, previous_day)
        recent_closed_trades = [
            trade.model_copy(deep=True)
            for trade in reversed(self.trade_history)
            if trade.status == "CLOSED"
            and (
                trade.trade_security_id == self.instrument_spec.security_id
                or trade.instrument_label == self.instrument_spec.label
                or trade.symbol.startswith(self.instrument_spec.symbol)
            )
        ][:6]
        return StrategyContext(
            instrument=self.instrument_state(),
            current_candle=current_candle,
            live_current_candle=self.live_current_candle,
            recent_candles=recent_candles,
            session_candles=session_candles,
            previous_day_candles=previous_day_candles,
            previous_day=previous_day,
            liquidity_zones=liquidity_zones,
            liquidity_ledger=liquidity_ledger,
            operator_zones=operator_zones,
            signal_events=signal_events,
            market_structure=self.describe_market_structure(
                session_candles=session_candles,
                previous_day_candles=previous_day_candles,
                previous_day=previous_day,
                live_current_candle=self.live_current_candle,
                liquidity_ledger=liquidity_ledger,
            ),
            companion_symbol=self.companion_instrument_spec.symbol if companion_session_candles else None,
            companion_current_candle=companion_current_candle,
            companion_recent_candles=companion_recent_candles,
            companion_session_candles=companion_session_candles,
            companion_previous_day_candles=companion_previous_day_candles,
            companion_previous_day=companion_previous_day,
            pending_setup=self.pending_setup,
            active_trade=self.active_trade,
            recent_closed_trades=recent_closed_trades,
            portfolio_order_count_estimate=self._portfolio_order_count_estimate_locked(),
            rulebook_markdown=self.rulebook_service.get_rulebook(),
            stock_partial_profit_enabled=self.credential_store.get_stock_partial_profit_enabled(self.settings),
            stock_trailing_stop_enabled=self.credential_store.get_stock_trailing_stop_enabled(self.settings),
            stock_heuristic_early_exit_enabled=self.credential_store.get_stock_heuristic_early_exit_enabled(self.settings),
            nifty_trailing_stop_enabled=self.credential_store.get_nifty_trailing_stop_enabled(self.settings),
            nifty_heuristic_early_exit_enabled=self.credential_store.get_nifty_heuristic_early_exit_enabled(self.settings),
            nifty_cost_sl_enabled=self._nifty_cost_sl_enabled(),
            nifty_cost_sl_points=self._nifty_cost_sl_points(),
            nifty_min_sl_points=self._nifty_min_sl_points(),
            nifty_max_sl_points=self._nifty_max_sl_points(),
            nifty_target_enabled=self._nifty_target_enabled(),
            nifty_target_points=self._nifty_target_points(),
            nifty_daily_max_loss_enabled=self._nifty_daily_max_loss_enabled(),
            nifty_daily_max_loss=self._nifty_daily_max_loss(),
            pyramiding_enabled=False if self._stock_derivative_execution_mode_enabled() else self.credential_store.get_pyramiding_enabled(self.settings),
            intelligent_pyramiding_enabled=False if self._stock_derivative_execution_mode_enabled() else self.credential_store.get_intelligent_pyramiding_enabled(self.settings),
            stock_percent_pyramiding_enabled=False if self._stock_derivative_execution_mode_enabled() else self._stock_percent_pyramiding_enabled(),
            stock_percent_pyramiding_step=self._stock_percent_pyramiding_step(),
            nifty_point_pyramiding_enabled=self._nifty_point_pyramiding_enabled(),
            nifty_point_pyramiding_points=self._nifty_point_pyramiding_points(),
            nifty_trade_bias=self._nifty_trade_bias(),
            nifty_option_trade_mode=self.credential_store.get_nifty_option_trade_mode(self.settings),
        )

    def get_state(self) -> DashboardState:
        with self._state_cache_lock:
            if self._cached_state is not None and self._cached_state_revision == self._state_revision:
                return self._cached_state
        with self.lock:
            state_revision = self._state_revision
            current_index = self.current_index
            candles = [candle.model_copy(deep=True) for candle in self.candles]
            live_current_candle = self.live_current_candle.model_copy(deep=True) if self.live_current_candle is not None else None
            active_trade = self.active_trade.model_copy(deep=True) if self.active_trade is not None else None
            decision = self.decision.model_copy(deep=True) if self.decision is not None else None
            pending_setup = self.pending_setup.model_copy(deep=True) if self.pending_setup is not None else None
            display_trade_history = self._bulk_replay_trade_history or self.trade_history
            trade_history = [trade.model_copy(deep=True) for trade in reversed(display_trade_history)]
            signal_history = [event.model_copy(deep=True) for event in reversed(self.signal_history)]
            heuristic_trace = self.heuristic_engine.trace_snapshot()
            heuristic_narrative = self.heuristic_engine.narrative_snapshot()
            live_feed = self.live_feed.model_copy(deep=True)
            execution = self.execution_state.model_copy(deep=True)
            data_sync = self.data_sync.model_copy(deep=True)
            operation_job = self.operation_job.model_copy(deep=True)
            replay_pnl_summary = self.replay_pnl_summary.model_copy(deep=True)
            rulebook_job = self.rulebook_job.model_copy(deep=True)
            learning_log = list(self.rulebook_service.learning_log[:10])
            rulebook = self.rulebook_service.get_rulebook()
            stock_watchlist = self._build_stock_watchlist_state_locked()
            operating_mode = self.operating_mode
            balance = self.balance
            realized_pnl = self.realized_pnl
            ai_enabled = self.ai_service.enabled
            instrument = self.instrument_state()
            reference_time = (
                self.live_current_candle.timestamp
                if self.live_current_candle is not None
                else self.candles[self.current_index].timestamp
                if self.candles and 0 <= self.current_index < len(self.candles)
                else None
            )
            integrated_pnl = self._build_integrated_pnl_state_locked(reference_time=reference_time)

        latest_closed = candles[current_index] if candles and current_index >= 0 else None
        latest_candle = live_current_candle or latest_closed
        recent_closed = candles[max(0, current_index - 39) : current_index + 1] if latest_closed else []
        recent_candles = list(recent_closed)
        session_candles = get_session_candles_up_to_index(candles, current_index) if latest_closed else []
        state_context_candles = list(session_candles)
        if live_current_candle is not None:
            if not recent_candles or recent_candles[-1].timestamp != live_current_candle.timestamp:
                recent_candles.append(live_current_candle)
            else:
                recent_candles[-1] = live_current_candle
            if not state_context_candles or state_context_candles[-1].timestamp != live_current_candle.timestamp:
                state_context_candles.append(live_current_candle)
            else:
                state_context_candles[-1] = live_current_candle
        previous_day = (
            calculate_previous_day_levels_for_timestamp(candles, latest_candle.timestamp)
            if latest_candle
            else PreviousDayLevels()
        )
        previous_day_candles = get_previous_day_candles(candles, current_index) if latest_closed else []
        liquidity_zones = self.find_liquidity_zones(state_context_candles, previous_day, previous_day_candles) if latest_candle else []
        liquidity_ledger = self.build_liquidity_ledger(state_context_candles, previous_day, previous_day_candles) if latest_candle else []
        operator_zones = self.find_operator_zones(state_context_candles) if latest_candle else []
        signal_events = self.detect_signal_events(latest_candle, liquidity_zones, previous_day) if latest_candle else []
        market_structure = (
            self.describe_market_structure(
                session_candles=state_context_candles,
                previous_day_candles=previous_day_candles,
                previous_day=previous_day,
                live_current_candle=live_current_candle,
                liquidity_ledger=liquidity_ledger,
            )
            if latest_candle
            else "No closed session candles are loaded yet."
        )
        nifty_market_mechanics = {}
        if latest_candle and self.instrument_mode == InstrumentMode.nifty and state_context_candles:
            mechanics_context = StrategyContext(
                instrument=instrument,
                current_candle=latest_candle,
                live_current_candle=live_current_candle,
                recent_candles=recent_candles[-20:],
                session_candles=state_context_candles,
                previous_day_candles=previous_day_candles,
                previous_day=previous_day,
                liquidity_zones=liquidity_zones,
                liquidity_ledger=liquidity_ledger,
                operator_zones=operator_zones,
                signal_events=signal_events,
                market_structure=market_structure,
                pending_setup=pending_setup,
                active_trade=active_trade,
                recent_closed_trades=[],
                rulebook_markdown="",
            )
            nifty_market_mechanics = self.heuristic_engine.nifty_market_mechanics_profile(mechanics_context).as_dict()
        unrealized_pnl = active_trade.pnl if active_trade else 0.0
        if decision is not None and active_trade is None and decision.action == TradeAction.hold:
            decision.action = TradeAction.no_trade
            decision.reason = "No active paper trade is open."
        if pending_setup is not None and pending_setup.status in {"consumed", "invalidated"}:
            pending_setup = None
        if active_trade and latest_candle and active_trade.price_mode == "cash":
            simulated_current = self.current_trade_market_price(latest_candle.close, active_trade)
            active_trade.current_price = simulated_current
            active_trade.current_option_price = simulated_current
            active_trade.current_quote_time = latest_candle.timestamp
            active_trade.pnl = self.calculate_trade_pnl(active_trade, simulated_current)
            unrealized_pnl = active_trade.pnl
        elif active_trade and latest_candle and active_trade.current_quote_source == "simulated":
            simulated_current = self.current_trade_market_price(latest_candle.close, active_trade)
            active_trade.current_price = simulated_current
            active_trade.current_option_price = simulated_current
            active_trade.current_quote_time = latest_candle.timestamp
            active_trade.pnl = self.calculate_trade_pnl(active_trade, simulated_current)
            unrealized_pnl = active_trade.pnl
        live_feed.current_candle = live_current_candle
        state = DashboardState(
            state_revision=state_revision,
            mode="live-paper" if live_feed.connected else "paper",
            instrument=instrument,
            operating_mode=operating_mode,
            current_index=current_index,
            total_candles=len(candles),
            latest_candle=latest_candle,
            recent_candles=recent_candles,
            previous_day=previous_day,
            liquidity_zones=liquidity_zones,
            liquidity_ledger=liquidity_ledger,
            operator_zones=operator_zones,
            signal_events=signal_events,
            signal_history=signal_history,
            heuristic_trace=heuristic_trace,
            heuristic_narrative=heuristic_narrative,
            market_structure=market_structure,
            nifty_market_mechanics=nifty_market_mechanics,
            pending_setup=pending_setup,
            decision=decision,
            active_trade=active_trade,
            trade_history=trade_history,
            rulebook=rulebook,
            learning_log=learning_log,
            balance=balance,
            realized_pnl=realized_pnl,
            unrealized_pnl=unrealized_pnl,
            integrated_pnl=integrated_pnl,
            replay_pnl_summary=replay_pnl_summary,
            ai_enabled=ai_enabled,
            live_feed=live_feed,
            execution=execution,
            data_sync=data_sync,
            operation_job=operation_job,
            rulebook_job=rulebook_job,
            credentials=self.get_credential_summary(),
            stock_watchlist=stock_watchlist,
        )
        with self.lock:
            if self._state_revision == state_revision:
                with self._state_cache_lock:
                    self._cached_state = state
                    self._cached_state_revision = state_revision
        return state

    def handle_live_status(
        self,
        status: str,
        message: str | None,
        retry_attempt: int = 0,
        next_retry_at: datetime | None = None,
    ) -> None:
        with self.lock:
            self.live_feed.status = status
            self.live_feed.connected = status == "connected"
            self.live_feed.source = self.live_feed.source or "dhan-websocket"
            self.live_feed.instrument_label = self.instrument_spec.label
            self.live_feed.security_id = self.instrument_spec.security_id
            self.live_feed.status_message = message
            self.live_feed.error = message if status in {"error", "reconnecting"} else None
            self.live_feed.retry_attempt = retry_attempt
            self.live_feed.next_retry_at = next_retry_at
            if status == "connected":
                self.live_feed.error = None
                self._sync_active_trade_subscription_locked()
            self._mark_state_dirty_locked()

    def handle_live_packet(self, packet: dict) -> None:
        packet_key = str(packet.get("security_id") or packet.get("type") or "")
        if not packet_key:
            return
        with self.lock:
            if self._replay_simulation_active_locked():
                return
            self._pending_live_packets[packet_key] = packet
            if packet_key in self._queued_live_packet_keys:
                return
            self._queued_live_packet_keys.add(packet_key)
        self._live_packet_queue.put(packet_key)

    def _run_live_packet_worker(self) -> None:
        while True:
            packet_key = self._live_packet_queue.get()
            if packet_key is None:
                return
            with self.lock:
                packet = self._pending_live_packets.pop(packet_key, None)
                self._queued_live_packet_keys.discard(packet_key)
            if packet is None:
                continue
            try:
                self._handle_live_packet_now(packet)
            except Exception as exc:
                self.handle_live_status("error", str(exc))

    def _run_live_evaluation_worker(self) -> None:
        while True:
            task = self._live_evaluation_queue.get()
            if task is None:
                return
            symbol, evaluation_index = task
            try:
                if symbol is None:
                    self._evaluate_index(evaluation_index, source="live")
                    continue

                def operation():
                    self._evaluate_index(evaluation_index, source="live")
                    return None

                self._run_in_stock_session(symbol, operation)
            except Exception as exc:
                self.handle_live_status("error", str(exc))

    def _queue_live_evaluation(self, symbol: str | None, evaluation_index: int) -> None:
        with self.lock:
            if self._replay_simulation_active_locked():
                return
        self._live_evaluation_queue.put((symbol, evaluation_index))

    def _handle_live_packet_now(self, packet: dict) -> None:
        evaluation_index = None
        evaluation_symbol: str | None = None
        live_trade_control_check: tuple[float, datetime] | None = None
        option_loss_cap_exit: tuple[Candle, str] | None = None
        with self.lock:
            if self._replay_simulation_active_locked():
                return
            security_id = str(packet.get("security_id", ""))
            ltp = self._as_float(packet.get("LTP"))
            if ltp is None:
                return
            tick_time = self._packet_timestamp(packet)
            raw_volume = self._as_float(packet.get("volume")) or 0.0
            packet_type = str(packet.get("type", "Unknown"))
            feed_source = str(packet.get("source") or self.live_feed.source or "dhan-websocket")
            if self.active_trade and self.active_trade.option_security_id and security_id == self.active_trade.option_security_id:
                cap_note = self._update_active_trade_quote_locked(
                    OptionQuote(
                        security_id=security_id,
                        option_type=self.active_trade.option_type,
                        strike=self.active_trade.strike,
                        last_price=ltp,
                        quote_time=tick_time,
                        source=feed_source,
                        volume=self._as_int(raw_volume),
                        oi=self._as_int(packet.get("OI")),
                    )
                )
                if cap_note:
                    spot = self.live_feed.last_ltp or self.active_trade.entry_spot_price
                    option_loss_cap_exit = (
                        Candle(timestamp=tick_time, open=spot, high=spot, low=spot, close=spot, volume=0.0),
                        cap_note,
                    )
                self._mark_state_dirty_locked()
                if option_loss_cap_exit is not None:
                    pass
                else:
                    return
        if option_loss_cap_exit is not None:
            exit_candle, note = option_loss_cap_exit
            if self._should_send_live_orders("live"):
                self._exit_live_trade(exit_candle, note)
            else:
                with self.lock:
                    if self.active_trade is not None:
                        self.close_active_trade(exit_candle, note)
            return
        self._check_global_mtm_square_off()
        with self.lock:
            if self._replay_simulation_active_locked():
                return
            security_id = str(packet.get("security_id", ""))
            ltp = self._as_float(packet.get("LTP"))
            if ltp is None:
                return
            tick_time = self._packet_timestamp(packet)
            raw_volume = self._as_float(packet.get("volume")) or 0.0
            volume_delta = self._live_volume_delta_locked(security_id, tick_time, raw_volume)
            packet_type = str(packet.get("type", "Unknown"))
            feed_source = str(packet.get("source") or self.live_feed.source or "dhan-websocket")
            if self.instrument_mode == InstrumentMode.stock:
                matched_symbol = self._stock_symbol_by_security_id.get(security_id)
                companion_spec = self._active_companion_instrument_spec()
                if companion_spec is not None and security_id == companion_spec.security_id:
                    self._update_companion_live_candle_locked(tick_time, ltp, volume_delta)
                    self._mark_state_dirty_locked()
                    return
                if matched_symbol is not None:
                    meta = self.stock_watch_meta.setdefault(matched_symbol, {})
                    meta["last_ltp"] = ltp
                    meta["last_tick_at"] = tick_time
                    meta["ticks_received"] = meta.get("ticks_received", 0) + 1
                    self._mark_state_dirty_locked()
                if matched_symbol is not None and matched_symbol != self.selected_stock_symbol:
                    self._update_nonselected_stock_tick(matched_symbol, tick_time, ltp, volume_delta)
                    return
                evaluation_symbol = matched_symbol
            elif self._use_banknifty_companion() and security_id == self.companion_instrument_spec.security_id:
                self._update_companion_live_candle_locked(tick_time, ltp, volume_delta)
                self._mark_state_dirty_locked()
                return
            if (
                self.active_trade is not None
                and security_id == self.instrument_spec.security_id
            ):
                live_trade_control_check = (ltp, tick_time)
            self.live_feed.connected = True
            self.live_feed.status = "connected"
            self.live_feed.source = feed_source
            self.live_feed.instrument_label = self.instrument_spec.label
            self.live_feed.security_id = str(packet.get("security_id", self.instrument_spec.security_id))
            self.live_feed.last_packet_type = packet_type
            self.live_feed.last_tick_at = tick_time
            self.live_feed.last_ltp = ltp
            self.live_feed.ticks_received += 1
            self.live_feed.error = None
            evaluation_index = self._update_live_candle_locked(tick_time, ltp, volume_delta)
            self._mark_state_dirty_locked()
        if live_trade_control_check is not None:
            control_ltp, control_time = live_trade_control_check
            if self._apply_live_ltp_trade_controls(control_ltp, control_time):
                return
            self._check_global_mtm_square_off(control_time)
        if evaluation_index is not None:
            self._queue_live_evaluation(evaluation_symbol, evaluation_index)

    def _live_volume_delta_locked(self, security_id: str, tick_time: datetime, cumulative_volume: float) -> float:
        if not security_id or cumulative_volume <= 0:
            return 0.0
        session_day = tick_time.date()
        previous = self._live_cumulative_volume_by_security_id.get(security_id)
        self._live_cumulative_volume_by_security_id[security_id] = (session_day, cumulative_volume)
        if previous is None:
            return 0.0
        previous_day, previous_volume = previous
        if previous_day != session_day or cumulative_volume < previous_volume:
            return 0.0
        return max(cumulative_volume - previous_volume, 0.0)

    def _update_companion_live_candle_locked(self, tick_time: datetime, ltp: float, volume: float) -> None:
        bucket = tick_time.replace(second=0, microsecond=0)
        if self.companion_live_current_candle is None:
            self.companion_live_current_candle = Candle(
                timestamp=bucket,
                open=ltp,
                high=ltp,
                low=ltp,
                close=ltp,
                volume=volume,
            )
            return
        if bucket == self.companion_live_current_candle.timestamp:
            self.companion_live_current_candle.high = max(self.companion_live_current_candle.high, ltp)
            self.companion_live_current_candle.low = min(self.companion_live_current_candle.low, ltp)
            self.companion_live_current_candle.close = ltp
            self.companion_live_current_candle.volume += max(volume, 0.0)
            return
        completed_candle = self.companion_live_current_candle
        if self.companion_candles and self.companion_candles[-1].timestamp == completed_candle.timestamp:
            self.companion_candles[-1] = completed_candle
        else:
            self.companion_candles.append(completed_candle)
        self.companion_live_current_candle = Candle(
            timestamp=bucket,
            open=ltp,
            high=ltp,
            low=ltp,
            close=ltp,
            volume=volume,
        )

    def _update_nonselected_stock_tick(self, symbol: str, tick_time: datetime, ltp: float, volume: float) -> None:
        with self.lock:
            session = self.stock_sessions.get(symbol)
            if session is None:
                return
            evaluation_index = self._update_live_candle_for_session_locked(session, tick_time, ltp, volume)
            if session.active_trade is not None and session.active_trade.price_mode == "cash":
                session.active_trade.current_price = round(ltp, 2)
                session.active_trade.current_option_price = session.active_trade.current_price
                session.active_trade.current_quote_source = self.live_feed.source or "dhan-websocket"
                session.active_trade.current_quote_time = tick_time
                session.active_trade.pnl = self.calculate_trade_pnl(session.active_trade, session.active_trade.current_price)
            self._mark_state_dirty_locked()
            hard_stop_crossed = self._live_ltp_crossed_invalidation(session.active_trade, ltp)
        if hard_stop_crossed and self._exit_stock_session_on_ltp_invalidation(symbol, ltp, tick_time):
            return
        self._check_global_mtm_square_off(tick_time)
        if evaluation_index is None:
            return
        self._queue_live_evaluation(symbol, evaluation_index)

    def _update_live_candle_for_session_locked(
        self,
        session: StockRuntimeSession,
        tick_time: datetime,
        ltp: float,
        volume: float,
    ) -> int | None:
        bucket = tick_time.replace(second=0, microsecond=0)
        if session.live_current_candle is None:
            session.live_current_candle = Candle(
                timestamp=bucket,
                open=ltp,
                high=ltp,
                low=ltp,
                close=ltp,
                volume=volume,
            )
            return None

        if bucket == session.live_current_candle.timestamp:
            session.live_current_candle.high = max(session.live_current_candle.high, ltp)
            session.live_current_candle.low = min(session.live_current_candle.low, ltp)
            session.live_current_candle.close = ltp
            session.live_current_candle.volume += max(volume, 0.0)
            return None

        completed_candle = session.live_current_candle
        if session.candles and session.candles[-1].timestamp == completed_candle.timestamp:
            session.candles[-1] = completed_candle
        else:
            session.candles.append(completed_candle)
        session.current_index = len(session.candles) - 1
        evaluation_index = session.current_index
        session.live_current_candle = Candle(
            timestamp=bucket,
            open=ltp,
            high=ltp,
            low=ltp,
            close=ltp,
            volume=volume,
        )
        return evaluation_index

    def _update_live_candle_locked(self, tick_time: datetime, ltp: float, volume: float) -> int | None:
        bucket = tick_time.replace(second=0, microsecond=0)
        if self.live_current_candle is None:
            self.live_current_candle = Candle(
                timestamp=bucket,
                open=ltp,
                high=ltp,
                low=ltp,
                close=ltp,
                volume=volume,
            )
            return None

        if bucket == self.live_current_candle.timestamp:
            self.live_current_candle.high = max(self.live_current_candle.high, ltp)
            self.live_current_candle.low = min(self.live_current_candle.low, ltp)
            self.live_current_candle.close = ltp
            self.live_current_candle.volume += max(volume, 0.0)
            return None

        completed_candle = self.live_current_candle
        if self.candles and self.candles[-1].timestamp == completed_candle.timestamp:
            self.candles[-1] = completed_candle
        else:
            self.candles.append(completed_candle)
        self.current_index = len(self.candles) - 1
        evaluation_index = self.current_index
        self.live_current_candle = Candle(
            timestamp=bucket,
            open=ltp,
            high=ltp,
            low=ltp,
            close=ltp,
            volume=volume,
        )
        return evaluation_index

    def _evaluate_index(
        self,
        evaluation_index: int,
        *,
        source: str = "manual",
        replay_decision_duration_minutes: int = 1,
    ) -> None:
        replay_decision_duration_minutes = self._normalize_replay_decision_duration_minutes(
            replay_decision_duration_minutes
        )
        with self._evaluation_lock:
            with self.lock:
                if evaluation_index < 0 or evaluation_index >= len(self.candles):
                    return
                self.current_index = evaluation_index
                self._clear_pending_setup_if_new_session_locked(self.candles[evaluation_index])
                if source == "replay" and not self._is_replay_decision_boundary(
                    evaluation_index,
                    replay_decision_duration_minutes,
                ):
                    if self.active_trade and self.active_trade.current_quote_source == "simulated":
                        self.active_trade.current_price = self.current_trade_market_price(
                            self.candles[evaluation_index].close,
                            self.active_trade,
                        )
                        self.active_trade.current_option_price = self.active_trade.current_price
                        self.active_trade.current_quote_time = self.candles[evaluation_index].timestamp
                        self.active_trade.pnl = self.calculate_trade_pnl(
                            self.active_trade,
                            self.active_trade.current_price,
                        )
                    self._mark_state_dirty_locked()
                    return
                snapshot = self._build_evaluation_context_locked(
                    evaluation_index,
                    source=source,
                    replay_decision_duration_minutes=replay_decision_duration_minutes,
                )
                trigger_decision = None
                if not self._stock_pre_arm_paper_execution_disabled(source):
                    trigger_decision = self.evaluate_pending_setup_trigger(snapshot.current_candle)
                if trigger_decision is not None:
                    trigger_decision = self._apply_stock_trade_bias_filter_locked(trigger_decision)
                    trigger_decision = self._apply_nifty_trade_bias_filter_locked(trigger_decision)
                    trigger_decision = self._apply_stock_turnover_filter_locked(snapshot.current_candle, trigger_decision)
                    trigger_decision = self._apply_nifty_daily_loss_cap_filter_locked(
                        snapshot.current_candle,
                        trigger_decision,
                        source,
                    )
                    self.decision = trigger_decision
                    self._record_signal_events_locked(snapshot.signal_events)
                    self.apply_pending_setup_decision(snapshot.current_candle, trigger_decision)
                    if trigger_decision.action in {TradeAction.enter_call, TradeAction.enter_put}:
                        self.apply_trade_logic(snapshot.current_candle, trigger_decision, source=source)
                    self._mark_state_dirty_locked()
                    return
            heuristic_decision = self.heuristic_decision(snapshot)
            if source == "replay" and self.instrument_mode == InstrumentMode.nifty and self.instrument_spec.symbol == "NIFTY":
                decision = heuristic_decision
            else:
                decision = self.ai_service.decide(snapshot, heuristic_decision, self.operating_mode)
            decision = self.normalize_trade_decision(decision, snapshot.active_trade)
            with self.lock:
                if evaluation_index >= len(self.candles):
                    return
                self.current_index = evaluation_index
                decision = self._apply_stock_trade_bias_filter_locked(decision)
                decision = self._apply_nifty_trade_bias_filter_locked(decision)
                decision = self._apply_stock_turnover_filter_locked(snapshot.current_candle, decision)
                decision = self._apply_nifty_daily_loss_cap_filter_locked(snapshot.current_candle, decision, source)
                self.decision = decision
                self.apply_pending_setup_decision(snapshot.current_candle, decision)
                self._record_signal_events_locked(snapshot.signal_events)
                self.apply_trade_logic(snapshot.current_candle, decision, source=source)
                self._mark_state_dirty_locked()
            if source == "live":
                self._check_global_mtm_square_off(snapshot.current_candle.timestamp)

    def _clear_live_packet_queue(self) -> None:
        with self.lock:
            self._pending_live_packets.clear()
            self._queued_live_packet_keys.clear()
        try:
            while True:
                self._live_packet_queue.get_nowait()
        except queue.Empty:
            return

    def _clear_live_evaluation_queue(self) -> None:
        try:
            while True:
                self._live_evaluation_queue.get_nowait()
        except queue.Empty:
            return

    def _begin_replay_simulation(self) -> None:
        with self.lock:
            self._replay_simulation_depth += 1
            self._pending_live_packets.clear()
            self._queued_live_packet_keys.clear()
        self._clear_live_packet_queue()
        self._clear_live_evaluation_queue()

    def _end_replay_simulation(self) -> None:
        self._clear_live_packet_queue()
        self._clear_live_evaluation_queue()
        with self.lock:
            self._replay_simulation_depth = max(self._replay_simulation_depth - 1, 0)

    def _replay_simulation_active_locked(self) -> bool:
        return self._replay_simulation_depth > 0

    def _packet_timestamp(self, packet: dict) -> datetime:
        timestamp = packet.get("timestamp") or packet.get("exchange_timestamp") or packet.get("last_trade_time")
        if isinstance(timestamp, datetime):
            return timestamp.replace(tzinfo=None)
        if isinstance(timestamp, str):
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%H:%M:%S"):
                try:
                    parsed = datetime.strptime(timestamp[:19], fmt)
                    if fmt == "%H:%M:%S":
                        return datetime.combine(datetime.now().date(), parsed.time())
                    return parsed
                except ValueError:
                    continue
        ltt = packet.get("LTT")
        if isinstance(ltt, str):
            try:
                today = datetime.now().date()
                parsed = datetime.strptime(ltt, "%H:%M:%S").time()
                return datetime.combine(today, parsed)
            except ValueError:
                pass
        return datetime.now()

    def _as_float(self, value) -> float | None:
        if value is None or value == "":
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def find_liquidity_zones(
        self,
        candles: list[Candle],
        previous_day: PreviousDayLevels,
        previous_day_candles: list[Candle] | None = None,
    ) -> list[Zone]:
        if len(candles) < 5:
            return []
        previous_day_candles = previous_day_candles or []
        session_high = max(candle.high for candle in candles)
        session_low = min(candle.low for candle in candles)
        recent_high = max(candle.high for candle in candles[-20:])
        recent_low = min(candle.low for candle in candles[-20:])
        recent_ranges = [max(candle.high - candle.low, 0.01) for candle in candles[-20:]]
        atr = sum(recent_ranges) / max(len(recent_ranges), 1)
        zones = [
            Zone(
                label="Session Buy-Side Liquidity",
                zone_type="liquidity",
                price=session_high,
                upper=session_high + 12,
                lower=session_high - 12,
                strength=0.8,
                notes="Current session high where buy stops are likely resting.",
            ),
            Zone(
                label="Session Sell-Side Liquidity",
                zone_type="liquidity",
                price=session_low,
                upper=session_low + 12,
                lower=session_low - 12,
                strength=0.8,
                notes="Current session low where sell stops are likely resting.",
            ),
            Zone(
                label="Recent Buy-Side Liquidity",
                zone_type="liquidity",
                price=recent_high,
                upper=recent_high + 10,
                lower=recent_high - 10,
                strength=0.74,
                notes="Clustered highs likely to hold resting buy stops.",
            ),
            Zone(
                label="Recent Sell-Side Liquidity",
                zone_type="liquidity",
                price=recent_low,
                upper=recent_low + 10,
                lower=recent_low - 10,
                strength=0.76,
                notes="Clustered lows likely to hold resting sell stops.",
            ),
        ]
        if previous_day.high:
            zones.append(
                Zone(
                    label="Previous Day High",
                    zone_type="reference",
                    price=previous_day.high,
                    upper=previous_day.high + 8,
                    lower=previous_day.high - 8,
                    strength=0.84,
                    notes="Classic SL-hunting reference above prior session.",
                )
            )
        if previous_day.low:
            zones.append(
                Zone(
                    label="Previous Day Low",
                    zone_type="reference",
                    price=previous_day.low,
                    upper=previous_day.low + 8,
                    lower=previous_day.low - 8,
                    strength=0.84,
                    notes="Classic SL-hunting reference below prior session.",
                )
            )
        mapped_buy, mapped_sell = self.heuristic_engine.build_liquidity_maps(
            candles,
            previous_day_candles,
            previous_day,
            candles[-1].close,
            atr,
            index_round_numbers=self.instrument_mode == InstrumentMode.nifty,
        )
        extra_levels = mapped_buy + mapped_sell
        seen_labels = {zone.label for zone in zones}
        seen_price_families = [(self.heuristic_engine._label_family(zone.label), zone.price) for zone in zones]
        zone_width = max(atr * 0.18, 0.5 if candles[-1].close < 500 else 2.0)
        for label, price, primary in extra_levels:
            label_family = self.heuristic_engine._label_family(label)
            if label in seen_labels or any(
                existing_family == label_family and abs(existing_price - price) <= zone_width * 0.35
                for existing_family, existing_price in seen_price_families
            ):
                continue
            if "Round Number" in label:
                note = "Round-number shelf often becomes psychological SL-hunting liquidity."
            elif "Pivot" in label:
                note = "Classic pivot-point support or resistance can attract stop hunts and reaction trades."
            elif "Previous-Day Resistance Shelf" in label or "Previous-Day Support Shelf" in label:
                note = "Repeated prior-day rejection or defense created an obvious structural liquidity shelf."
            elif "Previous-Day Swing" in label:
                note = "Prior-day swing pivot can act as structural support or resistance liquidity."
            elif "Equal High Cluster" in label or "Equal Low Cluster" in label:
                note = "Repeated same-price pivots suggest clustered stops and trap potential."
            else:
                note = "Same-day swing map marks intraday liquidity where stops can accumulate."
            zones.append(
                Zone(
                    label=label,
                    zone_type="liquidity",
                    price=price,
                    upper=price + zone_width,
                    lower=price - zone_width,
                    strength=0.8 if primary else 0.7,
                    notes=note,
                )
            )
            seen_labels.add(label)
            seen_price_families.append((label_family, price))
        return zones

    def build_liquidity_ledger(
        self,
        candles: list[Candle],
        previous_day: PreviousDayLevels,
        previous_day_candles: list[Candle] | None = None,
    ) -> list[LiquidityLedgerEntry]:
        if len(candles) < 3:
            return []
        previous_day_candles = previous_day_candles or []
        ordered = sorted(candles, key=lambda candle: candle.timestamp)
        latest = ordered[-1]
        span_minutes = 1
        if len(ordered) >= 2:
            delta_seconds = (ordered[-1].timestamp - ordered[-2].timestamp).total_seconds()
            span_minutes = max(1, int(round(delta_seconds / 60.0)) or 1)
        ranges = [max(candle.high - candle.low, 0.01) for candle in ordered[-20:]]
        atr = sum(ranges) / max(len(ranges), 1)
        price_tolerance = max(atr * 0.06, 0.03 if latest.close < 500 else 0.2)
        windows: list[tuple[str, int | None, list[Candle], float]] = []
        for minutes, base_strength in ((5, 0.55), (10, 0.6), (15, 0.66), (30, 0.72), (60, 0.78)):
            count = max(3, math.ceil(minutes / span_minutes))
            if len(ordered) >= count:
                windows.append((f"last {minutes}m", minutes, ordered[-count:], base_strength))
        windows.append(("session", None, ordered, 0.86))

        entries: list[LiquidityLedgerEntry] = []
        seen: set[tuple[str, str, float]] = set()

        def clamp_strength(value: float) -> float:
            return max(0.0, min(value, 1.0))

        def append_window_entries(
            *,
            label: str,
            minutes: int | None,
            window: list[Candle],
            base_strength: float,
        ) -> None:
            if len(window) < 3:
                return
            reference = window[:-1]
            window_latest = window[-1]
            buy_level = max(candle.high for candle in reference)
            sell_level = min(candle.low for candle in reference)
            levels = (
                ("buy-side", f"{label} buy stops", buy_level),
                ("sell-side", f"{label} sell stops", sell_level),
            )
            for side, level_label, level in levels:
                status = "untouched"
                trap_side = "none"
                status_bonus = 0.0
                if side == "buy-side" and window_latest.high > level + price_tolerance:
                    if window_latest.close < level:
                        status = "reclaimed"
                        trap_side = "buyers"
                        status_bonus = 0.1
                    elif window_latest.close > level + price_tolerance:
                        status = "accepted"
                        trap_side = "sellers"
                        status_bonus = 0.08
                    else:
                        status = "swept"
                        trap_side = "buyers"
                        status_bonus = 0.04
                elif side == "sell-side" and window_latest.low < level - price_tolerance:
                    if window_latest.close > level:
                        status = "reclaimed"
                        trap_side = "sellers"
                        status_bonus = 0.1
                    elif window_latest.close < level - price_tolerance:
                        status = "accepted"
                        trap_side = "buyers"
                        status_bonus = 0.08
                    else:
                        status = "swept"
                        trap_side = "sellers"
                        status_bonus = 0.04
                key = (label, side, round(level, 2))
                if key in seen:
                    continue
                seen.add(key)
                if status == "reclaimed":
                    direction_note = (
                        "buyers are trapped after a failed upside break"
                        if side == "buy-side"
                        else "sellers are trapped after a failed downside break"
                    )
                elif status == "accepted":
                    direction_note = (
                        "price is accepting above buy-side liquidity"
                        if side == "buy-side"
                        else "price is accepting below sell-side liquidity"
                    )
                elif status == "swept":
                    direction_note = f"{trap_side} may be trapped but reclaim is still incomplete"
                else:
                    direction_note = "liquidity remains available"
                entries.append(
                    LiquidityLedgerEntry(
                        window_label=label,
                        window_minutes=minutes,
                        side=side,
                        level_label=level_label,
                        level=round(level, 2),
                        status=status,
                        trap_side=trap_side,
                        strength=round(clamp_strength(base_strength + status_bonus), 2),
                        candle_count=len(window),
                        created_at=window[0].timestamp,
                        updated_at=window_latest.timestamp,
                        notes=f"{level_label} at {level:.2f}: {direction_note}.",
                    )
                )

        for label, minutes, window, base_strength in windows:
            append_window_entries(label=label, minutes=minutes, window=window, base_strength=base_strength)

        if previous_day.high:
            entries.append(
                LiquidityLedgerEntry(
                    window_label="previous day",
                    side="buy-side",
                    level_label="previous day high buy stops",
                    level=round(previous_day.high, 2),
                    status="accepted" if latest.close > previous_day.high + price_tolerance else "reclaimed" if latest.high > previous_day.high + price_tolerance and latest.close < previous_day.high else "untouched",
                    trap_side="sellers" if latest.close > previous_day.high + price_tolerance else "buyers" if latest.high > previous_day.high + price_tolerance and latest.close < previous_day.high else "none",
                    strength=0.9,
                    candle_count=len(previous_day_candles),
                    created_at=previous_day_candles[0].timestamp if previous_day_candles else None,
                    updated_at=latest.timestamp,
                    notes="Previous day high remains a primary buy-side liquidity reference.",
                )
            )
        if previous_day.low:
            entries.append(
                LiquidityLedgerEntry(
                    window_label="previous day",
                    side="sell-side",
                    level_label="previous day low sell stops",
                    level=round(previous_day.low, 2),
                    status="accepted" if latest.close < previous_day.low - price_tolerance else "reclaimed" if latest.low < previous_day.low - price_tolerance and latest.close > previous_day.low else "untouched",
                    trap_side="buyers" if latest.close < previous_day.low - price_tolerance else "sellers" if latest.low < previous_day.low - price_tolerance and latest.close > previous_day.low else "none",
                    strength=0.9,
                    candle_count=len(previous_day_candles),
                    created_at=previous_day_candles[0].timestamp if previous_day_candles else None,
                    updated_at=latest.timestamp,
                    notes="Previous day low remains a primary sell-side liquidity reference.",
                )
            )
        return entries

    def describe_market_structure(
        self,
        *,
        session_candles: list[Candle],
        previous_day_candles: list[Candle],
        previous_day: PreviousDayLevels,
        live_current_candle: Candle | None,
        liquidity_ledger: list[LiquidityLedgerEntry] | None = None,
    ) -> str:
        if not session_candles:
            return "No closed session candles are loaded yet."

        first_session = session_candles[0]
        last_session = session_candles[-1]
        session_high_candle = max(session_candles, key=lambda candle: candle.high)
        session_low_candle = min(session_candles, key=lambda candle: candle.low)
        buy_side_sweeps = [
            candle for candle in session_candles
            if previous_day.high and candle.high > previous_day.high and candle.close < previous_day.high
        ]
        sell_side_sweeps = [
            candle for candle in session_candles
            if previous_day.low and candle.low < previous_day.low and candle.close > previous_day.low
        ]

        lines = [
            (
                "Closed intraday session context spans "
                f"{len(session_candles)} candles from {first_session.timestamp.strftime('%Y-%m-%d %H:%M')} "
                f"to {last_session.timestamp.strftime('%Y-%m-%d %H:%M')}."
            ),
            (
                "Session structure: "
                f"open={first_session.open:.2f}, high={session_high_candle.high:.2f} at {session_high_candle.timestamp.strftime('%H:%M')}, "
                f"low={session_low_candle.low:.2f} at {session_low_candle.timestamp.strftime('%H:%M')}, "
                f"last_close={last_session.close:.2f}, range={(session_high_candle.high - session_low_candle.low):.2f}."
            ),
            (
                "Current closed candle is "
                f"{'bullish' if last_session.close >= last_session.open else 'bearish'} with "
                f"body={(last_session.close - last_session.open):.2f} and range={(last_session.high - last_session.low):.2f}."
            ),
            (
                "Current close is "
                f"{(last_session.close - first_session.open):+.2f} points from the session open, "
                f"{(session_high_candle.high - last_session.close):.2f} below session high, and "
                f"{(last_session.close - session_low_candle.low):.2f} above session low."
            ),
        ]

        if previous_day_candles:
            lines.append(
                (
                    "Previous day structure: "
                    f"open={previous_day_candles[0].open:.2f}, high={previous_day.high:.2f}, "
                    f"low={previous_day.low:.2f}, close={previous_day.close:.2f}, "
                    f"range={(previous_day.high - previous_day.low):.2f}."
                )
            )

        if previous_day.high:
            lines.append(
                (
                    "Previous day high sweep status: "
                    f"{len(buy_side_sweeps)} rejection sweep(s) detected today."
                )
            )
        if previous_day.low:
            lines.append(
                (
                    "Previous day low sweep status: "
                    f"{len(sell_side_sweeps)} reclaim sweep(s) detected today."
                )
            )
        if live_current_candle is not None:
            lines.append(
                (
                    "Forming live candle at analysis time: "
                    f"{live_current_candle.timestamp.strftime('%Y-%m-%d %H:%M')} "
                    f"open={live_current_candle.open:.2f}, high={live_current_candle.high:.2f}, "
                    f"low={live_current_candle.low:.2f}, close={live_current_candle.close:.2f}."
                )
            )
        active_ledger = [
            entry
            for entry in (liquidity_ledger or [])
            if entry.status in {"swept", "reclaimed", "accepted"}
        ][:6]
        if active_ledger:
            ledger_text = "; ".join(
                f"{entry.window_label} {entry.side} {entry.status} at {entry.level:.2f}"
                for entry in active_ledger
            )
            lines.append(f"Liquidity memory: {ledger_text}.")
        return "\n".join(lines)

    def find_operator_zones(self, candles: list[Candle]) -> list[Zone]:
        if len(candles) < 6:
            return []
        last_impulse = max(candles[-12:], key=lambda candle: abs(candle.close - candle.open))
        last_rejection = min(candles[-12:], key=lambda candle: candle.close - candle.low)
        return [
            Zone(
                label="Operator Supply",
                zone_type="operator",
                price=last_impulse.high,
                upper=last_impulse.high + 12,
                lower=last_impulse.close - 5,
                strength=0.66,
                notes="Recent impulse candle high acting as supply.",
            ),
            Zone(
                label="Operator Demand",
                zone_type="operator",
                price=last_rejection.low,
                upper=last_rejection.close + 5,
                lower=last_rejection.low - 12,
                strength=0.68,
                notes="Recent reclaim candle low acting as demand.",
            ),
        ]

    def detect_signal_events(
        self,
        current_candle: Candle | None,
        liquidity_zones: list[Zone],
        previous_day: PreviousDayLevels,
    ) -> list[SignalEvent]:
        if current_candle is None:
            return []

        events: list[SignalEvent] = []
        for zone in liquidity_zones:
            if zone.label == "Previous Day High" and current_candle.high > zone.price and current_candle.close < zone.price:
                events.append(
                    SignalEvent(
                        timestamp=current_candle.timestamp,
                        title="Buy-side sweep rejected",
                        sentiment="bearish",
                        description="Price traded above previous day high and closed back below it.",
                    )
                )
            if zone.label == "Previous Day Low" and current_candle.low < zone.price and current_candle.close > zone.price:
                events.append(
                    SignalEvent(
                        timestamp=current_candle.timestamp,
                        title="Sell-side sweep reclaimed",
                        sentiment="bullish",
                        description="Price traded below previous day low and reclaimed it before close.",
                    )
                )

        if previous_day.close and abs(current_candle.close - previous_day.close) < 8:
            events.append(
                SignalEvent(
                    timestamp=current_candle.timestamp,
                    title="Close near previous day close",
                    sentiment="neutral",
                    description="Market is rotating around the prior settlement reference.",
                )
            )
        return events

    def _record_signal_events_locked(self, events: list[SignalEvent]) -> None:
        for event in events:
            if event.title == "Close near previous day close" and event.sentiment == "neutral":
                last_same = next(
                    (
                        existing
                        for existing in reversed(self.signal_history)
                        if existing.title == event.title and existing.description == event.description
                    ),
                    None,
                )
                if last_same is not None and (event.timestamp - last_same.timestamp) < timedelta(minutes=15):
                    continue
            event_key = (
                event.timestamp.isoformat(),
                event.title.strip().lower(),
                event.description.strip().lower(),
            )
            if event_key in self._signal_history_keys:
                continue
            self._signal_history_keys.add(event_key)
            self.signal_history.append(event)

    def _rebuild_signal_history_up_to_index_locked(self, current_index: int) -> None:
        self.signal_history = []
        self._signal_history_keys = set()
        if current_index < 0 or current_index >= len(self.candles):
            return

        session_candles = get_session_candles_up_to_index(self.candles, current_index)
        if not session_candles:
            return

        session_day = session_candles[0].timestamp.date()
        session_indexes = [
            index
            for index, candle in enumerate(self.candles[: current_index + 1])
            if candle.timestamp.date() == session_day
        ]
        for index in session_indexes:
            candle = self.candles[index]
            session_slice = get_session_candles_up_to_index(self.candles, index)
            previous_day = calculate_previous_day_levels(self.candles, index)
            liquidity_zones = self.find_liquidity_zones(session_slice, previous_day)
            events = self.detect_signal_events(candle, liquidity_zones, previous_day)
            self._record_signal_events_locked(events)

    def normalize_trade_decision(
        self,
        decision: TradeDecision,
        active_trade: SimulatedTrade | None,
    ) -> TradeDecision:
        if active_trade is None and decision.action in {
            TradeAction.hold,
            TradeAction.exit,
            TradeAction.partial_exit,
            TradeAction.add_position,
            TradeAction.exit_pyramid_leg,
            TradeAction.update_stop,
            TradeAction.update_target,
        }:
            decision.action = TradeAction.no_trade
        normalized_option_type = self.normalize_option_type(
            decision.option_type,
            action=decision.action,
            active_trade=active_trade,
        )
        decision.option_type = normalized_option_type
        if decision.action == TradeAction.enter_call:
            decision.option_type = "CE"
        elif decision.action == TradeAction.enter_put:
            decision.option_type = "PE"
        if active_trade and decision.action in {
            TradeAction.hold,
            TradeAction.exit,
            TradeAction.partial_exit,
            TradeAction.add_position,
            TradeAction.exit_pyramid_leg,
            TradeAction.update_stop,
            TradeAction.update_target,
        }:
            decision.option_type = active_trade.option_type
            decision.strike = active_trade.strike
        decision.pending_setup_action = self.normalize_pending_setup_action(decision.pending_setup_action)
        decision.pending_setup_option_type = self.normalize_option_type(
            decision.pending_setup_option_type,
            active_trade=active_trade,
        )
        if decision.pending_setup_direction:
            decision.pending_setup_direction = {
                "BULLISH": "LONG_CALL",
                "LONG": "LONG_CALL",
                "BEARISH": "LONG_PUT",
                "SHORT": "LONG_PUT",
            }.get(
                decision.pending_setup_direction.strip().upper(),
                decision.pending_setup_direction.strip().upper(),
            )
        if decision.pending_setup_action in {"ARM", "REPLACE", "KEEP"}:
            if not decision.pending_setup_option_type and decision.pending_setup_direction in {"LONG_CALL", "BULLISH"}:
                decision.pending_setup_option_type = "CE"
            if not decision.pending_setup_option_type and decision.pending_setup_direction in {"LONG_PUT", "BEARISH"}:
                decision.pending_setup_option_type = "PE"
            if not decision.pending_setup_direction and decision.pending_setup_option_type == "CE":
                decision.pending_setup_direction = "LONG_CALL"
            if not decision.pending_setup_direction and decision.pending_setup_option_type == "PE":
                decision.pending_setup_direction = "LONG_PUT"
        return decision

    def normalize_option_type(
        self,
        option_type: str | None,
        *,
        action: TradeAction | None = None,
        active_trade: SimulatedTrade | None = None,
    ) -> str | None:
        if action == TradeAction.enter_call:
            return "CE"
        if action == TradeAction.enter_put:
            return "PE"

        normalized = {
            "CE": "CE",
            "CALL": "CE",
            "C": "CE",
            "PE": "PE",
            "PUT": "PE",
            "P": "PE",
        }.get((option_type or "").strip().upper())
        if normalized:
            return normalized
        if active_trade is not None:
            return active_trade.option_type
        return None

    def normalize_pending_setup_action(self, action: str | None) -> str:
        normalized = (action or "NONE").strip().upper()
        if normalized in {"ARM", "KEEP", "REPLACE", "INVALIDATE", "NONE"}:
            return normalized
        return "NONE"

    def apply_pending_setup_decision(self, current_candle: Candle, decision: TradeDecision) -> None:
        self._clear_pending_setup_if_new_session_locked(current_candle)
        if decision.action in {TradeAction.enter_call, TradeAction.enter_put} and self.pending_setup is not None:
            self._consume_pending_setup_locked(current_candle, decision.reason, None)
            return
        if self.active_trade and self.normalize_pending_setup_action(decision.pending_setup_action) == "NONE":
            return

        action = self.normalize_pending_setup_action(decision.pending_setup_action)
        existing = self.pending_setup
        if existing is not None and existing.status != "armed" and action == "ARM":
            existing = None

        if action == "INVALIDATE":
            if existing is not None:
                existing.status = "invalidated"
                existing.invalidated_at = current_candle.timestamp
                existing.updated_at = current_candle.timestamp
                existing.last_evaluated_at = current_candle.timestamp
                existing.status_reason = decision.pending_setup_notes or decision.reason or "Setup invalidated."
            return

        if existing is not None and action in {"NONE", "KEEP"}:
            existing.last_evaluated_at = current_candle.timestamp
            if decision.pending_setup_notes:
                existing.status_reason = decision.pending_setup_notes
            if action == "KEEP":
                existing.updated_at = current_candle.timestamp
                existing.confidence = decision.confidence
                if decision.setup_score is not None:
                    existing.setup_score = decision.setup_score
                if decision.market_state:
                    existing.market_state = decision.market_state
                if decision.pending_setup_invalidation_level is not None:
                    existing.invalidation_level = round(decision.pending_setup_invalidation_level, 2)
                if decision.target_spot_price is not None:
                    existing.target_spot_price = round(decision.target_spot_price, 2)
                if decision.first_target_price is not None:
                    existing.first_target_price = round(decision.first_target_price, 2)
                if existing.status != "consumed":
                    existing.status = "armed"
            return

        if action not in {"ARM", "REPLACE", "KEEP"}:
            return

        trigger_price = decision.pending_setup_trigger_price
        option_type = decision.pending_setup_option_type
        direction = decision.pending_setup_direction
        setup_type = decision.pending_setup_type or ("bullish_reclaim_watch" if option_type == "CE" else "bearish_rejection_watch")
        if trigger_price is None or option_type not in {"CE", "PE"} or direction not in {"LONG_CALL", "LONG_PUT"}:
            return

        if existing is not None and action == "KEEP":
            existing.last_evaluated_at = current_candle.timestamp
            return

        if existing is not None and action != "REPLACE":
            price_gap = abs(existing.trigger_price - trigger_price)
            same_direction = existing.direction == direction and existing.option_type == option_type
            if same_direction and price_gap <= 2:
                existing.last_evaluated_at = current_candle.timestamp
                existing.updated_at = current_candle.timestamp
                existing.confidence = decision.confidence
                if decision.pending_setup_notes:
                    existing.status_reason = decision.pending_setup_notes
                return
            return

        strike = decision.pending_setup_strike
        if strike is None and self.instrument_spec.supports_options:
            strike = decision.strike or self.select_itm_strike(current_candle.close, option_type)

        self.pending_setup = PendingSetup(
            setup_id=uuid.uuid4().hex[:10],
            status="armed",
            setup_type=setup_type,
            direction=direction,
            option_type=option_type,
            strike=strike,
            trigger_price=round(trigger_price, 2),
            invalidation_level=round(decision.pending_setup_invalidation_level, 2) if decision.pending_setup_invalidation_level is not None else None,
            target_spot_price=round(decision.target_spot_price, 2) if decision.target_spot_price is not None else None,
            first_target_price=round(decision.first_target_price, 2) if decision.first_target_price is not None else None,
            setup_score=decision.setup_score,
            market_state=decision.market_state,
            trigger_basis=(decision.pending_setup_trigger_basis or "close_above").strip().lower(),
            created_at=current_candle.timestamp,
            updated_at=current_candle.timestamp,
            last_evaluated_at=current_candle.timestamp,
            source=decision.decision_source,
            confidence=decision.confidence,
            notes=decision.pending_setup_notes or decision.reason,
            replacement_reason=(decision.reason if existing is not None and action == "REPLACE" else None),
            status_reason=decision.reason or decision.pending_setup_notes,
        )

    def _clear_pending_setup_if_new_session_locked(self, current_candle: Candle) -> None:
        if self.pending_setup is None:
            return
        if self.pending_setup.created_at.date() != current_candle.timestamp.date():
            self.pending_setup = None

    def evaluate_pending_setup_trigger(self, current_candle: Candle) -> TradeDecision | None:
        setup = self.pending_setup
        if setup is None or self.active_trade is not None:
            return None
        if setup.status != "armed":
            return None
        if not self.pending_setup_triggered(current_candle, setup):
            setup.last_evaluated_at = current_candle.timestamp
            return None
        if (
            self.instrument_mode == InstrumentMode.nifty
            and self.instrument_spec.supports_options
            and setup.setup_score is not None
            and setup.setup_score < self.heuristic_engine.enter_threshold
        ):
            setup.status = "invalidated"
            setup.invalidated_at = current_candle.timestamp
            setup.updated_at = current_candle.timestamp
            setup.last_evaluated_at = current_candle.timestamp
            setup.status_reason = (
                f"Pending {setup.option_type} trigger was rejected because refreshed setup score "
                f"{setup.setup_score:.1f} is below Nifty entry threshold {self.heuristic_engine.enter_threshold:.1f}."
            )
            return TradeDecision(
                action=TradeAction.no_trade,
                confidence=0.76,
                reason=setup.status_reason,
                decision_source="pending-setup-trigger",
                market_state=setup.market_state,
                setup_score=setup.setup_score,
                setup_type=setup.setup_type,
                pending_setup_action="INVALIDATE",
                pending_setup_type=setup.setup_type,
                pending_setup_direction=setup.direction,
                pending_setup_trigger_price=setup.trigger_price,
                pending_setup_invalidation_level=setup.invalidation_level,
                pending_setup_trigger_basis=setup.trigger_basis,
                pending_setup_notes=setup.status_reason,
                pending_setup_strike=setup.strike,
                pending_setup_option_type=setup.option_type,
                rule_ids_used=["R37", "R38", "R39", "R56", "R87"],
            )

        setup.status = "triggered"
        setup.triggered_at = current_candle.timestamp
        setup.updated_at = current_candle.timestamp
        setup.last_evaluated_at = current_candle.timestamp
        setup.status_reason = (
            f"Trigger satisfied on candle close {current_candle.close:.2f} "
            f"for {setup.trigger_basis} at {setup.trigger_price:.2f}."
        )
        action = TradeAction.enter_call if setup.option_type == "CE" else TradeAction.enter_put
        trigger_word = "above" if "above" in setup.trigger_basis else "below"
        reason = (
            f"Mechanical pending setup trigger fired: {setup.option_type} {trigger_word} "
            f"{setup.trigger_price:.2f} on candle {current_candle.timestamp.strftime('%H:%M')} "
            f"with close {current_candle.close:.2f}. {setup.notes}"
        ).strip()
        return TradeDecision(
            action=action,
            confidence=max(setup.confidence, 0.75),
            reason=reason,
            decision_source="pending-setup-trigger",
            strike=setup.strike,
            option_type=setup.option_type,
            invalidation_level=setup.invalidation_level,
            target_spot_price=setup.target_spot_price,
            first_target_price=setup.first_target_price,
            market_state=setup.market_state,
            setup_score=setup.setup_score,
            setup_type=setup.setup_type,
            pending_setup_action="KEEP",
            pending_setup_type=setup.setup_type,
            pending_setup_direction=setup.direction,
            pending_setup_trigger_price=setup.trigger_price,
            pending_setup_invalidation_level=setup.invalidation_level,
            pending_setup_trigger_basis=setup.trigger_basis,
            pending_setup_notes=setup.notes,
            pending_setup_strike=setup.strike,
            pending_setup_option_type=setup.option_type,
        )

    def pending_setup_triggered(self, candle: Candle, setup: PendingSetup) -> bool:
        basis = (setup.trigger_basis or "").strip().lower()
        if basis == "close_below":
            return candle.close < setup.trigger_price and candle.close < candle.open
        if basis == "close_above":
            return candle.close > setup.trigger_price and candle.close > candle.open
        if basis == "reclaim_above":
            return candle.low <= setup.trigger_price and candle.close > setup.trigger_price and candle.close > candle.open
        if basis == "reclaim_below":
            return candle.high >= setup.trigger_price and candle.close < setup.trigger_price and candle.close < candle.open
        if basis == "reject_above":
            return candle.high >= setup.trigger_price and candle.close < setup.trigger_price and candle.close < candle.open
        if basis == "reject_below":
            return candle.low <= setup.trigger_price and candle.close > setup.trigger_price and candle.close > candle.open
        if basis == "break_below":
            return candle.low < setup.trigger_price and candle.close < candle.open
        if basis == "break_above":
            return candle.high > setup.trigger_price and candle.close > candle.open
        return False

    def _consume_pending_setup_locked(self, current_candle: Candle, reason: str, executed_trade_id: str | None) -> None:
        if self.pending_setup is None:
            return
        self.pending_setup.status = "consumed"
        if self.pending_setup.triggered_at is None:
            self.pending_setup.triggered_at = current_candle.timestamp
        self.pending_setup.consumed_at = current_candle.timestamp
        self.pending_setup.updated_at = current_candle.timestamp
        self.pending_setup.last_evaluated_at = current_candle.timestamp
        self.pending_setup.status_reason = reason
        self.pending_setup.executed_trade_id = executed_trade_id

    def _trigger_pending_setup_after_exit(self, current_candle: Candle, source: str) -> None:
        trigger_decision = self.evaluate_pending_setup_trigger(current_candle)
        if trigger_decision is None or trigger_decision.action not in {TradeAction.enter_call, TradeAction.enter_put}:
            return
        trigger_decision = self._apply_stock_trade_bias_filter_locked(trigger_decision)
        trigger_decision = self._apply_nifty_trade_bias_filter_locked(trigger_decision)
        trigger_decision = self._apply_stock_turnover_filter_locked(current_candle, trigger_decision)
        trigger_decision = self._apply_nifty_daily_loss_cap_filter_locked(current_candle, trigger_decision, source)
        self.decision = trigger_decision
        self.apply_pending_setup_decision(current_candle, trigger_decision)
        self.apply_trade_logic(current_candle, trigger_decision, source=source)

    def current_trade_market_price(self, current_spot: float, trade: SimulatedTrade) -> float:
        if trade.price_mode == "cash":
            return round(current_spot, 2)
        if trade.current_quote_source != "simulated" and trade.current_option_price > 0:
            return trade.current_option_price
        return self.price_option(current_spot, trade.strike, trade.option_type)

    def _is_short_trade_direction(self, direction: str) -> bool:
        return direction in {"SHORT_STOCK", "SHORT_CALL", "SHORT_PUT"}

    def _is_long_trade_direction(self, direction: str) -> bool:
        return not self._is_short_trade_direction(direction)

    def _is_bullish_spot_trade_direction(self, direction: str) -> bool:
        return direction in {"LONG_CALL", "LONG_STOCK", "SHORT_PUT"}

    def _live_ltp_crossed_invalidation(self, trade: SimulatedTrade | None, ltp: float) -> bool:
        if trade is None or trade.invalidation_level is None:
            return False
        if self._is_bullish_spot_trade_direction(trade.direction):
            return ltp <= trade.invalidation_level
        return ltp >= trade.invalidation_level

    def _live_ltp_controls_allowed_locked(self, trade: SimulatedTrade | None, tick_time: datetime) -> bool:
        if trade is None:
            return False
        if self._replay_simulation_active_locked():
            return False
        execution_source = getattr(trade, "execution_source", "simulated")
        if execution_source == "replay":
            return False
        if (
            execution_source != "live"
            and not self._trade_is_broker_backed(trade)
            and trade.entry_time.date() != tick_time.date()
        ):
            return False
        return True

    def _exit_active_trade_on_ltp_invalidation(self, ltp: float, tick_time: datetime) -> bool:
        with self.lock:
            trade = self.active_trade
            if not self._live_ltp_controls_allowed_locked(trade, tick_time):
                return False
            if not self._live_ltp_crossed_invalidation(trade, ltp):
                return False
            assert trade is not None
            invalidation = trade.invalidation_level or ltp
            note = (
                f"Hard LTP stop triggered: live price {ltp:.2f} crossed invalidation "
                f"{invalidation:.2f} before candle close."
            )
            exit_candle = Candle(
                timestamp=tick_time,
                open=ltp,
                high=ltp,
                low=ltp,
                close=ltp,
                volume=0.0,
            )
        if self._should_send_live_orders("live"):
            return self._exit_live_trade(exit_candle, note)
        with self.lock:
            if self._live_ltp_crossed_invalidation(self.active_trade, ltp):
                self.close_active_trade(exit_candle, note)
                return True
        return False

    def _is_nifty_active_trade(self, trade: SimulatedTrade | None) -> bool:
        if trade is None:
            return False
        mode_value = getattr(trade.instrument_mode, "value", trade.instrument_mode)
        return (
            mode_value == InstrumentMode.nifty.value
            and self.instrument_mode == InstrumentMode.nifty
            and self.instrument_spec.symbol == "NIFTY"
        )

    def _nifty_daily_pnl_locked(self, trading_day: date, *, include_active: bool = True) -> float:
        total = 0.0
        active_id = self.active_trade.trade_id if self.active_trade is not None else None
        for trade in self.trade_history:
            if trade.trade_id == active_id:
                continue
            if not self._is_nifty_trade_record(trade):
                continue
            if trade.entry_time.date() != trading_day:
                continue
            if trade.status == "CLOSED":
                total += float(trade.booked_pnl or trade.pnl or 0.0)
        if include_active and self.active_trade is not None and self._is_nifty_active_trade(self.active_trade):
            if self.active_trade.entry_time.date() == trading_day:
                total += float(self.active_trade.pnl or 0.0)
        return round(total, 2)

    def _is_nifty_trade_record(self, trade: SimulatedTrade) -> bool:
        mode_value = getattr(trade.instrument_mode, "value", trade.instrument_mode)
        return mode_value == InstrumentMode.nifty.value and (
            trade.trade_security_id == self.instrument_spec.security_id
            or trade.instrument_label == self.instrument_spec.label
            or str(trade.symbol or "").startswith("NIFTY")
        )

    def _nifty_daily_loss_cap_hit_locked(self, trading_day: date, *, include_active: bool = True) -> bool:
        if not self._nifty_daily_max_loss_enabled():
            return False
        daily_cap = self._nifty_daily_max_loss()
        if daily_cap <= 0:
            return False
        return self._nifty_daily_pnl_locked(trading_day, include_active=include_active) <= -daily_cap

    def _nifty_daily_loss_cap_note_locked(self, trading_day: date, *, include_active: bool = True) -> str:
        day_pnl = self._nifty_daily_pnl_locked(trading_day, include_active=include_active)
        return (
            f"Nifty daily max loss cap hit for {trading_day.isoformat()}: "
            f"P&L {day_pnl:.2f} reached the configured cap {self._nifty_daily_max_loss():.2f}. "
            "Stop trading this NIFTY session."
        )

    def _nifty_next_round_profit_exit_note(self, trade: SimulatedTrade, ltp: float, bullish_trade: bool) -> tuple[str, float] | None:
        if abs(trade.entry_spot_price) < 10000:
            return None
        round_step = 100.0
        if bullish_trade:
            round_level = math.ceil(trade.entry_spot_price / round_step) * round_step
            if round_level <= trade.entry_spot_price:
                round_level += round_step
            exit_trigger = round_level - 20.0
            if exit_trigger <= trade.entry_spot_price:
                return None
            if self.heuristic_engine._nifty_round_reversal_structure_confirmed(
                list(self.candles),
                trade.entry_time,
                bullish_trade=True,
                exit_trigger=exit_trigger,
            ):
                return (
                    f"Nifty moved from entry toward the next 100-point round shelf {round_level:.2f}; "
                    f"live candles tagged the square-off band at {exit_trigger:.2f} and confirmed reversal structure, "
                    "so exit before round-number resistance expands against the trade.",
                    round(exit_trigger, 2),
                )
            return None
        round_level = math.floor(trade.entry_spot_price / round_step) * round_step
        if round_level >= trade.entry_spot_price:
            round_level -= round_step
        exit_trigger = round_level + 20.0
        if exit_trigger >= trade.entry_spot_price:
            return None
        if self.heuristic_engine._nifty_round_reversal_structure_confirmed(
            list(self.candles),
            trade.entry_time,
            bullish_trade=False,
            exit_trigger=exit_trigger,
        ):
            return (
                f"Nifty moved from entry toward the next 100-point round shelf {round_level:.2f}; "
                f"live candles tagged the square-off band at {exit_trigger:.2f} and confirmed reversal structure, "
                "so exit before round-number support expands against the trade.",
                round(exit_trigger, 2),
            )
        return None

    def _apply_live_ltp_trade_controls(self, ltp: float, tick_time: datetime) -> bool:
        with self.lock:
            trade = self.active_trade
            if trade is None:
                return False
            if not self._live_ltp_controls_allowed_locked(trade, tick_time):
                return False
            if trade.price_mode == "cash":
                trade.current_price = round(ltp, 2)
                trade.current_option_price = trade.current_price
                trade.current_quote_source = self.live_feed.source or "dhan-websocket"
                trade.current_quote_time = tick_time
                trade.pnl = self.calculate_trade_pnl(trade, trade.current_price)
            bullish_trade = self._is_bullish_spot_trade_direction(trade.direction)
            nifty_trade = self._is_nifty_active_trade(trade)
            exit_note: str | None = None
            if nifty_trade and self._nifty_target_enabled() and self._nifty_target_points() > 0:
                target_points = self._nifty_target_points()
                target_spot = trade.entry_spot_price + target_points if bullish_trade else trade.entry_spot_price - target_points
                target_hit = ltp >= target_spot if bullish_trade else ltp <= target_spot
                if target_hit:
                    exit_note = (
                        f"Nifty fixed target control booked profit: live spot {ltp:.2f} reached "
                        f"{target_spot:.2f}, {target_points:.2f} points from entry."
                    )
            if exit_note is None and nifty_trade:
                round_exit = self._nifty_next_round_profit_exit_note(trade, ltp, bullish_trade)
                if round_exit is not None:
                    exit_note, _ = round_exit
            if exit_note is None and self._live_ltp_crossed_invalidation(trade, ltp):
                invalidation = trade.invalidation_level or ltp
                exit_note = (
                    f"Hard LTP stop triggered: live price {ltp:.2f} crossed invalidation "
                    f"{invalidation:.2f} before candle close."
                )
            if exit_note is None and nifty_trade and self._nifty_daily_loss_cap_hit_locked(
                tick_time.date(),
                include_active=True,
            ):
                exit_note = self._nifty_daily_loss_cap_note_locked(tick_time.date(), include_active=True)
            if exit_note is None and nifty_trade and self._nifty_cost_sl_enabled() and self._nifty_cost_sl_points() > 0:
                cost_points = self._nifty_cost_sl_points()
                favorable_spot = trade.entry_spot_price + cost_points if bullish_trade else trade.entry_spot_price - cost_points
                favorable_hit = ltp >= favorable_spot if bullish_trade else ltp <= favorable_spot
                already_at_cost = (
                    trade.invalidation_level is not None
                    and (
                        (bullish_trade and trade.invalidation_level >= trade.entry_spot_price)
                        or ((not bullish_trade) and trade.invalidation_level <= trade.entry_spot_price)
                    )
                )
                if favorable_hit and not already_at_cost:
                    trade.invalidation_level = round(trade.entry_spot_price, 2)
                    next_stop = (
                        self.price_option(trade.invalidation_level, trade.strike, trade.option_type)
                        if trade.price_mode == "option"
                        else trade.invalidation_level
                    )
                    if self._is_short_trade_direction(trade.direction):
                        trade.stop_price = min(trade.stop_price, round(next_stop, 2))
                    else:
                        trade.stop_price = max(trade.stop_price, round(next_stop, 2))
                    trade.stop_option_price = trade.stop_price
                    trade.notes = (
                        f"Nifty cost-SL control moved invalidation to entry after live spot moved "
                        f"{cost_points:.2f} points in favor."
                    )
                    self._mark_state_dirty_locked()
                    return True
            if exit_note is None:
                return False
            exit_candle = Candle(
                timestamp=tick_time,
                open=ltp,
                high=ltp,
                low=ltp,
                close=ltp,
                volume=0.0,
            )
        if self._should_send_live_orders("live"):
            return self._exit_live_trade(exit_candle, exit_note)
        with self.lock:
            if self.active_trade is not None:
                self.close_active_trade(exit_candle, exit_note)
                return True
        return False

    def _exit_stock_session_on_ltp_invalidation(self, symbol: str, ltp: float, tick_time: datetime) -> bool:
        def operation():
            return self._exit_active_trade_on_ltp_invalidation(ltp, tick_time)

        return bool(self._run_in_stock_session(symbol, operation))

    def calculate_trade_pnl(self, trade: SimulatedTrade, current_price: float) -> float:
        open_quantity = trade.open_quantity if trade.open_quantity is not None else trade.quantity
        if self._is_short_trade_direction(trade.direction):
            unrealized = (trade.entry_price - current_price) * open_quantity
        else:
            unrealized = (current_price - trade.entry_price) * open_quantity
        return round(trade.booked_pnl + unrealized, 2)

    def derive_option_trade_plan(
        self,
        *,
        current_spot: float,
        strike: int,
        option_type: str,
        decision: TradeDecision,
        entry_price: float,
        short_trade: bool = False,
    ) -> tuple[float, float]:
        stop_price = decision.stop_option_price
        target_price = decision.target_option_price
        if decision.invalidation_level is not None:
            stop_price = self.price_option(decision.invalidation_level, strike, option_type)
        if decision.target_spot_price is not None:
            target_price = self.price_option(decision.target_spot_price, strike, option_type)
        if short_trade:
            resolved_stop = round(stop_price or (entry_price + 25), 2)
            resolved_target = round(target_price or max(entry_price - 50, 5), 2)
        else:
            resolved_stop = round(stop_price or max(entry_price - 25, 5), 2)
            resolved_target = round(target_price or (entry_price + 50), 2)
        return resolved_stop, resolved_target

    def _normalize_nifty_entry_invalidation(
        self,
        entry_spot: float,
        signal_option_type: str,
        invalidation_level: float | None,
    ) -> float | None:
        if self.instrument_mode != InstrumentMode.nifty or self.instrument_spec.symbol != "NIFTY":
            return invalidation_level
        if invalidation_level is None or abs(entry_spot) < 10000:
            return invalidation_level
        min_sl_points = max(self._nifty_min_sl_points(), 0.0)
        max_sl_points = max(self._nifty_max_sl_points(), min_sl_points)
        bullish_signal = signal_option_type == "CE"
        if bullish_signal:
            distance = entry_spot - invalidation_level
            if distance <= 0:
                return round(entry_spot - min_sl_points, 2)
            if distance < min_sl_points:
                return round(entry_spot - min_sl_points, 2)
            if distance > max_sl_points:
                return round(entry_spot - max_sl_points, 2)
            return round(invalidation_level, 2)
        distance = invalidation_level - entry_spot
        if distance <= 0:
            return round(entry_spot + min_sl_points, 2)
        if distance < min_sl_points:
            return round(entry_spot + min_sl_points, 2)
        if distance > max_sl_points:
            return round(entry_spot + max_sl_points, 2)
        return round(invalidation_level, 2)

    def _update_trade_level_from_structure(self, trade: SimulatedTrade, current_spot: float, decision: TradeDecision) -> tuple[float | None, float | None]:
        next_stop = decision.stop_option_price
        next_target = decision.target_option_price
        if trade.price_mode == "option":
            if decision.invalidation_level is not None:
                next_stop = self.price_option(decision.invalidation_level, trade.strike, trade.option_type)
            if decision.target_spot_price is not None:
                next_target = self.price_option(decision.target_spot_price, trade.strike, trade.option_type)
        else:
            if decision.invalidation_level is not None:
                next_stop = round(decision.invalidation_level, 2)
            if decision.target_spot_price is not None:
                next_target = round(decision.target_spot_price, 2)
        return next_stop, next_target

    def _sync_active_trade_subscription_locked(self) -> None:
        if self.live_feed_adapter is None:
            return
        next_subscription = None
        if (
            self.active_trade
            and self.active_trade.option_security_id
            and self.active_trade.quote_exchange_segment
            and self.active_trade.broker_provider != "zerodha"
        ):
            next_subscription = resolve_quote_subscription(
                self.active_trade.option_security_id,
                self.active_trade.quote_exchange_segment,
            )
        elif self.active_trade and self.active_trade.price_mode == "option" and self.active_trade.broker_provider == "zerodha":
            try:
                next_subscription = self._zerodha_quote_subscription_for_trade_locked(self.active_trade)
            except Exception as exc:
                self.execution_state.order_updates_message = f"Zerodha option feed subscription skipped: {exc}"

        if self._active_option_subscription and self._active_option_subscription != next_subscription:
            self.live_feed_adapter.unsubscribe_symbols([self._active_option_subscription])
            self._active_option_subscription = None

        if next_subscription and self._active_option_subscription != next_subscription:
            self.live_feed_adapter.subscribe_symbols([next_subscription])
            self._active_option_subscription = next_subscription

    def _load_option_contract_from_dhan(
        self,
        *,
        strike: int,
        option_type: str,
        reference_time: datetime,
    ) -> OptionContract | None:
        client_id, access_token = self.credential_store.get_dhan_credentials(self.settings)
        if not client_id or not access_token:
            return None
        try:
            contract = self.option_quote_service.resolve_option_contract(
                client_id=client_id,
                access_token=access_token,
                underlying_security_id=int(self.instrument_spec.security_id),
                underlying_segment=self.instrument_spec.exchange_segment,
                strike=strike,
                option_type=option_type,
                reference_time=reference_time,
                underlying_label=self.instrument_spec.symbol,
                expiry_preference=self._nifty_expiry_preference(),
            )
            try:
                quote = self.option_quote_service.fetch_quote(
                    client_id=client_id,
                    access_token=access_token,
                    security_id=contract.security_id,
                    exchange_segment="NSE_FNO",
                    option_type=contract.option_type,
                    strike=contract.strike,
                )
                contract.quote = quote
            except DhanOptionQuoteError:
                pass
            return contract
        except DhanOptionQuoteError as exc:
            self.rulebook_service.learning_log.insert(0, f"Option quote fallback used: {exc}")
            return None

    def _update_active_trade_quote_locked(self, quote: OptionQuote) -> str | None:
        if not self.active_trade:
            return None
        self.active_trade.current_price = round(quote.last_price, 2)
        self.active_trade.current_option_price = round(quote.last_price, 2)
        self.active_trade.current_quote_source = quote.source
        self.active_trade.current_quote_time = quote.quote_time
        self.active_trade.pnl = self.calculate_trade_pnl(self.active_trade, self.active_trade.current_price)
        self._mark_state_dirty_locked()
        if self._is_nifty_active_trade(self.active_trade) and self._nifty_daily_loss_cap_hit_locked(
            quote.quote_time.date(),
            include_active=True,
        ):
            return self._nifty_daily_loss_cap_note_locked(quote.quote_time.date(), include_active=True)
        return None

    def _global_mtm_square_off_note_locked(self, reference_time: datetime | None = None) -> str | None:
        if (
            not self.live_trading_enabled
            or self._global_mtm_square_off_triggered
            or not self._global_mtm_square_off_enabled()
        ):
            return None
        threshold = round(float(self._global_mtm_square_off_threshold()), 2)
        if threshold == 0:
            return None
        pnl_state = self._build_integrated_pnl_state_locked(reference_time=reference_time)
        total_pnl = round(float(pnl_state.total_pnl), 2)
        hit = total_pnl >= threshold if threshold > 0 else total_pnl <= threshold
        if not hit:
            return None
        direction = "profit" if threshold > 0 else "loss"
        return (
            f"Global MTM {direction} threshold hit: total P&L {total_pnl:.2f} "
            f"reached configured threshold {threshold:.2f}."
        )

    def _check_global_mtm_square_off(self, reference_time: datetime | None = None) -> None:
        with self.lock:
            note = self._global_mtm_square_off_note_locked(reference_time=reference_time)
            if note is None:
                return
            self._global_mtm_square_off_triggered = True
        self._square_off_all_trades_with_reason(note)

    def heuristic_decision(self, context: StrategyContext) -> TradeDecision:
        current_trade_price = None
        if context.active_trade is not None:
            current_trade_price = self.current_trade_market_price(context.current_candle.close, context.active_trade)
        decision = self.heuristic_engine.decide(context, current_trade_price=current_trade_price)
        decision.decision_source = "heuristic"
        if context.instrument.supports_options and decision.action in {TradeAction.enter_call, TradeAction.enter_put}:
            decision.strike = decision.strike or self.select_itm_strike(context.current_candle.close, decision.option_type or "CE")
        return decision

    def _should_send_live_orders(self, source: str) -> bool:
        return (
            source == "live"
            and self.live_trading_enabled
            and self.operating_mode == OperatingMode.heuristic
            and self.live_feed_adapter is not None
        )

    def _resolve_trade_quantity(self, current_spot: float, is_option_trade: bool) -> int:
        if is_option_trade:
            return max(self.settings.simulation_lot_size * self._nifty_order_lots(), self.settings.simulation_lot_size)
        return max(int(self._stock_trade_capital() // max(current_spot, 0.01)), 1)

    def _live_feed_security_id_for_spec(self, spec: InstrumentSpec, broker: str | None = None) -> str:
        security_id = str(spec.security_id or "").strip()
        if security_id:
            return security_id
        if (broker or self._selected_broker_provider()) == "zerodha" and spec.symbol:
            return f"ZERODHA:{spec.symbol.strip().upper()}"
        return ""

    def _zerodha_quote_subscription_for_spec(self, spec: InstrumentSpec) -> tuple[int, str, str]:
        api_key, _, access_token = self._available_zerodha_credentials()
        if not api_key or not access_token:
            raise ValueError("Zerodha API key and access token are required to connect Kite websocket.")
        instrument = self.zerodha_execution_service.resolve_feed_instrument(
            api_key=api_key,
            access_token=access_token,
            symbol=spec.symbol,
            exchange_segment=spec.exchange_segment,
            tradingsymbol=spec.symbol,
        )
        if instrument.instrument_token is None:
            raise ValueError(f"Could not resolve Zerodha feed token for {spec.symbol}.")
        return int(instrument.instrument_token), self._live_feed_security_id_for_spec(spec, "zerodha"), "quote"

    def _zerodha_quote_subscription_for_trade_locked(self, trade: SimulatedTrade) -> tuple[int, str, str] | None:
        if trade.broker_provider != "zerodha" or not trade.broker_exchange or not trade.broker_tradingsymbol:
            return None
        api_key, _, access_token = self._available_zerodha_credentials()
        if not api_key or not access_token:
            return None
        instrument = self.zerodha_execution_service.resolve_feed_instrument_by_tradingsymbol(
            api_key=api_key,
            access_token=access_token,
            exchange=trade.broker_exchange,
            tradingsymbol=trade.broker_tradingsymbol,
        )
        if instrument.instrument_token is None:
            return None
        security_id = trade.option_security_id or str(instrument.instrument_token)
        trade.option_security_id = security_id
        trade.trade_security_id = trade.trade_security_id or security_id
        return int(instrument.instrument_token), security_id, "quote"

    def _resolve_stock_future_execution(self, current_candle: Candle):
        if self._selected_broker_provider() == "zerodha":
            symbol = self.instrument_spec.symbol
            if not self.stock_universe.is_derivative_symbol(symbol):
                raise ValueError(f"{symbol} is not in the configured F&O stock list.")
            api_key, _, access_token = self._available_zerodha_credentials()
            if not api_key or not access_token:
                raise ValueError("Zerodha API key and access token are required to resolve stock futures.")
            instrument = self.zerodha_execution_service.resolve_fno_tradingsymbol(
                api_key=api_key,
                access_token=access_token,
                underlying=symbol,
                instrument_type="FUT",
                expiry=None,
            )
            contract = StockFutureContract(
                symbol=symbol,
                label=instrument.tradingsymbol,
                trading_symbol=instrument.tradingsymbol,
                security_id=str(instrument.instrument_token or ""),
                expiry=instrument.expiry,
                lot_size=instrument.lot_size,
                exchange_segment=instrument.exchange or "NFO",
            )
            lots = self._stock_future_lots()
            quantity = max(int(contract.lot_size) * lots, 1)
            return contract, quantity
        contract = self.stock_universe.resolve_current_future(
            self.instrument_spec.symbol,
            reference_date=current_candle.timestamp.date(),
        )
        lots = self._stock_future_lots()
        quantity = max(int(contract.lot_size) * lots, 1)
        return contract, quantity

    def _resolve_stock_option_execution(self, current_candle: Candle, option_type: str) -> tuple[OptionContract, OptionQuote, int]:
        symbol = self.instrument_spec.symbol
        if not self.stock_universe.is_derivative_symbol(symbol):
            raise ValueError(f"{symbol} is not in the configured F&O stock list.")
        if self._selected_broker_provider() == "zerodha":
            api_key, _, access_token = self._available_zerodha_credentials()
            if not api_key or not access_token:
                raise ValueError("Zerodha API key and access token are required to resolve stock option contracts.")
            instrument = self.zerodha_execution_service.resolve_atm_option_tradingsymbol(
                api_key=api_key,
                access_token=access_token,
                underlying=symbol,
                spot=current_candle.close,
                option_type=option_type,
            )
            try:
                ltp = self.zerodha_execution_service.fetch_ltp(
                    api_key=api_key,
                    access_token=access_token,
                    exchange=instrument.exchange,
                    tradingsymbol=instrument.tradingsymbol,
                )
            except ZerodhaExecutionError as exc:
                raise ValueError(f"Could not fetch Zerodha option LTP for {instrument.tradingsymbol}: {exc}") from exc
            quote = OptionQuote(
                security_id=str(instrument.instrument_token or ""),
                option_type=option_type,
                strike=int(round(instrument.strike)),
                last_price=ltp,
                quote_time=current_candle.timestamp,
                source="zerodha-ltp",
            )
            contract = OptionContract(
                security_id=str(instrument.instrument_token or ""),
                option_type=option_type,
                strike=int(round(instrument.strike)),
                expiry=instrument.expiry or current_candle.timestamp.date(),
                symbol=instrument.tradingsymbol,
                lot_size=instrument.lot_size,
                quote=quote,
            )
            quantity = max(int(instrument.lot_size) * self._stock_option_lots(), 1)
            return contract, quote, quantity
        client_id, access_token = self.credential_store.get_dhan_credentials(self.settings)
        if not client_id or not access_token:
            raise ValueError("Dhan credentials are required to resolve stock option contracts.")
        try:
            underlying_security_id = int(self.instrument_spec.security_id)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Cash security id is required to resolve {symbol} stock options.") from exc
        contract = self.option_quote_service.resolve_atm_option_contract(
            client_id=client_id,
            access_token=access_token,
            underlying_security_id=underlying_security_id,
            underlying_segment=self.instrument_spec.exchange_segment,
            spot=current_candle.close,
            option_type=option_type,
            reference_time=current_candle.timestamp,
            underlying_label=symbol,
            expiry_preference=self._nifty_expiry_preference(),
        )
        try:
            quote = self.option_quote_service.fetch_quote(
                client_id=client_id,
                access_token=access_token,
                security_id=contract.security_id,
                exchange_segment="NSE_FNO",
                option_type=contract.option_type,
                strike=contract.strike,
            )
        except DhanOptionQuoteError as exc:
            if contract.quote is None or contract.quote.last_price <= 0:
                raise ValueError(f"Could not fetch live quote for {contract.symbol}: {exc}") from exc
            quote = contract.quote
        if quote.last_price <= 0:
            raise ValueError(f"Stock option quote is not tradable for {contract.symbol}.")
        contract.quote = quote
        lot_size = contract.lot_size
        if not lot_size or lot_size <= 0:
            try:
                future_contract = self.stock_universe.resolve_current_future(
                    symbol,
                    reference_date=current_candle.timestamp.date(),
                )
                lot_size = future_contract.lot_size
            except ValueError as exc:
                raise ValueError(f"Could not resolve lot size for {symbol} stock option.") from exc
        quantity = max(int(lot_size) * self._stock_option_lots(), 1)
        return contract, quote, quantity

    def _use_spot_pricing_for_source(self, source: str) -> bool:
        return source == "replay" and self.instrument_spec.supports_options

    def _use_nifty_live_option_selling(self, source: str) -> bool:
        return (
            source == "live"
            and self.instrument_mode == InstrumentMode.nifty
            and self.instrument_spec.symbol == "NIFTY"
            and self.instrument_spec.supports_options
            and self.credential_store.get_nifty_option_trade_mode(self.settings) == "selling"
        )

    def _use_nifty_live_option_buying(self, source: str) -> bool:
        return (
            source == "live"
            and self.instrument_mode == InstrumentMode.nifty
            and self.instrument_spec.symbol == "NIFTY"
            and self.instrument_spec.supports_options
            and self.credential_store.get_nifty_option_trade_mode(self.settings) == "buying"
        )

    def _nifty_sell_option_type_for_signal(self, signal_option_type: str) -> str:
        return "PE" if signal_option_type == "CE" else "CE"

    def _nifty_short_direction_for_option(self, option_type: str) -> str:
        return "SHORT_CALL" if option_type == "CE" else "SHORT_PUT"

    def _zerodha_broker_symbol_for_trade(
        self,
        *,
        current_candle: Candle,
        is_option_trade: bool,
        use_stock_future_execution: bool,
        option_type: str,
        strike: int,
        contract,
    ) -> tuple[str | None, str | None]:
        if self._selected_broker_provider() != "zerodha":
            return None, None
        api_key, _, access_token = self._available_zerodha_credentials()
        if not api_key or not access_token:
            raise ValueError("Zerodha API key and access token are required before live order placement.")
        if not is_option_trade and not use_stock_future_execution:
            return "NSE", self.instrument_spec.symbol
        if use_stock_future_execution:
            instrument = self.zerodha_execution_service.resolve_fno_tradingsymbol(
                api_key=api_key,
                access_token=access_token,
                underlying=self.instrument_spec.symbol,
                instrument_type="FUT",
                expiry=getattr(contract, "expiry", None),
            )
            return instrument.exchange, instrument.tradingsymbol
        if is_option_trade:
            if contract is not None and getattr(contract, "symbol", "") and self._stock_option_mode_enabled():
                return "NFO", contract.symbol
            expiry = getattr(contract, "expiry", None)
            if expiry is None:
                expiry = self.option_expiry_for_preference(current_candle.timestamp.date(), self._nifty_expiry_preference())
            instrument = self.zerodha_execution_service.resolve_fno_tradingsymbol(
                api_key=api_key,
                access_token=access_token,
                underlying=self.instrument_spec.symbol,
                instrument_type=option_type,
                expiry=expiry,
                strike=strike,
                option_type=option_type,
            )
            return instrument.exchange, instrument.tradingsymbol
        return None, None

    def _stock_pre_arm_paper_execution_disabled(self, source: str) -> bool:
        return (
            self.instrument_mode == InstrumentMode.stock
            and not self.instrument_spec.supports_options
            and source in {"sync", "live"}
            and not self.live_trading_enabled
        )

    def _trade_is_broker_backed(self, trade: SimulatedTrade | None) -> bool:
        if trade is None:
            return False
        return bool(trade.broker_order_id or trade.broker_entry_correlation_id)

    def _clear_simulated_active_trade_from_session(self, session: StockRuntimeSession) -> bool:
        trade = session.active_trade
        if trade is None or self._trade_is_broker_backed(trade):
            return False
        session.trade_history = [item for item in session.trade_history if item.trade_id != trade.trade_id]
        session.active_trade = None
        return True

    def _build_entry_trade(
        self,
        current_candle: Candle,
        decision: TradeDecision,
        *,
        source: str = "manual",
    ) -> SimulatedTrade:
        signal_option_type = self.normalize_option_type(decision.option_type, action=decision.action) or "CE"
        normalized_invalidation = self._normalize_nifty_entry_invalidation(
            current_candle.close,
            signal_option_type,
            decision.invalidation_level,
        )
        if normalized_invalidation != decision.invalidation_level:
            decision = decision.model_copy(update={"invalidation_level": normalized_invalidation})
        option_type = signal_option_type
        use_stock_option_execution = (
            source == "live"
            and self.instrument_mode == InstrumentMode.stock
            and not self.instrument_spec.supports_options
            and self._stock_execution_mode() == "option"
        )
        is_option_trade = (
            self.instrument_spec.supports_options and not self._use_spot_pricing_for_source(source)
        ) or use_stock_option_execution
        use_option_selling = is_option_trade and self._use_nifty_live_option_selling(source)
        use_nifty_live_buying = is_option_trade and self._use_nifty_live_option_buying(source)
        use_stock_future_execution = (
            source == "live"
            and self.instrument_mode == InstrumentMode.stock
            and not self.instrument_spec.supports_options
            and self._stock_execution_mode() == "future"
        )
        if use_option_selling:
            option_type = self._nifty_sell_option_type_for_signal(signal_option_type)
            strike = self.select_nifty_live_sell_strike(current_candle.close, option_type)
        elif use_nifty_live_buying:
            strike = self.select_nifty_live_buy_strike(current_candle.close, option_type)
        elif use_stock_option_execution:
            strike = 0
        else:
            strike = decision.strike or (self.select_itm_strike(current_candle.close, option_type) if is_option_trade else 0)
        contract = None
        entry_quote = None
        quantity = self._resolve_trade_quantity(current_candle.close, is_option_trade)
        if is_option_trade:
            if use_stock_option_execution:
                contract, entry_quote, quantity = self._resolve_stock_option_execution(current_candle, option_type)
                strike = contract.strike
                entry_price = entry_quote.last_price
                trade_symbol = contract.symbol
                direction = "LONG_CALL" if option_type == "CE" else "LONG_PUT"
            else:
                contract = self._load_option_contract_from_dhan(
                    strike=strike,
                    option_type=option_type,
                    reference_time=current_candle.timestamp,
                )
                entry_quote = contract.quote if contract and contract.quote else None
                entry_price = entry_quote.last_price if entry_quote else self.price_option(current_candle.close, strike, option_type)
                trade_symbol = contract.symbol if contract else self.format_option_symbol(current_candle.timestamp, strike, option_type)
                direction = (
                    self._nifty_short_direction_for_option(option_type)
                    if use_option_selling
                    else ("LONG_CALL" if option_type == "CE" else "LONG_PUT")
                )
            trade_security_id = contract.security_id if contract else None
            quote_exchange_segment = "NSE_FNO"
            stop_price, target_price = self.derive_option_trade_plan(
                current_spot=current_candle.close,
                strike=strike,
                option_type=option_type,
                decision=decision,
                entry_price=entry_price,
                short_trade=use_option_selling,
            )
        else:
            entry_price = round(current_candle.close, 2)
            direction = "LONG_STOCK" if option_type == "CE" else "SHORT_STOCK"
            if use_stock_future_execution:
                contract, quantity = self._resolve_stock_future_execution(current_candle)
                trade_symbol = contract.trading_symbol
                trade_security_id = contract.security_id
                quote_exchange_segment = contract.exchange_segment
                entry_price = round(current_candle.close, 2)
            else:
                trade_symbol = (
                    f"{self.instrument_spec.symbol} SPOT" if self.instrument_spec.supports_options else f"{self.instrument_spec.symbol} EQ"
                )
                trade_security_id = self.instrument_spec.security_id
                quote_exchange_segment = self.instrument_spec.exchange_segment
            stop_price = round(decision.invalidation_level or decision.stop_option_price or entry_price, 2)
            target_price = round(decision.target_spot_price or decision.target_option_price or entry_price, 2)
        entry_time = entry_quote.quote_time if entry_quote else current_candle.timestamp
        broker_provider = self._selected_broker_provider() if source == "live" else None
        broker_exchange = None
        broker_tradingsymbol = None
        if broker_provider == "zerodha":
            broker_exchange, broker_tradingsymbol = self._zerodha_broker_symbol_for_trade(
                current_candle=current_candle,
                is_option_trade=is_option_trade,
                use_stock_future_execution=use_stock_future_execution,
                option_type=option_type,
                strike=strike,
                contract=contract,
            )
        return SimulatedTrade(
            trade_id=uuid.uuid4().hex[:10],
            status="OPEN",
            direction=direction,
            instrument_mode=self.instrument_mode,
            instrument_label=self.instrument_spec.label,
            price_mode="option" if is_option_trade else "cash",
            trade_security_id=trade_security_id,
            quote_exchange_segment=quote_exchange_segment,
            option_type=option_type,
            strike=strike,
            symbol=trade_symbol,
            option_security_id=contract.security_id if contract else None,
            quantity=quantity,
            base_quantity=quantity,
            open_quantity=quantity,
            entry_time=entry_time,
            entry_price=entry_price,
            entry_spot_price=current_candle.close,
            entry_option_price=entry_price,
            execution_source=source,
            entry_quote_source=entry_quote.source if entry_quote else "simulated",
            entry_quote_time=entry_quote.quote_time if entry_quote else current_candle.timestamp,
            current_price=entry_price,
            current_option_price=entry_price,
            current_quote_source=entry_quote.source if entry_quote else "simulated",
            current_quote_time=entry_quote.quote_time if entry_quote else current_candle.timestamp,
            stop_price=stop_price,
            stop_option_price=stop_price,
            target_price=target_price,
            target_option_price=target_price,
            invalidation_level=round(decision.invalidation_level, 2) if decision.invalidation_level is not None else None,
            target_spot_price=round(decision.target_spot_price, 2) if decision.target_spot_price is not None else None,
            first_target_price=round(decision.first_target_price, 2) if decision.first_target_price is not None else None,
            setup_type=decision.setup_type,
            setup_score=decision.setup_score,
            market_state=decision.market_state,
            entry_notes=decision.reason,
            notes=decision.reason,
            broker_product_type="INTRADAY",
            broker_provider=broker_provider,
            broker_exchange=broker_exchange,
            broker_tradingsymbol=broker_tradingsymbol,
        )

    def _finalize_open_trade(self, current_candle: Candle, trade: SimulatedTrade, reason: str) -> None:
        self.active_trade = trade
        if self.pending_setup is not None:
            self._consume_pending_setup_locked(current_candle, reason, trade.trade_id)
        self.trade_history.append(trade)
        self._sync_active_trade_subscription_locked()
        self._mark_state_dirty_locked()

    def _place_market_order_for_trade(
        self,
        *,
        trade: SimulatedTrade,
        transaction_type: str,
        quantity: int,
        correlation_id: str,
    ) -> BrokerOrderResult:
        broker = trade.broker_provider or self._selected_broker_provider()
        if broker == "zerodha":
            api_key, _, access_token = self._available_zerodha_credentials()
            if not api_key or not access_token:
                raise ZerodhaExecutionError("Zerodha API key and access token are unavailable.")
            return self.zerodha_execution_service.place_market_order(
                api_key=api_key,
                access_token=access_token,
                exchange=trade.broker_exchange or "",
                tradingsymbol=trade.broker_tradingsymbol or "",
                transaction_type=transaction_type,
                quantity=quantity,
                product_type=trade.broker_product_type or "INTRADAY",
                correlation_id=correlation_id,
            )
        client_id, access_token = self._available_dhan_credentials()
        if not client_id or not access_token:
            raise DhanExecutionError("Dhan credentials are unavailable.")
        security_id = trade.option_security_id if trade.price_mode == "option" else trade.trade_security_id
        exchange_segment = trade.quote_exchange_segment or self.instrument_spec.exchange_segment
        if not security_id or not exchange_segment:
            raise DhanExecutionError("The execution contract could not be resolved.")
        return self.execution_service.place_market_order(
            client_id=client_id,
            access_token=access_token,
            security_id=security_id,
            exchange_segment=exchange_segment,
            transaction_type=transaction_type,
            quantity=quantity,
            product_type=trade.broker_product_type or "INTRADAY",
            correlation_id=correlation_id,
        )

    def _enter_live_trade(self, current_candle: Candle, decision: TradeDecision, trade: SimulatedTrade) -> None:
        transaction_type = "BUY" if self._is_long_trade_direction(trade.direction) else "SELL"
        correlation_id = f"entry-{trade.trade_id}"
        try:
            result = self._place_market_order_for_trade(
                trade=trade,
                transaction_type=transaction_type,
                quantity=trade.quantity,
                correlation_id=correlation_id,
            )
        except (DhanExecutionError, ZerodhaExecutionError) as exc:
            error_message = str(exc)
            self._record_execution_feedback_locked(
                symbol=trade.symbol,
                message=f"Live entry failed for {trade.symbol}: {error_message}",
                error=error_message,
            )
            self.rulebook_service.learning_log.insert(0, f"Live entry failed for {trade.symbol}: {error_message}")
            return
        if not result.ok:
            self._record_execution_feedback_locked(
                symbol=trade.symbol,
                message=f"Live entry rejected for {trade.symbol}: {result.message}",
                error=result.message,
            )
            self.rulebook_service.learning_log.insert(0, f"Live entry rejected for {trade.symbol}: {result.message}")
            return
        trade.broker_order_id = result.order_id
        trade.broker_entry_correlation_id = correlation_id
        trade.broker_status = result.order_status or "PENDING"
        trade.broker_status_message = result.message
        trade.entry_quote_source = f"{trade.broker_provider or self._selected_broker_provider()}-market-order"
        trade.current_quote_source = trade.entry_quote_source
        success_message = f"Live entry order sent for {trade.symbol} with qty {trade.quantity}."
        self._record_execution_feedback_locked(symbol=trade.symbol, message=success_message)
        self.rulebook_service.learning_log.insert(0, success_message)
        self._finalize_open_trade(current_candle, trade, decision.reason)

    def _exit_live_trade(
        self,
        current_candle: Candle,
        note: str,
        quantity: int | None = None,
        *,
        pyramid_leg_ids: list[str] | None = None,
    ) -> bool:
        if not self.active_trade:
            return False
        trade = self.active_trade
        open_quantity = trade.open_quantity if trade.open_quantity is not None else trade.quantity
        exit_quantity = open_quantity if quantity is None else max(1, min(quantity, open_quantity))
        transaction_type = "SELL" if self._is_long_trade_direction(trade.direction) else "BUY"
        correlation_id = (
            f"pyramid-exit-{trade.trade_id}-{trade.partial_exit_count + 1}"
            if pyramid_leg_ids
            else f"exit-{trade.trade_id}-{trade.partial_exit_count + 1}"
        )
        try:
            result = self._place_market_order_for_trade(
                trade=trade,
                transaction_type=transaction_type,
                quantity=exit_quantity,
                correlation_id=correlation_id,
            )
        except (DhanExecutionError, ZerodhaExecutionError) as exc:
            error_message = str(exc)
            self._record_execution_feedback_locked(
                symbol=trade.symbol,
                message=f"Live exit failed for {trade.symbol}: {error_message}",
                error=error_message,
            )
            self.rulebook_service.learning_log.insert(0, f"Live exit failed for {trade.symbol}: {error_message}")
            return False
        if not result.ok:
            self._record_execution_feedback_locked(
                symbol=trade.symbol,
                message=f"Live exit rejected for {trade.symbol}: {result.message}",
                error=result.message,
            )
            self.rulebook_service.learning_log.insert(0, f"Live exit rejected for {trade.symbol}: {result.message}")
            return False
        trade.broker_exit_order_id = result.order_id
        trade.broker_exit_correlation_id = correlation_id
        trade.broker_status = result.order_status or "PENDING"
        trade.broker_status_message = result.message
        success_message = f"Live exit order sent for {trade.symbol} with qty {exit_quantity}."
        self._record_execution_feedback_locked(symbol=trade.symbol, message=success_message)
        self.rulebook_service.learning_log.insert(0, success_message)
        self._mark_state_dirty_locked()
        if pyramid_leg_ids:
            self._close_pyramid_legs(
                current_candle,
                note,
                pyramid_leg_ids,
                broker_exit_order_id=result.order_id,
                broker_exit_correlation_id=correlation_id,
                broker_status=result.order_status or "PENDING",
                broker_status_message=result.message,
            )
        elif exit_quantity >= open_quantity:
            self.close_active_trade(current_candle, note)
        else:
            self.partial_exit_active_trade(current_candle, note, quantity=exit_quantity)
        return True

    def _add_to_active_trade(
        self,
        current_candle: Candle,
        note: str,
        quantity: int | None = None,
        *,
        add_invalidation_level: float | None = None,
        broker_order_id: str | None = None,
        broker_entry_correlation_id: str | None = None,
        broker_status: str | None = None,
        broker_status_message: str | None = None,
    ) -> bool:
        if not self.active_trade:
            return False
        trade = self.active_trade
        if max(int(trade.pyramid_count or 0), 0) >= 2:
            return False
        base_quantity = max(int(trade.base_quantity or trade.quantity or 1), 1)
        add_quantity = base_quantity
        open_quantity = trade.open_quantity if trade.open_quantity is not None else trade.quantity
        if open_quantity <= 0:
            return False
        add_price = self.current_trade_market_price(current_candle.close, trade)
        new_open_quantity = open_quantity + add_quantity
        trade.entry_price = round(
            ((trade.entry_price * open_quantity) + (add_price * add_quantity)) / new_open_quantity,
            2,
        )
        trade.entry_option_price = trade.entry_price
        trade.quantity += add_quantity
        trade.open_quantity = new_open_quantity
        trade.pyramid_count += 1
        trade.last_pyramid_time = current_candle.timestamp
        trade.last_pyramid_price = add_price
        trade.pyramid_legs.append(
            PyramidLeg(
                leg_id=uuid.uuid4().hex[:10],
                add_number=trade.pyramid_count,
                quantity=add_quantity,
                open_quantity=add_quantity,
                entry_time=current_candle.timestamp,
                entry_price=add_price,
                entry_spot_price=current_candle.close,
                invalidation_level=round(add_invalidation_level if add_invalidation_level is not None else (trade.invalidation_level or current_candle.close), 2),
                broker_order_id=broker_order_id,
                broker_entry_correlation_id=broker_entry_correlation_id,
                broker_status=broker_status,
                broker_status_message=broker_status_message,
            )
        )
        trade.current_price = add_price
        trade.current_option_price = add_price
        trade.current_quote_time = current_candle.timestamp
        trade.pnl = self.calculate_trade_pnl(trade, add_price)
        trade.notes = note
        if note:
            trade.entry_notes = f"{trade.entry_notes} Pyramiding add {trade.pyramid_count}: {note}".strip()
        self._mark_state_dirty_locked()
        return True

    def _close_pyramid_legs(
        self,
        candle: Candle,
        note: str,
        leg_ids: list[str],
        *,
        broker_exit_order_id: str | None = None,
        broker_exit_correlation_id: str | None = None,
        broker_status: str | None = None,
        broker_status_message: str | None = None,
    ) -> bool:
        if not self.active_trade:
            return False
        trade = self.active_trade
        requested = set(leg_ids)
        legs = [
            leg
            for leg in trade.pyramid_legs
            if leg.leg_id in requested and leg.status == "OPEN" and leg.open_quantity > 0
        ]
        if not legs:
            return False
        exit_price = self.current_trade_market_price(candle.close, trade)
        old_open_quantity = trade.open_quantity if trade.open_quantity is not None else trade.quantity
        exit_quantity = sum(max(int(leg.open_quantity or 0), 0) for leg in legs)
        if exit_quantity <= 0 or old_open_quantity <= 0:
            return False
        removed_value = 0.0
        booked_increment = 0.0
        for leg in legs:
            leg_quantity = max(int(leg.open_quantity or 0), 0)
            removed_value += leg.entry_price * leg_quantity
            if self._is_short_trade_direction(trade.direction):
                booked_increment += (leg.entry_price - exit_price) * leg_quantity
            else:
                booked_increment += (exit_price - leg.entry_price) * leg_quantity
            leg.status = "CLOSED"
            leg.exit_time = candle.timestamp
            leg.exit_price = exit_price
            leg.exit_spot_price = candle.close
            leg.open_quantity = 0
            leg.broker_exit_order_id = broker_exit_order_id or leg.broker_exit_order_id
            leg.broker_exit_correlation_id = broker_exit_correlation_id or leg.broker_exit_correlation_id
            leg.broker_status = broker_status or leg.broker_status
            leg.broker_status_message = broker_status_message or leg.broker_status_message
        new_open_quantity = max(old_open_quantity - exit_quantity, 0)
        if new_open_quantity > 0:
            remaining_value = max((trade.entry_price * old_open_quantity) - removed_value, 0.0)
            trade.entry_price = round(remaining_value / new_open_quantity, 2)
            trade.entry_option_price = trade.entry_price
        trade.booked_pnl = round(trade.booked_pnl + booked_increment, 2)
        trade.open_quantity = new_open_quantity
        trade.closed_quantity += exit_quantity
        trade.partial_exit_count += 1
        trade.last_partial_exit_time = candle.timestamp
        trade.current_price = exit_price
        trade.current_option_price = exit_price
        trade.current_quote_time = candle.timestamp
        trade.pnl = self.calculate_trade_pnl(trade, exit_price)
        trade.exit_notes = note
        trade.notes = note
        self._mark_state_dirty_locked()
        return True

    def _add_live_trade(self, current_candle: Candle, decision: TradeDecision) -> bool:
        if not self.active_trade:
            return False
        trade = self.active_trade
        base_quantity = max(int(trade.base_quantity or trade.quantity or 1), 1)
        add_quantity = base_quantity
        transaction_type = "BUY" if self._is_long_trade_direction(trade.direction) else "SELL"
        correlation_id = f"add-{trade.trade_id}-{trade.pyramid_count + 1}"
        try:
            result = self._place_market_order_for_trade(
                trade=trade,
                transaction_type=transaction_type,
                quantity=add_quantity,
                correlation_id=correlation_id,
            )
        except (DhanExecutionError, ZerodhaExecutionError) as exc:
            error_message = str(exc)
            self._record_execution_feedback_locked(
                symbol=trade.symbol,
                message=f"Live add failed for {trade.symbol}: {error_message}",
                error=error_message,
            )
            self.rulebook_service.learning_log.insert(0, f"Live add failed for {trade.symbol}: {error_message}")
            return False
        if not result.ok:
            self._record_execution_feedback_locked(
                symbol=trade.symbol,
                message=f"Live add rejected for {trade.symbol}: {result.message}",
                error=result.message,
            )
            self.rulebook_service.learning_log.insert(0, f"Live add rejected for {trade.symbol}: {result.message}")
            return False
        trade.broker_status = result.order_status or "PENDING"
        trade.broker_status_message = result.message
        success_message = f"Live pyramiding add order sent for {trade.symbol} with qty {add_quantity}."
        self._record_execution_feedback_locked(symbol=trade.symbol, message=success_message)
        self.rulebook_service.learning_log.insert(0, success_message)
        return self._add_to_active_trade(
            current_candle,
            decision.reason,
            quantity=add_quantity,
            add_invalidation_level=decision.invalidation_level,
            broker_order_id=result.order_id,
            broker_entry_correlation_id=correlation_id,
            broker_status=result.order_status or "PENDING",
            broker_status_message=result.message,
        )

    def _square_off_active_trade_locked(self, client_id: str, access_token: str, *, reason: str) -> bool:
        if self.active_trade is None:
            return False
        return self._exit_live_trade(
            Candle(
                timestamp=datetime.now(),
                open=self.active_trade.current_price,
                high=self.active_trade.current_price,
                low=self.active_trade.current_price,
                close=self.active_trade.current_price,
                volume=0.0,
            ),
            reason,
            quantity=self.active_trade.open_quantity if self.active_trade.open_quantity is not None else self.active_trade.quantity,
        )

    def apply_trade_logic(self, current_candle: Candle, decision: TradeDecision, *, source: str = "manual") -> None:
        if self._stock_pre_arm_paper_execution_disabled(source):
            return

        if self.active_trade:
            if self.active_trade.current_quote_source == "simulated":
                self.active_trade.current_price = self.current_trade_market_price(current_candle.close, self.active_trade)
                self.active_trade.current_option_price = self.active_trade.current_price
                self.active_trade.current_quote_time = current_candle.timestamp
            self.active_trade.pnl = self.calculate_trade_pnl(self.active_trade, self.active_trade.current_price)

        if decision.action in {TradeAction.enter_call, TradeAction.enter_put}:
            if self.active_trade:
                return
            try:
                trade = self._build_entry_trade(current_candle, decision, source=source)
            except ValueError as exc:
                message = str(exc)
                self._record_execution_feedback_locked(
                    symbol=self.instrument_spec.symbol,
                    message=f"Live entry skipped: {message}",
                    error=message,
                )
                self.rulebook_service.learning_log.insert(0, f"Live entry skipped: {message}")
                self._mark_state_dirty_locked()
                return
            if self._should_send_live_orders(source):
                self._enter_live_trade(current_candle, decision, trade)
            else:
                self._finalize_open_trade(current_candle, trade, decision.reason)
            return

        if not self.active_trade:
            return

        if decision.action == TradeAction.add_position:
            if self._stock_derivative_execution_mode_enabled():
                self.rulebook_service.learning_log.insert(0, "Pyramiding add ignored because Stock F&O execution mode is enabled.")
                self._mark_state_dirty_locked()
                return
            if self._should_send_live_orders(source):
                self._add_live_trade(current_candle, decision)
            else:
                self._add_to_active_trade(
                    current_candle,
                    decision.reason,
                    quantity=decision.add_quantity,
                    add_invalidation_level=decision.invalidation_level,
                )
            return

        if decision.action == TradeAction.exit_pyramid_leg:
            if self._should_send_live_orders(source):
                self._exit_live_trade(
                    current_candle,
                    decision.reason,
                    quantity=decision.partial_exit_quantity,
                    pyramid_leg_ids=decision.pyramid_leg_ids,
                )
            else:
                self._close_pyramid_legs(current_candle, decision.reason, decision.pyramid_leg_ids)
            return

        if decision.action == TradeAction.update_stop:
            next_stop, _ = self._update_trade_level_from_structure(self.active_trade, current_candle.close, decision)
            if next_stop is None:
                return
            next_stop = round(next_stop, 2)
            if self._is_short_trade_direction(self.active_trade.direction):
                next_stop = min(self.active_trade.stop_price, next_stop)
            else:
                next_stop = max(self.active_trade.stop_price, next_stop)
            self.active_trade.stop_price = next_stop
            self.active_trade.stop_option_price = next_stop
            if decision.invalidation_level is not None:
                self.active_trade.invalidation_level = round(decision.invalidation_level, 2)
            self.active_trade.notes = decision.reason
            return

        if decision.action == TradeAction.update_target:
            _, next_target = self._update_trade_level_from_structure(self.active_trade, current_candle.close, decision)
            if next_target is None:
                return
            next_target = round(next_target, 2)
            if self._is_short_trade_direction(self.active_trade.direction):
                next_target = min(self.active_trade.target_price, next_target)
            else:
                next_target = max(self.active_trade.target_price, next_target)
            self.active_trade.target_price = next_target
            self.active_trade.target_option_price = next_target
            if decision.target_spot_price is not None:
                self.active_trade.target_spot_price = round(decision.target_spot_price, 2)
            self.active_trade.notes = decision.reason
            return

        if decision.action == TradeAction.partial_exit:
            if self._should_send_live_orders(source):
                self._exit_live_trade(current_candle, decision.reason, quantity=decision.partial_exit_quantity)
            else:
                self.partial_exit_active_trade(current_candle, decision.reason, quantity=decision.partial_exit_quantity)
            return

        if decision.action == TradeAction.exit:
            exited = False
            if self._should_send_live_orders(source):
                exited = self._exit_live_trade(current_candle, decision.reason)
            else:
                exit_candle = current_candle
                if (
                    decision.target_spot_price is not None
                    and self.normalize_pending_setup_action(decision.pending_setup_action) == "NONE"
                ):
                    exit_spot = round(decision.target_spot_price, 2)
                    exit_candle = Candle(
                        timestamp=current_candle.timestamp,
                        open=exit_spot,
                        high=max(current_candle.high, exit_spot),
                        low=min(current_candle.low, exit_spot),
                        close=exit_spot,
                        volume=current_candle.volume,
                    )
                self.close_active_trade(exit_candle, decision.reason)
                exited = True
            if exited and self.normalize_pending_setup_action(decision.pending_setup_action) in {"ARM", "REPLACE", "KEEP"}:
                self._trigger_pending_setup_after_exit(current_candle, source)

    def close_active_trade(self, candle: Candle, note: str) -> None:
        if not self.active_trade:
            return
        exit_quote = None
        if (
            self.active_trade.price_mode == "option"
            and self.active_trade.option_security_id
            and self.active_trade.broker_provider != "zerodha"
        ):
            client_id, access_token = self.credential_store.get_dhan_credentials(self.settings)
            if client_id and access_token:
                try:
                    exit_quote = self.option_quote_service.fetch_quote(
                        client_id=client_id,
                        access_token=access_token,
                        security_id=self.active_trade.option_security_id,
                        exchange_segment="NSE_FNO",
                        option_type=self.active_trade.option_type,
                        strike=self.active_trade.strike,
                    )
                except DhanOptionQuoteError:
                    exit_quote = None
        self.active_trade.exit_time = exit_quote.quote_time if exit_quote else candle.timestamp
        exit_price = (
            exit_quote.last_price
            if exit_quote
            else self.current_trade_market_price(candle.close, self.active_trade)
        )
        self.active_trade.exit_price = exit_price
        self.active_trade.exit_option_price = exit_price
        self.active_trade.current_price = exit_price
        self.active_trade.current_option_price = self.active_trade.exit_option_price
        self.active_trade.current_quote_source = exit_quote.source if exit_quote else self.active_trade.current_quote_source
        self.active_trade.current_quote_time = exit_quote.quote_time if exit_quote else self.active_trade.current_quote_time
        self.active_trade.exit_quote_source = exit_quote.source if exit_quote else self.active_trade.current_quote_source
        self.active_trade.exit_quote_time = exit_quote.quote_time if exit_quote else candle.timestamp
        remaining_quantity = self.active_trade.open_quantity if self.active_trade.open_quantity is not None else self.active_trade.quantity
        if self._is_short_trade_direction(self.active_trade.direction):
            self.active_trade.booked_pnl = round(
                self.active_trade.booked_pnl + ((self.active_trade.entry_price - exit_price) * remaining_quantity),
                2,
            )
        else:
            self.active_trade.booked_pnl = round(
                self.active_trade.booked_pnl + ((exit_price - self.active_trade.entry_price) * remaining_quantity),
                2,
            )
        self.active_trade.closed_quantity += remaining_quantity
        self.active_trade.open_quantity = 0
        for leg in self.active_trade.pyramid_legs:
            if leg.status == "OPEN":
                leg.status = "CLOSED"
                leg.exit_time = self.active_trade.exit_time
                leg.exit_price = exit_price
                leg.exit_spot_price = candle.close
                leg.open_quantity = 0
        self.active_trade.pnl = round(self.active_trade.booked_pnl, 2)
        self.active_trade.status = "CLOSED"
        self.active_trade.exit_notes = note
        self.active_trade.notes = note
        self.realized_pnl = round(self.realized_pnl + self.active_trade.pnl, 2)
        self.balance = round(self.settings.simulation_starting_balance + self.realized_pnl, 2)
        self.active_trade = None
        self._sync_active_trade_subscription_locked()
        self._mark_state_dirty_locked()

    def partial_exit_active_trade(self, candle: Candle, note: str, quantity: int | None = None) -> None:
        if not self.active_trade:
            return
        open_quantity = self.active_trade.open_quantity if self.active_trade.open_quantity is not None else self.active_trade.quantity
        if open_quantity <= 1:
            return
        exit_quantity = max(1, min(quantity or max(1, open_quantity // 2), open_quantity - 1))
        exit_price = self.current_trade_market_price(candle.close, self.active_trade)
        if self._is_short_trade_direction(self.active_trade.direction):
            booked_increment = (self.active_trade.entry_price - exit_price) * exit_quantity
        else:
            booked_increment = (exit_price - self.active_trade.entry_price) * exit_quantity
        self.active_trade.booked_pnl = round(self.active_trade.booked_pnl + booked_increment, 2)
        self.active_trade.open_quantity = open_quantity - exit_quantity
        self.active_trade.closed_quantity += exit_quantity
        self.active_trade.partial_exit_count += 1
        self.active_trade.last_partial_exit_time = candle.timestamp
        self.active_trade.current_price = exit_price
        self.active_trade.current_option_price = exit_price
        self.active_trade.current_quote_time = candle.timestamp
        self.active_trade.pnl = self.calculate_trade_pnl(self.active_trade, exit_price)
        self.active_trade.exit_notes = note
        self.active_trade.notes = note
        self._mark_state_dirty_locked()

    def select_itm_strike(self, spot: float, option_type: str) -> int:
        if option_type == "CE":
            return int(spot // 100) * 100
        return int(((spot + 99) // 100) * 100)

    def select_nifty_live_sell_strike(self, spot: float, option_type: str) -> int:
        if option_type == "CE":
            return int(math.floor(spot / 100.0) * 100 + 100)
        return int(math.ceil(spot / 100.0) * 100 - 100)

    def select_nifty_live_buy_strike(self, spot: float, option_type: str) -> int:
        if option_type == "CE":
            return int(math.ceil(spot / 100.0) * 100 - 100)
        return int(math.floor(spot / 100.0) * 100 + 100)

    def format_option_symbol(self, candle_time: datetime, strike: int, option_type: str) -> str:
        expiry = self.option_expiry_for_preference(candle_time.date(), self._nifty_expiry_preference())
        return f"NIFTY {expiry.strftime('%d%b%Y').upper()} {strike}{option_type}"

    def next_thursday(self, current_date) -> datetime.date:
        offset = (3 - current_date.weekday()) % 7
        return current_date + timedelta(days=offset)

    def option_expiry_for_preference(self, current_date: date, preference: str) -> date:
        current = self.next_thursday(current_date)
        if (preference or "").strip().lower() == "next-weekly":
            return current + timedelta(days=7)
        return current

    def price_option(self, spot: float, strike: int, option_type: str) -> float:
        intrinsic = max(spot - strike, 0) if option_type == "CE" else max(strike - spot, 0)
        distance = abs(spot - strike)
        extrinsic = max(42 - (distance * 0.12), 10)
        return round(intrinsic + extrinsic, 2)
