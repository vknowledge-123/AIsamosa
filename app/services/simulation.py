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
    OperationJobState,
    OperatingMode,
    PendingSetup,
    PreviousDayLevels,
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
from app.services.dhan_history import DhanChartService
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
from app.services.stock_universe import StockUniverseService


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
        self._runtime_dhan_client_id = ""
        self._runtime_dhan_access_token = ""
        self.live_trading_enabled = False
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

    def _stock_min_5m_turnover(self) -> float:
        return max(float(getattr(self.settings, "stock_min_5m_turnover", 30000000.0)), 0.0)

    def _nifty_expiry_preference(self) -> str:
        return self.credential_store.get_nifty_expiry_preference(self.settings)

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
    ) -> tuple[object, object]:
        def fetch(spec: InstrumentSpec):
            return self.chart_service.fetch_market_context(
                client_id=client_id,
                access_token=access_token,
                security_id=spec.security_id,
                exchange_segment=spec.exchange_segment,
                instrument_type=spec.instrument_type,
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
    ):
        companion_spec = self._active_companion_instrument_spec()
        if companion_spec is None:
            return None
        return self.chart_service.fetch_market_context(
            client_id=client_id,
            access_token=access_token,
            security_id=companion_spec.security_id,
            exchange_segment=companion_spec.exchange_segment,
            instrument_type=companion_spec.instrument_type,
            prefer_last_closed_session_before_open=True,
        )

    def _fetch_nifty_and_banknifty_historical_bundles(
        self,
        *,
        client_id: str,
        access_token: str,
        replay_session_day: date,
        previous_day: date,
    ) -> tuple[object, object]:
        def fetch(spec: InstrumentSpec):
            return self.chart_service.fetch_market_context_for_days(
                client_id=client_id,
                access_token=access_token,
                session_day=replay_session_day,
                previous_context_day=previous_day,
                security_id=spec.security_id,
                exchange_segment=spec.exchange_segment,
                instrument_type=spec.instrument_type,
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
    ):
        companion_spec = self._active_companion_instrument_spec()
        if companion_spec is None:
            return None
        return self.chart_service.fetch_market_context_for_days(
            client_id=client_id,
            access_token=access_token,
            session_day=replay_session_day,
            previous_context_day=previous_day,
            security_id=companion_spec.security_id,
            exchange_segment=companion_spec.exchange_segment,
            instrument_type=companion_spec.instrument_type,
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
            operator_zones=operator_zones,
            signal_events=signal_events,
            market_structure=self.describe_market_structure(
                session_candles=session_candles,
                previous_day_candles=previous_day_candles,
                previous_day=previous_day,
                live_current_candle=None,
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
            pyramiding_enabled=self.credential_store.get_pyramiding_enabled(self.settings),
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
        client, token = self._available_dhan_credentials()
        with self.lock:
            unique_symbols = [symbol for symbol in dict.fromkeys(symbols) if symbol]
            selected_symbol = self.selected_stock_symbol
            if selected_symbol:
                selected_spec = self.stock_watchlist.get(selected_symbol)
                status_text = (
                    "syncing"
                    if selected_spec and selected_spec.security_id and client and token
                    else ("queued" if selected_spec and selected_spec.security_id else "resolving")
                )
                self.stock_watch_meta.setdefault(selected_symbol, {})["history_status"] = status_text
                if not client or not token:
                    self._clear_active_session_locked()
                    self.data_sync = DataSyncState(
                        status="idle",
                        source="stock-watchlist",
                        message=(
                            f"{self.instrument_spec.label} is active now. Resolving its Dhan security in the background; "
                            "save credentials or press sync/connect to backfill 1-minute context."
                        ),
                    )
                else:
                    self.data_sync = DataSyncState(
                        status="syncing",
                        source="stock-watchlist",
                        message=f"Preparing {self.instrument_spec.label} in the background and syncing 1-minute context.",
                    )
                self._mark_state_dirty_locked()
        if not unique_symbols:
            return
        worker = threading.Thread(
            target=self._run_watchlist_prepare_batch,
            args=(unique_symbols, client or None, token or None),
            name=f"watchlist-prepare-{(selected_symbol or unique_symbols[0]).lower()}",
            daemon=True,
        )
        worker.start()

    def _run_watchlist_prepare_batch(self, symbols: list[str], client_id: str | None, access_token: str | None) -> None:
        unique_symbols = [symbol for symbol in dict.fromkeys(symbols) if symbol]
        if not unique_symbols:
            return
        with ThreadPoolExecutor(max_workers=self._max_stock_sync_workers(len(unique_symbols))) as executor:
            futures = [
                executor.submit(self._run_selected_stock_prepare, symbol, client_id, access_token)
                for symbol in unique_symbols
            ]
            for future in as_completed(futures):
                future.result()

    def _run_selected_stock_prepare(self, symbol: str, client_id: str | None, access_token: str | None) -> None:
        try:
            self._resolve_watchlist_symbol_if_needed(symbol)
            self._schedule_watchlist_subscription_refresh()
            if client_id and access_token:
                self._sync_stock_symbol_now(symbol, client_id=client_id, access_token=access_token)
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

    def _sync_stock_symbol_now(self, symbol: str, *, client_id: str, access_token: str) -> None:
        spec = self._resolve_watchlist_symbol_if_needed(symbol)
        if not spec.security_id:
            raise ValueError(f"Could not resolve a Dhan NSE cash security id for {symbol}.")
        bundle = self.chart_service.fetch_market_context(
            client_id=client_id,
            access_token=access_token,
            security_id=spec.security_id,
            exchange_segment=spec.exchange_segment,
            instrument_type=spec.instrument_type,
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
                    f"Synced Dhan chart history for {spec.label}: "
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
    ) -> None:
        def operation():
            with self.lock:
                result = self._load_dhan_bundle(bundle, replay_from_session_start=replay_from_session_start)
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

        self._run_in_stock_session(symbol, operation)

    def _build_stock_watchlist_state_locked(self) -> list[StockWatchItem]:
        items: list[StockWatchItem] = []
        selected_runtime_view = self._selected_runtime_session_view_locked()
        for symbol, spec in self.stock_watchlist.items():
            meta = self.stock_watch_meta.get(symbol, {})
            feedback = self._stock_execution_feedback.get(symbol, {})
            session = selected_runtime_view if symbol == self.selected_stock_symbol and selected_runtime_view is not None else self.stock_sessions.get(symbol)
            active_trade_pnl = self._trade_pnl_for_session_locked(session) if session is not None else None
            turnover_snapshot = self._stock_turnover_snapshot_for_session_locked(session)
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
                    subscribed=spec.security_id in self._stock_quote_subscriptions,
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

    def _build_watchlist_subscription_plan(self) -> tuple[DhanMarketFeedAdapter, dict[str, tuple], dict[str, tuple]] | None:
        with self.lock:
            if self.live_feed_adapter is None or self.instrument_mode != InstrumentMode.stock:
                return None
            adapter = self.live_feed_adapter
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
        client, token = self._available_dhan_credentials(client_id, access_token)
        if not client or not token:
            raise ValueError("Dhan client ID and access token are required to start the live feed")
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
            self.live_feed = self._build_live_feed_state(
                connected=False,
                status="connecting",
                source="dhan-websocket",
                status_message="Connecting to Dhan market feed.",
            )
            self._live_cumulative_volume_by_security_id.clear()
            if self.instrument_mode == InstrumentMode.stock:
                resolved_specs = [
                    spec
                    for spec in self.stock_watchlist.values()
                    if spec.security_id
                ]
                instruments = [
                    resolve_quote_subscription(spec.security_id, spec.exchange_segment)
                    for spec in resolved_specs
                ]
                companion_spec = self._active_companion_instrument_spec()
                if companion_spec is not None and companion_spec.security_id:
                    instruments.append(
                        resolve_quote_subscription(companion_spec.security_id, companion_spec.exchange_segment)
                    )
                if not instruments:
                    raise ValueError("No stock in the watchlist has a resolved Dhan security id yet. Search once or wait a moment, then retry.")
            else:
                instruments = [resolve_quote_subscription(self.instrument_spec.security_id, self.instrument_spec.exchange_segment)]
                if self._use_banknifty_companion():
                    instruments.append(
                        resolve_quote_subscription(
                            self.companion_instrument_spec.security_id,
                            self.companion_instrument_spec.exchange_segment,
                        )
                    )
            self.live_feed_adapter = DhanMarketFeedAdapter(client, token, instruments)
            self._stock_quote_subscriptions = {}
            if self.instrument_mode == InstrumentMode.stock:
                for spec, subscription in zip(resolved_specs, instruments):
                    self._stock_quote_subscriptions[spec.security_id] = subscription
            self.live_feed_adapter.start(self.handle_live_packet, self.handle_live_status)
            self._sync_active_trade_subscription_locked()
            self.rulebook_service.learning_log.insert(
                0,
                (
                    "Started Dhan live feed for "
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
        client_id, access_token = self._available_dhan_credentials()
        if not client_id or not access_token:
            raise ValueError("Saved or runtime Dhan credentials are required before starting live trading.")
        with self.lock:
            if self.live_feed_adapter is None:
                raise ValueError("Connect the Dhan live feed before starting live trading.")
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
            self.execution_state.live_trading_enabled = True
            self.execution_state.last_order_message = "Live heuristic execution is armed."
            self._mark_state_dirty_locked()
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
                    "Started live heuristic execution. New real orders will only be placed from realtime Dhan "
                    "websocket evaluations, not from sync or replay actions."
                ),
            )
            self._mark_state_dirty_locked()
            return self.get_state()

    def square_off_all_trades(self) -> DashboardState:
        client_id, access_token = self._available_dhan_credentials()
        with self.lock:
            self.live_trading_enabled = False
            self.execution_state.live_trading_enabled = False
            self.execution_state.last_order_message = "Square off requested. Live heuristic execution is disarmed."
            self._mark_state_dirty_locked()
        squared_off = 0
        if client_id and access_token:
            if self.instrument_mode == InstrumentMode.stock:
                watched_symbols = list(self.stock_watchlist.keys())
                selected = self.selected_stock_symbol
                for symbol in watched_symbols:
                    def operation():
                        return self._square_off_active_trade_locked(client_id, access_token, reason="Manual square off button pressed.")
                    if self._run_in_stock_session(symbol, operation):
                        squared_off += 1
                if selected:
                    with self.lock:
                        self._load_stock_session_locked(selected)
            else:
                if self._square_off_active_trade_locked(client_id, access_token, reason="Manual square off button pressed."):
                    squared_off += 1
        self._stop_order_updates()
        with self.lock:
            self.rulebook_service.learning_log.insert(
                0,
                (
                    f"Square off requested. Closed {squared_off} tracked trade(s) and stopped live heuristic execution."
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
    ) -> dict[str, object]:
        def fetch(symbol: str):
            spec = self._resolve_watchlist_symbol_if_needed(symbol)
            bundle = self.chart_service.fetch_market_context(
                client_id=client_id,
                access_token=access_token,
                security_id=spec.security_id,
                exchange_segment=spec.exchange_segment,
                instrument_type=spec.instrument_type,
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
    ) -> dict[str, object]:
        def fetch(symbol: str):
            spec = self._resolve_watchlist_symbol_if_needed(symbol)
            bundle = self.chart_service.fetch_market_context_for_days(
                client_id=client_id,
                access_token=access_token,
                session_day=replay_session_day,
                previous_context_day=previous_day,
                security_id=spec.security_id,
                exchange_segment=spec.exchange_segment,
                instrument_type=spec.instrument_type,
            )
            return symbol, spec, bundle

        bundles: dict[str, object] = {}
        with ThreadPoolExecutor(max_workers=self._max_stock_sync_workers(len(symbols))) as executor:
            futures = [executor.submit(fetch, symbol) for symbol in symbols]
            for future in as_completed(futures):
                symbol, spec, bundle = future.result()
                bundles[symbol] = (spec, bundle)
        return bundles

    def _apply_order_update_to_trade_locked(self, trade: SimulatedTrade | None, payload: dict) -> None:
        if trade is None:
            return
        order_id = str(payload.get("OrderNo") or payload.get("orderNo") or payload.get("orderId") or "").strip()
        if not order_id:
            return
        if order_id not in {trade.broker_order_id, trade.broker_exit_order_id}:
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
        client, token = self._available_dhan_credentials(client_id, access_token)
        if not client or not token:
            raise ValueError("Dhan client ID and access token are required to fetch chart history")
        self._remember_dhan_credentials(client, token)
        started_at = datetime.now()
        job_id = uuid.uuid4().hex
        with self.lock:
            self._ensure_no_running_operation_locked()
            self._operation_job_token = job_id
            self._set_operation_job_locked(
                job_id=job_id,
                job_type="sync-history",
                status="running",
                message=f"Syncing Dhan 1-minute context for {self.instrument_spec.label} in the background.",
                started_at=started_at,
            )
            self.data_sync = DataSyncState(
                status="syncing",
                source="dhan-rest",
                message=f"Background sync started for {self.instrument_spec.label}.",
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
                "success_message": f"Background Dhan sync completed for {self.instrument_spec.label}.",
                "error_prefix": "Background Dhan sync failed",
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
        client, token = self._available_dhan_credentials(client_id, access_token)
        if not client or not token:
            raise ValueError("Dhan client ID and access token are required to simulate today data")
        self._remember_dhan_credentials(client, token)
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
                source="dhan-rest",
                message=f"Background today replay started for {scope_label}.",
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
                "success_message": f"Background today replay completed for {scope_label}.",
                "error_prefix": "Background today replay failed",
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
        client, token = self._available_dhan_credentials(client_id, access_token)
        if not client or not token:
            raise ValueError("Dhan client ID and access token are required to simulate historical data")
        replay_session_day = self._parse_replay_date(replay_date, field_name="replay_date")
        previous_day = self._parse_replay_date(previous_context_date, field_name="previous_context_date")
        if previous_day >= replay_session_day:
            raise ValueError("Previous context day must be earlier than the replay session day.")
        self._remember_dhan_credentials(client, token)
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
                source="dhan-rest",
                message=(
                    f"Background historical replay started for {scope_label}: "
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
                    f"Background historical replay completed for {scope_label}: "
                    f"{replay_session_day.isoformat()}."
                ),
                "error_prefix": "Background historical replay failed",
            },
            name="simulate-historical-job",
            daemon=True,
        )
        worker.start()
        return self.get_state()

    def sync_dhan_context(self, client_id: str | None = None, access_token: str | None = None) -> DashboardState:
        client, token = self._available_dhan_credentials(client_id, access_token)
        if not client or not token:
            raise ValueError("Dhan client ID and access token are required to fetch chart history")
        self._remember_dhan_credentials(client, token)

        if self.instrument_mode == InstrumentMode.stock:
            watched_symbols = list(self.stock_watchlist.keys())
            selected = self.selected_stock_symbol or (watched_symbols[0] if watched_symbols else None)
            bundles = self._fetch_stock_market_context_bundles(
                watched_symbols,
                client_id=client,
                access_token=token,
            )
            companion_bundle = self._fetch_companion_market_context_bundle(
                client_id=client,
                access_token=token,
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
                            f"Synced Dhan chart history for {spec.label}: "
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
            )
            with self.lock:
                evaluation_index = self._load_dhan_bundle(bundle)
                self._load_companion_bundle_locked(companion_bundle)
                self.rulebook_service.learning_log.insert(
                    0,
                    (
                        f"Synced Dhan chart history for {self.instrument_spec.label} with Bank Nifty confirmation: "
                        f"{len(bundle.previous_day_candles)} Nifty previous-day candles, {len(bundle.intraday_candles)} Nifty intraday candles, "
                        f"{len(companion_bundle.previous_day_candles)} Bank Nifty previous-day candles, and "
                        f"{len(companion_bundle.intraday_candles)} Bank Nifty intraday candles."
                    ),
                )
                self._mark_state_dirty_locked()
            if evaluation_index is not None:
                self._evaluate_index(evaluation_index, source="sync")
            return self.get_state()

        bundle = self.chart_service.fetch_market_context(
            client_id=client,
            access_token=token,
            security_id=self.instrument_spec.security_id,
            exchange_segment=self.instrument_spec.exchange_segment,
            instrument_type=self.instrument_spec.instrument_type,
            prefer_last_closed_session_before_open=True,
        )
        with self.lock:
            evaluation_index = self._load_dhan_bundle(bundle)
            self.rulebook_service.learning_log.insert(
                0,
                (
                    f"Synced Dhan chart history for {self.instrument_spec.label}: "
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
        client, token = self._available_dhan_credentials(client_id, access_token)
        if not client or not token:
            raise ValueError("Dhan client ID and access token are required to simulate today data")
        self._remember_dhan_credentials(client, token)
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
            )
            companion_bundle = self._fetch_companion_market_context_bundle(
                client_id=client,
                access_token=token,
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
            )
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
            return self.get_state()

        bundle = self.chart_service.fetch_market_context(
            client_id=client,
            access_token=token,
            security_id=self.instrument_spec.security_id,
            exchange_segment=self.instrument_spec.exchange_segment,
            instrument_type=self.instrument_spec.instrument_type,
            prefer_last_closed_session_before_open=True,
        )
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
        client, token = self._available_dhan_credentials(client_id, access_token)
        if not client or not token:
            raise ValueError("Dhan client ID and access token are required to simulate historical data")
        replay_session_day = self._parse_replay_date(replay_date, field_name="replay_date")
        previous_day = self._parse_replay_date(previous_context_date, field_name="previous_context_date")
        if previous_day >= replay_session_day:
            raise ValueError("Previous context day must be earlier than the replay session day.")
        self._remember_dhan_credentials(client, token)
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
            )
            companion_bundle = self._fetch_companion_historical_bundle(
                client_id=client,
                access_token=token,
                replay_session_day=replay_session_day,
                previous_day=previous_day,
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
            )
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
            return self.get_state()

        bundle = self.chart_service.fetch_market_context_for_days(
            client_id=client,
            access_token=token,
            session_day=replay_session_day,
            previous_context_day=previous_day,
            security_id=self.instrument_spec.security_id,
            exchange_segment=self.instrument_spec.exchange_segment,
            instrument_type=self.instrument_spec.instrument_type,
        )
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
        return self.get_state()

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
        openai_api_key: str | None = None,
        openai_model: str | None = None,
        deepseek_api_key: str | None = None,
        deepseek_model: str | None = None,
        full_ai_provider: str | None = None,
        operating_mode: str | None = None,
        nifty_order_lots: int | None = None,
        stock_trade_capital: float | None = None,
        nifty_expiry_preference: str | None = None,
        stock_partial_profit_enabled: bool | None = None,
        stock_trailing_stop_enabled: bool | None = None,
        stock_heuristic_early_exit_enabled: bool | None = None,
        pyramiding_enabled: bool | None = None,
    ) -> DashboardState:
        with self.lock:
            self.credential_store.save(
                client_id=client_id,
                access_token=access_token,
                openai_api_key=openai_api_key,
                openai_model=openai_model,
                deepseek_api_key=deepseek_api_key,
                deepseek_model=deepseek_model,
                full_ai_provider=full_ai_provider,
                operating_mode=operating_mode,
                nifty_order_lots=nifty_order_lots,
                stock_trade_capital=stock_trade_capital,
                nifty_expiry_preference=nifty_expiry_preference,
                stock_partial_profit_enabled=stock_partial_profit_enabled,
                stock_trailing_stop_enabled=stock_trailing_stop_enabled,
                stock_heuristic_early_exit_enabled=stock_heuristic_early_exit_enabled,
                pyramiding_enabled=pyramiding_enabled,
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
        self.decision = None
        self.realized_pnl = 0.0
        self.balance = self.settings.simulation_starting_balance
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
        self.data_sync = DataSyncState(
            status="ready",
            source="dhan-rest",
            message=(
                f"Loaded Dhan 1-minute context for {self.instrument_spec.label} using {intraday_source_text} data "
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
            operator_zones=operator_zones,
            signal_events=signal_events,
            market_structure=self.describe_market_structure(
                session_candles=session_candles,
                previous_day_candles=previous_day_candles,
                previous_day=previous_day,
                live_current_candle=self.live_current_candle,
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
            pyramiding_enabled=self.credential_store.get_pyramiding_enabled(self.settings),
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
            trade_history = [trade.model_copy(deep=True) for trade in reversed(self.trade_history)]
            signal_history = [event.model_copy(deep=True) for event in reversed(self.signal_history)]
            heuristic_trace = self.heuristic_engine.trace_snapshot()
            heuristic_narrative = self.heuristic_engine.narrative_snapshot()
            live_feed = self.live_feed.model_copy(deep=True)
            execution = self.execution_state.model_copy(deep=True)
            data_sync = self.data_sync.model_copy(deep=True)
            operation_job = self.operation_job.model_copy(deep=True)
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
        operator_zones = self.find_operator_zones(state_context_candles) if latest_candle else []
        signal_events = self.detect_signal_events(latest_candle, liquidity_zones, previous_day) if latest_candle else []
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
            operator_zones=operator_zones,
            signal_events=signal_events,
            signal_history=signal_history,
            heuristic_trace=heuristic_trace,
            heuristic_narrative=heuristic_narrative,
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
            self.live_feed.source = "dhan-websocket"
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
        self._live_evaluation_queue.put((symbol, evaluation_index))

    def _handle_live_packet_now(self, packet: dict) -> None:
        evaluation_index = None
        evaluation_symbol: str | None = None
        hard_stop_check: tuple[float, datetime] | None = None
        with self.lock:
            security_id = str(packet.get("security_id", ""))
            ltp = self._as_float(packet.get("LTP"))
            if ltp is None:
                return
            tick_time = self._packet_timestamp(packet)
            raw_volume = self._as_float(packet.get("volume")) or 0.0
            volume_delta = self._live_volume_delta_locked(security_id, tick_time, raw_volume)
            packet_type = str(packet.get("type", "Unknown"))
            if self.active_trade and self.active_trade.option_security_id and security_id == self.active_trade.option_security_id:
                self._update_active_trade_quote_locked(
                    OptionQuote(
                        security_id=security_id,
                        option_type=self.active_trade.option_type,
                        strike=self.active_trade.strike,
                        last_price=ltp,
                        quote_time=tick_time,
                        source="dhan-websocket",
                        volume=self._as_int(packet.get("volume")),
                        oi=self._as_int(packet.get("OI")),
                    )
                )
                return
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
                and self._live_ltp_crossed_invalidation(self.active_trade, ltp)
            ):
                hard_stop_check = (ltp, tick_time)
            self.live_feed.connected = True
            self.live_feed.status = "connected"
            self.live_feed.source = "dhan-websocket"
            self.live_feed.instrument_label = self.instrument_spec.label
            self.live_feed.security_id = str(packet.get("security_id", self.instrument_spec.security_id))
            self.live_feed.last_packet_type = packet_type
            self.live_feed.last_tick_at = tick_time
            self.live_feed.last_ltp = ltp
            self.live_feed.ticks_received += 1
            self.live_feed.error = None
            evaluation_index = self._update_live_candle_locked(tick_time, ltp, volume_delta)
            self._mark_state_dirty_locked()
        if hard_stop_check is not None:
            stop_ltp, stop_time = hard_stop_check
            if self._exit_active_trade_on_ltp_invalidation(stop_ltp, stop_time):
                return
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
            self._mark_state_dirty_locked()
            hard_stop_crossed = self._live_ltp_crossed_invalidation(session.active_trade, ltp)
        if hard_stop_crossed and self._exit_stock_session_on_ltp_invalidation(symbol, ltp, tick_time):
            return
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
                    trigger_decision = self._apply_stock_turnover_filter_locked(snapshot.current_candle, trigger_decision)
                    self.decision = trigger_decision
                    self._record_signal_events_locked(snapshot.signal_events)
                    self.apply_pending_setup_decision(snapshot.current_candle, trigger_decision)
                    if trigger_decision.action in {TradeAction.enter_call, TradeAction.enter_put}:
                        self.apply_trade_logic(snapshot.current_candle, trigger_decision, source=source)
                    self._mark_state_dirty_locked()
                    return
            heuristic_decision = self.heuristic_decision(snapshot)
            decision = self.ai_service.decide(snapshot, heuristic_decision, self.operating_mode)
            decision = self.normalize_trade_decision(decision, snapshot.active_trade)
            with self.lock:
                if evaluation_index >= len(self.candles):
                    return
                self.current_index = evaluation_index
                decision = self._apply_stock_trade_bias_filter_locked(decision)
                decision = self._apply_stock_turnover_filter_locked(snapshot.current_candle, decision)
                self.decision = decision
                self.apply_pending_setup_decision(snapshot.current_candle, decision)
                self._record_signal_events_locked(snapshot.signal_events)
                self.apply_trade_logic(snapshot.current_candle, decision, source=source)
                self._mark_state_dirty_locked()

    def _clear_live_packet_queue(self) -> None:
        with self.lock:
            self._pending_live_packets.clear()
            self._queued_live_packet_keys.clear()
        try:
            while True:
                self._live_packet_queue.get_nowait()
        except queue.Empty:
            return

    def _packet_timestamp(self, packet: dict) -> datetime:
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

    def describe_market_structure(
        self,
        *,
        session_candles: list[Candle],
        previous_day_candles: list[Candle],
        previous_day: PreviousDayLevels,
        live_current_candle: Candle | None,
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

    def _exit_active_trade_on_ltp_invalidation(self, ltp: float, tick_time: datetime) -> bool:
        with self.lock:
            trade = self.active_trade
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
        if self.active_trade and self.active_trade.option_security_id and self.active_trade.quote_exchange_segment:
            next_subscription = resolve_quote_subscription(
                self.active_trade.option_security_id,
                self.active_trade.quote_exchange_segment,
            )

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

    def _update_active_trade_quote_locked(self, quote: OptionQuote) -> None:
        if not self.active_trade:
            return
        self.active_trade.current_price = round(quote.last_price, 2)
        self.active_trade.current_option_price = round(quote.last_price, 2)
        self.active_trade.current_quote_source = quote.source
        self.active_trade.current_quote_time = quote.quote_time
        self.active_trade.pnl = self.calculate_trade_pnl(self.active_trade, self.active_trade.current_price)
        self._mark_state_dirty_locked()

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

    def _use_spot_pricing_for_source(self, source: str) -> bool:
        return source == "replay" and self.instrument_spec.supports_options

    def _use_nifty_live_option_selling(self, source: str) -> bool:
        return (
            source == "live"
            and self.instrument_mode == InstrumentMode.nifty
            and self.instrument_spec.symbol == "NIFTY"
            and self.instrument_spec.supports_options
        )

    def _nifty_sell_option_type_for_signal(self, signal_option_type: str) -> str:
        return "PE" if signal_option_type == "CE" else "CE"

    def _nifty_short_direction_for_option(self, option_type: str) -> str:
        return "SHORT_CALL" if option_type == "CE" else "SHORT_PUT"

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
        option_type = signal_option_type
        is_option_trade = self.instrument_spec.supports_options and not self._use_spot_pricing_for_source(source)
        use_option_selling = is_option_trade and self._use_nifty_live_option_selling(source)
        if use_option_selling:
            option_type = self._nifty_sell_option_type_for_signal(signal_option_type)
            strike = self.select_nifty_live_sell_strike(current_candle.close, option_type)
        else:
            strike = decision.strike or (self.select_itm_strike(current_candle.close, option_type) if is_option_trade else 0)
        contract = None
        entry_quote = None
        quantity = self._resolve_trade_quantity(current_candle.close, is_option_trade)
        if is_option_trade:
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
            trade_symbol = (
                f"{self.instrument_spec.symbol} SPOT" if self.instrument_spec.supports_options else f"{self.instrument_spec.symbol} EQ"
            )
            direction = "LONG_STOCK" if option_type == "CE" else "SHORT_STOCK"
            trade_security_id = self.instrument_spec.security_id
            quote_exchange_segment = self.instrument_spec.exchange_segment
            stop_price = round(decision.invalidation_level or decision.stop_option_price or entry_price, 2)
            target_price = round(decision.target_spot_price or decision.target_option_price or entry_price, 2)
        entry_time = entry_quote.quote_time if entry_quote else current_candle.timestamp
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
        )

    def _finalize_open_trade(self, current_candle: Candle, trade: SimulatedTrade, reason: str) -> None:
        self.active_trade = trade
        if self.pending_setup is not None:
            self._consume_pending_setup_locked(current_candle, reason, trade.trade_id)
        self.trade_history.append(trade)
        self._sync_active_trade_subscription_locked()
        self._mark_state_dirty_locked()

    def _enter_live_trade(self, current_candle: Candle, decision: TradeDecision, trade: SimulatedTrade) -> None:
        client_id, access_token = self._available_dhan_credentials()
        if not client_id or not access_token:
            message = "Live order skipped because Dhan credentials are unavailable."
            self._record_execution_feedback_locked(symbol=trade.symbol, message=message, error=message)
            self.rulebook_service.learning_log.insert(0, message)
            return
        security_id = trade.option_security_id if trade.price_mode == "option" else trade.trade_security_id
        exchange_segment = trade.quote_exchange_segment or self.instrument_spec.exchange_segment
        if not security_id or not exchange_segment:
            message = "Live order skipped because the execution contract could not be resolved."
            self._record_execution_feedback_locked(symbol=trade.symbol, message=message, error=message)
            self.rulebook_service.learning_log.insert(0, message)
            return
        transaction_type = "BUY" if self._is_long_trade_direction(trade.direction) else "SELL"
        correlation_id = f"entry-{trade.trade_id}"
        try:
            result = self.execution_service.place_market_order(
                client_id=client_id,
                access_token=access_token,
                security_id=security_id,
                exchange_segment=exchange_segment,
                transaction_type=transaction_type,
                quantity=trade.quantity,
                product_type=trade.broker_product_type or "INTRADAY",
                correlation_id=correlation_id,
            )
        except DhanExecutionError as exc:
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
        trade.entry_quote_source = "dhan-market-order"
        trade.current_quote_source = "dhan-market-order"
        success_message = f"Live entry order sent for {trade.symbol} with qty {trade.quantity}."
        self._record_execution_feedback_locked(symbol=trade.symbol, message=success_message)
        self.rulebook_service.learning_log.insert(0, success_message)
        self._finalize_open_trade(current_candle, trade, decision.reason)

    def _exit_live_trade(self, current_candle: Candle, note: str, quantity: int | None = None) -> bool:
        if not self.active_trade:
            return False
        trade = self.active_trade
        client_id, access_token = self._available_dhan_credentials()
        if not client_id or not access_token:
            message = "Square off or exit skipped because Dhan credentials are unavailable."
            self._record_execution_feedback_locked(symbol=trade.symbol, message=message, error=message)
            self.rulebook_service.learning_log.insert(0, message)
            return False
        open_quantity = trade.open_quantity if trade.open_quantity is not None else trade.quantity
        exit_quantity = open_quantity if quantity is None else max(1, min(quantity, open_quantity))
        security_id = trade.option_security_id if trade.price_mode == "option" else trade.trade_security_id
        exchange_segment = trade.quote_exchange_segment or self.instrument_spec.exchange_segment
        if not security_id or not exchange_segment:
            message = "Exit skipped because the execution contract could not be resolved."
            self._record_execution_feedback_locked(symbol=trade.symbol, message=message, error=message)
            self.rulebook_service.learning_log.insert(0, message)
            return False
        transaction_type = "SELL" if self._is_long_trade_direction(trade.direction) else "BUY"
        correlation_id = f"exit-{trade.trade_id}-{trade.partial_exit_count + 1}"
        try:
            result = self.execution_service.place_market_order(
                client_id=client_id,
                access_token=access_token,
                security_id=security_id,
                exchange_segment=exchange_segment,
                transaction_type=transaction_type,
                quantity=exit_quantity,
                product_type=trade.broker_product_type or "INTRADAY",
                correlation_id=correlation_id,
            )
        except DhanExecutionError as exc:
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
        if exit_quantity >= open_quantity:
            self.close_active_trade(current_candle, note)
        else:
            self.partial_exit_active_trade(current_candle, note, quantity=exit_quantity)
        return True

    def _add_to_active_trade(self, current_candle: Candle, note: str, quantity: int | None = None) -> bool:
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
        trade.current_price = add_price
        trade.current_option_price = add_price
        trade.current_quote_time = current_candle.timestamp
        trade.pnl = self.calculate_trade_pnl(trade, add_price)
        trade.notes = note
        if note:
            trade.entry_notes = f"{trade.entry_notes} Pyramiding add {trade.pyramid_count}: {note}".strip()
        self._mark_state_dirty_locked()
        return True

    def _add_live_trade(self, current_candle: Candle, decision: TradeDecision) -> bool:
        if not self.active_trade:
            return False
        trade = self.active_trade
        client_id, access_token = self._available_dhan_credentials()
        if not client_id or not access_token:
            message = "Live add skipped because Dhan credentials are unavailable."
            self._record_execution_feedback_locked(symbol=trade.symbol, message=message, error=message)
            self.rulebook_service.learning_log.insert(0, message)
            return False
        security_id = trade.option_security_id if trade.price_mode == "option" else trade.trade_security_id
        exchange_segment = trade.quote_exchange_segment or self.instrument_spec.exchange_segment
        if not security_id or not exchange_segment:
            message = "Live add skipped because the execution contract could not be resolved."
            self._record_execution_feedback_locked(symbol=trade.symbol, message=message, error=message)
            self.rulebook_service.learning_log.insert(0, message)
            return False
        base_quantity = max(int(trade.base_quantity or trade.quantity or 1), 1)
        add_quantity = base_quantity
        transaction_type = "BUY" if self._is_long_trade_direction(trade.direction) else "SELL"
        correlation_id = f"add-{trade.trade_id}-{trade.pyramid_count + 1}"
        try:
            result = self.execution_service.place_market_order(
                client_id=client_id,
                access_token=access_token,
                security_id=security_id,
                exchange_segment=exchange_segment,
                transaction_type=transaction_type,
                quantity=add_quantity,
                product_type=trade.broker_product_type or "INTRADAY",
                correlation_id=correlation_id,
            )
        except DhanExecutionError as exc:
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
        return self._add_to_active_trade(current_candle, decision.reason, quantity=add_quantity)

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
            trade = self._build_entry_trade(current_candle, decision, source=source)
            if self._should_send_live_orders(source):
                self._enter_live_trade(current_candle, decision, trade)
            else:
                self._finalize_open_trade(current_candle, trade, decision.reason)
            return

        if not self.active_trade:
            return

        if decision.action == TradeAction.add_position:
            if self._should_send_live_orders(source):
                self._add_live_trade(current_candle, decision)
            else:
                self._add_to_active_trade(current_candle, decision.reason, quantity=decision.add_quantity)
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
            if self._should_send_live_orders(source):
                self._exit_live_trade(current_candle, decision.reason)
            else:
                self.close_active_trade(current_candle, decision.reason)

    def close_active_trade(self, candle: Candle, note: str) -> None:
        if not self.active_trade:
            return
        exit_quote = None
        if self.active_trade.price_mode == "option" and self.active_trade.option_security_id:
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
            return int(math.ceil(spot / 100.0) * 100 + 100)
        return int(math.floor(spot / 100.0) * 100 - 100)

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
