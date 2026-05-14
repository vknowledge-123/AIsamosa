from __future__ import annotations

import queue
import threading
import uuid
from datetime import datetime, timedelta

from app.config import Settings
from app.schemas import (
    Candle,
    CredentialSummary,
    DataSyncState,
    DashboardState,
    FullAIProvider,
    InstrumentMode,
    LiveFeedState,
    OperatingMode,
    PendingSetup,
    PreviousDayLevels,
    RulebookJobState,
    SignalEvent,
    SimulatedTrade,
    StrategyContext,
    TradeAction,
    TradeDecision,
    Zone,
)
from app.services.ai_service import AIDecisionService
from app.services.credential_store import CredentialStore
from app.services.dhan_adapter import DhanMarketFeedAdapter, resolve_quote_subscription
from app.services.dhan_history import DhanChartService
from app.services.instruments import InstrumentSpec, get_instrument_spec
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


class SimulationEngine:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.lock = threading.RLock()
        self.rulebook_service = RulebookService()
        self.credential_store = CredentialStore()
        self.ai_service = AIDecisionService(settings)
        self.chart_service = DhanChartService()
        self.option_quote_service = DhanOptionQuoteService()
        self.heuristic_engine = HeuristicDecisionEngine()
        self._configure_ai_service()
        self.operating_mode = self.credential_store.get_operating_mode(settings)
        self.instrument_mode = InstrumentMode.nifty
        self.instrument_spec = get_instrument_spec(self.instrument_mode)
        self.live_feed_adapter: DhanMarketFeedAdapter | None = None
        self.live_feed = self._build_live_feed_state()
        self.live_current_candle: Candle | None = None
        self.data_sync = DataSyncState()
        self.rulebook_job = RulebookJobState()
        self._rulebook_job_token = ""
        self._evaluation_lock = threading.Lock()
        self._live_packet_queue: queue.Queue[dict | None] = queue.Queue()
        self._live_packet_worker = threading.Thread(
            target=self._run_live_packet_worker,
            name="live-packet-worker",
            daemon=True,
        )
        self._live_packet_worker.start()
        self.signal_history: list[SignalEvent] = []
        self._signal_history_keys: set[tuple[str, str, str]] = set()
        self._active_option_subscription: tuple | None = None
        self.pending_setup: PendingSetup | None = None
        self.reset_with_candles(generate_sample_candles())

    def _build_live_feed_state(
        self,
        *,
        connected: bool = False,
        status: str = "disconnected",
        source: str = "sample",
    ) -> LiveFeedState:
        return LiveFeedState(
            connected=connected,
            status=status,
            source=source,
            security_id=self.instrument_spec.security_id,
            instrument_label=self.instrument_spec.label,
        )

    def instrument_state(self):
        return self.instrument_spec.to_state(self.settings.simulation_lot_size)

    def set_instrument_mode(self, mode: InstrumentMode | str) -> DashboardState:
        normalized_mode = InstrumentMode(mode)
        adapter = None
        with self.lock:
            if normalized_mode == self.instrument_mode:
                return self.get_state()
            if self.live_feed_adapter is not None:
                adapter = self.live_feed_adapter
                self.live_feed_adapter = None
            self.instrument_mode = normalized_mode
            self.instrument_spec = get_instrument_spec(normalized_mode)
            self.live_feed = self._build_live_feed_state()
        if adapter is not None:
            adapter.stop()
        self._clear_live_packet_queue()
        self.heuristic_engine.reset_session()
        self.reset_with_candles(generate_sample_candles())
        with self.lock:
            self.rulebook_service.learning_log.insert(
                0,
                f"Switched active instrument to {self.instrument_spec.label} ({self.instrument_spec.security_id}).",
            )
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
            self._evaluate_index(evaluation_index)
        return self.get_state()

    def connect_live_feed(self, client_id: str | None = None, access_token: str | None = None) -> DashboardState:
        stored_client, stored_token = self.credential_store.get_dhan_credentials(self.settings)
        client = (client_id or stored_client or "").strip()
        token = (access_token or stored_token or "").strip()
        if not client or not token:
            raise ValueError("Dhan client ID and access token are required to start the live feed")

        with self.lock:
            if self.live_feed.connected and self.live_feed_adapter is not None:
                return self.get_state()

        self.sync_dhan_context(client_id=client, access_token=token)
        with self.lock:
            self.live_feed = self._build_live_feed_state(
                connected=False,
                status="connecting",
                source="dhan-websocket",
            )
            instruments = [resolve_quote_subscription(self.instrument_spec.security_id, self.instrument_spec.exchange_segment)]
            self.live_feed_adapter = DhanMarketFeedAdapter(client, token, instruments)
            self.live_feed_adapter.start(self.handle_live_packet, self.handle_live_status)
            self._sync_active_trade_subscription_locked()
            self.rulebook_service.learning_log.insert(
                0,
                (
                    "Started Dhan live feed for "
                    f"{self.instrument_spec.label} (security {self.instrument_spec.security_id}) in paper-trading mode."
                ),
            )
            return self.get_state()

    def sync_dhan_context(self, client_id: str | None = None, access_token: str | None = None) -> DashboardState:
        stored_client, stored_token = self.credential_store.get_dhan_credentials(self.settings)
        client = (client_id or stored_client or "").strip()
        token = (access_token or stored_token or "").strip()
        if not client or not token:
            raise ValueError("Dhan client ID and access token are required to fetch chart history")

        bundle = self.chart_service.fetch_market_context(
            client_id=client,
            access_token=token,
            security_id=self.instrument_spec.security_id,
            exchange_segment=self.instrument_spec.exchange_segment,
            instrument_type=self.instrument_spec.instrument_type,
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
        if evaluation_index is not None:
            self._evaluate_index(evaluation_index)
        return self.get_state()

    def simulate_today_session(self, client_id: str | None = None, access_token: str | None = None) -> DashboardState:
        stored_client, stored_token = self.credential_store.get_dhan_credentials(self.settings)
        client = (client_id or stored_client or "").strip()
        token = (access_token or stored_token or "").strip()
        if not client or not token:
            raise ValueError("Dhan client ID and access token are required to simulate today data")

        bundle = self.chart_service.fetch_market_context(
            client_id=client,
            access_token=token,
            security_id=self.instrument_spec.security_id,
            exchange_segment=self.instrument_spec.exchange_segment,
            instrument_type=self.instrument_spec.instrument_type,
        )
        with self.lock:
            intraday_count = len(bundle.intraday_candles)
            if intraday_count == 0:
                raise ValueError("No closed intraday candles were returned for today yet.")
            start_index, end_index = self._load_dhan_bundle(bundle, replay_from_session_start=True)
        for evaluation_index in range(start_index, end_index + 1):
            self._evaluate_index(evaluation_index)
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
        return self.get_state()

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
            )
            self._configure_ai_service()
            self.operating_mode = self.credential_store.get_operating_mode(self.settings)
            self.rulebook_service.learning_log.insert(
                0,
                (
                    "Saved Dhan, AI provider, and operating-mode settings locally. "
                    f"Active trading mode: {self.operating_mode.value}. "
                    f"Full AI provider: {self.credential_store.get_full_ai_provider(self.settings).value}."
                ),
            )
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

        self.data_sync = DataSyncState(
            status="ready",
            source="dhan-rest",
            message=(
                f"Loaded Dhan 1-minute context for {self.instrument_spec.label} using the intraday chart API for today "
                f"and {bundle.previous_day_source} data for the previous session."
            ),
            last_synced_at=datetime.now(),
            previous_day_candles=len(bundle.previous_day_candles),
            intraday_candles=len(bundle.intraday_candles),
            total_loaded=len(self.candles),
            has_live_open_candle=(bundle.live_open_candle is not None and not replay_from_session_start),
        )
        if replay_from_session_start:
            return previous_day_count, len(self.candles) - 1
        return evaluation_index

    def get_credential_summary(self) -> CredentialSummary:
        return self.credential_store.summary(self.settings)

    def disconnect_live_feed(self) -> DashboardState:
        adapter = None
        with self.lock:
            if self.live_feed_adapter is not None:
                adapter = self.live_feed_adapter
                self.live_feed_adapter = None
        if adapter is not None:
            adapter.stop()
        self._clear_live_packet_queue()
        with self.lock:
            self.live_feed.connected = False
            self.live_feed.status = "disconnected"
            self.live_feed.error = None
            self.live_feed.current_candle = self.live_current_candle
            self._active_option_subscription = None
            return self.get_state()

    def evaluate_current_candle(self) -> None:
        with self.lock:
            if self.current_index < 0:
                return
            evaluation_index = self.current_index
        self._evaluate_index(evaluation_index)

    def build_context(self) -> StrategyContext:
        current_candle = self.candles[self.current_index]
        session_candles = get_session_candles_up_to_index(self.candles, self.current_index)
        previous_day_candles = get_previous_day_candles(self.candles, self.current_index)
        recent_candles = session_candles[-20:]
        previous_day = calculate_previous_day_levels(self.candles, self.current_index)
        liquidity_zones = self.find_liquidity_zones(session_candles, previous_day)
        operator_zones = self.find_operator_zones(session_candles)
        signal_events = self.detect_signal_events(current_candle, liquidity_zones, previous_day)
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
            pending_setup=self.pending_setup,
            active_trade=self.active_trade,
            rulebook_markdown=self.rulebook_service.get_rulebook(),
        )

    def get_state(self) -> DashboardState:
        with self.lock:
            latest_closed = self.candles[self.current_index] if self.candles and self.current_index >= 0 else None
            latest_candle = self.live_current_candle or latest_closed
            recent_closed = self.candles[max(0, self.current_index - 39) : self.current_index + 1] if latest_closed else []
            recent_candles = list(recent_closed)
            session_candles = get_session_candles_up_to_index(self.candles, self.current_index) if latest_closed else []
            state_context_candles = list(session_candles)
            if self.live_current_candle is not None:
                if not recent_candles or recent_candles[-1].timestamp != self.live_current_candle.timestamp:
                    recent_candles.append(self.live_current_candle)
                else:
                    recent_candles[-1] = self.live_current_candle
                if not state_context_candles or state_context_candles[-1].timestamp != self.live_current_candle.timestamp:
                    state_context_candles.append(self.live_current_candle)
                else:
                    state_context_candles[-1] = self.live_current_candle
            previous_day = (
                calculate_previous_day_levels_for_timestamp(self.candles, latest_candle.timestamp)
                if latest_candle
                else PreviousDayLevels()
            )
            liquidity_zones = self.find_liquidity_zones(state_context_candles, previous_day) if latest_candle else []
            operator_zones = self.find_operator_zones(state_context_candles) if latest_candle else []
            signal_events = self.detect_signal_events(latest_candle, liquidity_zones, previous_day) if latest_candle else []
            self._record_signal_events_locked(signal_events)
            unrealized_pnl = self.active_trade.pnl if self.active_trade else 0.0
            display_decision = self.decision.model_copy(deep=True) if self.decision is not None else None
            if display_decision is not None and self.active_trade is None and display_decision.action == TradeAction.hold:
                display_decision.action = TradeAction.no_trade
                display_decision.reason = "No active paper trade is open."
            display_pending_setup = self.pending_setup
            if display_pending_setup is not None and display_pending_setup.status in {"consumed", "invalidated"}:
                display_pending_setup = None
            if self.active_trade and latest_candle and self.active_trade.current_quote_source == "simulated":
                simulated_current = self.current_trade_market_price(latest_candle.close, self.active_trade)
                self.active_trade.current_price = simulated_current
                self.active_trade.current_option_price = simulated_current
                self.active_trade.current_quote_time = latest_candle.timestamp
                self.active_trade.pnl = self.calculate_trade_pnl(self.active_trade, simulated_current)
                unrealized_pnl = self.active_trade.pnl
            self.live_feed.current_candle = self.live_current_candle
            return DashboardState(
                mode="live-paper" if self.live_feed.connected else "paper",
                instrument=self.instrument_state(),
                operating_mode=self.operating_mode,
                current_index=self.current_index,
                total_candles=len(self.candles),
                latest_candle=latest_candle,
                recent_candles=recent_candles,
                previous_day=previous_day,
                liquidity_zones=liquidity_zones,
                operator_zones=operator_zones,
                signal_events=signal_events,
                signal_history=list(reversed(self.signal_history)),
                heuristic_trace=self.heuristic_engine.trace_snapshot(),
                heuristic_narrative=self.heuristic_engine.narrative_snapshot(),
                pending_setup=display_pending_setup,
                decision=display_decision,
                active_trade=self.active_trade,
                trade_history=list(reversed(self.trade_history[-10:])),
                rulebook=self.rulebook_service.get_rulebook(),
                learning_log=self.rulebook_service.learning_log[:10],
                balance=self.balance,
                realized_pnl=self.realized_pnl,
                unrealized_pnl=unrealized_pnl,
                ai_enabled=self.ai_service.enabled,
                live_feed=self.live_feed,
                data_sync=self.data_sync,
                rulebook_job=self.rulebook_job,
                credentials=self.get_credential_summary(),
            )

    def handle_live_status(self, status: str, message: str | None) -> None:
        with self.lock:
            self.live_feed.status = status
            self.live_feed.connected = status == "connected"
            self.live_feed.source = "dhan-websocket"
            self.live_feed.instrument_label = self.instrument_spec.label
            self.live_feed.security_id = self.instrument_spec.security_id
            self.live_feed.error = message if status == "error" else None
            if status == "connected":
                self._sync_active_trade_subscription_locked()

    def handle_live_packet(self, packet: dict) -> None:
        self._live_packet_queue.put(packet)

    def _run_live_packet_worker(self) -> None:
        while True:
            packet = self._live_packet_queue.get()
            if packet is None:
                return
            try:
                self._handle_live_packet_now(packet)
            except Exception as exc:
                self.handle_live_status("error", str(exc))

    def _handle_live_packet_now(self, packet: dict) -> None:
        evaluation_index = None
        with self.lock:
            security_id = str(packet.get("security_id", ""))
            ltp = self._as_float(packet.get("LTP"))
            if ltp is None:
                return
            tick_time = self._packet_timestamp(packet)
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
            evaluation_index = self._update_live_candle_locked(tick_time, ltp, self._as_float(packet.get("volume")) or 0.0)
        if evaluation_index is not None:
            self._evaluate_index(evaluation_index)

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
            self.live_current_candle.volume = max(self.live_current_candle.volume, volume)
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

    def _evaluate_index(self, evaluation_index: int) -> None:
        with self._evaluation_lock:
            with self.lock:
                if evaluation_index < 0 or evaluation_index >= len(self.candles):
                    return
                self.current_index = evaluation_index
                self._clear_pending_setup_if_new_session_locked(self.candles[evaluation_index])
                snapshot = self.build_context()
                trigger_decision = self.evaluate_pending_setup_trigger(snapshot.current_candle)
                if trigger_decision is not None:
                    self.decision = trigger_decision
                    self._record_signal_events_locked(snapshot.signal_events)
                    self.apply_trade_logic(snapshot.current_candle, trigger_decision)
                    return
            heuristic_decision = self.heuristic_decision(snapshot)
            decision = self.ai_service.decide(snapshot, heuristic_decision, self.operating_mode)
            decision = self.normalize_trade_decision(decision, snapshot.active_trade)
            with self.lock:
                if evaluation_index >= len(self.candles):
                    return
                self.current_index = evaluation_index
                self.decision = decision
                self.apply_pending_setup_decision(snapshot.current_candle, decision)
                self._record_signal_events_locked(snapshot.signal_events)
                self.apply_trade_logic(snapshot.current_candle, decision)

    def _clear_live_packet_queue(self) -> None:
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

    def find_liquidity_zones(self, candles: list[Candle], previous_day: PreviousDayLevels) -> list[Zone]:
        if len(candles) < 5:
            return []
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
        mapped_buy, mapped_sell = self.heuristic_engine.build_liquidity_maps(candles, candles[-1].close, atr)
        extra_levels = mapped_buy + mapped_sell
        seen_labels = {zone.label for zone in zones}
        seen_price_families = [(zone.label.split()[0], zone.price) for zone in zones]
        zone_width = max(atr * 0.18, 0.5 if candles[-1].close < 500 else 2.0)
        for label, price, primary in extra_levels:
            label_family = label.split()[0]
            if label in seen_labels or any(
                existing_family == label_family and abs(existing_price - price) <= zone_width * 0.35
                for existing_family, existing_price in seen_price_families
            ):
                continue
            if "Round Number" in label:
                note = "Round-number shelf often becomes psychological SL-hunting liquidity."
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
        if active_trade is None and decision.action in {TradeAction.hold, TradeAction.exit, TradeAction.partial_exit, TradeAction.update_stop, TradeAction.update_target}:
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
        if active_trade and decision.action in {TradeAction.hold, TradeAction.exit, TradeAction.partial_exit, TradeAction.update_stop, TradeAction.update_target}:
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

    def calculate_trade_pnl(self, trade: SimulatedTrade, current_price: float) -> float:
        open_quantity = trade.open_quantity if trade.open_quantity is not None else trade.quantity
        if trade.direction == "SHORT_STOCK":
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
    ) -> tuple[float, float]:
        stop_price = decision.stop_option_price
        target_price = decision.target_option_price
        if decision.invalidation_level is not None:
            stop_price = self.price_option(decision.invalidation_level, strike, option_type)
        if decision.target_spot_price is not None:
            target_price = self.price_option(decision.target_spot_price, strike, option_type)
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

    def heuristic_decision(self, context: StrategyContext) -> TradeDecision:
        current_trade_price = None
        if context.active_trade is not None:
            current_trade_price = self.current_trade_market_price(context.current_candle.close, context.active_trade)
        decision = self.heuristic_engine.decide(context, current_trade_price=current_trade_price)
        decision.decision_source = "heuristic"
        if context.instrument.supports_options and decision.action in {TradeAction.enter_call, TradeAction.enter_put}:
            decision.strike = decision.strike or self.select_itm_strike(context.current_candle.close, decision.option_type or "CE")
        return decision

    def apply_trade_logic(self, current_candle: Candle, decision: TradeDecision) -> None:
        if self.active_trade:
            if self.active_trade.current_quote_source == "simulated":
                self.active_trade.current_price = self.current_trade_market_price(current_candle.close, self.active_trade)
                self.active_trade.current_option_price = self.active_trade.current_price
                self.active_trade.current_quote_time = current_candle.timestamp
            self.active_trade.pnl = self.calculate_trade_pnl(self.active_trade, self.active_trade.current_price)

        if decision.action in {TradeAction.enter_call, TradeAction.enter_put}:
            if self.active_trade:
                return
            option_type = self.normalize_option_type(decision.option_type, action=decision.action) or "CE"
            is_option_trade = self.instrument_spec.supports_options
            strike = decision.strike or (self.select_itm_strike(current_candle.close, option_type) if is_option_trade else 0)
            contract = None
            entry_quote = None
            if is_option_trade:
                contract = self._load_option_contract_from_dhan(
                    strike=strike,
                    option_type=option_type,
                    reference_time=current_candle.timestamp,
                )
                entry_quote = contract.quote if contract and contract.quote else None
                entry_price = entry_quote.last_price if entry_quote else self.price_option(current_candle.close, strike, option_type)
                trade_symbol = contract.symbol if contract else self.format_option_symbol(current_candle.timestamp, strike, option_type)
                direction = "LONG_CALL" if option_type == "CE" else "LONG_PUT"
                trade_security_id = contract.security_id if contract else None
                quote_exchange_segment = "NSE_FNO"
                stop_price, target_price = self.derive_option_trade_plan(
                    current_spot=current_candle.close,
                    strike=strike,
                    option_type=option_type,
                    decision=decision,
                    entry_price=entry_price,
                )
            else:
                entry_price = round(current_candle.close, 2)
                trade_symbol = f"{self.instrument_spec.symbol} EQ"
                direction = "LONG_STOCK" if option_type == "CE" else "SHORT_STOCK"
                trade_security_id = self.instrument_spec.security_id
                quote_exchange_segment = self.instrument_spec.exchange_segment
                stop_price = round(decision.invalidation_level or decision.stop_option_price or entry_price, 2)
                target_price = round(decision.target_spot_price or decision.target_option_price or entry_price, 2)
            entry_time = entry_quote.quote_time if entry_quote else current_candle.timestamp
            trade = SimulatedTrade(
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
                quantity=self.settings.simulation_lot_size,
                open_quantity=self.settings.simulation_lot_size,
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
                notes=decision.reason,
            )
            self.active_trade = trade
            if self.pending_setup is not None:
                self._consume_pending_setup_locked(current_candle, decision.reason, trade.trade_id)
            self.trade_history.append(trade)
            self._sync_active_trade_subscription_locked()
            return

        if not self.active_trade:
            return

        if decision.action == TradeAction.update_stop:
            next_stop, _ = self._update_trade_level_from_structure(self.active_trade, current_candle.close, decision)
            if next_stop is None:
                return
            next_stop = round(next_stop, 2)
            if self.active_trade.direction == "SHORT_STOCK":
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
            if self.active_trade.direction == "SHORT_STOCK":
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
            self.partial_exit_active_trade(current_candle, decision.reason, quantity=decision.partial_exit_quantity)
            return

        if decision.action == TradeAction.exit:
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
        if self.active_trade.direction == "SHORT_STOCK":
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
        self.active_trade.notes = note
        self.realized_pnl = round(self.realized_pnl + self.active_trade.pnl, 2)
        self.balance = round(self.settings.simulation_starting_balance + self.realized_pnl, 2)
        self.active_trade = None
        self._sync_active_trade_subscription_locked()

    def partial_exit_active_trade(self, candle: Candle, note: str, quantity: int | None = None) -> None:
        if not self.active_trade:
            return
        open_quantity = self.active_trade.open_quantity if self.active_trade.open_quantity is not None else self.active_trade.quantity
        if open_quantity <= 1:
            return
        exit_quantity = max(1, min(quantity or max(1, open_quantity // 2), open_quantity - 1))
        exit_price = self.current_trade_market_price(candle.close, self.active_trade)
        if self.active_trade.direction == "SHORT_STOCK":
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
        self.active_trade.notes = note

    def select_itm_strike(self, spot: float, option_type: str) -> int:
        if option_type == "CE":
            return int(spot // 100) * 100
        return int(((spot + 99) // 100) * 100)

    def format_option_symbol(self, candle_time: datetime, strike: int, option_type: str) -> str:
        expiry = self.next_thursday(candle_time.date())
        return f"NIFTY {expiry.strftime('%d%b%Y').upper()} {strike}{option_type}"

    def next_thursday(self, current_date) -> datetime.date:
        offset = (3 - current_date.weekday()) % 7
        return current_date + timedelta(days=offset)

    def price_option(self, spot: float, strike: int, option_type: str) -> float:
        intrinsic = max(spot - strike, 0) if option_type == "CE" else max(strike - spot, 0)
        distance = abs(spot - strike)
        extrinsic = max(42 - (distance * 0.12), 10)
        return round(intrinsic + extrinsic, 2)
