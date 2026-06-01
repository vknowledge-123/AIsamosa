from __future__ import annotations

import base64
import json
import tempfile
import time
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

import app.main as main_module
from app.config import get_settings
from app.schemas import Candle, InstrumentMode, InstrumentState, LiquidityLedgerEntry, LiveFeedState, OperatingMode, PendingSetup, PreviousDayLevels, SimulatedTrade, StrategyContext, TradeAction, TradeDecision
from app.services.credential_store import CredentialStore
from app.services.dhan_execution import BrokerOrderResult
from app.services.dhan_history import DhanChartEmptyDataError, DhanChartError, DhanChartRateLimitError, DhanChartService, DhanSessionBundle
from app.services.dhan_order_updates import DhanOrderUpdateAdapter
from app.services.dhan_options import DhanOptionQuoteError, OptionContract, OptionQuote
from app.services.heuristic_engine import HeuristicDecisionEngine, Observation, SetupCandidate, SweepEvent
from app.services.instruments import build_stock_instrument
from app.services.simulation import SimulationEngine
from app.services.stock_universe import StockUniverseEntry, StockUniverseService


class AppIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.original_engine = main_module.engine
        self.temp_store = CredentialStore(Path(self.tempdir.name) / "credentials.json")
        with patch("app.services.simulation.CredentialStore", return_value=self.temp_store):
            self.test_engine = SimulationEngine(get_settings())
        self.test_engine.credential_store = self.temp_store
        self.test_engine.ai_service.enabled = False
        self.test_engine.live_feed = self.test_engine._build_live_feed_state()
        main_module.engine = self.test_engine
        self.client = TestClient(main_module.app)

    def tearDown(self) -> None:
        self.test_engine.disconnect_live_feed()
        main_module.engine = self.original_engine
        self.tempdir.cleanup()

    def _make_candle(self, offset_minutes: int, open_price: float, high: float, low: float, close: float, volume: float = 1000.0) -> Candle:
        return Candle(
            timestamp=datetime(2026, 2, 18, 9, 15) + timedelta(minutes=offset_minutes),
            open=open_price,
            high=high,
            low=low,
            close=close,
            volume=volume,
        )

    def _build_context(
        self,
        session_candles: list[Candle],
        *,
        previous_close: float = 100.0,
        active_trade: SimulatedTrade | None = None,
        recent_closed_trades: list[SimulatedTrade] | None = None,
    ) -> StrategyContext:
        previous_day_candles = [
            Candle(timestamp=datetime(2026, 2, 17, 15, 28), open=99.5, high=101.0, low=98.8, close=100.0, volume=1000),
            Candle(timestamp=datetime(2026, 2, 17, 15, 29), open=100.0, high=100.8, low=99.6, close=previous_close, volume=900),
        ]
        return StrategyContext(
            instrument=InstrumentState(),
            current_candle=session_candles[-1],
            recent_candles=session_candles[-10:],
            session_candles=session_candles,
            previous_day_candles=previous_day_candles,
            previous_day=PreviousDayLevels(high=101.0, low=98.8, close=previous_close),
            liquidity_zones=[],
            operator_zones=[],
            signal_events=[],
            market_structure="",
            pending_setup=None,
            active_trade=active_trade,
            recent_closed_trades=recent_closed_trades or [],
            rulebook_markdown="",
        )

    def _make_dhan_token(self, dhan_client_id: str) -> str:
        header = base64.urlsafe_b64encode(json.dumps({"typ": "JWT", "alg": "HS256"}).encode()).decode().rstrip("=")
        payload = base64.urlsafe_b64encode(
            json.dumps({"iss": "dhan", "dhanClientId": dhan_client_id, "tokenConsumerType": "SELF"}).encode()
        ).decode().rstrip("=")
        return f"{header}.{payload}.signature"

    def _build_observation(self, **overrides) -> Observation:
        base = Observation(
            session_phase="primary-trap-window",
            day_type="trap-day",
            value_state="fair",
            range_state="balanced",
            participation_state="two_sided_active",
            regime_quality=0.0,
            previous_day_bias="bullish-recovery",
            prior_close_psychology="balanced-close",
            opening_confirmation="flat-open",
            stop_availability="partially-cleared",
            operator_bias="neutral",
            crowding_bias="balanced",
            vwap=100.0,
            opening_range_high=101.0,
            opening_range_low=99.0,
            first_fifteen_high=101.0,
            first_fifteen_low=99.0,
            prior_hour_high=101.0,
            prior_hour_low=99.0,
            session_high=101.5,
            session_low=98.8,
            prior_session_high=101.2,
            prior_session_low=99.2,
            atr=4.0,
            overlap_ratio=0.42,
            gap=0.6,
            strong_intent=False,
            weak_intent=False,
            expiry_session=False,
            large_gap_reset=False,
            compression_day=False,
            two_sided_participation=True,
            previous_close_reclaim_long_ready=False,
            previous_close_reclaim_short_ready=False,
            previous_close_touched=False,
            mapped_buy_liquidity=[],
            mapped_sell_liquidity=[],
            buy_sweeps=[],
            sell_sweeps=[],
        )
        for key, value in overrides.items():
            setattr(base, key, value)
        return base

    def _build_trade(
        self,
        *,
        entry_time: datetime,
        entry_spot_price: float,
        invalidation_level: float,
        setup_type: str,
    ) -> SimulatedTrade:
        return SimulatedTrade(
            trade_id="trade-1",
            status="OPEN",
            direction="LONG_CALL",
            option_type="CE",
            strike=100,
            symbol="NIFTY SPOT",
            quantity=1,
            open_quantity=1,
            entry_time=entry_time,
            entry_price=10.0,
            entry_spot_price=entry_spot_price,
            entry_option_price=10.0,
            current_price=10.2,
            current_option_price=10.2,
            stop_price=9.0,
            stop_option_price=9.0,
            target_price=12.0,
            target_option_price=12.0,
            invalidation_level=invalidation_level,
            target_spot_price=entry_spot_price + 2.0,
            first_target_price=entry_spot_price + 1.0,
            setup_type=setup_type,
            setup_score=82.0,
            market_state="trap-day",
        )

    def test_dashboard_and_health_routes_render(self) -> None:
        with patch.object(self.test_engine.ai_service, "health", return_value={"reachable": True, "model_available": True, "model": "gpt-5.4-mini", "message": "ok"}):
            dashboard = self.client.get("/")
            health = self.client.get("/api/health/ai")

        self.assertEqual(dashboard.status_code, 200)
        self.assertIn("SL Hunting Paper Trader", dashboard.text)
        self.assertEqual(health.status_code, 200)
        self.assertTrue(health.json()["reachable"])

    def test_state_endpoint_returns_304_when_revision_is_unchanged(self) -> None:
        first = self.client.get("/api/state")

        self.assertEqual(first.status_code, 200)
        self.assertIn("etag", first.headers)

        second = self.client.get("/api/state", headers={"If-None-Match": first.headers["etag"]})

        self.assertEqual(second.status_code, 304)

    def test_upload_rulebook_txt_updates_learning_log(self) -> None:
        self.test_engine.ai_service.enabled = False
        response = self.client.post(
            "/api/upload/rulebook",
            files={"file": ("rulebook.txt", b"Bullish setup waits for reclaim.\nBearish setup waits for rejection.\n", "text/plain")},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("Started rulebook learning for rulebook.txt", payload["message"])

        final_state = None
        for _ in range(20):
            state = self.client.get("/api/state").json()
            if state["rulebook_job"]["status"] == "success":
                final_state = state
                break
            time.sleep(0.05)

        self.assertIsNotNone(final_state)
        self.assertIn("Learned Notes", final_state["rulebook"])
        self.assertTrue(final_state["learning_log"])

    def test_upload_candles_accepts_utf8_bom_csv(self) -> None:
        csv_bytes = (
            "\ufefftimestamp,open,high,low,close,volume\n"
            "2026-04-23T09:15:00,24380,24395,24372,24392,1000\n"
            "2026-04-23T09:16:00,24392,24402,24388,24399,1200\n"
        ).encode("utf-8-sig")
        response = self.client.post(
            "/api/upload/candles",
            files={"file": ("candles.csv", csv_bytes, "text/csv")},
        )

        self.assertEqual(response.status_code, 200)
        state = response.json()["state"]
        self.assertEqual(state["total_candles"], 2)
        self.assertEqual(state["data_sync"]["source"], "uploaded-csv")

    def test_save_credentials_uses_temp_store(self) -> None:
        response = self.client.post(
            "/api/settings/credentials",
            data={
                "client_id": "cid-123",
                "access_token": "tok-456",
                "openai_api_key": "sk-test-123",
                "openai_model": "gpt-5.4-mini",
                "deepseek_api_key": "sk-deepseek-123",
                "deepseek_model": "deepseek-v4-flash",
                "full_ai_provider": "deepseek",
                "operating_mode": "heuristic",
                "nifty_order_lots": "2",
                "stock_trade_capital": "50000",
                "nifty_expiry_preference": "next-weekly",
                "stock_partial_profit_enabled": "false",
                "stock_trailing_stop_enabled": "false",
                "stock_heuristic_early_exit_enabled": "false",
                "nifty_trailing_stop_enabled": "false",
                "nifty_heuristic_early_exit_enabled": "false",
                "nifty_min_sl_points": "18",
                "nifty_max_sl_points": "45",
                "pyramiding_enabled": "true",
                "intelligent_pyramiding_enabled": "true",
                "nifty_option_trade_mode": "buying",
            },
        )

        self.assertEqual(response.status_code, 200)
        summary = self.client.get("/api/settings/credentials").json()
        self.assertEqual(summary["client_id"], "cid-123")
        self.assertTrue(summary["dhan_access_token_saved"])
        self.assertTrue(summary["openai_api_key_saved"])
        self.assertEqual(summary["openai_model"], "gpt-5.4-mini")
        self.assertTrue(summary["deepseek_api_key_saved"])
        self.assertEqual(summary["deepseek_model"], "deepseek-v4-flash")
        self.assertEqual(summary["full_ai_provider"], "deepseek")
        self.assertEqual(summary["operating_mode"], "heuristic")
        self.assertEqual(summary["nifty_order_lots"], 2)
        self.assertEqual(summary["stock_trade_capital"], 50000.0)
        self.assertEqual(summary["nifty_expiry_preference"], "next-weekly")
        self.assertFalse(summary["stock_partial_profit_enabled"])
        self.assertFalse(summary["stock_trailing_stop_enabled"])
        self.assertFalse(summary["stock_heuristic_early_exit_enabled"])
        self.assertFalse(summary["nifty_trailing_stop_enabled"])
        self.assertFalse(summary["nifty_heuristic_early_exit_enabled"])
        self.assertEqual(summary["nifty_min_sl_points"], 18.0)
        self.assertEqual(summary["nifty_max_sl_points"], 45.0)
        self.assertTrue(summary["pyramiding_enabled"])
        self.assertTrue(summary["intelligent_pyramiding_enabled"])
        self.assertEqual(summary["nifty_option_trade_mode"], "buying")
        self.assertTrue(Path(summary["storage_path"]).exists())

    def test_extract_bulk_stock_symbols_from_nse_style_table(self) -> None:
        raw_text = (
            "Symbol\tOpen\tHigh\tLow\n"
            "NIFTY 500\t22,501.15\t22,549.45\t22,501.10\n"
            "AMBER\t7,158.00\t7,208.00\t6,976.00\n"
            "GLAND\t2,122.00\t2,122.00\t2,034.00\n"
            "SJVN\t100.00\t101.00\t99.00\n"
        )

        added, skipped = self.test_engine.extract_bulk_stock_symbols(raw_text)

        self.assertEqual(added, ["AMBER", "GLAND", "SJVN"])
        self.assertEqual(skipped, ["NIFTY 500"])

    def test_bulk_add_stock_watchlist_api_extracts_and_adds_symbols(self) -> None:
        with patch.object(self.test_engine, "_auto_prepare_watchlist_symbols_async", return_value=None):
            response = self.client.post(
                "/api/stocks/watchlist/bulk-add",
                data={
                    "bulk_text": (
                        "Symbol\tOpen\tHigh\tLow\n"
                        "NIFTY 500\t22,501.15\t22,549.45\t22,501.10\n"
                        "AMBER\t7,158.00\t7,208.00\t6,976.00\n"
                        "GLAND\t2,122.00\t2,122.00\t2,034.00\n"
                        "SJVN\t100.00\t101.00\t99.00\n"
                    )
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["added_symbols"], ["AMBER", "GLAND", "SJVN"])
        self.assertEqual(payload["skipped_symbols"], ["NIFTY 500"])
        watch_symbols = [item["symbol"] for item in payload["state"]["stock_watchlist"]]
        self.assertIn("AMBER", watch_symbols)
        self.assertIn("GLAND", watch_symbols)
        self.assertIn("SJVN", watch_symbols)
        self.assertTrue(all(item["trade_bias"] == "both" for item in payload["state"]["stock_watchlist"] if item["symbol"] in {"AMBER", "GLAND", "SJVN"}))

    def test_bulk_add_stock_watchlist_api_marks_long_and_short_bias(self) -> None:
        with patch.object(self.test_engine, "_auto_prepare_watchlist_symbols_async", return_value=None):
            long_response = self.client.post(
                "/api/stocks/watchlist/bulk-add",
                data={"bulk_text": "AMBER\nGLAND\n", "trade_bias": "long"},
            )
            short_response = self.client.post(
                "/api/stocks/watchlist/bulk-add",
                data={"bulk_text": "SJVN\n", "trade_bias": "short"},
            )

        self.assertEqual(long_response.status_code, 200)
        self.assertEqual(short_response.status_code, 200)
        by_symbol = {item["symbol"]: item for item in short_response.json()["state"]["stock_watchlist"]}
        self.assertEqual(by_symbol["AMBER"]["trade_bias"], "long")
        self.assertEqual(by_symbol["GLAND"]["trade_bias"], "long")
        self.assertEqual(by_symbol["SJVN"]["trade_bias"], "short")

    def test_stock_trade_bias_filter_blocks_wrong_side_decision(self) -> None:
        self.test_engine.set_instrument_mode("stock")
        with self.test_engine.lock:
            self.test_engine.stock_watch_meta["SBIN"]["trade_bias"] = "long"
            self.test_engine.instrument_spec = self.test_engine.stock_watchlist["SBIN"]

        blocked = self.test_engine._apply_stock_trade_bias_filter_locked(
            TradeDecision(
                action=TradeAction.enter_put,
                confidence=0.82,
                reason="Short setup should be blocked.",
                option_type="PE",
                pending_setup_action="ARM",
                pending_setup_option_type="PE",
                pending_setup_direction="LONG_PUT",
                pending_setup_trigger_price=99.0,
            )
        )
        allowed = self.test_engine._apply_stock_trade_bias_filter_locked(
            TradeDecision(action=TradeAction.enter_call, confidence=0.82, reason="Long setup is allowed.", option_type="CE")
        )

        self.assertEqual(blocked.action, TradeAction.no_trade)
        self.assertEqual(blocked.pending_setup_action, "NONE")
        self.assertIn("long-only", blocked.reason)
        self.assertEqual(allowed.action, TradeAction.enter_call)

    def test_dashboard_state_tracks_integrated_stock_pnl_and_extrema(self) -> None:
        selected_spec = build_stock_instrument("SBIN", "3045", label="SBIN")
        other_spec = build_stock_instrument("TCS", "11536", label="TCS")
        selected_trade = SimulatedTrade(
            trade_id="agg-selected",
            status="OPEN",
            direction="LONG_STOCK",
            instrument_mode="stock",
            instrument_label="SBIN",
            price_mode="cash",
            trade_security_id="3045",
            quote_exchange_segment="NSE_EQ",
            option_type="CE",
            strike=0,
            symbol="SBIN EQ",
            option_security_id=None,
            quantity=10,
            open_quantity=10,
            entry_time=datetime.fromisoformat("2026-05-18T09:15:00"),
            entry_price=800.0,
            entry_spot_price=800.0,
            entry_option_price=800.0,
            entry_quote_source="simulated",
            entry_quote_time=datetime.fromisoformat("2026-05-18T09:15:00"),
            current_price=810.0,
            current_option_price=810.0,
            current_quote_source="simulated",
            current_quote_time=datetime.fromisoformat("2026-05-18T09:16:00"),
            stop_price=790.0,
            stop_option_price=790.0,
            target_price=820.0,
            target_option_price=820.0,
            invalidation_level=790.0,
            target_spot_price=820.0,
            first_target_price=812.0,
            pnl=100.0,
        )
        other_trade = SimulatedTrade(
            trade_id="agg-other",
            status="OPEN",
            direction="SHORT_STOCK",
            instrument_mode="stock",
            instrument_label="TCS",
            price_mode="cash",
            trade_security_id="11536",
            quote_exchange_segment="NSE_EQ",
            option_type="PE",
            strike=0,
            symbol="TCS EQ",
            option_security_id=None,
            quantity=5,
            open_quantity=5,
            entry_time=datetime.fromisoformat("2026-05-18T09:15:00"),
            entry_price=200.0,
            entry_spot_price=200.0,
            entry_option_price=200.0,
            entry_quote_source="simulated",
            entry_quote_time=datetime.fromisoformat("2026-05-18T09:15:00"),
            current_price=195.0,
            current_option_price=195.0,
            current_quote_source="simulated",
            current_quote_time=datetime.fromisoformat("2026-05-18T09:16:00"),
            stop_price=205.0,
            stop_option_price=205.0,
            target_price=190.0,
            target_option_price=190.0,
            invalidation_level=205.0,
            target_spot_price=190.0,
            first_target_price=194.0,
            pnl=25.0,
        )
        other_session = self.test_engine._build_stock_runtime_session(other_spec)
        other_session.realized_pnl = -20.0
        other_session.active_trade = other_trade
        other_session.live_current_candle = Candle(
            timestamp=datetime.fromisoformat("2026-05-18T09:16:00"),
            open=195.0,
            high=195.0,
            low=195.0,
            close=195.0,
            volume=1000.0,
        )

        with self.test_engine.lock:
            self.test_engine._integrated_pnl_peak = None
            self.test_engine._integrated_pnl_peak_at = None
            self.test_engine._integrated_pnl_trough = None
            self.test_engine._integrated_pnl_trough_at = None
            self.test_engine.instrument_mode = InstrumentMode.stock
            self.test_engine.instrument_spec = selected_spec
            self.test_engine.selected_stock_symbol = "SBIN"
            self.test_engine.stock_watchlist = {"SBIN": selected_spec, "TCS": other_spec}
            self.test_engine.stock_sessions = {"TCS": other_session}
            self.test_engine.candles = [
                Candle(
                    timestamp=datetime.fromisoformat("2026-05-18T09:16:00"),
                    open=810.0,
                    high=810.0,
                    low=810.0,
                    close=810.0,
                    volume=1000.0,
                )
            ]
            self.test_engine.current_index = 0
            self.test_engine.live_current_candle = None
            self.test_engine.active_trade = selected_trade
            self.test_engine.realized_pnl = 100.0
            self.test_engine.balance = self.test_engine.settings.simulation_starting_balance + 100.0

        state = self.test_engine.get_state()
        self.assertEqual(state.integrated_pnl.total_pnl, 205.0)
        self.assertEqual(state.integrated_pnl.realized_pnl, 80.0)
        self.assertEqual(state.integrated_pnl.unrealized_pnl, 125.0)
        self.assertEqual(state.integrated_pnl.max_total_pnl, 205.0)
        self.assertEqual(state.integrated_pnl.min_total_pnl, 205.0)
        self.assertIsNotNone(state.integrated_pnl.max_total_pnl_at)
        self.assertIsNotNone(state.integrated_pnl.min_total_pnl_at)

        with self.test_engine.lock:
            self.test_engine.candles[0].close = 790.0
            tcs_session = self.test_engine.stock_sessions["TCS"]
            tcs_session.live_current_candle.close = 210.0
            tcs_session.live_current_candle.high = 210.0
            tcs_session.live_current_candle.low = 210.0
            self.test_engine._mark_state_dirty_locked()

        state = self.test_engine.get_state()
        self.assertEqual(state.integrated_pnl.total_pnl, -70.0)
        self.assertEqual(state.integrated_pnl.max_total_pnl, 205.0)
        self.assertEqual(state.integrated_pnl.min_total_pnl, -70.0)
        self.assertIsNotNone(state.integrated_pnl.min_total_pnl_at)

    def test_stock_turnover_filter_blocks_low_5m_turnover_entry(self) -> None:
        self.test_engine.set_instrument_mode("stock")
        self.test_engine.operating_mode = OperatingMode.heuristic
        previous_day = [
            Candle(timestamp=datetime(2026, 5, 20, 15, 29), open=350, high=352, low=349, close=351, volume=10000),
        ]
        intraday = [
            Candle(
                timestamp=datetime(2026, 5, 21, 9, 15) + timedelta(minutes=minute),
                open=370 + minute * 0.2,
                high=371 + minute * 0.2,
                low=369 + minute * 0.2,
                close=370.5 + minute * 0.2,
                volume=2000,
            )
            for minute in range(13)
        ]
        self.test_engine.reset_with_candles(previous_day + intraday)
        self.test_engine.current_index = len(previous_day + intraday) - 1

        decision = TradeDecision(
            action=TradeAction.enter_call,
            confidence=0.82,
            reason="Heuristic long entry confirmed.",
            decision_source="heuristic",
            option_type="CE",
        )

        blocked = self.test_engine._apply_stock_turnover_filter_locked(intraday[-1], decision)

        self.assertEqual(blocked.action, TradeAction.no_trade)
        self.assertIn("turnover gate blocked long entry", blocked.reason)
        self.assertIn("below required 3.00 crore", blocked.reason)

    def test_stock_turnover_filter_allows_high_5m_turnover_entry(self) -> None:
        self.test_engine.set_instrument_mode("stock")
        self.test_engine.operating_mode = OperatingMode.heuristic
        previous_day = [
            Candle(timestamp=datetime(2026, 5, 20, 15, 29), open=350, high=352, low=349, close=351, volume=10000),
        ]
        intraday = [
            Candle(
                timestamp=datetime(2026, 5, 21, 9, 15) + timedelta(minutes=minute),
                open=370 + minute * 0.2,
                high=371 + minute * 0.2,
                low=369 + minute * 0.2,
                close=370.5 + minute * 0.2,
                volume=25000 if 5 <= minute <= 9 else 2000,
            )
            for minute in range(13)
        ]
        self.test_engine.reset_with_candles(previous_day + intraday)
        self.test_engine.current_index = len(previous_day + intraday) - 1

        decision = TradeDecision(
            action=TradeAction.enter_call,
            confidence=0.82,
            reason="Heuristic long entry confirmed.",
            decision_source="heuristic",
            option_type="CE",
        )

        allowed = self.test_engine._apply_stock_turnover_filter_locked(intraday[-1], decision)
        snapshot = self.test_engine._stock_turnover_snapshot_from_candles(self.test_engine.candles, intraday[-1].timestamp)

        self.assertEqual(allowed.action, TradeAction.enter_call)
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot.window_start, datetime(2026, 5, 21, 9, 20))
        self.assertEqual(snapshot.window_end, datetime(2026, 5, 21, 9, 25))
        self.assertGreaterEqual(snapshot.turnover, 30000000.0)

    def test_live_stock_turnover_uses_incremental_volume_not_cumulative_day_volume(self) -> None:
        self.test_engine.set_instrument_mode("stock")
        self.test_engine.candles = []
        self.test_engine.live_current_candle = None
        self.test_engine._live_cumulative_volume_by_security_id.clear()
        ticks = [
            (datetime(2026, 5, 22, 13, 54, 30), 194.00, 9500),
            (datetime(2026, 5, 22, 13, 55, 10), 194.10, 10000),
            (datetime(2026, 5, 22, 13, 56, 10), 194.20, 10500),
            (datetime(2026, 5, 22, 13, 57, 10), 194.35, 11000),
            (datetime(2026, 5, 22, 13, 58, 10), 194.45, 11700),
            (datetime(2026, 5, 22, 13, 59, 10), 194.55, 12000),
            (datetime(2026, 5, 22, 14, 0, 5), 194.60, 12600),
        ]

        with self.test_engine.lock:
            for tick_time, ltp, cumulative_volume in ticks:
                volume_delta = self.test_engine._live_volume_delta_locked("2263", tick_time, cumulative_volume)
                self.test_engine._update_live_candle_locked(tick_time, ltp, volume_delta)

        snapshot = self.test_engine._stock_turnover_snapshot_from_candles(
            self.test_engine.candles,
            datetime(2026, 5, 22, 14, 0, 5),
        )

        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot.window_start, datetime(2026, 5, 22, 13, 55))
        self.assertEqual(snapshot.window_end, datetime(2026, 5, 22, 14, 0))
        self.assertEqual(snapshot.volume, 2500)
        self.assertEqual(snapshot.turnover, round(194.55 * 2500, 2))
        self.assertLess(snapshot.turnover, 1000000.0)

    def test_stock_watchlist_and_integrated_pnl_ignore_stale_cash_trade_snapshot_values(self) -> None:
        selected_spec = build_stock_instrument("SBIN", "3045", label="SBIN")
        other_spec = build_stock_instrument("TCS", "11536", label="TCS")
        selected_trade = SimulatedTrade(
            trade_id="stale-selected",
            status="OPEN",
            direction="LONG_STOCK",
            instrument_mode="stock",
            instrument_label="SBIN",
            price_mode="cash",
            trade_security_id="3045",
            quote_exchange_segment="NSE_EQ",
            option_type="CE",
            strike=0,
            symbol="SBIN EQ",
            option_security_id=None,
            quantity=10,
            open_quantity=10,
            entry_time=datetime.fromisoformat("2026-05-18T09:15:00"),
            entry_price=800.0,
            entry_spot_price=800.0,
            entry_option_price=800.0,
            entry_quote_source="simulated",
            entry_quote_time=datetime.fromisoformat("2026-05-18T09:15:00"),
            current_price=9999.0,
            current_option_price=9999.0,
            current_quote_source="simulated",
            current_quote_time=datetime.fromisoformat("2026-05-18T09:16:00"),
            stop_price=790.0,
            stop_option_price=790.0,
            target_price=820.0,
            target_option_price=820.0,
            invalidation_level=790.0,
            target_spot_price=820.0,
            first_target_price=810.0,
            pnl=91990.0,
        )
        other_trade = SimulatedTrade(
            trade_id="stale-other",
            status="OPEN",
            direction="SHORT_STOCK",
            instrument_mode="stock",
            instrument_label="TCS",
            price_mode="cash",
            trade_security_id="11536",
            quote_exchange_segment="NSE_EQ",
            option_type="PE",
            strike=0,
            symbol="TCS EQ",
            option_security_id=None,
            quantity=5,
            open_quantity=5,
            entry_time=datetime.fromisoformat("2026-05-18T09:15:00"),
            entry_price=200.0,
            entry_spot_price=200.0,
            entry_option_price=200.0,
            entry_quote_source="simulated",
            entry_quote_time=datetime.fromisoformat("2026-05-18T09:15:00"),
            current_price=-500.0,
            current_option_price=-500.0,
            current_quote_source="simulated",
            current_quote_time=datetime.fromisoformat("2026-05-18T09:16:00"),
            stop_price=205.0,
            stop_option_price=205.0,
            target_price=190.0,
            target_option_price=190.0,
            invalidation_level=205.0,
            target_spot_price=190.0,
            first_target_price=194.0,
            pnl=3500.0,
        )
        other_session = self.test_engine._build_stock_runtime_session(other_spec)
        other_session.active_trade = other_trade
        other_session.live_current_candle = Candle(
            timestamp=datetime.fromisoformat("2026-05-18T09:16:00"),
            open=195.0,
            high=195.0,
            low=195.0,
            close=195.0,
            volume=1000.0,
        )

        with self.test_engine.lock:
            self.test_engine._integrated_pnl_peak = None
            self.test_engine._integrated_pnl_peak_at = None
            self.test_engine._integrated_pnl_trough = None
            self.test_engine._integrated_pnl_trough_at = None
            self.test_engine.instrument_mode = InstrumentMode.stock
            self.test_engine.instrument_spec = selected_spec
            self.test_engine.selected_stock_symbol = "SBIN"
            self.test_engine.stock_watchlist = {"SBIN": selected_spec, "TCS": other_spec}
            self.test_engine.stock_sessions = {"TCS": other_session}
            self.test_engine.candles = [
                Candle(
                    timestamp=datetime.fromisoformat("2026-05-18T09:16:00"),
                    open=810.0,
                    high=810.0,
                    low=810.0,
                    close=810.0,
                    volume=1000.0,
                )
            ]
            self.test_engine.current_index = 0
            self.test_engine.live_current_candle = None
            self.test_engine.active_trade = selected_trade
            self.test_engine.realized_pnl = 0.0
            self.test_engine.balance = self.test_engine.settings.simulation_starting_balance

        state = self.test_engine.get_state()
        by_symbol = {item.symbol: item for item in state.stock_watchlist}
        self.assertEqual(by_symbol["SBIN"].active_trade_pnl, 100.0)
        self.assertEqual(by_symbol["TCS"].active_trade_pnl, 25.0)
        self.assertEqual(state.unrealized_pnl, 100.0)
        self.assertEqual(state.integrated_pnl.unrealized_pnl, 125.0)
        self.assertEqual(state.integrated_pnl.total_pnl, 125.0)

    def test_engine_restores_persisted_stock_mode_and_selected_symbol(self) -> None:
        store = CredentialStore(Path(self.tempdir.name) / "persisted-ui.json")
        store.save_ui_preferences(
            instrument_mode="stock",
            selected_stock_symbol="TCS",
            stock_watchlist_symbols=["SBIN", "TCS"],
        )

        with patch("app.services.simulation.CredentialStore", return_value=store):
            restored_engine = SimulationEngine(get_settings())

        try:
            state = restored_engine.get_state()
            self.assertEqual(state.instrument.mode, "stock")
            self.assertEqual(state.instrument.symbol, "TCS")
            self.assertTrue(any(item.symbol == "TCS" for item in restored_engine.stock_watchlist.values()))
        finally:
            restored_engine.disconnect_live_feed()

    def test_engine_keeps_persisted_empty_stock_watchlist_empty(self) -> None:
        store = CredentialStore(Path(self.tempdir.name) / "persisted-empty-ui.json")
        store.save_ui_preferences(
            instrument_mode="stock",
            selected_stock_symbol="",
            stock_watchlist_symbols=[],
        )

        with patch("app.services.simulation.CredentialStore", return_value=store):
            restored_engine = SimulationEngine(get_settings())

        try:
            state = restored_engine.get_state()
            self.assertEqual(state.instrument.mode, "stock")
            self.assertEqual(state.instrument.label, "Stock Watchlist")
            self.assertEqual(state.instrument.security_id, "")
            self.assertFalse(restored_engine.stock_watchlist)
        finally:
            restored_engine.disconnect_live_feed()

    def test_start_live_trading_arms_execution_state(self) -> None:
        self.client.post(
            "/api/settings/credentials",
            data={"operating_mode": "heuristic", "client_id": "cid-123", "access_token": "tok-456"},
        )
        self.test_engine.live_feed_adapter = Mock()
        with patch.object(self.test_engine, "_start_order_updates", return_value=None):
            response = self.client.post("/api/trading/start")

        self.assertEqual(response.status_code, 200)
        state = response.json()["state"]
        self.assertTrue(state["execution"]["live_trading_enabled"])

    def test_start_live_trading_clears_simulated_open_stock_trade(self) -> None:
        self.test_engine.set_instrument_mode("stock")
        self.client.post(
            "/api/settings/credentials",
            data={"operating_mode": "heuristic", "client_id": "cid-123", "access_token": "tok-456"},
        )
        spec = build_stock_instrument("SBIN", "3045", label="STATE BANK OF INDIA")
        self.test_engine.stock_watchlist = {"SBIN": spec}
        self.test_engine.selected_stock_symbol = "SBIN"
        self.test_engine.instrument_spec = spec
        self.test_engine.live_feed_adapter = Mock()
        open_trade = SimulatedTrade(
            trade_id="paper-stock-1",
            status="OPEN",
            direction="LONG_STOCK",
            instrument_mode="stock",
            instrument_label="SBIN",
            price_mode="cash",
            trade_security_id="3045",
            quote_exchange_segment="NSE_EQ",
            option_type="CE",
            strike=0,
            symbol="SBIN EQ",
            quantity=1,
            open_quantity=1,
            entry_time=datetime.fromisoformat("2026-05-20T09:30:00"),
            entry_price=800.0,
            entry_spot_price=800.0,
            entry_option_price=800.0,
            current_price=804.0,
            current_option_price=804.0,
            stop_price=790.0,
            stop_option_price=790.0,
            target_price=820.0,
            target_option_price=820.0,
            invalidation_level=790.0,
            pnl=4.0,
        )
        session = self.test_engine._build_stock_runtime_session(spec)
        session.active_trade = open_trade.model_copy(deep=True)
        session.trade_history = [open_trade.model_copy(deep=True)]
        self.test_engine.stock_sessions["SBIN"] = session
        self.test_engine.active_trade = open_trade.model_copy(deep=True)
        self.test_engine.trade_history = [open_trade.model_copy(deep=True)]

        with patch.object(self.test_engine, "_start_order_updates", return_value=None):
            state = self.test_engine.start_live_trading()

        self.assertTrue(state.execution.live_trading_enabled)
        self.assertIsNone(self.test_engine.active_trade)
        self.assertFalse(self.test_engine.trade_history)
        self.assertIsNone(self.test_engine.stock_sessions["SBIN"].active_trade)
        self.assertFalse(self.test_engine.stock_sessions["SBIN"].trade_history)

    def test_stock_sync_or_live_does_not_open_paper_trade_before_trading_is_armed(self) -> None:
        self.test_engine.set_instrument_mode("stock")
        spec = build_stock_instrument("SBIN", "3045", label="STATE BANK OF INDIA")
        self.test_engine.stock_watchlist = {"SBIN": spec}
        self.test_engine.selected_stock_symbol = "SBIN"
        self.test_engine.instrument_spec = spec
        decision = TradeDecision(
            action=TradeAction.enter_call,
            confidence=0.8,
            reason="Entry is allowed now.",
            option_type="CE",
            invalidation_level=790.0,
            target_spot_price=820.0,
            first_target_price=810.0,
            setup_type="stock_first_pullback_trend_long",
            setup_score=82.0,
        )

        self.test_engine.apply_trade_logic(
            self._make_candle(0, 800.0, 804.0, 799.0, 803.0),
            decision,
            source="sync",
        )

        self.assertIsNone(self.test_engine.active_trade)
        self.assertFalse(self.test_engine.trade_history)

    def test_live_connect_uses_saved_credentials_when_form_is_blank(self) -> None:
        self.client.post(
            "/api/settings/credentials",
            data={"client_id": "cid-123", "access_token": "tok-456"},
        )
        fake_adapter = Mock()
        fake_adapter.start = Mock()

        with patch.object(self.test_engine, "sync_dhan_context", return_value=self.test_engine.get_state()) as sync_mock:
            with patch("app.services.simulation.resolve_quote_subscription", return_value=("IDX", "13", "Quote")):
                with patch("app.services.simulation.DhanMarketFeedAdapter", return_value=fake_adapter) as adapter_mock:
                    response = self.client.post("/api/live/connect", data={"client_id": "", "access_token": ""})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(sync_mock.call_count, 1)
        self.assertEqual(adapter_mock.call_args.args[0], "cid-123")
        self.assertEqual(adapter_mock.call_args.args[1], "tok-456")
        fake_adapter.start.assert_called_once()

    def test_background_sync_history_route_starts_job_and_completes(self) -> None:
        def fake_sync(*, client_id=None, access_token=None):
            time.sleep(0.05)
            with self.test_engine.lock:
                self.test_engine.data_sync = self.test_engine.data_sync.model_copy(
                    update={"status": "ready", "message": "Background sync completed."}
                )
                self.test_engine._mark_state_dirty_locked()
            return self.test_engine.get_state()

        with patch.object(self.test_engine, "sync_dhan_context", side_effect=fake_sync):
            response = self.client.post("/api/live/sync-history/start", data={"client_id": "cid", "access_token": "tok"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["state"]["operation_job"]["status"], "running")
        deadline = time.time() + 2.0
        final_state = self.test_engine.get_state()
        while time.time() < deadline and final_state.operation_job.status == "running":
            time.sleep(0.02)
            final_state = self.test_engine.get_state()
        self.assertEqual(final_state.operation_job.status, "success")

    def test_background_historical_replay_route_starts_job_and_rejects_overlap(self) -> None:
        def fake_replay(
            *,
            client_id=None,
            access_token=None,
            replay_date=None,
            previous_context_date=None,
            replay_decision_duration_minutes=1,
            stock_replay_scope="all",
        ):
            time.sleep(0.15)
            with self.test_engine.lock:
                self.test_engine.data_sync = self.test_engine.data_sync.model_copy(
                    update={"status": "ready", "message": f"Replay completed for {replay_date}."}
                )
                self.test_engine._mark_state_dirty_locked()
            return self.test_engine.get_state()

        with patch.object(self.test_engine, "simulate_historical_session", side_effect=fake_replay):
            first = self.client.post(
                "/api/simulation/historical/start",
                data={
                    "client_id": "cid",
                    "access_token": "tok",
                    "replay_date": "2026-05-13",
                    "previous_context_date": "2026-05-12",
                    "decision_duration_minutes": "1",
                },
            )
            second = self.client.post(
                "/api/simulation/historical/start",
                data={
                    "client_id": "cid",
                    "access_token": "tok",
                    "replay_date": "2026-05-13",
                    "previous_context_date": "2026-05-12",
                    "decision_duration_minutes": "1",
                },
            )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["state"]["operation_job"]["status"], "running")
        self.assertEqual(second.status_code, 400)
        self.assertIn("already running", second.json()["detail"])
        deadline = time.time() + 2.0
        final_state = self.test_engine.get_state()
        while time.time() < deadline and final_state.operation_job.status == "running":
            time.sleep(0.02)
            final_state = self.test_engine.get_state()
        self.assertEqual(final_state.operation_job.status, "success")

    def test_order_update_packet_updates_active_trade_status_and_price(self) -> None:
        trade = SimulatedTrade(
            trade_id="trade-active",
            status="OPEN",
            direction="LONG_STOCK",
            instrument_mode="stock",
            instrument_label="SBIN",
            price_mode="cash",
            trade_security_id="3045",
            quote_exchange_segment="NSE_EQ",
            option_type="CE",
            strike=0,
            symbol="SBIN EQ",
            quantity=10,
            open_quantity=10,
            entry_time=datetime.fromisoformat("2026-05-14T09:15:00"),
            entry_price=800.0,
            entry_spot_price=800.0,
            entry_option_price=800.0,
            current_price=800.0,
            current_option_price=800.0,
            stop_price=790.0,
            stop_option_price=790.0,
            target_price=820.0,
            target_option_price=820.0,
            broker_order_id="ORD123",
            broker_status="PENDING",
            broker_status_message="Waiting",
        )
        self.test_engine.active_trade = trade
        self.test_engine.trade_history = [trade]

        self.test_engine.handle_order_update_packet(
            {
                "Data": {
                    "OrderNo": "ORD123",
                    "Status": "TRADED",
                    "AvgTradedPrice": "805.50",
                }
            }
        )

        self.assertEqual(trade.broker_status, "TRADED")
        self.assertEqual(trade.entry_price, 805.5)
        self.assertEqual(trade.current_price, 805.5)
        self.assertEqual(trade.current_quote_source, "dhan-order-update")
        self.assertIn("TRADED", self.test_engine.execution_state.last_order_message or "")

    def test_order_update_packet_updates_closed_trade_history_entry(self) -> None:
        trade = SimulatedTrade(
            trade_id="trade-closed",
            status="CLOSED",
            direction="LONG_STOCK",
            instrument_mode="stock",
            instrument_label="SBIN",
            price_mode="cash",
            trade_security_id="3045",
            quote_exchange_segment="NSE_EQ",
            option_type="CE",
            strike=0,
            symbol="SBIN EQ",
            quantity=10,
            open_quantity=0,
            closed_quantity=10,
            entry_time=datetime.fromisoformat("2026-05-14T09:15:00"),
            entry_price=800.0,
            entry_spot_price=800.0,
            entry_option_price=800.0,
            current_price=810.0,
            current_option_price=810.0,
            stop_price=790.0,
            stop_option_price=790.0,
            target_price=820.0,
            target_option_price=820.0,
            exit_time=datetime.fromisoformat("2026-05-14T09:25:00"),
            exit_price=810.0,
            exit_option_price=810.0,
            broker_order_id="ORD123",
            broker_exit_order_id="ORD124",
            broker_status="PENDING",
            broker_status_message="Exit submitted",
        )
        self.test_engine.active_trade = None
        self.test_engine.trade_history = [trade]

        self.test_engine.handle_order_update_packet(
            {
                "Data": {
                    "OrderNo": "ORD124",
                    "Status": "TRADED",
                    "AvgTradedPrice": "812.25",
                }
            }
        )

        self.assertEqual(trade.broker_status, "TRADED")
        self.assertEqual(trade.exit_price, 812.25)
        self.assertEqual(trade.exit_option_price, 812.25)

    def test_order_update_adapter_parses_newline_separated_json_packets(self) -> None:
        adapter = DhanOrderUpdateAdapter("cid", "tok")
        raw_message = (
            '{"Type":"order_alert","Data":{"OrderNo":"ORD123","Status":"PENDING"}}\n'
            '{"Type":"order_alert","Data":{"OrderNo":"ORD123","Status":"TRADED"}}'
        )

        packets = adapter._parse_order_update_message(raw_message)

        self.assertEqual(len(packets), 2)
        self.assertEqual(packets[0]["Data"]["Status"], "PENDING")
        self.assertEqual(packets[1]["Data"]["Status"], "TRADED")

    def test_order_update_adapter_ignores_non_dict_prefix_before_json_packet(self) -> None:
        adapter = DhanOrderUpdateAdapter("cid", "tok")

        packets = adapter._parse_order_update_message('42\n{"Type":"order_alert","Data":{"OrderNo":"ORD123"}}')

        self.assertEqual(len(packets), 1)
        self.assertEqual(packets[0]["Data"]["OrderNo"], "ORD123")

    def test_order_update_connected_status_clears_previous_error(self) -> None:
        self.test_engine.handle_order_update_status("error", "Extra data: line 2 column 1 (char 2)")
        self.assertEqual(self.test_engine.execution_state.last_order_error, "Extra data: line 2 column 1 (char 2)")

        self.test_engine.handle_order_update_status("connected", "Dhan order update websocket connected.")

        self.assertIsNone(self.test_engine.execution_state.last_order_error)
        self.assertIsNone(self.test_engine.execution_state.last_order_error_at)

    def test_square_off_route_disables_live_execution(self) -> None:
        self.test_engine.live_feed_adapter = Mock()
        self.test_engine.live_trading_enabled = True
        self.test_engine.execution_state.live_trading_enabled = True

        response = self.client.post("/api/trading/square-off")

        self.assertEqual(response.status_code, 200)
        state = response.json()["state"]
        self.assertFalse(state["execution"]["live_trading_enabled"])

    def test_live_entry_rejection_is_visible_in_execution_state_and_stock_watchlist(self) -> None:
        self.test_engine.set_instrument_mode("stock")
        self.temp_store.save(client_id="cid-123", access_token="tok-456")
        spec = build_stock_instrument("SBIN", "3045", label="STATE BANK OF INDIA")
        self.test_engine.stock_watchlist = {"SBIN": spec}
        self.test_engine.selected_stock_symbol = "SBIN"
        self.test_engine.stock_sessions["SBIN"] = self.test_engine._build_stock_runtime_session(spec)
        trade = SimulatedTrade(
            trade_id="trade-live-reject",
            status="OPEN",
            direction="LONG_STOCK",
            instrument_mode="stock",
            instrument_label="SBIN",
            price_mode="cash",
            trade_security_id="3045",
            quote_exchange_segment="NSE_EQ",
            option_type="CE",
            strike=0,
            symbol="SBIN EQ",
            quantity=10,
            open_quantity=10,
            entry_time=datetime.fromisoformat("2026-05-14T09:15:00"),
            entry_price=800.0,
            entry_spot_price=800.0,
            entry_option_price=800.0,
            current_price=800.0,
            current_option_price=800.0,
            stop_price=790.0,
            stop_option_price=790.0,
            target_price=820.0,
            target_option_price=820.0,
        )
        decision = TradeDecision(action=TradeAction.enter_call, reason="Entry is allowed now.")
        self.test_engine.execution_service.place_market_order = Mock(
            return_value=BrokerOrderResult(
                ok=False,
                order_id=None,
                order_status=None,
                message="Invalid IP",
                raw={},
            )
        )

        self.test_engine._enter_live_trade(self._make_candle(0, 800, 801, 799, 800.5), decision, trade)

        state = self.test_engine.get_state()
        self.assertEqual(state.execution.last_order_error, "Invalid IP")
        self.assertEqual(state.execution.last_order_symbol, "SBIN EQ")
        stock_item = next(item for item in state.stock_watchlist if item.symbol == "SBIN")
        self.assertEqual(stock_item.live_order_error, "Invalid IP")
        self.assertIn("Live entry rejected", stock_item.live_order_message or "")

    def test_heuristic_mode_uses_deterministic_decision_source(self) -> None:
        save_response = self.client.post(
            "/api/settings/credentials",
            data={"operating_mode": "heuristic"},
        )
        self.assertEqual(save_response.status_code, 200)

        step_response = self.client.post("/api/simulation/step", data={"steps": 1})
        self.assertEqual(step_response.status_code, 200)
        state = step_response.json()
        self.assertEqual(state["operating_mode"], "heuristic")
        self.assertEqual(state["decision"]["decision_source"], "heuristic")

    def test_full_ai_mode_stops_new_entries_when_ai_is_disabled(self) -> None:
        save_response = self.client.post(
            "/api/settings/credentials",
            data={"operating_mode": "full-ai"},
        )
        self.assertEqual(save_response.status_code, 200)

        step_response = self.client.post("/api/simulation/step", data={"steps": 1})
        self.assertEqual(step_response.status_code, 200)
        state = step_response.json()
        self.assertEqual(state["operating_mode"], "full-ai")
        self.assertEqual(state["decision"]["decision_source"], "full-ai-fallback")
        self.assertEqual(state["decision"]["action"], "NO_TRADE")

    def test_previous_close_setup_requires_recent_interaction(self) -> None:
        engine = HeuristicDecisionEngine()
        session = [
            self._make_candle(0, 99.8, 100.2, 99.6, 100.0),
            self._make_candle(1, 100.0, 100.6, 99.9, 100.5),
            self._make_candle(2, 103.0, 103.8, 102.8, 103.6),
            self._make_candle(3, 103.5, 104.0, 103.1, 103.8),
            self._make_candle(4, 103.7, 104.1, 103.4, 104.0),
            self._make_candle(5, 103.9, 104.2, 103.6, 104.1),
            self._make_candle(6, 104.0, 104.4, 103.8, 104.2),
            self._make_candle(7, 104.1, 104.5, 103.9, 104.3),
            self._make_candle(8, 104.2, 104.6, 104.0, 104.4),
            self._make_candle(9, 104.3, 104.7, 104.1, 104.5),
        ]
        context = self._build_context(session, previous_close=100.0)

        observation = engine.observe(context)

        self.assertFalse(observation.previous_close_touched)
        self.assertEqual(engine.build_previous_close_candidates(context, observation), [])

    def test_previous_close_setup_rejects_chasing_price_far_from_level(self) -> None:
        engine = HeuristicDecisionEngine()
        session = [
            self._make_candle(0, 99.7, 100.3, 99.6, 100.1),
            self._make_candle(1, 100.0, 101.5, 99.9, 101.2),
            self._make_candle(2, 101.1, 103.5, 101.0, 103.2),
            self._make_candle(3, 103.0, 106.4, 102.8, 106.0),
        ]
        context = self._build_context(session, previous_close=100.0)
        observation = self._build_observation(
            atr=4.0,
            gap=0.8,
            previous_close_touched=True,
            previous_close_reclaim_long_ready=True,
            value_state="inflated",
        )

        candidates = engine.build_previous_close_candidates(context, observation)

        self.assertEqual(candidates, [])

    def test_previous_close_setup_is_blocked_in_midday_churn(self) -> None:
        engine = HeuristicDecisionEngine()
        session = [
            self._make_candle(0, 99.8, 100.2, 99.7, 100.1),
            self._make_candle(1, 100.0, 100.8, 99.9, 100.4),
            self._make_candle(2, 100.3, 101.1, 100.1, 100.9),
            self._make_candle(3, 100.8, 101.2, 100.4, 100.7),
        ]
        context = self._build_context(session, previous_close=100.0)
        observation = self._build_observation(
            session_phase="midday",
            participation_state="fair_value_churn",
            previous_close_touched=True,
            previous_close_reclaim_long_ready=True,
        )

        candidates = engine.build_previous_close_candidates(context, observation)

        self.assertEqual(candidates, [])

    def test_nifty_mode_skips_previous_close_liquidity_path(self) -> None:
        engine = HeuristicDecisionEngine()
        session = [
            self._make_candle(0, 99.8, 100.2, 99.7, 100.1),
            self._make_candle(1, 100.0, 100.8, 99.9, 100.4),
            self._make_candle(2, 100.3, 101.1, 100.1, 100.9),
            self._make_candle(3, 100.8, 101.2, 100.4, 100.7),
        ]
        context = self._build_context(session, previous_close=100.0)
        observation = self._build_observation(
            previous_close_touched=True,
            previous_close_reclaim_long_ready=True,
        )

        candidates = engine.build_candidates(context, observation)

        self.assertEqual(candidates, [])

    def test_stock_mode_keeps_previous_close_liquidity_path(self) -> None:
        engine = HeuristicDecisionEngine()
        session = [
            self._make_candle(0, 99.8, 100.2, 99.7, 100.1),
            self._make_candle(1, 100.0, 100.8, 99.9, 100.4),
            self._make_candle(2, 100.3, 101.1, 100.1, 100.9),
            self._make_candle(3, 100.8, 101.2, 100.4, 100.7),
        ]
        context = self._build_context(session, previous_close=100.0).model_copy(
            update={
                "instrument": InstrumentState(
                    mode="stock",
                    label="SBIN",
                    symbol="SBIN",
                    security_id="3045",
                    exchange_segment="NSE_EQ",
                    instrument_type="EQUITY",
                    supports_options=False,
                    lot_size=1,
                )
            }
        )
        observation = self._build_observation(
            previous_close_touched=True,
            previous_close_reclaim_long_ready=True,
        )

        candidates = engine.build_candidates(context, observation)

        self.assertTrue(any(candidate.setup_type == "previous_close_reclaim_long" for candidate in candidates))

    def test_active_trade_exits_when_regime_deteriorates_after_entry(self) -> None:
        engine = HeuristicDecisionEngine()
        session = [
            self._make_candle(0, 100.0, 100.8, 99.8, 100.4),
            self._make_candle(1, 100.3, 100.7, 100.1, 100.5),
            self._make_candle(2, 100.4, 100.6, 100.2, 100.45),
            self._make_candle(3, 100.4, 100.55, 100.15, 100.35),
            self._make_candle(4, 100.3, 100.5, 100.1, 100.3),
            self._make_candle(5, 100.25, 100.45, 100.05, 100.28),
        ]
        trade = self._build_trade(
            entry_time=session[0].timestamp,
            entry_spot_price=100.4,
            invalidation_level=99.4,
            setup_type="previous_close_reclaim_long",
        )
        context = self._build_context(session, previous_close=100.0, active_trade=trade)
        observation = self._build_observation(
            session_phase="midday",
            range_state="compressing",
            participation_state="post_trend_balance",
            previous_close_touched=False,
            previous_close_reclaim_long_ready=False,
            previous_close_reclaim_short_ready=False,
            atr=1.0,
        )

        decision = engine.manage_active_trade(context, observation, current_trade_price=None)

        self.assertEqual(decision.action, TradeAction.exit)
        self.assertIn("regime has deteriorated", decision.reason.lower())

    def test_early_discount_short_is_blocked_on_gap_down_recovery_context(self) -> None:
        engine = HeuristicDecisionEngine()
        session = [
            self._make_candle(0, 100.0, 100.2, 97.6, 97.9),
            self._make_candle(1, 97.9, 98.6, 97.8, 98.4),
            self._make_candle(2, 98.5, 99.1, 98.2, 98.9),
            self._make_candle(3, 98.9, 99.0, 98.3, 98.4),
        ]
        context = self._build_context(session, previous_close=101.5)
        observation = self._build_observation(
            session_phase="opening-map",
            value_state="discount",
            gap=-2.2,
            vwap=99.3,
        )
        event = SweepEvent(
            side="buy",
            level_label="Same-Day Swing High 09:17",
            level_price=99.0,
            sweep_index=2,
            reclaim_index=3,
            trigger_index=3,
            sweep_price=99.1,
            defended_level=99.0,
            trigger_price=98.5,
            invalidation_level=99.3,
            primary=False,
            quality="tradable",
            notes=["Same-day high was poked and then faded."],
        )

        candidate = engine.build_candidate_from_event(context, observation, event, option_type="PE", direction="LONG_PUT")

        self.assertIsNone(candidate)

    def test_minor_retest_becomes_continuation_not_fresh_trap(self) -> None:
        engine = HeuristicDecisionEngine()
        session = [
            self._make_candle(0, 100.0, 100.5, 99.8, 100.2),
            self._make_candle(1, 100.2, 100.6, 100.0, 100.5),
            self._make_candle(2, 100.4, 100.9, 100.3, 100.8),
            self._make_candle(3, 100.8, 101.1, 100.6, 101.0),
        ]
        context = self._build_context(session, previous_close=99.8)
        observation = self._build_observation(
            value_state="inflated",
            vwap=100.4,
            atr=0.6,
            prior_hour_high=101.0,
            prior_hour_low=99.8,
            session_high=101.1,
            session_low=99.8,
        )
        event = SweepEvent(
            side="sell",
            level_label="Prior Hour Low",
            level_price=100.0,
            sweep_index=1,
            reclaim_index=1,
            trigger_index=1,
            sweep_price=100.0,
            defended_level=100.0,
            trigger_price=100.6,
            invalidation_level=99.7,
            primary=False,
            quality="tradable",
            notes=["Intraday pullback held and reclaimed."],
        )

        candidate = engine.build_candidate_from_event(context, observation, event, option_type="CE", direction="LONG_CALL")

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.setup_type, "bullish_pullback_continuation")

    def test_minor_bearish_retest_becomes_continuation_not_fresh_trap(self) -> None:
        engine = HeuristicDecisionEngine()
        session = [
            self._make_candle(0, 100.0, 100.2, 99.8, 100.0),
            self._make_candle(1, 100.0, 101.0, 99.9, 100.9),
            self._make_candle(2, 100.9, 101.1, 100.6, 100.7),
            self._make_candle(3, 100.7, 100.8, 100.0, 100.1),
        ]
        context = self._build_context(session, previous_close=100.0)
        observation = self._build_observation(
            value_state="discount",
            vwap=100.4,
            atr=0.7,
            prior_hour_high=101.1,
            prior_hour_low=99.8,
            session_high=101.1,
            session_low=99.8,
        )
        event = SweepEvent(
            side="buy",
            level_label="Prior Hour High",
            level_price=101.0,
            sweep_index=1,
            reclaim_index=1,
            trigger_index=1,
            sweep_price=101.0,
            defended_level=101.0,
            trigger_price=100.4,
            invalidation_level=101.3,
            primary=False,
            quality="tradable",
            notes=["Intraday pullback failed after probing higher."],
        )

        candidate = engine.build_candidate_from_event(context, observation, event, option_type="PE", direction="LONG_PUT")

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.setup_type, "bearish_pullback_continuation")

    def test_fresh_primary_bullish_sweep_stays_reclaim_watch(self) -> None:
        engine = HeuristicDecisionEngine()
        session = [
            self._make_candle(0, 100.0, 100.2, 99.8, 100.1),
            self._make_candle(1, 100.1, 100.3, 98.9, 99.2),
            self._make_candle(2, 99.2, 100.8, 99.1, 100.7),
            self._make_candle(3, 100.7, 101.2, 100.5, 101.0),
        ]
        context = self._build_context(session, previous_close=100.2)
        observation = self._build_observation(
            value_state="discount",
            vwap=100.1,
            atr=0.9,
            strong_intent=False,
            session_high=101.2,
            session_low=98.9,
        )
        event = SweepEvent(
            side="sell",
            level_label="Previous Day Low",
            level_price=99.0,
            sweep_index=1,
            reclaim_index=2,
            trigger_index=2,
            sweep_price=98.9,
            defended_level=99.0,
            trigger_price=100.4,
            invalidation_level=98.7,
            primary=True,
            quality="tradable",
            notes=["Previous day low was swept and reclaimed quickly."],
        )

        candidate = engine.build_candidate_from_event(context, observation, event, option_type="CE", direction="LONG_CALL")

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.setup_type, "bullish_reclaim_watch")

    def test_fresh_primary_bearish_sweep_stays_rejection_watch(self) -> None:
        engine = HeuristicDecisionEngine()
        session = [
            self._make_candle(0, 100.0, 100.2, 99.8, 100.1),
            self._make_candle(1, 100.1, 101.2, 100.0, 101.1),
            self._make_candle(2, 101.0, 101.1, 99.4, 99.5),
            self._make_candle(3, 99.5, 99.8, 99.1, 99.2),
        ]
        context = self._build_context(session, previous_close=99.8)
        observation = self._build_observation(
            value_state="inflated",
            vwap=100.0,
            atr=0.9,
            strong_intent=False,
            session_high=101.2,
            session_low=99.1,
        )
        event = SweepEvent(
            side="buy",
            level_label="Previous Day High",
            level_price=101.0,
            sweep_index=1,
            reclaim_index=2,
            trigger_index=2,
            sweep_price=101.2,
            defended_level=101.0,
            trigger_price=99.7,
            invalidation_level=101.4,
            primary=True,
            quality="tradable",
            notes=["Previous day high was swept and rejected quickly."],
        )

        candidate = engine.build_candidate_from_event(context, observation, event, option_type="PE", direction="LONG_PUT")

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.setup_type, "bearish_rejection_watch")

    def test_stock_mode_allows_early_bullish_retest_hold_before_mature_breakout(self) -> None:
        engine = HeuristicDecisionEngine()
        session = [
            self._make_candle(0, 100.2, 100.4, 99.9, 100.1),
            self._make_candle(1, 100.1, 100.2, 99.2, 99.4),
            self._make_candle(2, 99.4, 100.6, 99.3, 100.35),
            self._make_candle(3, 100.22, 100.48, 100.02, 100.4),
        ]
        context = self._build_context(session, previous_close=100.3).model_copy(
            update={
                "instrument": InstrumentState(
                    mode="stock",
                    label="CHAMBLFERT",
                    symbol="CHAMBLFERT",
                    security_id="1134",
                    exchange_segment="NSE_EQ",
                    instrument_type="EQUITY",
                    supports_options=False,
                    lot_size=1,
                )
            }
        )
        observation = self._build_observation(
            value_state="discount",
            vwap=100.1,
            atr=0.9,
            strong_intent=False,
            weak_intent=False,
            session_high=100.6,
            session_low=99.2,
            stock_dow_bias="bullish",
            stock_dow_state="early-uptrend",
        )
        event = SweepEvent(
            side="sell",
            level_label="Previous Day Low",
            level_price=100.0,
            sweep_index=1,
            reclaim_index=2,
            trigger_index=2,
            sweep_price=99.2,
            defended_level=100.0,
            trigger_price=100.6,
            invalidation_level=99.1,
            primary=True,
            quality="tradable",
            notes=["Previous day low was swept and reclaimed quickly."],
        )

        candidate = engine.build_candidate_from_event(context, observation, event, option_type="CE", direction="LONG_CALL")

        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertEqual(candidate.setup_type, "bullish_reclaim_watch")
        self.assertTrue(candidate.ready_to_enter)
        self.assertTrue(any("first shallow defended retest" in note.lower() for note in candidate.notes))

    def test_stock_mode_allows_early_bearish_retest_hold_before_mature_breakdown(self) -> None:
        engine = HeuristicDecisionEngine()
        session = [
            self._make_candle(0, 100.0, 100.2, 99.8, 100.0),
            self._make_candle(1, 100.0, 101.4, 99.9, 101.2),
            self._make_candle(2, 101.2, 101.3, 100.2, 100.5),
            self._make_candle(3, 100.55, 100.78, 100.32, 100.4),
        ]
        context = self._build_context(session, previous_close=99.9).model_copy(
            update={
                "instrument": InstrumentState(
                    mode="stock",
                    label="CHAMBLFERT",
                    symbol="CHAMBLFERT",
                    security_id="1134",
                    exchange_segment="NSE_EQ",
                    instrument_type="EQUITY",
                    supports_options=False,
                    lot_size=1,
                )
            }
        )
        observation = self._build_observation(
            value_state="inflated",
            vwap=100.7,
            atr=0.9,
            strong_intent=False,
            weak_intent=False,
            session_high=101.4,
            session_low=99.8,
            stock_dow_bias="bearish",
            stock_dow_state="early-downtrend",
        )
        event = SweepEvent(
            side="buy",
            level_label="Previous Day High",
            level_price=100.8,
            sweep_index=1,
            reclaim_index=2,
            trigger_index=2,
            sweep_price=101.4,
            defended_level=100.8,
            trigger_price=100.2,
            invalidation_level=101.5,
            primary=True,
            quality="tradable",
            notes=["Previous day high was swept and rejected quickly."],
        )

        candidate = engine.build_candidate_from_event(context, observation, event, option_type="PE", direction="LONG_PUT")

        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertEqual(candidate.setup_type, "bearish_rejection_watch")
        self.assertTrue(candidate.ready_to_enter)
        self.assertTrue(any("first shallow defended retest" in note.lower() for note in candidate.notes))

    def test_stock_mode_builds_breakout_pullback_long_without_fresh_sweep(self) -> None:
        engine = HeuristicDecisionEngine()
        session = [
            self._make_candle(0, 100.0, 101.2, 99.9, 101.0),
            self._make_candle(1, 101.0, 102.4, 100.9, 102.2),
            self._make_candle(2, 102.2, 103.6, 102.1, 103.4),
            self._make_candle(3, 103.4, 104.8, 103.3, 104.6),
            self._make_candle(4, 104.6, 105.1, 104.5, 104.9),
            self._make_candle(5, 104.95, 105.15, 104.8, 105.0),
            self._make_candle(6, 104.98, 105.4, 104.88, 105.3),
            self._make_candle(7, 105.3, 105.95, 105.15, 105.85),
            self._make_candle(8, 105.85, 106.4, 105.7, 106.25),
        ]
        context = self._build_context(session, previous_close=99.8).model_copy(
            update={
                "instrument": InstrumentState(
                    mode="stock",
                    label="SAIL",
                    symbol="SAIL",
                    security_id="2963",
                    exchange_segment="NSE_EQ",
                    instrument_type="EQUITY",
                    supports_options=False,
                    lot_size=1,
                )
            }
        )
        observation = self._build_observation(
            day_type="gap-and-go",
            value_state="fair",
            atr=1.0,
            strong_intent=True,
            weak_intent=False,
            session_high=106.4,
            session_low=99.9,
            stock_dow_bias="bullish",
            stock_dow_state="higher-high-higher-low",
        )

        candidates = engine.build_stock_continuation_candidates(context, observation)

        candidate = next((item for item in candidates if item.setup_type == "stock_breakout_pullback_long"), None)
        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertTrue(candidate.ready_to_enter)
        self.assertEqual(candidate.option_type, "CE")

    def test_stock_mode_builds_breakdown_pullback_short_without_fresh_sweep(self) -> None:
        engine = HeuristicDecisionEngine()
        session = [
            self._make_candle(0, 106.0, 106.1, 104.8, 105.0),
            self._make_candle(1, 105.0, 105.1, 103.6, 103.9),
            self._make_candle(2, 103.9, 104.0, 102.4, 102.7),
            self._make_candle(3, 102.7, 102.8, 101.3, 101.6),
            self._make_candle(4, 101.6, 101.7, 100.9, 101.0),
            self._make_candle(5, 100.98, 101.18, 100.85, 101.0),
            self._make_candle(6, 101.02, 101.12, 100.45, 100.6),
            self._make_candle(7, 100.6, 100.72, 99.95, 100.1),
            self._make_candle(8, 100.08, 100.18, 99.55, 99.7),
        ]
        context = self._build_context(session, previous_close=106.2).model_copy(
            update={
                "instrument": InstrumentState(
                    mode="stock",
                    label="SAIL",
                    symbol="SAIL",
                    security_id="2963",
                    exchange_segment="NSE_EQ",
                    instrument_type="EQUITY",
                    supports_options=False,
                    lot_size=1,
                )
            }
        )
        observation = self._build_observation(
            day_type="gap-and-go",
            value_state="discount",
            atr=1.0,
            strong_intent=True,
            weak_intent=False,
            session_high=106.1,
            session_low=99.55,
            stock_dow_bias="bearish",
            stock_dow_state="lower-high-lower-low",
        )

        candidates = engine.build_stock_continuation_candidates(context, observation)

        candidate = next((item for item in candidates if item.setup_type == "stock_breakout_pullback_short"), None)
        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertTrue(candidate.ready_to_enter)
        self.assertEqual(candidate.option_type, "PE")
        self.assertGreaterEqual(candidate.score, 68.0)
        self.assertTrue(any("discount pricing is acceptable" in note.lower() for note in candidate.notes))

    def test_stock_mode_builds_first_pullback_trend_short_candidate(self) -> None:
        engine = HeuristicDecisionEngine()
        session = [
            self._make_candle(0, 100.0, 100.2, 96.4, 96.8),
            self._make_candle(1, 96.8, 97.0, 94.8, 95.2),
            self._make_candle(2, 95.3, 95.6, 94.2, 94.6),
            self._make_candle(3, 94.7, 95.4, 94.5, 95.3),
            self._make_candle(4, 95.2, 96.1, 95.1, 95.9),
            self._make_candle(5, 95.9, 96.0, 94.4, 94.6),
        ]
        context = self._build_context(session, previous_close=101.5).model_copy(
            update={
                "instrument": InstrumentState(
                    mode="stock",
                    label="AMBER",
                    symbol="AMBER",
                    security_id="1185",
                    exchange_segment="NSE_EQ",
                    instrument_type="EQUITY",
                    supports_options=False,
                    lot_size=1,
                )
            }
        )
        observation = self._build_observation(
            day_type="gap-and-go",
            value_state="discount",
            atr=1.3,
            strong_intent=True,
            weak_intent=False,
            session_high=100.2,
            session_low=94.2,
            vwap=96.9,
            stock_dow_bias="bearish",
            stock_dow_state="lower-high-lower-low",
        )

        candidates = engine.build_stock_continuation_candidates(context, observation)

        candidate = next((item for item in candidates if item.setup_type == "stock_first_pullback_trend_short"), None)
        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertTrue(candidate.ready_to_enter)
        self.assertEqual(candidate.option_type, "PE")
        self.assertGreaterEqual(candidate.score, 70.0)
        self.assertTrue(any("first defended retracement" in note.lower() for note in candidate.notes))

    def test_stock_mode_builds_early_retracement_reclaim_long_before_expansion(self) -> None:
        engine = HeuristicDecisionEngine()
        session = [
            self._make_candle(0, 100.0, 102.1, 99.9, 101.8),
            self._make_candle(1, 101.8, 103.3, 101.7, 103.0),
            self._make_candle(2, 103.0, 104.0, 102.9, 103.8),
            self._make_candle(3, 103.8, 104.0, 102.7, 103.0),
            self._make_candle(4, 103.0, 103.2, 102.4, 102.7),
            self._make_candle(5, 102.7, 102.9, 102.35, 102.6),
            self._make_candle(6, 102.75, 103.25, 102.55, 103.0),
        ]
        context = self._build_context(session, previous_close=99.4).model_copy(
            update={
                "instrument": InstrumentState(
                    mode="stock",
                    label="EMAMI",
                    symbol="EMAMI",
                    security_id="13517",
                    exchange_segment="NSE_EQ",
                    instrument_type="EQUITY",
                    supports_options=False,
                    lot_size=1,
                )
            }
        )
        observation = self._build_observation(
            day_type="gap-and-go",
            value_state="inflated",
            atr=1.0,
            strong_intent=True,
            weak_intent=False,
            session_high=104.0,
            session_low=99.9,
            vwap=102.4,
            stock_dow_bias="bullish",
            stock_dow_state="higher-high-higher-low",
            stock_nifty_bias="neutral",
            stock_nifty_state="nifty_value_churn",
        )

        candidates = engine.build_stock_continuation_candidates(context, observation)

        candidate = next((item for item in candidates if item.setup_type == "stock_early_retracement_reclaim_long"), None)
        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertTrue(candidate.ready_to_enter)
        self.assertGreaterEqual(candidate.score, 68.0)
        self.assertTrue(any("seller exhaustion" in note.lower() for note in candidate.notes))
        self.assertTrue(any("full quantity" in note.lower() for note in candidate.notes))

    def test_stock_mode_builds_early_retracement_reclaim_short_before_expansion(self) -> None:
        engine = HeuristicDecisionEngine()
        session = [
            self._make_candle(0, 104.0, 104.1, 101.9, 102.2),
            self._make_candle(1, 102.2, 102.3, 100.8, 101.0),
            self._make_candle(2, 101.0, 101.1, 99.9, 100.2),
            self._make_candle(3, 100.2, 101.3, 100.0, 101.0),
            self._make_candle(4, 101.0, 101.6, 100.8, 101.3),
            self._make_candle(5, 101.3, 101.65, 101.05, 101.4),
            self._make_candle(6, 101.25, 101.45, 100.8, 101.0),
        ]
        context = self._build_context(session, previous_close=104.4).model_copy(
            update={
                "instrument": InstrumentState(
                    mode="stock",
                    label="EMAMI",
                    symbol="EMAMI",
                    security_id="13517",
                    exchange_segment="NSE_EQ",
                    instrument_type="EQUITY",
                    supports_options=False,
                    lot_size=1,
                )
            }
        )
        observation = self._build_observation(
            day_type="gap-and-go",
            value_state="discount",
            atr=1.0,
            strong_intent=True,
            weak_intent=False,
            session_high=104.1,
            session_low=99.9,
            vwap=101.6,
            stock_dow_bias="bearish",
            stock_dow_state="lower-high-lower-low",
            stock_nifty_bias="neutral",
            stock_nifty_state="nifty_value_churn",
        )

        candidates = engine.build_stock_continuation_candidates(context, observation)

        candidate = next((item for item in candidates if item.setup_type == "stock_early_retracement_reclaim_short"), None)
        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertTrue(candidate.ready_to_enter)
        self.assertGreaterEqual(candidate.score, 68.0)
        self.assertTrue(any("buyer exhaustion" in note.lower() for note in candidate.notes))
        self.assertTrue(any("full quantity" in note.lower() for note in candidate.notes))

    def test_stock_mode_blocks_opening_gap_up_retracement_short_candidate(self) -> None:
        engine = HeuristicDecisionEngine()
        session = [
            self._make_candle(0, 100.0, 104.2, 99.8, 103.8),
            self._make_candle(1, 103.8, 104.1, 103.2, 103.4),
            self._make_candle(2, 103.4, 103.8, 102.8, 103.0),
            self._make_candle(3, 103.0, 103.6, 102.9, 103.3),
            self._make_candle(4, 103.3, 103.7, 103.1, 103.5),
            self._make_candle(5, 103.4, 103.5, 102.4, 102.6),
        ]
        context = self._build_context(session, previous_close=98.4).model_copy(
            update={
                "instrument": InstrumentState(
                    mode="stock",
                    label="GLAND",
                    symbol="GLAND",
                    security_id="1186",
                    exchange_segment="NSE_EQ",
                    instrument_type="EQUITY",
                    supports_options=False,
                    lot_size=1,
                )
            }
        )
        observation = self._build_observation(
            day_type="gap-and-go",
            gap=5.4,
            opening_confirmation="gap-up-confirmed",
            previous_day_bias="bullish-recovery",
            crowding_bias="balanced",
            session_phase="opening-map",
            value_state="fair",
            atr=1.0,
            strong_intent=True,
            weak_intent=False,
            session_high=104.2,
            session_low=99.8,
            opening_range_high=104.2,
            opening_range_low=99.8,
            vwap=103.1,
        )

        candidates = engine.build_stock_continuation_candidates(context, observation)

        candidate = next((item for item in candidates if item.setup_type == "stock_first_pullback_trend_short"), None)
        self.assertIsNone(candidate)

    def test_stock_mode_blocks_opening_gap_up_trap_risk_short_candidate(self) -> None:
        engine = HeuristicDecisionEngine()
        session = [
            self._make_candle(0, 103.0, 103.4, 99.0, 99.6),
            self._make_candle(1, 99.8, 102.2, 99.7, 101.8),
            self._make_candle(2, 101.7, 101.9, 100.5, 101.0),
            self._make_candle(3, 101.0, 101.4, 100.6, 100.9),
            self._make_candle(4, 100.8, 101.2, 100.2, 100.5),
            self._make_candle(5, 100.4, 100.7, 99.8, 99.9),
        ]
        context = self._build_context(session, previous_close=100.0).model_copy(
            update={
                "instrument": InstrumentState(
                    mode="stock",
                    label="GLAND",
                    symbol="GLAND",
                    security_id="1186",
                    exchange_segment="NSE_EQ",
                    instrument_type="EQUITY",
                    supports_options=False,
                    lot_size=1,
                )
            }
        )
        observation = self._build_observation(
            day_type="gap-and-go",
            gap=3.0,
            opening_confirmation="gap-up-trap-risk",
            previous_day_bias="bullish-recovery",
            crowding_bias="balanced",
            session_phase="primary-trap-window",
            value_state="fair",
            atr=1.0,
            strong_intent=True,
            weak_intent=False,
            session_high=103.4,
            session_low=99.0,
            opening_range_high=103.4,
            opening_range_low=99.0,
            vwap=100.6,
        )

        candidates = engine.build_stock_continuation_candidates(context, observation)

        candidate = next((item for item in candidates if item.setup_type == "stock_first_pullback_trend_short"), None)
        self.assertIsNone(candidate)

    def test_stock_mode_blocks_opening_gap_down_retracement_long_candidate(self) -> None:
        engine = HeuristicDecisionEngine()
        session = [
            self._make_candle(0, 100.0, 100.2, 95.8, 96.1),
            self._make_candle(1, 96.1, 96.6, 95.9, 96.3),
            self._make_candle(2, 96.3, 96.7, 96.1, 96.5),
            self._make_candle(3, 96.5, 97.0, 96.3, 96.7),
            self._make_candle(4, 96.7, 97.2, 96.4, 96.9),
            self._make_candle(5, 96.8, 97.3, 96.6, 97.1),
        ]
        context = self._build_context(session, previous_close=101.4).model_copy(
            update={
                "instrument": InstrumentState(
                    mode="stock",
                    label="AMBER",
                    symbol="AMBER",
                    security_id="1185",
                    exchange_segment="NSE_EQ",
                    instrument_type="EQUITY",
                    supports_options=False,
                    lot_size=1,
                )
            }
        )
        observation = self._build_observation(
            day_type="gap-and-go",
            gap=-4.6,
            opening_confirmation="gap-down-confirmed",
            previous_day_bias="bearish-continuation",
            crowding_bias="balanced",
            session_phase="opening-map",
            value_state="fair",
            atr=1.0,
            strong_intent=True,
            weak_intent=False,
            session_high=100.2,
            session_low=95.8,
            opening_range_high=100.2,
            opening_range_low=95.8,
            vwap=96.5,
        )

        candidates = engine.build_stock_continuation_candidates(context, observation)

        candidate = next((item for item in candidates if item.setup_type == "stock_first_pullback_trend_long"), None)
        self.assertIsNone(candidate)

    def test_stock_mode_same_trend_reentry_after_profitable_winner_gets_boost(self) -> None:
        engine = HeuristicDecisionEngine()
        session = [
            self._make_candle(0, 100.0, 101.2, 99.9, 101.0),
            self._make_candle(1, 101.0, 102.4, 100.9, 102.2),
            self._make_candle(2, 102.2, 103.6, 102.1, 103.4),
            self._make_candle(3, 103.4, 104.8, 103.3, 104.6),
            self._make_candle(4, 104.6, 105.1, 104.5, 104.9),
            self._make_candle(5, 104.95, 105.15, 104.8, 105.0),
            self._make_candle(6, 104.98, 105.4, 104.88, 105.3),
            self._make_candle(7, 105.3, 105.95, 105.15, 105.85),
            self._make_candle(8, 105.85, 106.4, 105.7, 106.25),
        ]
        winner = SimulatedTrade(
            trade_id="winner-1",
            status="CLOSED",
            direction="LONG_STOCK",
            instrument_mode="stock",
            instrument_label="SAIL",
            price_mode="cash",
            trade_security_id="2963",
            quote_exchange_segment="NSE_EQ",
            option_type="CE",
            strike=0,
            symbol="SAIL EQ",
            quantity=100,
            open_quantity=0,
            closed_quantity=100,
            entry_time=datetime.fromisoformat("2026-05-13T10:00:00"),
            entry_price=103.5,
            entry_spot_price=103.5,
            entry_option_price=103.5,
            current_price=105.5,
            current_option_price=105.5,
            stop_price=102.7,
            stop_option_price=102.7,
            target_price=106.0,
            target_option_price=106.0,
            invalidation_level=102.7,
            booked_pnl=180.0,
            pnl=180.0,
        )
        base_context = self._build_context(session, previous_close=99.8).model_copy(
            update={
                "instrument": InstrumentState(
                    mode="stock",
                    label="SAIL",
                    symbol="SAIL",
                    security_id="2963",
                    exchange_segment="NSE_EQ",
                    instrument_type="EQUITY",
                    supports_options=False,
                    lot_size=1,
                )
            }
        )
        reentry_context = base_context.model_copy(update={"recent_closed_trades": [winner]})
        observation = self._build_observation(
            day_type="gap-and-go",
            value_state="fair",
            atr=1.0,
            strong_intent=True,
            weak_intent=False,
            session_high=106.4,
            session_low=99.9,
            stock_dow_bias="bullish",
            stock_dow_state="higher-high-higher-low",
        )

        base_candidate = next(
            item for item in engine.build_stock_continuation_candidates(base_context, observation) if item.setup_type == "stock_breakout_pullback_long"
        )
        reentry_candidate = next(
            item for item in engine.build_stock_continuation_candidates(reentry_context, observation) if item.setup_type == "stock_breakout_pullback_long"
        )

        self.assertGreater(reentry_candidate.score, base_candidate.score)
        self.assertTrue(any("same-trend re-entry" in note.lower() for note in reentry_candidate.notes))

    def test_stock_reentry_requires_fresh_primary_sweep(self) -> None:
        engine = HeuristicDecisionEngine()
        session = [
            self._make_candle(0, 100.0, 100.2, 99.8, 100.1),
            self._make_candle(1, 100.1, 100.3, 98.9, 99.2),
            self._make_candle(2, 99.2, 100.8, 99.1, 100.7),
            self._make_candle(3, 100.7, 101.2, 100.5, 101.0),
        ]
        recent_long = SimulatedTrade(
            trade_id="stock-reentry-1",
            status="CLOSED",
            direction="LONG_STOCK",
            instrument_mode="stock",
            instrument_label="SAIL",
            price_mode="cash",
            trade_security_id="2963",
            quote_exchange_segment="NSE_EQ",
            option_type="CE",
            strike=0,
            symbol="SAIL EQ",
            quantity=1,
            open_quantity=0,
            closed_quantity=1,
            entry_time=session[1].timestamp,
            entry_price=99.4,
            entry_spot_price=99.4,
            entry_option_price=99.4,
            current_price=100.6,
            current_option_price=100.6,
            stop_price=98.8,
            stop_option_price=98.8,
            target_price=101.2,
            target_option_price=101.2,
            invalidation_level=98.8,
            exit_time=session[-1].timestamp,
            exit_price=100.6,
            exit_option_price=100.6,
            booked_pnl=1.2,
            pnl=1.2,
        )
        context = self._build_context(
            session,
            previous_close=100.2,
            recent_closed_trades=[recent_long],
        ).model_copy(
            update={
                "instrument": InstrumentState(
                    mode="stock",
                    label="SAIL",
                    symbol="SAIL",
                    security_id="2963",
                    exchange_segment="NSE_EQ",
                    instrument_type="EQUITY",
                    supports_options=False,
                    lot_size=1,
                )
            }
        )
        event = SweepEvent(
            side="sell",
            level_label="Previous Day Low",
            level_price=99.0,
            sweep_index=1,
            reclaim_index=2,
            trigger_index=2,
            sweep_price=98.9,
            defended_level=99.0,
            trigger_price=100.4,
            invalidation_level=98.7,
            primary=True,
            quality="tradable",
            notes=["Previous day low was swept and reclaimed quickly."],
        )
        observation = self._build_observation(
            value_state="discount",
            vwap=100.1,
            atr=0.9,
            strong_intent=False,
            session_high=101.2,
            session_low=98.9,
            stock_dow_bias="bullish",
            stock_dow_state="early-uptrend",
            sell_sweeps=[event],
        )

        candidates = engine.build_candidates(context, observation)
        candidate = next((item for item in candidates if item.setup_type == "bullish_reclaim_watch"), None)

        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertFalse(candidate.ready_to_enter)
        self.assertLess(candidate.score, engine.arm_threshold)
        self.assertTrue(any("brand-new primary liquidity sweep" in note.lower() for note in candidate.notes))

    def test_midday_stock_continuation_is_blocked_inside_balanced_churn(self) -> None:
        engine = HeuristicDecisionEngine()
        session = [
            self._make_candle(150, 100.0, 101.2, 99.9, 101.0),
            self._make_candle(151, 101.0, 102.4, 100.9, 102.2),
            self._make_candle(152, 102.2, 103.6, 102.1, 103.4),
            self._make_candle(153, 103.4, 104.8, 103.3, 104.6),
            self._make_candle(154, 104.6, 105.1, 104.5, 104.9),
            self._make_candle(155, 104.95, 105.15, 104.8, 105.0),
            self._make_candle(156, 104.98, 105.4, 104.88, 105.3),
            self._make_candle(157, 105.3, 105.95, 105.15, 105.85),
            self._make_candle(158, 105.72, 106.05, 105.7, 105.95),
        ]
        context = self._build_context(session, previous_close=99.8).model_copy(
            update={
                "instrument": InstrumentState(
                    mode="stock",
                    label="SAIL",
                    symbol="SAIL",
                    security_id="2963",
                    exchange_segment="NSE_EQ",
                    instrument_type="EQUITY",
                    supports_options=False,
                    lot_size=1,
                )
            }
        )
        observation = self._build_observation(
            session_phase="midday",
            day_type="gap-and-go",
            participation_state="fair_value_churn",
            range_state="balanced",
            overlap_ratio=0.67,
            value_state="fair",
            atr=1.0,
            strong_intent=False,
            weak_intent=False,
            session_high=106.05,
            session_low=99.9,
            stock_dow_bias="bullish",
            stock_dow_state="higher-high-higher-low",
        )

        candidates = engine.build_candidates(context, observation)
        candidate = next((item for item in candidates if item.setup_type == "stock_breakout_pullback_long"), None)

        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertFalse(candidate.ready_to_enter)
        self.assertLess(candidate.score, engine.arm_threshold)
        self.assertTrue(any("midday balanced churn" in note.lower() for note in candidate.notes))

    def test_stock_pending_setup_expires_after_three_candles(self) -> None:
        engine = HeuristicDecisionEngine()
        session = [
            self._make_candle(150, 100.0, 100.8, 99.8, 100.6),
            self._make_candle(151, 100.6, 100.9, 100.2, 100.7),
            self._make_candle(152, 100.7, 101.0, 100.4, 100.8),
            self._make_candle(153, 100.8, 101.1, 100.6, 100.9),
            self._make_candle(154, 100.9, 101.2, 100.7, 101.0),
        ]
        pending_setup = PendingSetup(
            setup_id="pending-stock-1",
            setup_type="bullish_reclaim_watch",
            direction="LONG_CALL",
            option_type="CE",
            trigger_price=100.95,
            invalidation_level=99.9,
            created_at=session[0].timestamp,
            updated_at=session[0].timestamp,
        )
        context = self._build_context(session, previous_close=99.8).model_copy(
            update={
                "instrument": InstrumentState(
                    mode="stock",
                    label="SAIL",
                    symbol="SAIL",
                    security_id="2963",
                    exchange_segment="NSE_EQ",
                    instrument_type="EQUITY",
                    supports_options=False,
                    lot_size=1,
                ),
                "pending_setup": pending_setup,
            }
        )
        observation = self._build_observation(
            session_phase="midday",
            day_type="gap-and-go",
            atr=0.8,
            strong_intent=False,
            weak_intent=False,
            session_high=101.2,
            session_low=99.8,
            stock_dow_bias="bullish",
            stock_dow_state="higher-high-higher-low",
        )

        decision = engine.evaluate_pending_setup(context, observation, [])

        self.assertIsNotNone(decision)
        assert decision is not None
        self.assertEqual(decision.pending_setup_action, "INVALIDATE")
        self.assertIn("expired after 5 candles", decision.reason)

    def test_stock_trade_frequency_guard_raises_thresholds(self) -> None:
        engine = HeuristicDecisionEngine()
        session = [
            self._make_candle(150, 100.0, 101.2, 99.9, 101.0),
            self._make_candle(151, 101.0, 102.4, 100.9, 102.2),
            self._make_candle(152, 102.2, 103.6, 102.1, 103.4),
            self._make_candle(153, 103.4, 104.8, 103.3, 104.6),
            self._make_candle(154, 104.6, 105.1, 104.5, 104.9),
            self._make_candle(155, 104.95, 105.15, 104.8, 105.0),
            self._make_candle(156, 104.98, 105.4, 104.88, 105.3),
            self._make_candle(157, 105.3, 105.95, 105.15, 105.85),
            self._make_candle(158, 105.85, 106.4, 105.7, 106.25),
        ]
        base_context = self._build_context(session, previous_close=99.8).model_copy(
            update={
                "instrument": InstrumentState(
                    mode="stock",
                    label="SAIL",
                    symbol="SAIL",
                    security_id="2963",
                    exchange_segment="NSE_EQ",
                    instrument_type="EQUITY",
                    supports_options=False,
                    lot_size=1,
                )
            }
        )
        pressured_context = base_context.model_copy(update={"portfolio_order_count_estimate": 72})
        observation = self._build_observation(
            day_type="gap-and-go",
            value_state="fair",
            atr=1.0,
            strong_intent=True,
            weak_intent=False,
            session_high=106.4,
            session_low=99.9,
            stock_dow_bias="bullish",
            stock_dow_state="higher-high-higher-low",
        )
        candidate = next(
            item for item in engine.build_stock_continuation_candidates(base_context, observation) if item.setup_type == "stock_breakout_pullback_long"
        )

        base_enter, base_arm, _ = engine._effective_entry_thresholds(base_context, observation, candidate)
        pressured_enter, pressured_arm, _ = engine._effective_entry_thresholds(pressured_context, observation, candidate)

        self.assertGreater(pressured_enter, base_enter)
        self.assertGreater(pressured_arm, base_arm)

    def test_observe_sets_bullish_stock_dow_bias_from_early_uptrend(self) -> None:
        engine = HeuristicDecisionEngine()
        session = [
            self._make_candle(0, 100.0, 100.8, 99.8, 100.7),
            self._make_candle(1, 100.7, 101.4, 100.6, 101.3),
            self._make_candle(2, 101.3, 101.9, 101.0, 101.8),
            self._make_candle(3, 101.8, 102.4, 101.5, 102.2),
            self._make_candle(4, 102.2, 103.0, 102.0, 102.8),
        ]
        context = self._build_context(session, previous_close=99.6).model_copy(
            update={
                "instrument": InstrumentState(
                    mode="stock",
                    label="SAIL",
                    symbol="SAIL",
                    security_id="2963",
                    exchange_segment="NSE_EQ",
                    instrument_type="EQUITY",
                    supports_options=False,
                    lot_size=1,
                )
            }
        )

        observation = engine.observe(context)

        self.assertEqual(observation.stock_dow_bias, "bullish")
        self.assertEqual(observation.stock_dow_state, "early-uptrend")

    def test_observe_sets_bullish_stock_nifty_bias_from_companion_context(self) -> None:
        engine = HeuristicDecisionEngine()
        session = [
            self._make_candle(0, 100.0, 100.8, 99.8, 100.7),
            self._make_candle(1, 100.7, 101.4, 100.6, 101.3),
            self._make_candle(2, 101.3, 101.9, 101.0, 101.8),
            self._make_candle(3, 101.8, 102.4, 101.5, 102.2),
            self._make_candle(4, 102.2, 103.0, 102.0, 102.8),
        ]
        companion_session = [
            Candle(timestamp=datetime(2026, 2, 18, 9, 15), open=22400.0, high=22435.0, low=22385.0, close=22418.0, volume=1000.0),
            Candle(timestamp=datetime(2026, 2, 18, 9, 16), open=22418.0, high=22455.0, low=22402.0, close=22442.0, volume=1100.0),
            Candle(timestamp=datetime(2026, 2, 18, 9, 17), open=22442.0, high=22448.0, low=22318.0, close=22336.0, volume=1250.0),
            Candle(timestamp=datetime(2026, 2, 18, 9, 18), open=22338.0, high=22482.0, low=22332.0, close=22470.0, volume=1400.0),
            Candle(timestamp=datetime(2026, 2, 18, 9, 19), open=22470.0, high=22510.0, low=22462.0, close=22498.0, volume=1450.0),
        ]
        context = self._build_context(session, previous_close=99.6).model_copy(
            update={
                "instrument": InstrumentState(
                    mode="stock",
                    label="SAIL",
                    symbol="SAIL",
                    security_id="2963",
                    exchange_segment="NSE_EQ",
                    instrument_type="EQUITY",
                    supports_options=False,
                    lot_size=1,
                ),
                "companion_symbol": "NIFTY",
                "companion_session_candles": companion_session,
                "companion_recent_candles": companion_session[-5:],
                "companion_current_candle": companion_session[-1],
                "companion_previous_day": PreviousDayLevels(high=22480.0, low=22340.0, close=22390.0),
                "companion_previous_day_candles": [
                    Candle(timestamp=datetime(2026, 2, 17, 15, 28), open=22370.0, high=22420.0, low=22340.0, close=22360.0, volume=1000.0),
                    Candle(timestamp=datetime(2026, 2, 17, 15, 29), open=22360.0, high=22480.0, low=22355.0, close=22390.0, volume=1000.0),
                ],
            }
        )

        observation = engine.observe(context)

        self.assertEqual(observation.stock_nifty_bias, "bullish")
        self.assertIn("bullish", observation.stock_nifty_state)

    def test_stock_continuation_is_blocked_when_nifty_bias_opposes(self) -> None:
        engine = HeuristicDecisionEngine()
        session = [
            self._make_candle(0, 100.0, 101.2, 99.9, 101.0),
            self._make_candle(1, 101.0, 102.4, 100.9, 102.2),
            self._make_candle(2, 102.2, 103.6, 102.1, 103.4),
            self._make_candle(3, 103.4, 104.8, 103.3, 104.6),
            self._make_candle(4, 104.6, 105.1, 104.5, 104.9),
            self._make_candle(5, 104.95, 105.15, 104.8, 105.0),
            self._make_candle(6, 104.98, 105.4, 104.88, 105.3),
            self._make_candle(7, 105.3, 105.95, 105.15, 105.85),
            self._make_candle(8, 105.85, 106.4, 105.7, 106.25),
        ]
        context = self._build_context(session, previous_close=99.8).model_copy(
            update={
                "instrument": InstrumentState(
                    mode="stock",
                    label="SAIL",
                    symbol="SAIL",
                    security_id="2963",
                    exchange_segment="NSE_EQ",
                    instrument_type="EQUITY",
                    supports_options=False,
                    lot_size=1,
                )
            }
        )
        observation = self._build_observation(
            day_type="gap-and-go",
            value_state="fair",
            atr=1.0,
            strong_intent=True,
            weak_intent=False,
            session_high=106.4,
            session_low=99.9,
            stock_dow_bias="bullish",
            stock_dow_state="higher-high-higher-low",
            stock_nifty_bias="bearish",
            stock_nifty_state="bearish_trend",
        )

        candidates = engine.build_candidates(context, observation)
        candidate = next((item for item in candidates if item.setup_type == "stock_breakout_pullback_long"), None)

        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertFalse(candidate.ready_to_enter)
        self.assertLess(candidate.score, engine.arm_threshold)
        self.assertTrue(any("nifty direction is opposing" in note.lower() for note in candidate.notes))

    def test_extreme_gap_reset_blocks_early_bullish_reclaim_chase(self) -> None:
        engine = HeuristicDecisionEngine()
        session = [
            self._make_candle(0, 130.0, 132.0, 99.0, 100.0),
            self._make_candle(1, 100.0, 100.8, 99.8, 100.3),
            self._make_candle(2, 100.2, 101.0, 100.1, 100.7),
            self._make_candle(3, 100.7, 101.4, 100.5, 101.0),
            self._make_candle(4, 101.0, 101.7, 100.8, 101.2),
            self._make_candle(5, 101.2, 102.0, 101.0, 101.5),
            self._make_candle(6, 101.5, 102.4, 101.3, 102.1),
            self._make_candle(7, 102.1, 104.6, 101.9, 104.0),
        ]
        context = self._build_context(session, previous_close=100.0)
        observation = self._build_observation(
            atr=6.0,
            gap=30.0,
            large_gap_reset=True,
            opening_confirmation="gap-up-trap-risk",
            value_state="discount",
            vwap=104.0,
            session_phase="opening-map",
            session_high=104.6,
            session_low=99.0,
        )
        event = SweepEvent(
            side="sell",
            level_label="Previous Day Low",
            level_price=99.0,
            sweep_index=5,
            reclaim_index=6,
            trigger_index=6,
            sweep_price=99.0,
            defended_level=99.0,
            trigger_price=102.0,
            invalidation_level=98.5,
            primary=True,
            quality="tradable",
            notes=["Violent opening washout reclaimed quickly."],
        )

        candidate = engine.build_candidate_from_event(context, observation, event, option_type="CE", direction="LONG_CALL")

        self.assertIsNone(candidate)

    def test_extreme_gap_reset_allows_later_retest_confirmed_reclaim(self) -> None:
        engine = HeuristicDecisionEngine()
        session = [self._make_candle(0, 130.0, 132.0, 99.0, 100.0)]
        session.extend(
            [
                self._make_candle(index, 100.0 + (index % 3) * 0.2, 100.7 + (index % 2) * 0.2, 99.6, 100.1 + (index % 4) * 0.15)
                for index in range(1, 20)
            ]
        )
        session.extend(
            [
                self._make_candle(20, 100.2, 100.5, 98.8, 99.2),
                self._make_candle(21, 99.3, 102.0, 99.1, 101.6),
                self._make_candle(22, 101.8, 103.5, 101.5, 103.1),
                self._make_candle(23, 103.0, 103.4, 102.5, 102.9),
            ]
        )
        context = self._build_context(session, previous_close=100.0)
        observation = self._build_observation(
            atr=6.0,
            gap=30.0,
            large_gap_reset=True,
            opening_confirmation="gap-up-trap-risk",
            value_state="discount",
            vwap=101.8,
            session_phase="primary-trap-window",
            session_high=103.5,
            session_low=98.8,
        )
        event = SweepEvent(
            side="sell",
            level_label="Previous Day Low",
            level_price=99.0,
            sweep_index=20,
            reclaim_index=21,
            trigger_index=21,
            sweep_price=98.8,
            defended_level=99.0,
            trigger_price=101.8,
            invalidation_level=98.4,
            primary=True,
            quality="tradable",
            notes=["Opening reset eventually reclaimed after a deeper stabilization."],
        )

        candidate = engine.build_candidate_from_event(context, observation, event, option_type="CE", direction="LONG_CALL")

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.setup_type, "bullish_reclaim_watch")

    def test_gap_down_recovery_short_requires_major_rejection_and_loss_of_acceptance(self) -> None:
        engine = HeuristicDecisionEngine()
        session = [
            self._make_candle(0, 98.0, 98.2, 95.0, 95.4),
            self._make_candle(1, 95.5, 96.6, 95.2, 96.4),
            self._make_candle(2, 96.5, 97.6, 96.4, 97.4),
            self._make_candle(3, 97.3, 98.2, 97.1, 97.9),
        ]
        context = self._build_context(session, previous_close=100.0)
        observation = self._build_observation(
            gap=-2.0,
            opening_confirmation="gap-down-confirmed",
            session_phase="opening-map",
            value_state="fair",
            vwap=96.8,
            atr=1.8,
            opening_range_high=98.2,
            opening_range_low=95.0,
            session_high=98.2,
            session_low=95.0,
        )
        event = SweepEvent(
            side="buy",
            level_label="Same-Day Swing High 09:18",
            level_price=97.9,
            sweep_index=3,
            reclaim_index=3,
            trigger_index=3,
            sweep_price=98.2,
            defended_level=97.9,
            trigger_price=97.0,
            invalidation_level=98.4,
            primary=True,
            quality="tradable",
            notes=["Early rebound started to stall."],
        )

        candidate = engine.build_candidate_from_event(context, observation, event, option_type="PE", direction="LONG_PUT")

        self.assertIsNone(candidate)

    def test_gap_down_recovery_long_requires_retest_hold_after_first_burst(self) -> None:
        engine = HeuristicDecisionEngine()
        session = [
            self._make_candle(0, 98.0, 98.2, 95.0, 95.4),
            self._make_candle(1, 95.5, 96.4, 95.3, 96.2),
            self._make_candle(2, 96.2, 97.2, 96.0, 97.0),
            self._make_candle(3, 97.0, 98.0, 97.6, 97.8),
        ]
        context = self._build_context(session, previous_close=96.6)
        observation = self._build_observation(
            gap=-1.4,
            opening_confirmation="gap-down-confirmed",
            session_phase="opening-map",
            value_state="discount",
            vwap=96.5,
            atr=1.2,
            opening_range_high=98.2,
            opening_range_low=95.0,
            session_high=98.0,
            session_low=95.0,
        )
        event = SweepEvent(
            side="sell",
            level_label="Previous Day Low",
            level_price=95.2,
            sweep_index=1,
            reclaim_index=2,
            trigger_index=2,
            sweep_price=95.0,
            defended_level=95.2,
            trigger_price=97.1,
            invalidation_level=94.8,
            primary=True,
            quality="tradable",
            notes=["Gap-down morning bounced hard from previous day low."],
        )

        candidate = engine.build_candidate_from_event(context, observation, event, option_type="CE", direction="LONG_CALL")

        self.assertIsNone(candidate)

    def test_extension_filter_blocks_non_primary_chase_entries(self) -> None:
        engine = HeuristicDecisionEngine()
        session = [
            self._make_candle(0, 100.0, 100.3, 99.8, 100.2),
            self._make_candle(1, 100.2, 102.2, 100.1, 102.0),
            self._make_candle(2, 102.0, 104.3, 101.9, 104.1),
            self._make_candle(3, 104.1, 105.1, 104.0, 105.0),
        ]
        context = self._build_context(session, previous_close=99.7)
        observation = self._build_observation(
            value_state="inflated",
            vwap=101.2,
            atr=0.9,
            strong_intent=True,
            session_high=105.1,
            prior_session_high=104.3,
        )
        event = SweepEvent(
            side="sell",
            level_label="Prior Hour Low",
            level_price=102.0,
            sweep_index=1,
            reclaim_index=1,
            trigger_index=1,
            sweep_price=101.9,
            defended_level=102.0,
            trigger_price=102.3,
            invalidation_level=101.7,
            primary=False,
            quality="tradable",
            notes=["Retest held after earlier push."],
        )

        candidate = engine.build_candidate_from_event(context, observation, event, option_type="CE", direction="LONG_CALL")

        self.assertIsNone(candidate)

    def test_after_three_pm_no_fresh_entries_are_allowed(self) -> None:
        engine = HeuristicDecisionEngine()
        session = [
            Candle(timestamp="2026-02-18T15:04:00", open=100.0, high=100.4, low=99.8, close=100.2, volume=1000),
            Candle(timestamp="2026-02-18T15:05:00", open=100.2, high=100.8, low=100.1, close=100.7, volume=1100),
        ]
        context = self._build_context(session, previous_close=99.8)
        observation = self._build_observation(session_phase="late-session")
        candidate = SetupCandidate(
            setup_type="bullish_reclaim_watch",
            direction="LONG_CALL",
            option_type="CE",
            trigger_basis="close_above",
            trigger_price=100.4,
            invalidation_level=99.9,
            defended_level=100.0,
            target_spot_price=101.8,
            first_target_price=101.1,
            score=85.0,
            ready_to_enter=True,
            notes=["Fresh reclaim but not exceptional enough for the closing stretch."],
            rule_ids=["R58"],
            event=SweepEvent(
                side="sell",
                level_label="Previous Day Low",
                level_price=99.8,
                sweep_index=0,
                reclaim_index=1,
                trigger_index=1,
                sweep_price=99.8,
                defended_level=99.8,
                trigger_price=100.4,
                invalidation_level=99.6,
                primary=True,
                quality="tradable",
                notes=["Recovered from a known low."],
            ),
        )

        decision = engine.decide_entry(context, observation, [candidate])

        self.assertEqual(decision.action, TradeAction.no_trade)
        self.assertIn("15:00", decision.reason)

    def test_fresh_entries_remain_allowed_before_three_pm(self) -> None:
        engine = HeuristicDecisionEngine()
        session = [
            Candle(timestamp="2026-02-18T11:04:00", open=100.0, high=100.4, low=99.8, close=100.2, volume=1000),
            Candle(timestamp="2026-02-18T11:05:00", open=100.2, high=100.8, low=100.1, close=100.7, volume=1100),
        ]
        context = self._build_context(session, previous_close=99.8)
        observation = self._build_observation(session_phase="midday")
        candidate = SetupCandidate(
            setup_type="bullish_reclaim_watch",
            direction="LONG_CALL",
            option_type="CE",
            trigger_basis="close_above",
            trigger_price=100.4,
            invalidation_level=99.9,
            defended_level=100.0,
            target_spot_price=101.8,
            first_target_price=101.1,
            score=85.0,
            ready_to_enter=True,
            notes=["Fresh reclaim remains valid before the 15:00 cutoff."],
            rule_ids=["R58"],
            event=SweepEvent(
                side="sell",
                level_label="Previous Day Low",
                level_price=99.8,
                sweep_index=0,
                reclaim_index=1,
                trigger_index=1,
                sweep_price=99.8,
                defended_level=99.8,
                trigger_price=100.4,
                invalidation_level=99.6,
                primary=True,
                quality="tradable",
                notes=["Recovered from a known low."],
            ),
        )

        decision = engine.decide_entry(context, observation, [candidate])

        self.assertEqual(decision.action, TradeAction.enter_call)

    def test_nifty_can_enter_reversal_from_banknifty_round_sweep_confirmation(self) -> None:
        engine = HeuristicDecisionEngine()
        session = [
            Candle(timestamp="2026-02-18T09:15:00", open=23340.0, high=23345.0, low=23308.0, close=23312.0, volume=1000),
            Candle(timestamp="2026-02-18T09:16:00", open=23312.0, high=23318.0, low=23292.0, close=23298.0, volume=1100),
            Candle(timestamp="2026-02-18T09:17:00", open=23298.0, high=23305.0, low=23280.0, close=23288.0, volume=1050),
            Candle(timestamp="2026-02-18T09:18:00", open=23288.0, high=23318.0, low=23284.0, close=23316.0, volume=1250),
            Candle(timestamp="2026-02-18T09:19:00", open=23316.0, high=23328.0, low=23310.0, close=23324.0, volume=1300),
        ]
        context = self._build_context(session, previous_close=23260.0).model_copy(
            update={
                "companion_symbol": "BANKNIFTY",
                "companion_session_candles": [
                    Candle(timestamp="2026-02-18T09:15:00", open=50040.0, high=50065.0, low=50010.0, close=50020.0, volume=1000),
                    Candle(timestamp="2026-02-18T09:16:00", open=50020.0, high=50024.0, low=49992.0, close=50002.0, volume=1050),
                    Candle(timestamp="2026-02-18T09:17:00", open=50002.0, high=50008.0, low=49972.0, close=49988.0, volume=1100),
                    Candle(timestamp="2026-02-18T09:18:00", open=49988.0, high=50034.0, low=49980.0, close=50022.0, volume=1300),
                    Candle(timestamp="2026-02-18T09:19:00", open=50022.0, high=50048.0, low=50016.0, close=50042.0, volume=1350),
                ],
                "companion_current_candle": Candle(
                    timestamp="2026-02-18T09:19:00", open=50022.0, high=50048.0, low=50016.0, close=50042.0, volume=1350
                ),
                "companion_recent_candles": [
                    Candle(timestamp="2026-02-18T09:15:00", open=50040.0, high=50065.0, low=50010.0, close=50020.0, volume=1000),
                    Candle(timestamp="2026-02-18T09:16:00", open=50020.0, high=50024.0, low=49992.0, close=50002.0, volume=1050),
                    Candle(timestamp="2026-02-18T09:17:00", open=50002.0, high=50008.0, low=49972.0, close=49988.0, volume=1100),
                    Candle(timestamp="2026-02-18T09:18:00", open=49988.0, high=50034.0, low=49980.0, close=50022.0, volume=1300),
                    Candle(timestamp="2026-02-18T09:19:00", open=50022.0, high=50048.0, low=50016.0, close=50042.0, volume=1350),
                ],
                "companion_previous_day": PreviousDayLevels(high=50120.0, low=49840.0, close=50010.0),
                "companion_previous_day_candles": [
                    Candle(timestamp="2026-02-17T15:28:00", open=50000.0, high=50080.0, low=49920.0, close=50010.0, volume=1000),
                ],
            }
        )
        observation = engine.observe(context)

        candidates = engine.build_candidates(context, observation)
        decision = engine.decide_entry(context, observation, candidates)

        self.assertTrue(any(candidate.setup_type == "companion_round_reclaim_long" for candidate in candidates))
        self.assertEqual(decision.action, TradeAction.enter_call)

    def test_nifty_companion_requires_banknifty_follow_through_after_reclaim(self) -> None:
        engine = HeuristicDecisionEngine()
        session = [
            Candle(timestamp="2026-02-18T09:15:00", open=23340.0, high=23345.0, low=23308.0, close=23312.0, volume=1000),
            Candle(timestamp="2026-02-18T09:16:00", open=23312.0, high=23318.0, low=23292.0, close=23298.0, volume=1100),
            Candle(timestamp="2026-02-18T09:17:00", open=23298.0, high=23305.0, low=23280.0, close=23288.0, volume=1050),
            Candle(timestamp="2026-02-18T09:18:00", open=23288.0, high=23318.0, low=23284.0, close=23316.0, volume=1250),
        ]
        context = self._build_context(session, previous_close=23260.0).model_copy(
            update={
                "companion_symbol": "BANKNIFTY",
                "companion_session_candles": [
                    Candle(timestamp="2026-02-18T09:15:00", open=50040.0, high=50065.0, low=50010.0, close=50020.0, volume=1000),
                    Candle(timestamp="2026-02-18T09:16:00", open=50020.0, high=50024.0, low=49992.0, close=50002.0, volume=1050),
                    Candle(timestamp="2026-02-18T09:17:00", open=50002.0, high=50008.0, low=49972.0, close=49988.0, volume=1100),
                    Candle(timestamp="2026-02-18T09:18:00", open=49988.0, high=50034.0, low=49980.0, close=50022.0, volume=1300),
                ],
                "companion_current_candle": Candle(
                    timestamp="2026-02-18T09:18:00", open=49988.0, high=50034.0, low=49980.0, close=50022.0, volume=1300
                ),
                "companion_recent_candles": [
                    Candle(timestamp="2026-02-18T09:15:00", open=50040.0, high=50065.0, low=50010.0, close=50020.0, volume=1000),
                    Candle(timestamp="2026-02-18T09:16:00", open=50020.0, high=50024.0, low=49992.0, close=50002.0, volume=1050),
                    Candle(timestamp="2026-02-18T09:17:00", open=50002.0, high=50008.0, low=49972.0, close=49988.0, volume=1100),
                    Candle(timestamp="2026-02-18T09:18:00", open=49988.0, high=50034.0, low=49980.0, close=50022.0, volume=1300),
                ],
            }
        )
        observation = engine.observe(context)

        candidates = engine.build_candidates(context, observation)

        self.assertFalse(any(candidate.setup_type == "companion_round_reclaim_long" for candidate in candidates))

    def test_nifty_companion_rejects_round_front_run_inside_compressing_range(self) -> None:
        engine = HeuristicDecisionEngine()
        session = [
            Candle(timestamp="2026-02-18T09:15:00", open=24150.0, high=24158.0, low=24142.0, close=24154.0, volume=1000),
            Candle(timestamp="2026-02-18T09:16:00", open=24154.0, high=24170.0, low=24150.0, close=24166.0, volume=1100),
            Candle(timestamp="2026-02-18T09:17:00", open=24166.0, high=24183.0, low=24160.0, close=24178.0, volume=1200),
            Candle(timestamp="2026-02-18T09:18:00", open=24178.0, high=24180.0, low=24160.0, close=24164.0, volume=1250),
            Candle(timestamp="2026-02-18T09:19:00", open=24164.0, high=24168.0, low=24150.0, close=24156.0, volume=1300),
        ]
        context = self._build_context(session, previous_close=24120.0).model_copy(
            update={
                "companion_symbol": "BANKNIFTY",
                "companion_session_candles": [
                    Candle(timestamp="2026-02-18T09:15:00", open=50620.0, high=50642.0, low=50590.0, close=50610.0, volume=1000),
                    Candle(timestamp="2026-02-18T09:16:00", open=50610.0, high=50624.0, low=50594.0, close=50605.0, volume=1050),
                    Candle(timestamp="2026-02-18T09:17:00", open=50605.0, high=50620.0, low=50590.0, close=50612.0, volume=1100),
                    Candle(timestamp="2026-02-18T09:18:00", open=50612.0, high=50616.0, low=50555.0, close=50570.0, volume=1300),
                    Candle(timestamp="2026-02-18T09:19:00", open=50570.0, high=50578.0, low=50535.0, close=50542.0, volume=1350),
                ],
                "companion_current_candle": Candle(
                    timestamp="2026-02-18T09:19:00", open=50570.0, high=50578.0, low=50535.0, close=50542.0, volume=1350
                ),
            }
        )
        observation = self._build_observation(
            range_state="compressing",
            participation_state="two_sided_active",
            strong_intent=False,
            vwap=24155.0,
            atr=20.0,
            higher_timeframe_context="neutral",
        )

        candidates = engine.build_companion_index_candidates(context, observation)

        self.assertFalse(any(candidate.setup_type == "companion_round_rejection_short" for candidate in candidates))

    def test_nifty_companion_allows_round_front_run_only_in_directional_expansion(self) -> None:
        engine = HeuristicDecisionEngine()
        session = [
            Candle(timestamp="2026-02-18T09:15:00", open=24150.0, high=24158.0, low=24142.0, close=24154.0, volume=1000),
            Candle(timestamp="2026-02-18T09:16:00", open=24154.0, high=24170.0, low=24150.0, close=24166.0, volume=1100),
            Candle(timestamp="2026-02-18T09:17:00", open=24166.0, high=24183.0, low=24160.0, close=24178.0, volume=1200),
            Candle(timestamp="2026-02-18T09:18:00", open=24178.0, high=24180.0, low=24160.0, close=24164.0, volume=1250),
            Candle(timestamp="2026-02-18T09:19:00", open=24164.0, high=24168.0, low=24150.0, close=24156.0, volume=1300),
        ]
        context = self._build_context(session, previous_close=24120.0).model_copy(
            update={
                "companion_symbol": "BANKNIFTY",
                "companion_session_candles": [
                    Candle(timestamp="2026-02-18T09:15:00", open=50620.0, high=50642.0, low=50590.0, close=50610.0, volume=1000),
                    Candle(timestamp="2026-02-18T09:16:00", open=50610.0, high=50624.0, low=50594.0, close=50605.0, volume=1050),
                    Candle(timestamp="2026-02-18T09:17:00", open=50605.0, high=50620.0, low=50590.0, close=50612.0, volume=1100),
                    Candle(timestamp="2026-02-18T09:18:00", open=50612.0, high=50616.0, low=50555.0, close=50570.0, volume=1300),
                    Candle(timestamp="2026-02-18T09:19:00", open=50570.0, high=50578.0, low=50535.0, close=50542.0, volume=1350),
                ],
                "companion_current_candle": Candle(
                    timestamp="2026-02-18T09:19:00", open=50570.0, high=50578.0, low=50535.0, close=50542.0, volume=1350
                ),
            }
        )
        observation = self._build_observation(
            range_state="expanding",
            participation_state="directional",
            strong_intent=True,
            vwap=24140.0,
            atr=20.0,
            higher_timeframe_context="neutral",
        )

        candidates = engine.build_companion_index_candidates(context, observation)

        self.assertTrue(any(candidate.setup_type == "companion_round_rejection_short" for candidate in candidates))

    def test_nifty_companion_ignores_round_number_inside_first_candle_range(self) -> None:
        engine = HeuristicDecisionEngine()
        session = [
            Candle(timestamp="2026-04-15T09:15:00", open=24163.8, high=24280.9, low=24162.7, close=24232.9, volume=1000),
            Candle(timestamp="2026-04-15T09:26:00", open=24208.0, high=24216.0, low=24194.0, close=24202.0, volume=1100),
            Candle(timestamp="2026-04-15T09:27:00", open=24202.0, high=24212.0, low=24188.0, close=24196.0, volume=1150),
            Candle(timestamp="2026-04-15T09:28:00", open=24196.0, high=24198.0, low=24174.0, close=24180.0, volume=1250),
            Candle(timestamp="2026-04-15T09:29:00", open=24180.0, high=24187.0, low=24162.0, close=24168.0, volume=1300),
            Candle(timestamp="2026-04-15T09:30:00", open=24193.5, high=24194.7, low=24176.35, close=24183.25, volume=1350),
        ]
        context = self._build_context(session, previous_close=24120.0).model_copy(
            update={
                "companion_symbol": "BANKNIFTY",
                "companion_session_candles": [
                    Candle(timestamp="2026-04-15T09:26:00", open=50620.0, high=50642.0, low=50590.0, close=50610.0, volume=1000),
                    Candle(timestamp="2026-04-15T09:27:00", open=50610.0, high=50624.0, low=50594.0, close=50605.0, volume=1050),
                    Candle(timestamp="2026-04-15T09:28:00", open=50605.0, high=50620.0, low=50590.0, close=50612.0, volume=1100),
                    Candle(timestamp="2026-04-15T09:29:00", open=50612.0, high=50616.0, low=50555.0, close=50570.0, volume=1300),
                    Candle(timestamp="2026-04-15T09:30:00", open=50570.0, high=50578.0, low=50535.0, close=50542.0, volume=1350),
                ],
                "companion_current_candle": Candle(
                    timestamp="2026-04-15T09:30:00", open=50570.0, high=50578.0, low=50535.0, close=50542.0, volume=1350
                ),
            }
        )
        observation = self._build_observation(
            range_state="expanding",
            participation_state="directional",
            strong_intent=True,
            vwap=24140.0,
            atr=20.0,
            higher_timeframe_context="neutral",
        )

        candidates = engine.build_companion_index_candidates(context, observation)

        self.assertFalse(any(candidate.setup_type == "companion_round_rejection_short" for candidate in candidates))

    def test_nifty_mid_noise_filter_skips_fresh_entries(self) -> None:
        engine = HeuristicDecisionEngine()
        session = [
            Candle(timestamp="2026-02-18T12:00:00", open=23300.0, high=23306.0, low=23296.0, close=23302.0, volume=1000),
            Candle(timestamp="2026-02-18T12:01:00", open=23302.0, high=23307.0, low=23298.0, close=23303.0, volume=1000),
        ]
        context = self._build_context(session, previous_close=23290.0)
        observation = self._build_observation(
            session_phase="midday",
            nifty_mid_noise=True,
        )
        candidate = SetupCandidate(
            setup_type="bullish_reclaim_watch",
            direction="LONG_CALL",
            option_type="CE",
            trigger_basis="close_above",
            trigger_price=23304.0,
            invalidation_level=23296.0,
            defended_level=23300.0,
            target_spot_price=23318.0,
            first_target_price=23310.0,
            score=84.0,
            ready_to_enter=True,
            notes=["Trap looks valid but the session is still noisy."],
            rule_ids=["R58"],
            event=SweepEvent(
                side="sell",
                level_label="Round Number 23300.00",
                level_price=23300.0,
                sweep_index=0,
                reclaim_index=1,
                trigger_index=1,
                sweep_price=23298.0,
                defended_level=23300.0,
                trigger_price=23304.0,
                invalidation_level=23296.0,
                primary=True,
                quality="tradable",
                notes=["Recovered back above the level."],
            ),
        )

        decision = engine.decide_entry(context, observation, [candidate])

        self.assertEqual(decision.action, TradeAction.no_trade)
        self.assertIn("overlapping noise", decision.reason.lower())

    def test_nifty_higher_timeframe_bearish_bias_blocks_bullish_entry(self) -> None:
        engine = HeuristicDecisionEngine()
        session = [
            self._make_candle(0, 23340.0, 23342.0, 23308.0, 23312.0),
            self._make_candle(1, 23312.0, 23314.0, 23296.0, 23300.0),
            self._make_candle(2, 23300.0, 23304.0, 23286.0, 23292.0),
            self._make_candle(3, 23292.0, 23310.0, 23288.0, 23308.0),
        ]
        context = self._build_context(session, previous_close=23320.0)
        observation = self._build_observation(
            higher_timeframe_context="bearish_trend",
            mapped_sell_liquidity=[("Round Number 23300.00", 23300.0, True)],
        )
        event = SweepEvent(
            side="sell",
            level_label="Round Number 23300.00",
            level_price=23300.0,
            sweep_index=2,
            reclaim_index=3,
            trigger_index=3,
            sweep_price=23286.0,
            defended_level=23300.0,
            trigger_price=23310.0,
            invalidation_level=23284.0,
            primary=True,
            quality="tradable",
            notes=["Recovered from a nearby round-number trap."],
        )

        candidate = engine.build_candidate_from_event(context, observation, event, option_type="CE", direction="LONG_CALL")

        self.assertIsNone(candidate)

    def test_nifty_round_number_tight_sweep_scores_better_than_loose_sweep(self) -> None:
        engine = HeuristicDecisionEngine()
        session = [
            self._make_candle(0, 23340.0, 23342.0, 23308.0, 23312.0),
            self._make_candle(1, 23312.0, 23314.0, 23296.0, 23300.0),
            self._make_candle(2, 23300.0, 23304.0, 23298.0, 23302.0),
            self._make_candle(3, 23302.0, 23312.0, 23300.0, 23310.0),
        ]
        context = self._build_context(session, previous_close=23320.0)
        observation = self._build_observation(
            higher_timeframe_context="bullish_reversal",
            mapped_sell_liquidity=[("Round Number 23300.00", 23300.0, True)],
        )
        tight_event = SweepEvent(
            side="sell",
            level_label="Round Number 23300.00",
            level_price=23300.0,
            sweep_index=1,
            reclaim_index=2,
            trigger_index=2,
            sweep_price=23296.0,
            defended_level=23300.0,
            trigger_price=23304.0,
            invalidation_level=23294.0,
            primary=True,
            quality="tradable",
            notes=["Recovered tightly around the round number."],
        )
        loose_event = SweepEvent(
            side="sell",
            level_label="Round Number 23300.00",
            level_price=23300.0,
            sweep_index=1,
            reclaim_index=2,
            trigger_index=2,
            sweep_price=23276.0,
            defended_level=23300.0,
            trigger_price=23304.0,
            invalidation_level=23274.0,
            primary=True,
            quality="tradable",
            notes=["Recovered but front-ran the round number too loosely."],
        )

        tight_candidate = engine.build_candidate_from_event(context, observation, tight_event, option_type="CE", direction="LONG_CALL")
        loose_candidate = engine.build_candidate_from_event(context, observation, loose_event, option_type="CE", direction="LONG_CALL")

        self.assertIsNotNone(tight_candidate)
        self.assertIsNotNone(loose_candidate)
        assert tight_candidate is not None
        assert loose_candidate is not None
        self.assertGreater(tight_candidate.score, loose_candidate.score)

    def test_nifty_shallow_retest_scores_better_than_deep_retest(self) -> None:
        engine = HeuristicDecisionEngine()
        shallow_session = [
            self._make_candle(0, 23340.0, 23342.0, 23308.0, 23312.0),
            self._make_candle(1, 23312.0, 23314.0, 23296.0, 23300.0),
            self._make_candle(2, 23300.0, 23312.0, 23298.0, 23308.0),
            self._make_candle(3, 23308.0, 23310.0, 23301.5, 23309.0),
            self._make_candle(4, 23309.0, 23318.0, 23306.0, 23316.0),
        ]
        deep_session = [
            self._make_candle(0, 23340.0, 23342.0, 23308.0, 23312.0),
            self._make_candle(1, 23312.0, 23314.0, 23296.0, 23300.0),
            self._make_candle(2, 23300.0, 23312.0, 23298.0, 23308.0),
            self._make_candle(3, 23308.0, 23310.0, 23292.0, 23298.0),
            self._make_candle(4, 23298.0, 23318.0, 23296.0, 23316.0),
        ]
        observation = self._build_observation(
            higher_timeframe_context="bullish_reversal",
            mapped_sell_liquidity=[("Round Number 23300.00", 23300.0, True)],
        )
        event = SweepEvent(
            side="sell",
            level_label="Round Number 23300.00",
            level_price=23300.0,
            sweep_index=1,
            reclaim_index=2,
            trigger_index=2,
            sweep_price=23296.0,
            defended_level=23300.0,
            trigger_price=23304.0,
            invalidation_level=23294.0,
            primary=True,
            quality="tradable",
            notes=["Recovered tightly around the round number."],
        )

        shallow_context = self._build_context(shallow_session, previous_close=23320.0)
        deep_context = self._build_context(deep_session, previous_close=23320.0)
        shallow_candidate = engine.build_candidate_from_event(shallow_context, observation, event, option_type="CE", direction="LONG_CALL")
        deep_candidate = engine.build_candidate_from_event(deep_context, observation, event, option_type="CE", direction="LONG_CALL")

        self.assertIsNotNone(shallow_candidate)
        self.assertIsNotNone(deep_candidate)
        assert shallow_candidate is not None
        assert deep_candidate is not None
        self.assertGreater(shallow_candidate.score, deep_candidate.score)

    def test_sync_history_endpoint_loads_market_context(self) -> None:
        bundle = DhanSessionBundle(
            previous_day_candles=[
                Candle(timestamp="2026-04-23T09:15:00", open=24000, high=24010, low=23990, close=24005, volume=1000),
                Candle(timestamp="2026-04-23T09:16:00", open=24005, high=24015, low=24000, close=24012, volume=1100),
            ],
            intraday_candles=[
                Candle(timestamp="2026-04-24T09:15:00", open=24100, high=24110, low=24095, close=24108, volume=900),
                Candle(timestamp="2026-04-24T09:16:00", open=24108, high=24120, low=24100, close=24118, volume=950),
            ],
            live_open_candle=None,
            previous_day_source="historical",
        )
        with patch.object(self.test_engine.chart_service, "fetch_market_context", return_value=bundle):
            response = self.client.post("/api/live/sync-history", data={"client_id": "cid", "access_token": "tok"})

        self.assertEqual(response.status_code, 200)
        state = response.json()["state"]
        self.assertEqual(state["data_sync"]["source"], "dhan-rest")
        self.assertEqual(state["data_sync"]["previous_day_candles"], 2)
        self.assertEqual(state["data_sync"]["intraday_candles"], 2)
        self.assertEqual(state["total_candles"], 4)
        self.assertEqual(state["instrument"]["label"], "Nifty 50")

    def test_nifty_sync_history_fetches_banknifty_companion_context(self) -> None:
        bundles = {
            "13": DhanSessionBundle(
                previous_day_candles=[Candle(timestamp="2026-04-23T09:15:00", open=23200, high=23210, low=23190, close=23205, volume=1000)],
                intraday_candles=[Candle(timestamp="2026-04-24T09:15:00", open=23210, high=23235, low=23200, close=23228, volume=900)],
                live_open_candle=None,
                previous_day_source="historical",
            ),
            "25": DhanSessionBundle(
                previous_day_candles=[Candle(timestamp="2026-04-23T09:15:00", open=49800, high=49880, low=49750, close=49820, volume=1000)],
                intraday_candles=[Candle(timestamp="2026-04-24T09:15:00", open=49830, high=49940, low=49810, close=49910, volume=950)],
                live_open_candle=None,
                previous_day_source="historical",
            ),
        }

        def fake_fetch_market_context(*, security_id, **kwargs):
            return bundles[str(security_id)]

        with patch.object(self.test_engine.chart_service, "fetch_market_context", side_effect=fake_fetch_market_context) as fetch_mock:
            response = self.client.post("/api/live/sync-history", data={"client_id": "cid", "access_token": "tok"})

        self.assertEqual(response.status_code, 200)
        requested_ids = {call.kwargs["security_id"] for call in fetch_mock.call_args_list}
        self.assertEqual(requested_ids, {"13", "25"})
        self.assertTrue(self.test_engine.companion_candles)

    def test_signal_history_keeps_earlier_session_signals_with_timestamp(self) -> None:
        bundle = DhanSessionBundle(
            previous_day_candles=[
                Candle(timestamp="2026-04-23T09:15:00", open=24000, high=24020, low=23990, close=24008, volume=1000),
                Candle(timestamp="2026-04-23T09:16:00", open=24008, high=24018, low=24000, close=24010, volume=1100),
            ],
            intraday_candles=[
                Candle(timestamp="2026-04-24T09:15:00", open=24004, high=24012, low=24000, close=24005, volume=900),
                Candle(timestamp="2026-04-24T09:16:00", open=24005, high=24040, low=24002, close=24032, volume=950),
            ],
            live_open_candle=None,
            previous_day_source="historical",
        )

        with patch.object(self.test_engine.chart_service, "fetch_market_context", return_value=bundle):
            response = self.client.post("/api/live/sync-history", data={"client_id": "cid", "access_token": "tok"})

        self.assertEqual(response.status_code, 200)
        state = response.json()["state"]
        titles = [event["title"] for event in state["signal_history"]]
        timestamps = [event["timestamp"] for event in state["signal_history"]]
        self.assertIn("Close near previous day close", titles)
        self.assertIn("2026-04-24T09:15:00", timestamps)

    def test_build_context_includes_all_closed_session_candles_since_morning(self) -> None:
        previous_day = [
            Candle(timestamp="2026-04-23T09:15:00", open=24000, high=24010, low=23995, close=24005, volume=1000),
            Candle(timestamp="2026-04-23T09:16:00", open=24005, high=24020, low=24000, close=24015, volume=1100),
        ]
        intraday = [
            Candle(
                timestamp=f"2026-04-24T{9 + ((15 + index) // 60):02d}:{(15 + index) % 60:02d}:00",
                open=24100 + index,
                high=24104 + index,
                low=24096 + index,
                close=24102 + index,
                volume=1200 + index,
            )
            for index in range(120)
        ]

        self.test_engine.reset_with_candles(previous_day + intraday)
        self.test_engine.current_index = len(previous_day + intraday) - 1

        context = self.test_engine.build_context()

        self.assertEqual(len(context.session_candles), 120)
        self.assertEqual(context.session_candles[0].timestamp.isoformat(), "2026-04-24T09:15:00")
        self.assertEqual(len(context.previous_day_candles), 2)
        self.assertEqual(len(context.recent_candles), 20)
        self.assertIn("Closed intraday session context spans 120 candles", context.market_structure)

    def test_full_ai_prompt_uses_session_context_and_updated_rulebook(self) -> None:
        previous_day = [
            Candle(timestamp="2026-04-23T09:15:00", open=24000, high=24020, low=23990, close=24010, volume=1000),
            Candle(timestamp="2026-04-23T09:16:00", open=24010, high=24030, low=24005, close=24025, volume=1100),
        ]
        intraday = [
            Candle(timestamp="2026-04-24T09:15:00", open=24100, high=24110, low=24095, close=24105, volume=900),
            Candle(timestamp="2026-04-24T09:16:00", open=24105, high=24140, low=24100, close=24112, volume=950),
            Candle(timestamp="2026-04-24T09:17:00", open=24112, high=24145, low=24108, close=24118, volume=970),
        ]
        self.test_engine.reset_with_candles(previous_day + intraday)
        self.test_engine.current_index = len(previous_day + intraday) - 1
        self.test_engine.live_current_candle = Candle(
            timestamp="2026-04-24T09:18:00",
            open=24118,
            high=24155,
            low=24115,
            close=24148,
            volume=1005,
        )
        self.test_engine.rulebook_service.rulebook_markdown = (
            "# SL Hunting Rulebook\n\n"
            + ("Base rule.\n" * 1500)
            + "\n## Learned Notes\n- Updated sweep confirmation requires full morning context.\n"
        )
        armed_setup = TradeDecision(
            action=TradeAction.no_trade,
            confidence=0.66,
            reason="Wait for reclaim above 24130.",
            pending_setup_action="ARM",
            pending_setup_type="bullish_reclaim_watch",
            pending_setup_direction="LONG_CALL",
            pending_setup_option_type="CE",
            pending_setup_trigger_price=24130,
            pending_setup_invalidation_level=24102,
            pending_setup_strike=24100,
        )
        self.test_engine.apply_pending_setup_decision(self.test_engine.candles[-1], armed_setup)

        context = self.test_engine.build_context()
        heuristic = TradeDecision(action=TradeAction.no_trade, confidence=0.3, reason="heuristic")
        captured: dict[str, str] = {}

        def fake_parse(*, prompt, schema_model, system_prompt, timeout):
            captured["prompt"] = prompt
            captured["system_prompt"] = system_prompt
            return TradeDecision(action=TradeAction.no_trade, confidence=0.61, reason="structured")

        self.test_engine.ai_service.enabled = True
        with patch.object(self.test_engine.ai_service, "_structured_parse", side_effect=fake_parse):
            decision = self.test_engine.ai_service.decide(context, heuristic, OperatingMode.full_ai)

        self.assertTrue(decision.decision_source.startswith("full-ai-"))
        self.assertIn("session_candles_since_open=", captured["prompt"])
        self.assertIn("2026-04-24 09:15", captured["prompt"])
        self.assertIn("forming_live_candle=", captured["prompt"])
        self.assertIn("pending_setup=", captured["prompt"])
        self.assertIn("Updated sweep confirmation requires full morning context.", captured["prompt"])
        self.assertIn("Do not base the decision on only the latest candle.", captured["system_prompt"])

    def test_pending_setup_stays_locked_until_explicit_replace_or_invalidate(self) -> None:
        self.test_engine.reset_with_candles(
            [
                Candle(timestamp="2026-05-14T11:25:00", open=23720, high=23742, low=23718, close=23739, volume=900),
                Candle(timestamp="2026-05-14T11:26:00", open=23739, high=23748, low=23731, close=23744, volume=920),
                Candle(timestamp="2026-05-14T11:27:00", open=23744, high=23760, low=23740, close=23758, volume=960),
            ]
        )
        arm_decision = TradeDecision(
            action=TradeAction.no_trade,
            confidence=0.71,
            reason="Need reclaim above 23747 before long.",
            pending_setup_action="ARM",
            pending_setup_type="bullish_reclaim_watch",
            pending_setup_direction="LONG_CALL",
            pending_setup_option_type="CE",
            pending_setup_trigger_price=23747,
            pending_setup_invalidation_level=23724,
            pending_setup_strike=23700,
            pending_setup_notes="Locked reclaim level from first breakout attempt.",
        )
        self.test_engine.apply_pending_setup_decision(self.test_engine.candles[0], arm_decision)

        self.assertIsNotNone(self.test_engine.pending_setup)
        self.assertEqual(self.test_engine.pending_setup.trigger_price, 23747)

        neutral_decision = TradeDecision(
            action=TradeAction.no_trade,
            confidence=0.64,
            reason="Still waiting.",
            pending_setup_action="NONE",
        )
        self.test_engine.apply_pending_setup_decision(self.test_engine.candles[1], neutral_decision)
        self.assertEqual(self.test_engine.pending_setup.trigger_price, 23747)

        replace_decision = TradeDecision(
            action=TradeAction.no_trade,
            confidence=0.68,
            reason="Old reclaim is obsolete after new sweep and rejection.",
            pending_setup_action="REPLACE",
            pending_setup_type="bullish_reclaim_watch",
            pending_setup_direction="LONG_CALL",
            pending_setup_option_type="CE",
            pending_setup_trigger_price=23760,
            pending_setup_invalidation_level=23736,
            pending_setup_strike=23700,
        )
        self.test_engine.apply_pending_setup_decision(self.test_engine.candles[2], replace_decision)
        self.assertEqual(self.test_engine.pending_setup.trigger_price, 23760)
        self.assertEqual(self.test_engine.pending_setup.replacement_reason, "Old reclaim is obsolete after new sweep and rejection.")

    def test_hold_without_active_trade_normalizes_to_no_trade(self) -> None:
        decision = TradeDecision(
            action=TradeAction.hold,
            confidence=0.9,
            reason="Keep bearish setup armed.",
            pending_setup_action="KEEP",
            pending_setup_type="bearish_rejection_watch",
            pending_setup_direction="LONG_PUT",
            pending_setup_option_type="PE",
            pending_setup_trigger_price=23763.65,
        )

        normalized = self.test_engine.normalize_trade_decision(decision, None)

        self.assertEqual(normalized.action, TradeAction.no_trade)
        self.assertEqual(normalized.pending_setup_action, "KEEP")

    def test_armed_pending_setup_auto_triggers_put_trade_on_red_close_below(self) -> None:
        candles = [
            Candle(timestamp="2026-05-14T14:18:00", open=23770, high=23775, low=23760, close=23768, volume=900),
            Candle(timestamp="2026-05-14T14:19:00", open=23768, high=23770, low=23724, close=23740, volume=1200),
        ]
        self.test_engine.reset_with_candles(candles)
        arm_decision = TradeDecision(
            action=TradeAction.no_trade,
            confidence=0.84,
            reason="Arm bearish trigger below 23763.65.",
            pending_setup_action="ARM",
            pending_setup_type="bearish_rejection_watch",
            pending_setup_direction="LONG_PUT",
            pending_setup_option_type="PE",
            pending_setup_trigger_price=23763.65,
            pending_setup_invalidation_level=23776.65,
            pending_setup_trigger_basis="close_below",
            pending_setup_strike=23800,
            pending_setup_notes="Wait for red candle close below trigger.",
        )
        self.test_engine.apply_pending_setup_decision(candles[0], arm_decision)

        with patch.object(self.test_engine, "_load_option_contract_from_dhan", return_value=None):
            self.test_engine._evaluate_index(1)

        self.assertIsNotNone(self.test_engine.active_trade)
        self.assertEqual(self.test_engine.active_trade.option_type, "PE")
        self.assertEqual(self.test_engine.active_trade.direction, "LONG_PUT")
        self.assertEqual(self.test_engine.decision.decision_source, "pending-setup-trigger")
        self.assertIsNotNone(self.test_engine.pending_setup)
        self.assertEqual(self.test_engine.pending_setup.status, "consumed")
        self.assertIsNotNone(self.test_engine.pending_setup.triggered_at)
        self.assertIsNotNone(self.test_engine.pending_setup.consumed_at)
        self.assertEqual(self.test_engine.pending_setup.executed_trade_id, self.test_engine.active_trade.trade_id)

    def test_triggered_pending_setup_preserves_target_and_setup_metadata_on_trade(self) -> None:
        candles = [
            Candle(timestamp="2026-05-14T14:18:00", open=23770, high=23775, low=23760, close=23768, volume=900),
            Candle(timestamp="2026-05-14T14:19:00", open=23768, high=23770, low=23724, close=23740, volume=1200),
        ]
        self.test_engine.reset_with_candles(candles)
        arm_decision = TradeDecision(
            action=TradeAction.no_trade,
            confidence=0.84,
            reason="Arm bearish trigger below 23763.65.",
            decision_source="heuristic",
            option_type="PE",
            target_spot_price=23710.0,
            first_target_price=23740.0,
            market_state="gap-reversal",
            setup_score=79.0,
            setup_type="bearish_rejection_watch",
            pending_setup_action="ARM",
            pending_setup_type="bearish_rejection_watch",
            pending_setup_direction="LONG_PUT",
            pending_setup_option_type="PE",
            pending_setup_trigger_price=23763.65,
            pending_setup_invalidation_level=23776.65,
            pending_setup_trigger_basis="close_below",
            pending_setup_strike=23800,
            pending_setup_notes="Wait for red candle close below trigger.",
        )
        self.test_engine.apply_pending_setup_decision(candles[0], arm_decision)

        triggered = self.test_engine.evaluate_pending_setup_trigger(candles[1])
        assert triggered is not None
        self.test_engine.apply_trade_logic(candles[1], triggered, source="replay")

        self.assertIsNotNone(self.test_engine.active_trade)
        self.assertEqual(self.test_engine.active_trade.setup_type, "bearish_rejection_watch")
        self.assertEqual(self.test_engine.active_trade.market_state, "gap-reversal")
        self.assertEqual(self.test_engine.active_trade.target_spot_price, 23710.0)
        self.assertEqual(self.test_engine.active_trade.first_target_price, 23740.0)
        self.assertEqual(self.test_engine.active_trade.target_price, 23710.0)

    def test_nifty_pending_setup_trigger_invalidates_when_refreshed_score_is_weak(self) -> None:
        candles = [
            Candle(timestamp="2026-05-18T10:29:00", open=23379.8, high=23385.05, low=23379.8, close=23379.85, volume=663621),
            Candle(timestamp="2026-05-18T10:30:00", open=23379.75, high=23382.65, low=23371.5, close=23372.65, volume=798743),
        ]
        self.test_engine.reset_with_candles(candles)
        self.test_engine.current_index = 1
        arm_decision = TradeDecision(
            action=TradeAction.no_trade,
            confidence=0.57,
            reason="Weak equal-high short should not be allowed to fire mechanically.",
            market_state="gap-and-go",
            setup_score=57.4,
            setup_type="bearish_rejection_watch",
            pending_setup_action="ARM",
            pending_setup_type="bearish_rejection_watch",
            pending_setup_direction="LONG_PUT",
            pending_setup_option_type="PE",
            pending_setup_trigger_price=23380.25,
            pending_setup_invalidation_level=23392.65,
            pending_setup_trigger_basis="close_below",
            pending_setup_strike=23400,
            pending_setup_notes="Setup should stay armed because score is only 57.4/100.",
        )
        self.test_engine.apply_pending_setup_decision(candles[0], arm_decision)

        triggered = self.test_engine.evaluate_pending_setup_trigger(candles[1])

        assert triggered is not None
        self.assertEqual(triggered.action, TradeAction.no_trade)
        self.assertEqual(triggered.pending_setup_action, "INVALIDATE")
        self.assertIn("below Nifty entry threshold", triggered.reason)
        self.assertIsNone(self.test_engine.active_trade)

    def test_ai_entry_decision_normalizes_call_option_type_before_trade_creation(self) -> None:
        self.test_engine.reset_with_candles(
            [
                Candle(timestamp="2026-04-24T11:27:00", open=23480, high=23498, low=23470, close=23492, volume=1000),
            ]
        )
        self.test_engine.current_index = 0

        decision = TradeDecision(
            action=TradeAction.enter_call,
            confidence=0.82,
            reason="Bullish continuation after reclaim.",
            decision_source="full-ai-openai",
            option_type="CALL",
        )
        normalized = self.test_engine.normalize_trade_decision(decision, None)
        self.test_engine.apply_trade_logic(self.test_engine.candles[0], normalized)

        self.assertIsNotNone(self.test_engine.active_trade)
        self.assertEqual(self.test_engine.active_trade.option_type, "CE")
        self.assertEqual(self.test_engine.active_trade.direction, "LONG_CALL")
        self.assertEqual(self.test_engine.active_trade.strike, 23400)
        self.assertEqual(self.test_engine.active_trade.symbol, "NIFTY 30APR2026 23400CE")
        self.assertEqual(self.test_engine.active_trade.entry_price, self.test_engine.active_trade.entry_option_price)

    def test_live_trade_uses_dhan_option_quote_metadata_when_available(self) -> None:
        self.test_engine.reset_with_candles(
            [
                Candle(timestamp="2026-05-14T11:27:00", open=23510, high=23538, low=23502, close=23532, volume=1000),
            ]
        )
        self.test_engine.current_index = 0
        decision = TradeDecision(
            action=TradeAction.enter_call,
            confidence=0.9,
            reason="Real option quote test.",
            decision_source="full-ai-openai",
            strike=23500,
            option_type="CE",
        )
        contract = OptionContract(
            security_id="42528",
            option_type="CE",
            strike=23500,
            expiry=date(2026, 5, 14),
            symbol="NIFTY 14MAY2026 23500CE",
            quote=OptionQuote(
                security_id="42528",
                option_type="CE",
                strike=23500,
                last_price=36.5,
                quote_time=datetime.fromisoformat("2026-05-14T11:27:14+05:30"),
                source="dhan-rest-quote",
            ),
        )

        with patch.object(self.test_engine, "_load_option_contract_from_dhan", return_value=contract):
            self.test_engine.apply_trade_logic(self.test_engine.candles[0], decision)

        self.assertIsNotNone(self.test_engine.active_trade)
        self.assertEqual(self.test_engine.active_trade.symbol, "NIFTY 14MAY2026 23500CE")
        self.assertEqual(self.test_engine.active_trade.option_security_id, "42528")
        self.assertEqual(self.test_engine.active_trade.entry_option_price, 36.5)
        self.assertEqual(self.test_engine.active_trade.entry_price, 36.5)
        self.assertEqual(self.test_engine.active_trade.entry_quote_source, "dhan-rest-quote")
        self.assertEqual(self.test_engine.active_trade.current_quote_source, "dhan-rest-quote")
        self.assertEqual(self.test_engine.active_trade.entry_time.isoformat(), "2026-05-14T11:27:14+05:30")

    def test_nifty_live_mode_sells_opposite_otm_option(self) -> None:
        candle = Candle(timestamp="2026-05-14T11:27:00", open=23510, high=23542, low=23502, close=23534, volume=1000)

        bullish_trade = self.test_engine._build_entry_trade(
            candle,
            TradeDecision(action=TradeAction.enter_call, option_type="CE", invalidation_level=23480, target_spot_price=23650),
            source="live",
        )
        bearish_trade = self.test_engine._build_entry_trade(
            candle,
            TradeDecision(action=TradeAction.enter_put, option_type="PE", invalidation_level=23590, target_spot_price=23410),
            source="live",
        )

        self.assertEqual(bullish_trade.direction, "SHORT_PUT")
        self.assertEqual(bullish_trade.option_type, "PE")
        self.assertEqual(bullish_trade.strike, 23500)
        self.assertEqual(bearish_trade.direction, "SHORT_CALL")
        self.assertEqual(bearish_trade.option_type, "CE")
        self.assertEqual(bearish_trade.strike, 23600)

    def test_nifty_live_mode_can_buy_one_strike_itm_option(self) -> None:
        self.temp_store.save(nifty_option_trade_mode="buying")
        candle = Candle(timestamp="2026-05-14T11:27:00", open=23180, high=23210, low=23170, close=23192, volume=1000)

        bullish_trade = self.test_engine._build_entry_trade(
            candle,
            TradeDecision(action=TradeAction.enter_call, option_type="CE", invalidation_level=23140, target_spot_price=23300),
            source="live",
        )
        bearish_trade = self.test_engine._build_entry_trade(
            candle,
            TradeDecision(action=TradeAction.enter_put, option_type="PE", invalidation_level=23240, target_spot_price=23080),
            source="live",
        )

        self.assertEqual(bullish_trade.direction, "LONG_CALL")
        self.assertEqual(bullish_trade.option_type, "CE")
        self.assertEqual(bullish_trade.strike, 23100)
        self.assertEqual(bearish_trade.direction, "LONG_PUT")
        self.assertEqual(bearish_trade.option_type, "PE")
        self.assertEqual(bearish_trade.strike, 23200)

    def test_nifty_entry_invalidation_is_normalized_to_20_40_point_risk(self) -> None:
        candle = Candle(timestamp="2026-05-14T11:27:00", open=24240, high=24258, low=24230, close=24252, volume=1000)

        tight_long = self.test_engine._build_entry_trade(
            candle,
            TradeDecision(action=TradeAction.enter_call, option_type="CE", invalidation_level=24245, target_spot_price=24340),
            source="replay",
        )
        wide_long = self.test_engine._build_entry_trade(
            candle,
            TradeDecision(action=TradeAction.enter_call, option_type="CE", invalidation_level=24180, target_spot_price=24380),
            source="replay",
        )
        tight_short = self.test_engine._build_entry_trade(
            candle,
            TradeDecision(action=TradeAction.enter_put, option_type="PE", invalidation_level=24260, target_spot_price=24180),
            source="replay",
        )
        wide_short = self.test_engine._build_entry_trade(
            candle,
            TradeDecision(action=TradeAction.enter_put, option_type="PE", invalidation_level=24320, target_spot_price=24120),
            source="replay",
        )

        self.assertEqual(tight_long.invalidation_level, 24232.0)
        self.assertEqual(wide_long.invalidation_level, 24212.0)
        self.assertEqual(tight_short.invalidation_level, 24272.0)
        self.assertEqual(wide_short.invalidation_level, 24292.0)

    def test_nifty_entry_invalidation_uses_saved_min_max_sl_points(self) -> None:
        self.temp_store.save(nifty_min_sl_points=12.0, nifty_max_sl_points=30.0)
        candle = Candle(timestamp="2026-05-14T11:27:00", open=24240, high=24258, low=24230, close=24252, volume=1000)

        tight_long = self.test_engine._build_entry_trade(
            candle,
            TradeDecision(action=TradeAction.enter_call, option_type="CE", invalidation_level=24245, target_spot_price=24340),
            source="replay",
        )
        wide_short = self.test_engine._build_entry_trade(
            candle,
            TradeDecision(action=TradeAction.enter_put, option_type="PE", invalidation_level=24320, target_spot_price=24120),
            source="replay",
        )

        self.assertEqual(tight_long.invalidation_level, 24240.0)
        self.assertEqual(wide_short.invalidation_level, 24282.0)

    def test_short_option_pnl_and_live_order_sides_are_reversed(self) -> None:
        candle = Candle(timestamp="2026-05-14T11:27:00", open=23510, high=23542, low=23502, close=23534, volume=1000)
        decision = TradeDecision(action=TradeAction.enter_put, option_type="PE", invalidation_level=23590, target_spot_price=23410)
        trade = self.test_engine._build_entry_trade(candle, decision, source="live")
        trade.option_security_id = "999001"
        trade.entry_price = 100.0
        trade.current_price = 70.0

        self.assertEqual(self.test_engine.calculate_trade_pnl(trade, 70.0), 1950.0)

        self.test_engine.live_trading_enabled = True
        self.test_engine.operating_mode = OperatingMode.heuristic
        self.test_engine.live_feed_adapter = Mock()
        self.temp_store.save(client_id="cid", access_token=self._make_dhan_token("cid"))
        with patch.object(
            self.test_engine.execution_service,
            "place_market_order",
            return_value=BrokerOrderResult(ok=True, order_id="entry-1", order_status="PENDING", message="ok", raw={}),
        ) as place_order:
            self.test_engine._enter_live_trade(candle, decision, trade)

        self.assertEqual(place_order.call_args.kwargs["transaction_type"], "SELL")
        self.assertIsNotNone(self.test_engine.active_trade)

        add_decision = TradeDecision(action=TradeAction.add_position, reason="Add after protected continuation.", add_quantity=999)
        with patch.object(
            self.test_engine.execution_service,
            "place_market_order",
            return_value=BrokerOrderResult(ok=True, order_id="add-1", order_status="PENDING", message="ok", raw={}),
        ) as add_order:
            self.test_engine._add_live_trade(candle, add_decision)

        self.assertEqual(add_order.call_args.kwargs["transaction_type"], "SELL")
        self.assertEqual(add_order.call_args.kwargs["quantity"], 65)
        self.assertEqual(self.test_engine.active_trade.quantity, 130)
        self.assertEqual(self.test_engine.active_trade.open_quantity, 130)
        self.assertEqual(self.test_engine.active_trade.pyramid_count, 1)

        with patch.object(
            self.test_engine.execution_service,
            "place_market_order",
            return_value=BrokerOrderResult(ok=True, order_id="exit-1", order_status="PENDING", message="ok", raw={}),
        ) as exit_order:
            with patch.object(self.test_engine.option_quote_service, "fetch_quote", side_effect=DhanOptionQuoteError("offline")):
                self.test_engine._exit_live_trade(candle, "test exit")

        self.assertEqual(exit_order.call_args.kwargs["transaction_type"], "BUY")
        self.assertEqual(exit_order.call_args.kwargs["quantity"], 130)

    def test_pyramiding_add_uses_initial_quantity_and_full_exit_closes_all_units(self) -> None:
        trade = SimulatedTrade(
            trade_id="stock-pyramid",
            status="OPEN",
            direction="LONG_STOCK",
            instrument_mode=InstrumentMode.stock,
            instrument_label="SBIN",
            price_mode="cash",
            trade_security_id="3045",
            quote_exchange_segment="NSE_EQ",
            option_type="CE",
            strike=0,
            symbol="SBIN EQ",
            quantity=10,
            base_quantity=10,
            open_quantity=10,
            entry_time=datetime(2026, 5, 29, 9, 30),
            entry_price=100.0,
            entry_spot_price=100.0,
            entry_option_price=100.0,
            current_price=100.0,
            current_option_price=100.0,
            stop_price=98.0,
            stop_option_price=98.0,
            target_price=130.0,
            target_option_price=130.0,
            invalidation_level=100.0,
            target_spot_price=130.0,
        )
        self.test_engine.active_trade = trade
        self.test_engine.trade_history = [trade]

        self.test_engine.apply_trade_logic(
            self._make_candle(20, 109, 111, 108, 110),
            TradeDecision(action=TradeAction.add_position, reason="Protected continuation add.", add_quantity=999),
            source="replay",
        )

        self.assertEqual(trade.quantity, 20)
        self.assertEqual(trade.open_quantity, 20)
        self.assertEqual(trade.base_quantity, 10)
        self.assertEqual(trade.pyramid_count, 1)
        self.assertEqual(trade.entry_price, 105.0)

        self.test_engine.close_active_trade(self._make_candle(30, 119, 121, 118, 120), "full exit")

        self.assertEqual(self.test_engine.trade_history[-1].closed_quantity, 20)
        self.assertEqual(self.test_engine.trade_history[-1].open_quantity, 0)
        self.assertEqual(self.test_engine.trade_history[-1].booked_pnl, 300.0)

    def test_heuristic_pyramiding_requires_toggle_protected_stop_and_same_side_setup(self) -> None:
        candles = [
            self._make_candle(0, 100, 101, 99, 100),
            self._make_candle(1, 100, 103, 99.8, 102),
            self._make_candle(2, 102, 105, 101.5, 104),
        ]
        trade = self._build_trade(
            entry_time=candles[0].timestamp,
            entry_spot_price=100.0,
            invalidation_level=100.0,
            setup_type="bullish_reclaim_watch",
        )
        trade.base_quantity = 1
        context = self._build_context(candles, active_trade=trade)
        observation = self._build_observation(atr=2.0, day_type="gap-and-go")
        candidate = SetupCandidate(
            setup_type="bullish_pullback_continuation",
            direction="LONG_CALL",
            option_type="CE",
            trigger_basis="close_above",
            trigger_price=103.0,
            invalidation_level=100.0,
            defended_level=101.5,
            target_spot_price=112.0,
            first_target_price=106.0,
            score=82.0,
            ready_to_enter=True,
            notes=["Same-side continuation held after the protected reclaim."],
            rule_ids=["R25", "R27", "R63"],
            event=SweepEvent(
                side="sell",
                level_label="opening-range-low",
                level_price=100.0,
                sweep_index=1,
                reclaim_index=2,
                trigger_index=2,
                sweep_price=99.8,
                defended_level=101.5,
                trigger_price=103.0,
                invalidation_level=100.0,
                primary=True,
                quality="tradable",
            ),
        )

        with patch.object(self.test_engine.heuristic_engine, "build_candidates", return_value=[candidate]):
            disabled = self.test_engine.heuristic_engine.manage_active_trade(context, observation, current_trade_price=12.0)
            enabled_context = context.model_copy(update={"pyramiding_enabled": True})
            enabled = self.test_engine.heuristic_engine.manage_active_trade(enabled_context, observation, current_trade_price=12.0)

        self.assertNotEqual(disabled.action, TradeAction.add_position)
        self.assertEqual(enabled.action, TradeAction.add_position)
        self.assertEqual(enabled.add_quantity, 1)

    def test_intelligent_pyramiding_uses_add_leg_risk_without_protected_main_stop(self) -> None:
        candles = [
            self._make_candle(0, 100, 101, 99, 100),
            self._make_candle(1, 100, 102.4, 99.8, 102),
        ]
        trade = self._build_trade(
            entry_time=candles[0].timestamp,
            entry_spot_price=100.0,
            invalidation_level=98.0,
            setup_type="bullish_reclaim_watch",
        )
        trade.quantity = 100
        trade.base_quantity = 100
        trade.open_quantity = 100
        context = self._build_context(candles, active_trade=trade).model_copy(
            update={"intelligent_pyramiding_enabled": True}
        )
        observation = self._build_observation(atr=2.0, day_type="gap-and-go")
        candidate = SetupCandidate(
            setup_type="bullish_pullback_continuation",
            direction="LONG_CALL",
            option_type="CE",
            trigger_basis="close_above",
            trigger_price=101.8,
            invalidation_level=101.3,
            defended_level=101.4,
            target_spot_price=106.0,
            first_target_price=103.0,
            score=84.0,
            ready_to_enter=True,
            notes=["Fresh continuation add with tight defended pullback stop."],
            rule_ids=["R25", "R27", "R63"],
            event=SweepEvent(
                side="sell",
                level_label="pullback-low",
                level_price=101.4,
                sweep_index=1,
                reclaim_index=1,
                trigger_index=1,
                sweep_price=101.3,
                defended_level=101.4,
                trigger_price=101.8,
                invalidation_level=101.3,
                primary=True,
                quality="tradable",
            ),
        )

        with patch.object(self.test_engine.heuristic_engine, "build_candidates", return_value=[candidate]):
            decision = self.test_engine.heuristic_engine.manage_active_trade(context, observation, current_trade_price=12.0)

        self.assertEqual(decision.action, TradeAction.add_position)
        self.assertEqual(decision.add_quantity, 100)
        self.assertEqual(decision.invalidation_level, 101.3)

        self.test_engine.active_trade = trade
        self.test_engine.trade_history = [trade]
        self.test_engine.apply_trade_logic(candles[-1], decision, source="replay")

        self.assertEqual(trade.open_quantity, 200)
        self.assertEqual(len(trade.pyramid_legs), 1)
        self.assertEqual(trade.pyramid_legs[0].invalidation_level, 101.3)

        stop_candle = self._make_candle(2, 102, 102.1, 101.0, 101.2)
        stop_context = self._build_context(candles + [stop_candle], active_trade=trade).model_copy(
            update={"intelligent_pyramiding_enabled": True}
        )
        with patch.object(self.test_engine.heuristic_engine, "build_candidates", return_value=[]):
            exit_decision = self.test_engine.heuristic_engine.manage_active_trade(stop_context, observation, current_trade_price=11.0)

        self.assertEqual(exit_decision.action, TradeAction.exit_pyramid_leg)
        self.assertEqual(exit_decision.partial_exit_quantity, 100)

        self.test_engine.apply_trade_logic(stop_candle, exit_decision, source="replay")

        self.assertEqual(trade.open_quantity, 100)
        self.assertEqual(trade.closed_quantity, 100)
        self.assertEqual(trade.pyramid_legs[0].status, "CLOSED")

    def test_nifty_live_ltp_crossing_invalidation_sends_immediate_exit(self) -> None:
        trade = SimulatedTrade(
            trade_id="nifty-hard-stop",
            status="OPEN",
            direction="SHORT_PUT",
            instrument_mode=InstrumentMode.nifty,
            instrument_label="Nifty 50",
            price_mode="option",
            trade_security_id="13",
            quote_exchange_segment="NSE_FNO",
            option_type="PE",
            strike=23400,
            symbol="NIFTY 23400 PE",
            option_security_id="opt-23400-pe",
            quantity=65,
            open_quantity=65,
            entry_time=datetime(2026, 5, 29, 10, 0),
            entry_price=100.0,
            entry_spot_price=23500.0,
            entry_option_price=100.0,
            execution_source="live",
            current_price=100.0,
            current_option_price=100.0,
            stop_price=140.0,
            stop_option_price=140.0,
            target_price=40.0,
            target_option_price=40.0,
            invalidation_level=23400.0,
            target_spot_price=23800.0,
        )
        self.test_engine.active_trade = trade
        self.test_engine.trade_history = [trade]
        self.test_engine.live_trading_enabled = True
        self.test_engine.operating_mode = OperatingMode.heuristic
        self.test_engine.live_feed_adapter = Mock()
        self.temp_store.save(client_id="cid", access_token=self._make_dhan_token("cid"))

        with patch.object(
            self.test_engine.execution_service,
            "place_market_order",
            return_value=BrokerOrderResult(ok=True, order_id="exit-hard-stop", order_status="PENDING", message="ok", raw={}),
        ) as place_order:
            with patch.object(self.test_engine.option_quote_service, "fetch_quote", side_effect=DhanOptionQuoteError("offline")):
                self.test_engine._handle_live_packet_now({"security_id": "13", "LTP": 23399.0, "LTT": "10:02:01", "volume": 1000})

        self.assertEqual(place_order.call_args.kwargs["transaction_type"], "BUY")
        self.assertIsNone(self.test_engine.active_trade)
        self.assertIn("Hard LTP stop triggered", self.test_engine.trade_history[-1].exit_notes or "")

    def test_live_ltp_controls_do_not_close_replay_trade_with_current_market_price(self) -> None:
        trade = SimulatedTrade(
            trade_id="historical-replay-trade",
            status="OPEN",
            direction="LONG_STOCK",
            instrument_mode=InstrumentMode.nifty,
            instrument_label="Nifty 50",
            price_mode="cash",
            trade_security_id="13",
            quote_exchange_segment="IDX_I",
            option_type="CE",
            strike=0,
            symbol="NIFTY SPOT",
            quantity=1,
            open_quantity=1,
            entry_time=datetime(2026, 2, 17, 14, 38),
            entry_price=25715.35,
            entry_spot_price=25715.35,
            entry_option_price=25715.35,
            execution_source="replay",
            current_price=25715.35,
            current_option_price=25715.35,
            stop_price=25695.35,
            stop_option_price=25695.35,
            target_price=25765.68,
            target_option_price=25765.68,
            invalidation_level=25695.35,
            target_spot_price=25765.68,
        )
        self.test_engine.active_trade = trade
        self.test_engine.trade_history = [trade]

        changed = self.test_engine._apply_live_ltp_trade_controls(23382.60, datetime(2026, 6, 1, 15, 31, 54))

        self.assertFalse(changed)
        self.assertIs(self.test_engine.active_trade, trade)
        self.assertIsNone(trade.exit_time)
        self.assertEqual(trade.current_price, 25715.35)

    def test_live_packets_are_ignored_while_replay_simulation_is_running(self) -> None:
        trade = SimulatedTrade(
            trade_id="running-replay-trade",
            status="OPEN",
            direction="SHORT_STOCK",
            instrument_mode=InstrumentMode.nifty,
            instrument_label="Nifty 50",
            price_mode="cash",
            trade_security_id="13",
            quote_exchange_segment="IDX_I",
            option_type="PE",
            strike=0,
            symbol="NIFTY SPOT",
            quantity=1,
            open_quantity=1,
            entry_time=datetime(2026, 2, 17, 10, 39),
            entry_price=25692.80,
            entry_spot_price=25692.80,
            entry_option_price=25692.80,
            execution_source="replay",
            current_price=25692.80,
            current_option_price=25692.80,
            stop_price=25692.80,
            stop_option_price=25692.80,
            target_price=25224.02,
            target_option_price=25224.02,
            invalidation_level=25692.80,
            target_spot_price=25224.02,
        )
        self.test_engine.active_trade = trade
        self.test_engine.trade_history = [trade]
        self.test_engine.candles = [
            Candle(timestamp=datetime(2026, 2, 17, 10, 39), open=25692.8, high=25700, low=25680, close=25692.8, volume=1000)
        ]
        self.test_engine.current_index = 0

        self.test_engine._begin_replay_simulation()
        try:
            self.test_engine._handle_live_packet_now({"security_id": "13", "LTP": 23382.60, "LTT": "15:31:54", "volume": 1000})
        finally:
            self.test_engine._end_replay_simulation()

        self.assertIs(self.test_engine.active_trade, trade)
        self.assertIsNone(trade.exit_time)
        self.assertEqual(len(self.test_engine.candles), 1)
        self.assertEqual(self.test_engine.candles[-1].close, 25692.8)

    def test_nifty_replay_invalidation_exit_books_at_stop_level_not_candle_close(self) -> None:
        candles = [
            Candle(timestamp="2026-05-18T14:34:00", open=23609.05, high=23611.5, low=23595.9, close=23597.4, volume=1000),
            Candle(timestamp="2026-05-18T15:00:00", open=23591.0, high=23666.3, low=23590.25, close=23665.8, volume=1200),
        ]
        trade = self._build_trade(
            entry_time=candles[0].timestamp,
            entry_spot_price=23597.4,
            invalidation_level=23617.4,
            setup_type="bearish_rejection_watch",
        ).model_copy(
            update={
                "direction": "SHORT_STOCK",
                "option_type": "PE",
                "price_mode": "cash",
                "entry_price": 23597.4,
                "current_price": 23597.4,
            }
        )
        self.test_engine.active_trade = trade
        self.test_engine.trade_history = [trade]
        context = self._build_context(candles, active_trade=trade)
        observation = self._build_observation(atr=20.0)

        decision = self.test_engine.heuristic_engine.manage_active_trade(context, observation, current_trade_price=23665.8)
        self.test_engine.apply_trade_logic(candles[1], decision, source="replay")

        self.assertEqual(decision.action, TradeAction.exit)
        self.assertEqual(decision.target_spot_price, 23617.4)
        self.assertEqual(trade.exit_price, 23617.4)
        self.assertEqual(trade.booked_pnl, -20.0)

    def test_nifty_late_session_blocks_equal_cluster_only_entry(self) -> None:
        engine = HeuristicDecisionEngine()
        candles = [
            Candle(timestamp="2026-05-18T14:31:00", open=23605.35, high=23621.9, low=23604.2, close=23619.9, volume=1000),
            Candle(timestamp="2026-05-18T14:32:00", open=23619.9, high=23624.25, low=23614.05, close=23614.3, volume=1000),
            Candle(timestamp="2026-05-18T14:33:00", open=23614.5, high=23616.05, low=23606.95, close=23609.95, volume=1000),
            Candle(timestamp="2026-05-18T14:34:00", open=23609.05, high=23611.5, low=23595.9, close=23597.4, volume=1000),
        ]
        context = self._build_context(candles, previous_close=23500.0)
        observation = self._build_observation(
            atr=12.0,
            day_type="gap-and-go",
            range_state="balanced",
            participation_state="two_sided_active",
            value_state="fair",
        )
        event = SweepEvent(
            side="buy",
            level_label="Equal High Cluster (13 touches)",
            level_price=23610.0,
            sweep_index=3,
            reclaim_index=3,
            trigger_index=3,
            sweep_price=23611.5,
            defended_level=23610.0,
            trigger_price=23597.4,
            invalidation_level=23617.4,
            primary=True,
            quality="tradable",
            notes=["Equal High Cluster (13 touches) swept.", "Sweep and reclaim happened on the same candle."],
        )

        candidate = engine.build_candidate_from_event(context, observation, event, option_type="PE", direction="LONG_PUT")

        assert candidate is not None
        self.assertFalse(candidate.ready_to_enter)
        self.assertLessEqual(candidate.score, 62.0)
        self.assertTrue(any("Late-session Nifty equal-high/equal-low" in note for note in candidate.notes))

    def test_nifty_live_ltp_square_offs_near_next_round_shelf(self) -> None:
        trade = SimulatedTrade(
            trade_id="nifty-round-exit",
            status="OPEN",
            direction="SHORT_PUT",
            instrument_mode=InstrumentMode.nifty,
            instrument_label="Nifty 50",
            price_mode="option",
            trade_security_id="13",
            quote_exchange_segment="NSE_FNO",
            option_type="PE",
            strike=24200,
            symbol="NIFTY 24200 PE",
            option_security_id="opt-24200-pe",
            quantity=65,
            open_quantity=65,
            entry_time=datetime(2026, 5, 29, 10, 0),
            entry_price=100.0,
            entry_spot_price=24252.0,
            entry_option_price=100.0,
            execution_source="live",
            current_price=100.0,
            current_option_price=100.0,
            stop_price=70.0,
            stop_option_price=70.0,
            target_price=30.0,
            target_option_price=30.0,
            invalidation_level=24220.0,
            target_spot_price=24500.0,
        )
        self.test_engine.active_trade = trade
        self.test_engine.trade_history = [trade]
        self.test_engine.live_trading_enabled = True
        self.test_engine.operating_mode = OperatingMode.heuristic
        self.test_engine.live_feed_adapter = Mock()
        self.test_engine.candles = [
            Candle(timestamp=datetime(2026, 5, 29, 10, 0), open=24252, high=24260, low=24245, close=24252, volume=1000),
            Candle(timestamp=datetime(2026, 5, 29, 10, 1), open=24252, high=24283, low=24248, close=24276, volume=1200),
            Candle(timestamp=datetime(2026, 5, 29, 10, 2), open=24276, high=24278, low=24258, close=24260, volume=1300),
            Candle(timestamp=datetime(2026, 5, 29, 10, 3), open=24260, high=24262, low=24242, close=24245, volume=1400),
        ]
        self.test_engine.current_index = len(self.test_engine.candles) - 1
        self.temp_store.save(client_id="cid", access_token=self._make_dhan_token("cid"))

        with patch.object(
            self.test_engine.execution_service,
            "place_market_order",
            return_value=BrokerOrderResult(ok=True, order_id="exit-round", order_status="PENDING", message="ok", raw={}),
        ) as place_order:
            with patch.object(self.test_engine.option_quote_service, "fetch_quote", side_effect=DhanOptionQuoteError("offline")):
                self.test_engine._apply_live_ltp_trade_controls(24245.0, datetime(2026, 5, 29, 10, 3))

        self.assertEqual(place_order.call_args.kwargs["transaction_type"], "BUY")
        self.assertIsNone(self.test_engine.active_trade)
        self.assertIn("next 100-point round shelf 24300.00", self.test_engine.trade_history[-1].exit_notes or "")

    def test_stock_live_ltp_crossing_invalidation_closes_paper_trade_immediately(self) -> None:
        self.test_engine.set_instrument_mode("stock")
        trade = SimulatedTrade(
            trade_id="stock-hard-stop",
            status="OPEN",
            direction="LONG_STOCK",
            instrument_mode=InstrumentMode.stock,
            instrument_label="SBIN",
            price_mode="cash",
            trade_security_id="3045",
            quote_exchange_segment="NSE_EQ",
            option_type="CE",
            strike=0,
            symbol="SBIN EQ",
            quantity=10,
            open_quantity=10,
            entry_time=datetime(2026, 5, 29, 10, 0),
            entry_price=100.0,
            entry_spot_price=100.0,
            entry_option_price=100.0,
            execution_source="live",
            current_price=100.0,
            current_option_price=100.0,
            stop_price=99.0,
            stop_option_price=99.0,
            target_price=110.0,
            target_option_price=110.0,
            invalidation_level=99.0,
            target_spot_price=110.0,
        )
        self.test_engine.active_trade = trade
        self.test_engine.trade_history = [trade]

        self.test_engine._handle_live_packet_now({"security_id": "3045", "LTP": 98.95, "LTT": "10:02:01", "volume": 1000})

        self.assertIsNone(self.test_engine.active_trade)
        self.assertEqual(self.test_engine.trade_history[-1].exit_price, 98.95)
        self.assertIn("Hard LTP stop triggered", self.test_engine.trade_history[-1].exit_notes or "")

    def test_nifty_option_trade_trails_stop_after_one_r(self) -> None:
        session = [
            self._make_candle(0, 23525, 23545, 23515, 23525),
            self._make_candle(1, 23525, 23560, 23520, 23550),
            self._make_candle(2, 23550, 23575, 23545, 23570),
        ]
        trade = SimulatedTrade(
            trade_id="nifty-short-put",
            status="OPEN",
            direction="SHORT_PUT",
            option_type="PE",
            strike=23400,
            symbol="NIFTY 14MAY2026 23400PE",
            quantity=65,
            open_quantity=65,
            entry_time=session[0].timestamp,
            entry_price=100.0,
            entry_spot_price=23525.0,
            entry_option_price=100.0,
            current_price=75.0,
            current_option_price=75.0,
            stop_price=130.0,
            stop_option_price=130.0,
            target_price=50.0,
            target_option_price=50.0,
            invalidation_level=23495.0,
            target_spot_price=23720.0,
            setup_type="bullish_reclaim_watch",
        )
        context = self._build_context(session, active_trade=trade)
        observation = self._build_observation(
            atr=50.0,
            vwap=23535.0,
            session_high=23575.0,
            session_low=23515.0,
            day_type="trend-day",
            range_state="expanding",
            participation_state="directional",
        )

        decision = self.test_engine.heuristic_engine.manage_active_trade(context, observation, current_trade_price=75.0)

        self.assertEqual(decision.action, TradeAction.update_stop)
        self.assertGreaterEqual(decision.invalidation_level, trade.entry_spot_price)

    def test_nifty_primary_sweep_enters_on_same_candle_reclaim_with_three_r_target(self) -> None:
        session = [
            Candle(timestamp="2026-05-26T09:15:00", open=23920, high=23924, low=23908, close=23910, volume=1000),
            Candle(timestamp="2026-05-26T09:16:00", open=23910, high=23918, low=23890, close=23912, volume=1400),
        ]
        context = self._build_context(session, previous_close=23900)
        observation = self._build_observation(
            atr=10.0,
            vwap=23900.0,
            session_high=23924.0,
            session_low=23890.0,
            day_type="trap-day",
            value_state="discount",
            range_state="expanding",
            participation_state="directional",
            strong_intent=True,
            weak_intent=False,
            higher_timeframe_context="neutral",
        )
        event = SweepEvent(
            side="sell",
            level_label="Round Number 23900.00",
            level_price=23900.0,
            sweep_index=1,
            reclaim_index=1,
            trigger_index=1,
            sweep_price=23890.0,
            defended_level=23900.0,
            trigger_price=23890.0,
            invalidation_level=23890.0,
            primary=True,
            quality="tradable",
            notes=["Round Number 23900.00 swept.", "Sweep and reclaim happened on the same candle."],
        )

        candidate = self.test_engine.heuristic_engine.build_candidate_from_event(
            context,
            observation,
            event,
            option_type="CE",
            direction="LONG_CALL",
        )

        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertTrue(candidate.ready_to_enter)
        self.assertIn("R140", candidate.rule_ids)
        risk = session[-1].close - candidate.invalidation_level
        self.assertGreaterEqual(candidate.target_spot_price - session[-1].close, risk * 3 - 0.01)

    def test_nifty_primary_sweep_enters_on_same_candle_rejection_with_three_r_target(self) -> None:
        session = [
            Candle(timestamp="2026-05-26T09:15:00", open=24070, high=24092, low=24068, close=24088, volume=1000),
            Candle(timestamp="2026-05-26T09:16:00", open=24105, high=24120, low=24080, close=24092, volume=1400),
        ]
        context = self._build_context(session, previous_close=24100)
        observation = self._build_observation(
            atr=10.0,
            vwap=24100.0,
            session_high=24120.0,
            session_low=24068.0,
            day_type="trap-day",
            value_state="inflated",
            range_state="expanding",
            participation_state="directional",
            strong_intent=True,
            weak_intent=False,
            higher_timeframe_context="neutral",
        )
        event = SweepEvent(
            side="buy",
            level_label="Round Number 24100.00",
            level_price=24100.0,
            sweep_index=1,
            reclaim_index=1,
            trigger_index=1,
            sweep_price=24120.0,
            defended_level=24100.0,
            trigger_price=24120.0,
            invalidation_level=24120.0,
            primary=True,
            quality="tradable",
            notes=["Round Number 24100.00 swept.", "Sweep and reclaim happened on the same candle."],
        )

        candidate = self.test_engine.heuristic_engine.build_candidate_from_event(
            context,
            observation,
            event,
            option_type="PE",
            direction="LONG_PUT",
        )

        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertTrue(candidate.ready_to_enter)
        self.assertIn("R140", candidate.rule_ids)
        risk = candidate.invalidation_level - session[-1].close
        self.assertGreaterEqual(session[-1].close - candidate.target_spot_price, risk * 3 - 0.01)

    def test_connect_live_feed_starts_adapter_once(self) -> None:
        fake_adapter = Mock()
        fake_adapter.start = Mock()

        with patch.object(self.test_engine, "sync_dhan_context", return_value=self.test_engine.get_state()) as sync_mock:
            with patch("app.services.simulation.resolve_quote_subscription", return_value=("IDX", "13", "Quote")):
                with patch("app.services.simulation.DhanMarketFeedAdapter", return_value=fake_adapter):
                    response = self.client.post("/api/live/connect", data={"client_id": "cid", "access_token": "tok"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(sync_mock.call_count, 1)
        fake_adapter.start.assert_called_once()

    def test_available_dhan_credentials_prefers_client_id_embedded_in_token(self) -> None:
        token = self._make_dhan_token("2200110011")
        client_id, access_token = self.test_engine._available_dhan_credentials("wrong-client", token)

        self.assertEqual(client_id, "2200110011")
        self.assertEqual(access_token, token)

    def test_save_credentials_uses_dhan_client_id_embedded_in_token(self) -> None:
        token = self._make_dhan_token("2200110011")
        self.temp_store.save(client_id="wrong-client", access_token=token)

        summary = self.temp_store.summary(get_settings())

        self.assertEqual(summary.client_id, "2200110011")
        self.assertEqual(summary.resolved_client_id, "2200110011")
        self.assertIsNone(summary.dhan_credential_message)

    def test_connect_live_feed_reuses_running_adapter(self) -> None:
        fake_adapter = Mock()
        fake_adapter.is_running.return_value = True
        self.test_engine.live_feed_adapter = fake_adapter
        self.test_engine.live_feed.status = "reconnecting"
        self.test_engine.live_feed.status_message = "Retrying shortly."

        with patch.object(self.test_engine, "_schedule_watchlist_subscription_refresh") as refresh_mock:
            with patch.object(self.test_engine, "sync_dhan_context") as sync_mock:
                with patch("app.services.simulation.DhanMarketFeedAdapter") as adapter_mock:
                    response = self.client.post("/api/live/connect", data={"client_id": "cid", "access_token": "tok"})

        self.assertEqual(response.status_code, 200)
        refresh_mock.assert_called_once()
        sync_mock.assert_not_called()
        adapter_mock.assert_not_called()
        fake_adapter.stop.assert_not_called()

    def test_connect_live_feed_replaces_stale_adapter(self) -> None:
        stale_adapter = Mock()
        stale_adapter.is_running.return_value = False
        self.test_engine.live_feed_adapter = stale_adapter
        self.test_engine.live_feed.status = "error"
        self.test_engine.live_feed.error = "HTTP 429"

        fake_adapter = Mock()
        fake_adapter.start = Mock()

        with patch.object(self.test_engine, "sync_dhan_context", return_value=self.test_engine.get_state()) as sync_mock:
            with patch("app.services.simulation.resolve_quote_subscription", return_value=("IDX", "13", "Quote")):
                with patch("app.services.simulation.DhanMarketFeedAdapter", return_value=fake_adapter):
                    response = self.client.post("/api/live/connect", data={"client_id": "cid", "access_token": "tok"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(sync_mock.call_count, 1)
        stale_adapter.stop.assert_called_once()
        fake_adapter.start.assert_called_once()

    def test_connect_live_feed_in_nifty_mode_subscribes_banknifty_companion(self) -> None:
        fake_adapter = Mock()
        fake_adapter.start = Mock()

        with patch.object(self.test_engine, "sync_dhan_context", return_value=self.test_engine.get_state()):
            with patch(
                "app.services.simulation.resolve_quote_subscription",
                side_effect=lambda security_id, segment: (segment, security_id, "Quote"),
            ):
                with patch("app.services.simulation.DhanMarketFeedAdapter", return_value=fake_adapter) as adapter_mock:
                    response = self.client.post("/api/live/connect", data={"client_id": "cid", "access_token": "tok"})

        self.assertEqual(response.status_code, 200)
        fake_adapter.start.assert_called_once()
        instruments = adapter_mock.call_args.args[2]
        self.assertEqual({instrument[1] for instrument in instruments}, {"13", "25"})

    def test_handle_live_status_tracks_reconnect_metadata(self) -> None:
        next_retry_at = datetime(2026, 5, 18, 9, 30)

        self.test_engine.handle_live_status(
            "reconnecting",
            "Dhan websocket rate-limited the connection (HTTP 429). Retrying in 20s.",
            retry_attempt=2,
            next_retry_at=next_retry_at,
        )

        state = self.test_engine.get_state()
        self.assertEqual(state.live_feed.status, "reconnecting")
        self.assertEqual(state.live_feed.retry_attempt, 2)
        self.assertEqual(state.live_feed.next_retry_at, next_retry_at)
        self.assertIn("HTTP 429", state.live_feed.error)

    def test_switching_to_stock_mode_updates_state(self) -> None:
        response = self.client.post("/api/instrument-mode", data={"instrument_mode": "stock"})

        self.assertEqual(response.status_code, 200)
        state = response.json()["state"]
        self.assertEqual(state["instrument"]["mode"], "stock")
        self.assertEqual(state["instrument"]["label"], "SBIN")
        self.assertEqual(state["instrument"]["security_id"], "3045")
        self.assertFalse(state["instrument"]["supports_options"])
        self.assertTrue(state["stock_watchlist"])
        self.assertEqual(state["stock_watchlist"][0]["symbol"], "SBIN")

    def test_stock_search_endpoint_returns_matching_symbols(self) -> None:
        response = self.client.get("/api/stocks/search?q=sb")

        self.assertEqual(response.status_code, 200)
        results = response.json()["results"]
        self.assertTrue(any(item["symbol"] == "SBIN" for item in results))

    def test_adding_stock_to_watchlist_selects_active_stock(self) -> None:
        resolved = StockUniverseEntry(symbol="TCS", label="TATA CONSULTANCY SERV LT", security_id="11536")
        with patch.object(self.test_engine.stock_universe, "preview", return_value=resolved):
            with patch.object(self.test_engine, "_auto_prepare_watchlist_symbols_async", return_value=None):
                response = self.client.post("/api/stocks/watchlist/add", data={"symbol": "TCS"})

        self.assertEqual(response.status_code, 200)
        state = response.json()["state"]
        self.assertEqual(state["instrument"]["mode"], "stock")
        self.assertEqual(state["instrument"]["symbol"], "TCS")
        self.assertEqual(state["instrument"]["security_id"], "11536")
        self.assertTrue(any(item["symbol"] == "TCS" and item["selected"] for item in state["stock_watchlist"]))

    def test_adding_stock_with_credentials_uses_background_selected_sync(self) -> None:
        preview = StockUniverseEntry(symbol="TCS", label="TCS", security_id="")
        with patch.object(self.test_engine.stock_universe, "preview", return_value=preview):
            with patch.object(self.test_engine, "_available_dhan_credentials", return_value=("cid", "tok")):
                with patch.object(self.test_engine, "_auto_prepare_watchlist_symbols_async", return_value=None) as auto_sync_mock:
                    response = self.client.post("/api/stocks/watchlist/add", data={"symbol": "TCS"})

        self.assertEqual(response.status_code, 200)
        auto_sync_mock.assert_called_once()

    def test_stock_universe_resolve_refreshes_remote_when_symbol_missing_from_cache(self) -> None:
        service = StockUniverseService()
        service._resolved_entries = {
            "SBIN": StockUniverseEntry(symbol="SBIN", label="STATE BANK OF INDIA", security_id="3045"),
        }
        service._remote_master_loaded = False

        def fake_load_master() -> None:
            service._resolved_entries["TCS"] = StockUniverseEntry(
                symbol="TCS",
                label="TATA CONSULTANCY SERV LT",
                security_id="11536",
            )
            service._remote_master_loaded = True

        with patch.object(service, "_load_master_locked", side_effect=fake_load_master) as load_mock:
            resolved = service.resolve("TCS")

        self.assertEqual(resolved.security_id, "11536")
        load_mock.assert_called_once()

    def test_removing_selected_stock_promotes_remaining_watchlist_stock(self) -> None:
        resolved = StockUniverseEntry(symbol="TCS", label="TATA CONSULTANCY SERV LT", security_id="11536")
        with patch.object(self.test_engine.stock_universe, "preview", return_value=resolved):
            with patch.object(self.test_engine, "_auto_prepare_watchlist_symbols_async", return_value=None):
                self.client.post("/api/stocks/watchlist/add", data={"symbol": "TCS"})

        response = self.client.post("/api/stocks/watchlist/remove", data={"symbol": "TCS"})

        self.assertEqual(response.status_code, 200)
        state = response.json()["state"]
        self.assertEqual(state["instrument"]["mode"], "stock")
        self.assertEqual(state["instrument"]["symbol"], "SBIN")
        self.assertEqual(len(state["stock_watchlist"]), 1)
        self.assertEqual(state["stock_watchlist"][0]["symbol"], "SBIN")
        self.assertTrue(state["stock_watchlist"][0]["selected"])

    def test_removing_last_stock_leaves_watchlist_empty(self) -> None:
        self.client.post("/api/instrument-mode", data={"instrument_mode": "stock"})

        response = self.client.post("/api/stocks/watchlist/remove", data={"symbol": "SBIN"})

        self.assertEqual(response.status_code, 200)
        state = response.json()["state"]
        self.assertEqual(state["instrument"]["mode"], "stock")
        self.assertEqual(state["instrument"]["label"], "Stock Watchlist")
        self.assertEqual(state["instrument"]["security_id"], "")
        self.assertEqual(state["stock_watchlist"], [])

    def test_connect_live_feed_in_stock_mode_subscribes_selected_watchlist(self) -> None:
        fake_adapter = Mock()
        fake_adapter.start = Mock()

        with patch.object(
            self.test_engine.stock_universe,
            "preview",
            return_value=StockUniverseEntry(symbol="TCS", label="TATA CONSULTANCY SERV LT", security_id="11536"),
        ):
            with patch.object(self.test_engine, "_auto_prepare_watchlist_symbols_async", return_value=None):
                self.test_engine.add_stock_to_watchlist("TCS")

        with patch.object(self.test_engine, "sync_dhan_context", return_value=self.test_engine.get_state()) as sync_mock:
            with patch(
                "app.services.simulation.resolve_quote_subscription",
                side_effect=lambda security_id, segment: (segment, security_id, "Quote"),
            ):
                with patch("app.services.simulation.DhanMarketFeedAdapter", return_value=fake_adapter) as adapter_mock:
                    with patch.object(self.test_engine, "_auto_prepare_watchlist_symbols_async") as prepare_mock:
                        response = self.client.post("/api/live/connect", data={"client_id": "cid", "access_token": "tok"})

        self.assertEqual(response.status_code, 200)
        sync_mock.assert_not_called()
        prepare_mock.assert_called_once()
        fake_adapter.start.assert_called_once()
        instruments = adapter_mock.call_args.args[2]
        self.assertEqual({instrument[1] for instrument in instruments}, {"13", "3045", "11536"})

    def test_connect_live_feed_in_stock_mode_maps_only_resolved_watchlist_subscriptions(self) -> None:
        fake_adapter = Mock()
        fake_adapter.start = Mock()
        self.test_engine.instrument_mode = InstrumentMode.stock
        self.test_engine.stock_watchlist = {
            "SBIN": build_stock_instrument("SBIN", "3045", label="SBIN"),
            "UNRESOLVED": build_stock_instrument("UNRESOLVED", "", label="UNRESOLVED"),
            "TCS": build_stock_instrument("TCS", "11536", label="TCS"),
        }
        self.test_engine.selected_stock_symbol = "SBIN"
        self.test_engine.instrument_spec = self.test_engine.stock_watchlist["SBIN"]

        with patch.object(self.test_engine, "sync_dhan_context", return_value=self.test_engine.get_state()):
            with patch(
                "app.services.simulation.resolve_quote_subscription",
                side_effect=lambda security_id, segment: (segment, security_id, "Quote"),
            ):
                with patch("app.services.simulation.DhanMarketFeedAdapter", return_value=fake_adapter):
                    with patch.object(self.test_engine, "_auto_prepare_watchlist_symbols_async"):
                        response = self.client.post("/api/live/connect", data={"client_id": "cid", "access_token": "tok"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(self.test_engine._stock_quote_subscriptions), {"3045", "11536"})

    def test_adding_stock_while_live_feed_running_schedules_background_subscription_refresh(self) -> None:
        self.test_engine.live_feed_adapter = Mock()
        self.test_engine.instrument_mode = InstrumentMode.stock
        self.test_engine.selected_stock_symbol = "SBIN"
        self.test_engine.instrument_spec = self.test_engine.stock_watchlist["SBIN"]

        resolved = StockUniverseEntry(symbol="TCS", label="TATA CONSULTANCY SERV LT", security_id="11536")
        with patch.object(self.test_engine.stock_universe, "preview", return_value=resolved):
            with patch.object(self.test_engine, "_schedule_watchlist_subscription_refresh") as refresh_mock:
                with patch.object(self.test_engine, "_auto_prepare_watchlist_symbols_async") as prepare_mock:
                    response = self.client.post("/api/stocks/watchlist/add", data={"symbol": "TCS"})

        self.assertEqual(response.status_code, 200)
        refresh_mock.assert_called_once()
        prepare_mock.assert_called_once_with(["TCS"])

    def test_bulk_add_while_live_feed_running_prepares_symbols_and_schedules_refresh(self) -> None:
        self.test_engine.live_feed_adapter = Mock()
        self.test_engine.instrument_mode = InstrumentMode.stock
        self.test_engine.selected_stock_symbol = "SBIN"
        self.test_engine.instrument_spec = self.test_engine.stock_watchlist["SBIN"]

        with patch.object(self.test_engine, "_schedule_watchlist_subscription_refresh") as refresh_mock:
            with patch.object(self.test_engine, "_auto_prepare_watchlist_symbols_async") as prepare_mock:
                response = self.client.post(
                    "/api/stocks/watchlist/bulk-add",
                    data={
                        "bulk_text": (
                            "Symbol\tOpen\tHigh\tLow\n"
                            "AMBER\t7,158.00\t7,208.00\t6,976.00\n"
                            "GLAND\t2,122.00\t2,122.00\t2,034.00\n"
                        )
                    },
                )

        self.assertEqual(response.status_code, 200)
        refresh_mock.assert_called_once()
        prepare_mock.assert_called_once_with(["AMBER", "GLAND"])

    def test_stock_mode_sync_history_updates_all_watchlist_sessions(self) -> None:
        with patch.object(
            self.test_engine.stock_universe,
            "preview",
            return_value=StockUniverseEntry(symbol="TCS", label="TATA CONSULTANCY SERV LT", security_id="11536"),
        ):
            self.test_engine.add_stock_to_watchlist("TCS")

        bundles = {
            "13": DhanSessionBundle(
                previous_day_candles=[Candle(timestamp="2026-05-13T09:15:00", open=22400, high=22440, low=22380, close=22420, volume=1000)],
                intraday_candles=[Candle(timestamp="2026-05-14T09:15:00", open=22425, high=22455, low=22410, close=22448, volume=1000)],
                live_open_candle=None,
                previous_day_source="historical",
            ),
            "3045": DhanSessionBundle(
                previous_day_candles=[Candle(timestamp="2026-05-13T09:15:00", open=790, high=792, low=788, close=791, volume=1000)],
                intraday_candles=[Candle(timestamp="2026-05-14T09:15:00", open=792, high=794, low=791, close=793, volume=1000)],
                live_open_candle=None,
                previous_day_source="historical",
            ),
            "11536": DhanSessionBundle(
                previous_day_candles=[Candle(timestamp="2026-05-13T09:15:00", open=3500, high=3510, low=3490, close=3505, volume=1000)],
                intraday_candles=[Candle(timestamp="2026-05-14T09:15:00", open=3510, high=3520, low=3508, close=3518, volume=1000)],
                live_open_candle=None,
                previous_day_source="historical",
            ),
        }

        def fake_fetch_market_context(*, security_id, **kwargs):
            return bundles[str(security_id)]

        with patch.object(self.test_engine.chart_service, "fetch_market_context", side_effect=fake_fetch_market_context):
            response = self.client.post("/api/live/sync-history", data={"client_id": "cid", "access_token": "tok"})

        self.assertEqual(response.status_code, 200)
        state = response.json()["state"]
        by_symbol = {item["symbol"]: item for item in state["stock_watchlist"]}
        self.assertEqual(by_symbol["SBIN"]["total_loaded"], 2)
        self.assertEqual(by_symbol["TCS"]["total_loaded"], 2)
        self.assertEqual(by_symbol["TCS"]["history_status"], "ready")
        self.assertTrue(self.test_engine.companion_candles)

    def test_stock_mode_simulate_today_runs_for_all_watchlist_sessions(self) -> None:
        with patch.object(
            self.test_engine.stock_universe,
            "preview",
            return_value=StockUniverseEntry(symbol="TCS", label="TATA CONSULTANCY SERV LT", security_id="11536"),
        ):
            self.test_engine.add_stock_to_watchlist("TCS")

        bundles = {
            "13": DhanSessionBundle(
                previous_day_candles=[Candle(timestamp="2026-05-13T09:15:00", open=22400, high=22440, low=22380, close=22420, volume=1000)],
                intraday_candles=[
                    Candle(timestamp="2026-05-14T09:15:00", open=22425, high=22455, low=22410, close=22448, volume=1000),
                    Candle(timestamp="2026-05-14T09:16:00", open=22448, high=22470, low=22440, close=22465, volume=1000),
                ],
                live_open_candle=None,
                previous_day_source="historical",
            ),
            "3045": DhanSessionBundle(
                previous_day_candles=[Candle(timestamp="2026-05-13T09:15:00", open=790, high=792, low=788, close=791, volume=1000)],
                intraday_candles=[
                    Candle(timestamp="2026-05-14T09:15:00", open=792, high=794, low=791, close=793, volume=1000),
                    Candle(timestamp="2026-05-14T09:16:00", open=793, high=795, low=792, close=794, volume=1000),
                ],
                live_open_candle=None,
                previous_day_source="historical",
            ),
            "11536": DhanSessionBundle(
                previous_day_candles=[Candle(timestamp="2026-05-13T09:15:00", open=3500, high=3510, low=3490, close=3505, volume=1000)],
                intraday_candles=[
                    Candle(timestamp="2026-05-14T09:15:00", open=3510, high=3520, low=3508, close=3518, volume=1000),
                    Candle(timestamp="2026-05-14T09:16:00", open=3518, high=3524, low=3516, close=3522, volume=1000),
                ],
                live_open_candle=None,
                previous_day_source="historical",
            ),
        }

        def fake_fetch_market_context(*, security_id, **kwargs):
            return bundles[str(security_id)]

        with patch.object(self.test_engine.chart_service, "fetch_market_context", side_effect=fake_fetch_market_context):
            response = self.client.post("/api/simulation/today", data={"client_id": "cid", "access_token": "tok"})

        self.assertEqual(response.status_code, 200)
        state = response.json()["state"]
        by_symbol = {item["symbol"]: item for item in state["stock_watchlist"]}
        self.assertEqual(by_symbol["SBIN"]["intraday_candles"], 2)
        self.assertEqual(by_symbol["TCS"]["intraday_candles"], 2)
        self.assertEqual(state["instrument"]["symbol"], "TCS")
        self.assertTrue(self.test_engine.companion_candles)

    def test_stock_mode_simulate_today_can_replay_only_active_stock(self) -> None:
        with patch.object(
            self.test_engine.stock_universe,
            "preview",
            return_value=StockUniverseEntry(symbol="TCS", label="TATA CONSULTANCY SERV LT", security_id="11536"),
        ):
            self.test_engine.add_stock_to_watchlist("TCS")
        self.client.post("/api/stocks/watchlist/select", data={"symbol": "SBIN"})

        bundles = {
            "13": DhanSessionBundle(
                previous_day_candles=[
                    Candle(timestamp="2026-05-13T09:15:00", open=22400, high=22440, low=22380, close=22420, volume=1000)
                ],
                intraday_candles=[
                    Candle(timestamp="2026-05-14T09:15:00", open=22425, high=22455, low=22410, close=22448, volume=1000),
                ],
                live_open_candle=None,
                previous_day_source="historical",
            ),
            "3045": DhanSessionBundle(
                previous_day_candles=[
                    Candle(timestamp="2026-05-13T09:15:00", open=790, high=792, low=788, close=791, volume=1000)
                ],
                intraday_candles=[
                    Candle(timestamp="2026-05-14T09:15:00", open=792, high=794, low=791, close=793, volume=1000),
                    Candle(timestamp="2026-05-14T09:16:00", open=793, high=795, low=792, close=794, volume=1000),
                ],
                live_open_candle=None,
                previous_day_source="historical",
            ),
            "11536": DhanSessionBundle(
                previous_day_candles=[
                    Candle(timestamp="2026-05-13T09:15:00", open=3500, high=3510, low=3490, close=3505, volume=1000)
                ],
                intraday_candles=[
                    Candle(timestamp="2026-05-14T09:15:00", open=3510, high=3520, low=3508, close=3518, volume=1000),
                ],
                live_open_candle=None,
                previous_day_source="historical",
            ),
        }

        requested_security_ids: list[str] = []

        def fake_fetch_market_context(*, security_id, **kwargs):
            requested_security_ids.append(str(security_id))
            return bundles[str(security_id)]

        with patch.object(self.test_engine.chart_service, "fetch_market_context", side_effect=fake_fetch_market_context):
            response = self.client.post(
                "/api/simulation/today",
                data={"client_id": "cid", "access_token": "tok", "stock_replay_scope": "active"},
            )

        self.assertEqual(response.status_code, 200)
        state = response.json()["state"]
        by_symbol = {item["symbol"]: item for item in state["stock_watchlist"]}
        self.assertEqual(by_symbol["SBIN"]["intraday_candles"], 2)
        self.assertEqual(by_symbol["TCS"]["intraday_candles"], 0)
        self.assertEqual(state["instrument"]["symbol"], "SBIN")
        self.assertIn("3045", requested_security_ids)
        self.assertNotIn("11536", requested_security_ids)

    def test_simulate_today_replays_intraday_candles_from_session_start(self) -> None:
        bundle = DhanSessionBundle(
            previous_day_candles=[
                Candle(timestamp="2026-05-13T09:15:00", open=790, high=792, low=788, close=791, volume=1000),
            ],
            intraday_candles=[
                Candle(timestamp="2026-05-14T09:15:00", open=792, high=794, low=791, close=793, volume=1000),
                Candle(timestamp="2026-05-14T09:16:00", open=793, high=796, low=792, close=795, volume=1100),
            ],
            live_open_candle=None,
            previous_day_source="historical",
        )
        with patch.object(self.test_engine.chart_service, "fetch_market_context", return_value=bundle):
            response = self.client.post("/api/simulation/today", data={"client_id": "cid", "access_token": "tok"})

        self.assertEqual(response.status_code, 200)
        state = response.json()["state"]
        self.assertEqual(state["current_index"], 2)
        self.assertEqual(state["data_sync"]["intraday_candles"], 2)

    def test_simulate_today_requests_last_closed_session_before_open(self) -> None:
        bundle = DhanSessionBundle(
            previous_day_candles=[
                Candle(timestamp="2026-05-13T09:15:00", open=790, high=792, low=788, close=791, volume=1000),
            ],
            intraday_candles=[
                Candle(timestamp="2026-05-14T09:15:00", open=792, high=794, low=791, close=793, volume=1000),
            ],
            live_open_candle=None,
            previous_day_source="historical",
        )
        with patch.object(self.test_engine.chart_service, "fetch_market_context", return_value=bundle) as fetch_mock:
            response = self.client.post("/api/simulation/today", data={"client_id": "cid", "access_token": "tok"})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(fetch_mock.called)
        self.assertTrue(
            any(call.kwargs.get("prefer_last_closed_session_before_open") for call in fetch_mock.call_args_list)
        )

    def test_historical_simulation_replays_selected_days(self) -> None:
        bundle = DhanSessionBundle(
            previous_day_candles=[
                Candle(timestamp="2026-05-13T09:15:00", open=790, high=792, low=788, close=791, volume=1000),
            ],
            intraday_candles=[
                Candle(timestamp="2026-05-14T09:15:00", open=792, high=794, low=791, close=793, volume=1000),
                Candle(timestamp="2026-05-14T09:16:00", open=793, high=796, low=792, close=795, volume=1100),
            ],
            live_open_candle=None,
            previous_day_source="historical",
            replay_session_day=date(2026, 5, 14),
            intraday_source="historical",
            previous_context_day=date(2026, 5, 13),
        )
        with patch.object(self.test_engine.chart_service, "fetch_market_context_for_days", return_value=bundle) as fetch_mock:
            response = self.client.post(
                "/api/simulation/historical",
                data={
                    "client_id": "cid",
                    "access_token": "tok",
                    "replay_date": "2026-05-14",
                    "previous_context_date": "2026-05-13",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(fetch_mock.called)
        self.assertEqual(fetch_mock.call_args.kwargs["session_day"], date(2026, 5, 14))
        self.assertEqual(fetch_mock.call_args.kwargs["previous_context_day"], date(2026, 5, 13))
        state = response.json()["state"]
        self.assertEqual(state["data_sync"]["replay_session_day"], "2026-05-14")
        self.assertEqual(state["data_sync"]["previous_context_day"], "2026-05-13")

    def test_historical_replay_uses_completed_selected_duration_candles_for_decisions(self) -> None:
        self.test_engine.operating_mode = OperatingMode.heuristic
        bundle = DhanSessionBundle(
            previous_day_candles=[
                Candle(timestamp="2026-05-13T09:15:00", open=790, high=792, low=788, close=791, volume=1000),
            ],
            intraday_candles=[
                Candle(timestamp="2026-05-14T09:15:00", open=792, high=793, low=791, close=792.2, volume=1000),
                Candle(timestamp="2026-05-14T09:16:00", open=792.2, high=793.5, low=792.0, close=793.1, volume=1100),
                Candle(timestamp="2026-05-14T09:17:00", open=793.1, high=794.0, low=792.8, close=793.4, volume=1200),
                Candle(timestamp="2026-05-14T09:18:00", open=793.4, high=794.3, low=793.2, close=794.0, volume=1150),
                Candle(timestamp="2026-05-14T09:19:00", open=794.0, high=795.0, low=793.8, close=794.8, volume=1300),
                Candle(timestamp="2026-05-14T09:20:00", open=794.8, high=795.2, low=794.1, close=794.5, volume=900),
            ],
            live_open_candle=None,
            previous_day_source="historical",
            replay_session_day=date(2026, 5, 14),
            intraday_source="historical",
            previous_context_day=date(2026, 5, 13),
        )
        evaluated_timestamps: list[str] = []

        def fake_heuristic(context):
            evaluated_timestamps.append(context.current_candle.timestamp.isoformat())
            return TradeDecision(action=TradeAction.no_trade, confidence=0.25, reason="Test replay duration")

        with patch.object(self.test_engine.chart_service, "fetch_market_context_for_days", return_value=bundle):
            with patch.object(self.test_engine, "heuristic_decision", side_effect=fake_heuristic):
                self.test_engine.simulate_historical_session(
                    client_id="cid",
                    access_token="tok",
                    replay_date="2026-05-14",
                    previous_context_date="2026-05-13",
                    replay_decision_duration_minutes=5,
                )

        self.assertEqual(evaluated_timestamps, ["2026-05-14T09:19:00"])

    def test_nifty_replay_entries_use_spot_pricing_not_option_pricing(self) -> None:
        decision = TradeDecision(
            action=TradeAction.enter_put,
            confidence=0.82,
            reason="Replay should short the spot for bearish Nifty setups.",
            option_type="PE",
            invalidation_level=23640.0,
            target_spot_price=23520.0,
        )
        candle = Candle(timestamp="2026-05-14T11:20:00", open=23610, high=23618, low=23598, close=23602, volume=1500)

        trade = self.test_engine._build_entry_trade(candle, decision, source="replay")

        self.assertEqual(trade.price_mode, "cash")
        self.assertEqual(trade.direction, "SHORT_STOCK")
        self.assertEqual(trade.symbol, "NIFTY SPOT")
        self.assertEqual(trade.entry_price, 23602.0)
        self.assertEqual(trade.stop_price, 23640.0)
        self.assertEqual(trade.target_price, 23520.0)

    def test_dhan_market_context_uses_closed_session_replay_before_open(self) -> None:
        service = DhanChartService()
        previous_context = [Candle(timestamp="2026-05-13T09:15:00", open=100, high=101, low=99, close=100.5, volume=1000)]
        replay_session = [Candle(timestamp="2026-05-14T09:15:00", open=101, high=103, low=100.5, close=102.5, volume=1200)]
        market_now = datetime(2026, 5, 15, 1, 30, tzinfo=service.market_timezone)

        with patch.object(
            service,
            "fetch_session_day_candles",
            side_effect=[(replay_session, "historical"), (previous_context, "historical")],
        ) as session_mock:
            with patch.object(service, "fetch_intraday_candles") as intraday_mock:
                bundle = service.fetch_market_context(
                    client_id="cid",
                    access_token="tok",
                    security_id="13",
                    prefer_last_closed_session_before_open=True,
                    market_now=market_now,
                )

        self.assertEqual(bundle.previous_day_candles, previous_context)
        self.assertEqual(bundle.intraday_candles, replay_session)
        self.assertIsNone(bundle.live_open_candle)
        self.assertEqual(bundle.replay_session_day, date(2026, 5, 14))
        self.assertEqual(bundle.intraday_source, "historical")
        self.assertEqual(session_mock.call_count, 2)
        self.assertFalse(intraday_mock.called)

    def test_dhan_market_context_skips_empty_holiday_session_before_open(self) -> None:
        service = DhanChartService()
        previous_context = [Candle(timestamp="2026-05-26T09:15:00", open=99, high=101, low=98, close=100, volume=1000)]
        replay_session = [Candle(timestamp="2026-05-27T09:15:00", open=101, high=103, low=100, close=102, volume=1200)]
        market_now = datetime(2026, 5, 29, 1, 30, tzinfo=service.market_timezone)

        def fake_fetch_session_day_candles(_client_id, _access_token, _security_id, session_day, *_args):
            if session_day == date(2026, 5, 28):
                raise DhanChartEmptyDataError("holiday")
            if session_day == date(2026, 5, 27):
                return replay_session, "intraday-fallback"
            if session_day == date(2026, 5, 26):
                return previous_context, "historical"
            raise AssertionError(f"Unexpected session day {session_day}")

        with patch.object(service, "fetch_session_day_candles", side_effect=fake_fetch_session_day_candles) as session_mock:
            bundle = service.fetch_market_context(
                client_id="cid",
                access_token="tok",
                security_id="13",
                prefer_last_closed_session_before_open=True,
                market_now=market_now,
            )

        self.assertEqual(bundle.intraday_candles, replay_session)
        self.assertEqual(bundle.previous_day_candles, previous_context)
        self.assertEqual(bundle.replay_session_day, date(2026, 5, 27))
        self.assertEqual(bundle.previous_context_day, date(2026, 5, 26))
        self.assertEqual(session_mock.call_count, 3)

    def test_dhan_market_context_for_days_uses_selected_replay_and_previous_context(self) -> None:
        service = DhanChartService()
        previous_context = [Candle(timestamp="2026-05-13T09:15:00", open=100, high=101, low=99, close=100.5, volume=1000)]
        replay_session = [Candle(timestamp="2026-05-14T09:15:00", open=101, high=103, low=100.5, close=102.5, volume=1200)]

        with patch.object(
            service,
            "fetch_session_day_candles",
            side_effect=[(previous_context, "historical"), (replay_session, "historical")],
        ) as session_mock:
            bundle = service.fetch_market_context_for_days(
                client_id="cid",
                access_token="tok",
                session_day=date(2026, 5, 14),
                previous_context_day=date(2026, 5, 13),
                security_id="13",
            )

        self.assertEqual(bundle.previous_day_candles, previous_context)
        self.assertEqual(bundle.intraday_candles, replay_session)
        self.assertEqual(bundle.previous_context_day, date(2026, 5, 13))
        self.assertEqual(bundle.replay_session_day, date(2026, 5, 14))
        self.assertEqual(session_mock.call_count, 2)

    def test_dhan_chart_error_mentions_endpoint_and_requested_range(self) -> None:
        service = DhanChartService()
        service.min_request_gap_seconds = 0
        response = Mock()
        response.status_code = 200
        response.json.return_value = {"data": []}

        with patch("app.services.dhan_history.httpx.Client") as client_cls:
            client = client_cls.return_value.__enter__.return_value
            client.post.return_value = response
            with self.assertRaises(DhanChartError) as exc:
                service._request_candles(
                    url=service.intraday_url,
                    payload={
                        "securityId": "3045",
                        "fromDate": "2026-05-14 09:15:00",
                        "toDate": "2026-05-14 15:30:00",
                    },
                    client_id="cid",
                    access_token="tok",
                )

        message = str(exc.exception)
        self.assertIn("intraday", message)
        self.assertIn("3045", message)
        self.assertIn("2026-05-14 09:15:00", message)

    def test_dhan_chart_request_retries_after_429_and_succeeds(self) -> None:
        service = DhanChartService()
        service.min_request_gap_seconds = 0
        rate_limited = Mock()
        rate_limited.status_code = 429
        rate_limited.headers = {"Retry-After": "0"}
        rate_limited.json.return_value = {"remarks": "Too many requests"}
        success = Mock()
        success.status_code = 200
        success.headers = {}
        success.json.return_value = {
            "data": [
                {
                    "timestamp": "2026-05-14T09:15:00",
                    "open": 100,
                    "high": 101,
                    "low": 99,
                    "close": 100.5,
                    "volume": 1000,
                }
            ]
        }

        with patch("app.services.dhan_history.httpx.Client") as client_cls:
            client = client_cls.return_value.__enter__.return_value
            client.post.side_effect = [rate_limited, success]

            candles = service._request_candles(
                url=service.intraday_url,
                payload={
                    "securityId": "3045",
                    "fromDate": "2026-05-14 09:15:00",
                    "toDate": "2026-05-14 09:16:00",
                },
                client_id="cid",
                access_token="tok",
            )

        self.assertEqual(client.post.call_count, 2)
        self.assertEqual(len(candles), 1)
        self.assertEqual(candles[0].close, 100.5)

    def test_dhan_rate_limit_does_not_trigger_intraday_fallback_request(self) -> None:
        service = DhanChartService()

        with patch.object(
            service,
            "_request_candles",
            side_effect=DhanChartRateLimitError("Dhan chart API rate limit hit (429)."),
        ) as request_mock:
            with patch.object(service, "_request_intraday_window") as intraday_mock:
                with self.assertRaises(DhanChartRateLimitError):
                    service.fetch_session_day_candles(
                        client_id="cid",
                        access_token="tok",
                        security_id="3045",
                        session_day=date(2026, 5, 14),
                        exchange_segment="NSE_EQ",
                        instrument_type="EQUITY",
                    )

        self.assertEqual(request_mock.call_count, 1)
        self.assertFalse(intraday_mock.called)

    def test_dhan_empty_historical_response_uses_intraday_fallback(self) -> None:
        service = DhanChartService()
        fallback_candles = [
            Candle(timestamp="2026-05-14T09:15:00", open=100, high=101, low=99, close=100.5, volume=1000),
            Candle(timestamp="2026-05-14T09:16:00", open=100.5, high=102, low=100, close=101.5, volume=1100),
        ]

        with patch.object(
            service,
            "_request_candles",
            side_effect=DhanChartEmptyDataError("No candles returned."),
        ) as request_mock:
            with patch.object(service, "_request_intraday_window", return_value=fallback_candles) as intraday_mock:
                candles, source = service.fetch_session_day_candles(
                    client_id="cid",
                    access_token="tok",
                    security_id="3045",
                    session_day=date(2026, 5, 14),
                    exchange_segment="NSE_EQ",
                    instrument_type="EQUITY",
                )

        self.assertEqual(request_mock.call_count, 1)
        self.assertTrue(intraday_mock.called)
        self.assertEqual(source, "intraday-fallback")
        self.assertEqual(candles, fallback_candles)
        self.assertEqual(intraday_mock.call_args.kwargs["window_start"], datetime(2026, 5, 14, 9, 14))

    def test_dhan_historical_400_uses_intraday_fallback_for_replay(self) -> None:
        service = DhanChartService()
        fallback_candles = [
            Candle(timestamp="2026-05-13T09:15:00", open=100, high=101, low=99, close=100.5, volume=1000),
            Candle(timestamp="2026-05-13T09:16:00", open=100.5, high=102, low=100, close=101.5, volume=1100),
        ]

        with patch.object(
            service,
            "_request_candles",
            side_effect=DhanChartError("Dhan chart API error 400."),
        ) as request_mock:
            with patch.object(service, "_request_intraday_window", return_value=fallback_candles) as intraday_mock:
                candles, source = service.fetch_session_day_candles(
                    client_id="cid",
                    access_token="tok",
                    security_id="13",
                    session_day=date(2026, 5, 13),
                    exchange_segment="IDX_I",
                    instrument_type="INDEX",
                )

        self.assertEqual(request_mock.call_count, 1)
        self.assertTrue(intraday_mock.called)
        self.assertEqual(source, "intraday-fallback")
        self.assertEqual(candles, fallback_candles)
        self.assertEqual(intraday_mock.call_args.kwargs["window_start"], datetime(2026, 5, 13, 9, 14))

    def test_fetch_intraday_candles_requests_one_minute_before_session_open(self) -> None:
        service = DhanChartService()
        returned_candles = [
            Candle(timestamp="2026-05-14T09:15:00", open=100, high=101, low=99, close=100.5, volume=1000),
            Candle(timestamp="2026-05-14T09:16:00", open=100.5, high=102, low=100, close=101.5, volume=1100),
        ]
        market_now = datetime(2026, 5, 14, 9, 16, tzinfo=service.market_timezone)

        with patch.object(service, "_request_intraday_window", return_value=returned_candles) as intraday_mock:
            candles, live_open_candle = service.fetch_intraday_candles(
                client_id="cid",
                access_token="tok",
                security_id="3045",
                session_day=date(2026, 5, 14),
                market_now=market_now,
                exchange_segment="NSE_EQ",
                instrument_type="EQUITY",
            )

        self.assertEqual(candles, returned_candles[:-1])
        self.assertEqual(live_open_candle, returned_candles[-1])
        self.assertEqual(intraday_mock.call_args.kwargs["window_start"], datetime(2026, 5, 14, 9, 14))

    def test_dhan_intraday_window_accepts_direct_five_minute_interval(self) -> None:
        service = DhanChartService()
        with patch.object(service, "_request_candles", return_value=[]) as request_mock:
            candles = service._request_intraday_window(
                client_id="cid",
                access_token="tok",
                security_id="1333",
                window_start=datetime(2026, 5, 31, 9, 15),
                window_end=datetime(2026, 5, 31, 15, 30),
                exchange_segment="NSE_EQ",
                instrument_type="EQUITY",
                interval=5,
            )

        self.assertEqual(candles, [])
        payload = request_mock.call_args.kwargs["payload"]
        self.assertEqual(payload["interval"], 5)
        self.assertEqual(payload["fromDate"], "2026-05-31 09:15:00")
        self.assertEqual(payload["toDate"], "2026-05-31 15:30:00")

    def test_liquidity_ledger_tracks_multi_window_sell_side_reclaim(self) -> None:
        candles = [
            self._make_candle(0, 100, 101, 99.0, 100.5),
            self._make_candle(1, 100.5, 101.2, 99.4, 100.8),
            self._make_candle(2, 100.8, 101.0, 99.6, 100.2),
            self._make_candle(3, 100.2, 100.7, 99.2, 99.8),
            self._make_candle(4, 99.8, 100.6, 98.6, 100.4),
        ]

        ledger = self.test_engine.build_liquidity_ledger(candles, PreviousDayLevels())

        sell_reclaims = [
            entry
            for entry in ledger
            if entry.window_label == "last 5m" and entry.side == "sell-side" and entry.status == "reclaimed"
        ]
        self.assertTrue(sell_reclaims)
        self.assertEqual(sell_reclaims[0].trap_side, "sellers")

    def test_heuristic_observation_uses_liquidity_memory_scores(self) -> None:
        candles = [
            self._make_candle(0, 100, 101, 99, 100.5),
            self._make_candle(1, 100.5, 101.2, 99.5, 101.0),
            self._make_candle(2, 101.0, 101.4, 100.2, 100.8),
            self._make_candle(3, 100.8, 101.0, 100.0, 100.3),
            self._make_candle(4, 100.3, 100.7, 98.8, 100.6),
        ]
        context = self._build_context(candles)
        context.liquidity_ledger = [
            LiquidityLedgerEntry(
                window_label="last 5m",
                window_minutes=5,
                side="sell-side",
                level_label="last 5m sell stops",
                level=99.0,
                status="reclaimed",
                trap_side="sellers",
                strength=0.65,
            )
        ]

        observation = self.test_engine.heuristic_engine.observe(context)

        self.assertGreater(observation.layered_bullish_trap_score, 0)
        self.assertIn("last 5m", observation.liquidity_ledger_summary)

    def test_heuristic_v2_enters_stock_long_after_multi_candle_reclaim(self) -> None:
        self.test_engine.set_instrument_mode("stock")
        candles = [
            Candle(timestamp="2026-05-13T09:15:00", open=810, high=812, low=806, close=808, volume=1000),
            Candle(timestamp="2026-05-13T09:16:00", open=808, high=811, low=804, close=805, volume=1100),
            Candle(timestamp="2026-05-14T09:15:00", open=806, high=808.2, low=805.5, close=807.8, volume=1300),
            Candle(timestamp="2026-05-14T09:16:00", open=807.8, high=809.4, low=807.2, close=809.1, volume=1500),
            Candle(timestamp="2026-05-14T09:17:00", open=809.1, high=810.2, low=808.6, close=809.8, volume=1450),
            Candle(timestamp="2026-05-14T09:18:00", open=809.8, high=810.0, low=803.6, close=804.6, volume=1480),
            Candle(timestamp="2026-05-14T09:19:00", open=804.6, high=809.8, low=804.2, close=809.6, volume=1520),
            Candle(timestamp="2026-05-14T09:20:00", open=809.6, high=812.4, low=809.4, close=811.9, volume=1550),
        ]
        self.test_engine.reset_with_candles(candles)
        self.test_engine.current_index = len(candles) - 1

        decision = self.test_engine.heuristic_decision(self.test_engine.build_context())

        self.assertEqual(decision.action, TradeAction.enter_call)
        self.assertEqual(decision.option_type, "CE")
        self.assertEqual(decision.market_state, "trap-day")
        self.assertGreater(decision.setup_score or 0, 70)

    def test_heuristic_v2_invalidates_stale_pending_setup_after_too_many_candles(self) -> None:
        candles = [
            Candle(timestamp=f"2026-05-14T09:{15 + index:02d}:00", open=23700 + (index * 0.2), high=23702 + (index * 0.2), low=23698 + (index * 0.2), close=23700.5 + (index * 0.2), volume=1000 + index)
            for index in range(12)
        ]
        self.test_engine.reset_with_candles(candles)
        arm_decision = TradeDecision(
            action=TradeAction.no_trade,
            confidence=0.72,
            reason="Arm bullish reclaim setup.",
            pending_setup_action="ARM",
            pending_setup_type="bullish_reclaim_watch",
            pending_setup_direction="LONG_CALL",
            pending_setup_option_type="CE",
            pending_setup_trigger_price=23710,
            pending_setup_invalidation_level=23692,
            pending_setup_trigger_basis="close_above",
        )
        self.test_engine.apply_pending_setup_decision(candles[0], arm_decision)
        self.test_engine.current_index = len(candles) - 1

        decision = self.test_engine.heuristic_decision(self.test_engine.build_context())

        self.assertEqual(decision.pending_setup_action, "INVALIDATE")
        self.assertEqual(decision.action, TradeAction.no_trade)

    def test_heuristic_liquidity_map_includes_pivot_levels(self) -> None:
        previous_day = PreviousDayLevels(high=980.0, low=960.0, close=970.0)
        previous_day_candles = [
            Candle(timestamp="2026-05-13T09:15:00", open=968, high=972, low=964, close=970, volume=1000),
            Candle(timestamp="2026-05-13T09:16:00", open=970, high=980, low=968, close=978, volume=1100),
            Candle(timestamp="2026-05-13T09:17:00", open=978, high=979, low=960, close=965, volume=1200),
        ]
        session = [
            Candle(timestamp="2026-05-14T09:15:00", open=971, high=973, low=969, close=972, volume=1000),
            Candle(timestamp="2026-05-14T09:16:00", open=972, high=974, low=970, close=973, volume=1100),
            Candle(timestamp="2026-05-14T09:17:00", open=973, high=975, low=971, close=974, volume=1200),
            Candle(timestamp="2026-05-14T09:18:00", open=974, high=976, low=972, close=975, volume=1300),
            Candle(timestamp="2026-05-14T09:19:00", open=975, high=977, low=973, close=976, volume=1400),
        ]

        buy_levels, sell_levels = self.test_engine.heuristic_engine.build_liquidity_maps(
            session,
            previous_day_candles,
            previous_day,
            session[-1].close,
            4.0,
        )

        labels = {label for label, _, _ in buy_levels + sell_levels}
        self.assertIn("Pivot Point", labels)
        self.assertIn("Pivot R1", labels)
        self.assertIn("Pivot S1", labels)

    def test_find_liquidity_zones_includes_previous_day_structural_shelves(self) -> None:
        previous_day = [
            Candle(timestamp="2026-05-13T09:15:00", open=100, high=104, low=98, close=102, volume=1000),
            Candle(timestamp="2026-05-13T09:16:00", open=102, high=110, low=101, close=109, volume=1100),
            Candle(timestamp="2026-05-13T09:17:00", open=109, high=109.8, low=103, close=104, volume=1200),
            Candle(timestamp="2026-05-13T09:18:00", open=104, high=110.1, low=102, close=103, volume=1200),
            Candle(timestamp="2026-05-13T09:19:00", open=103, high=105, low=97.8, close=99, volume=1300),
            Candle(timestamp="2026-05-13T09:20:00", open=99, high=100, low=97.9, close=98.5, volume=1200),
        ]
        session = [
            Candle(timestamp="2026-05-14T09:15:00", open=101, high=102, low=100, close=101.5, volume=1000),
            Candle(timestamp="2026-05-14T09:16:00", open=101.5, high=103, low=101, close=102.5, volume=1000),
            Candle(timestamp="2026-05-14T09:17:00", open=102.5, high=103.5, low=101.8, close=103.0, volume=1000),
            Candle(timestamp="2026-05-14T09:18:00", open=103.0, high=104.0, low=102.1, close=103.4, volume=1000),
            Candle(timestamp="2026-05-14T09:19:00", open=103.4, high=104.2, low=102.5, close=103.8, volume=1000),
        ]

        zones = self.test_engine.find_liquidity_zones(
            session,
            PreviousDayLevels(high=110.1, low=97.8, close=98.5),
            previous_day,
        )

        labels = {zone.label for zone in zones}
        self.assertTrue(any(label.startswith("Previous-Day Resistance Shelf") for label in labels))
        self.assertTrue(any(label.startswith("Previous-Day Support Shelf") for label in labels))

    def test_heuristic_v2_books_partial_on_first_stock_target(self) -> None:
        self.test_engine.set_instrument_mode("stock")
        candles = [
            Candle(timestamp="2026-05-13T09:15:00", open=805, high=810, low=803, close=808, volume=1000),
            Candle(timestamp="2026-05-14T09:15:00", open=808, high=812, low=807, close=811.5, volume=1200),
            Candle(timestamp="2026-05-14T09:16:00", open=811.5, high=815, low=811, close=814.2, volume=1400),
        ]
        self.test_engine.reset_with_candles(candles)
        self.test_engine.active_trade = SimulatedTrade(
            trade_id="trade1",
            status="OPEN",
            direction="LONG_STOCK",
            instrument_mode="stock",
            instrument_label="SBIN",
            price_mode="cash",
            trade_security_id="3045",
            quote_exchange_segment="NSE_EQ",
            option_type="CE",
            strike=0,
            symbol="SBIN EQ",
            option_security_id=None,
            quantity=65,
            open_quantity=65,
            entry_time=datetime.fromisoformat("2026-05-14T09:15:00"),
            entry_price=808,
            entry_spot_price=808,
            entry_option_price=808,
            entry_quote_source="simulated",
            entry_quote_time=datetime.fromisoformat("2026-05-14T09:15:00"),
            current_price=814.2,
            current_option_price=814.2,
            current_quote_source="simulated",
            current_quote_time=datetime.fromisoformat("2026-05-14T09:16:00"),
            stop_price=804,
            stop_option_price=804,
            target_price=818,
            target_option_price=818,
            invalidation_level=804,
            target_spot_price=818,
            first_target_price=812,
            exit_time=None,
            exit_price=None,
            exit_option_price=None,
            pnl=0.0,
        )
        self.test_engine.current_index = len(candles) - 1

        decision = self.test_engine.heuristic_decision(self.test_engine.build_context())

        self.assertEqual(decision.action, TradeAction.partial_exit)
        self.assertEqual(decision.partial_exit_quantity, 32)
        self.assertIn("book partial profits", decision.reason.lower())

    def test_heuristic_v2_tightens_stock_stop_after_partial_exit(self) -> None:
        self.test_engine.set_instrument_mode("stock")
        candles = [
            Candle(timestamp="2026-05-13T09:15:00", open=805, high=810, low=803, close=808, volume=1000),
            Candle(timestamp="2026-05-14T09:15:00", open=808, high=812, low=807, close=811.5, volume=1200),
            Candle(timestamp="2026-05-14T09:16:00", open=811.5, high=815, low=811, close=814.2, volume=1400),
            Candle(timestamp="2026-05-14T09:17:00", open=814.0, high=816.0, low=813.4, close=815.5, volume=1300),
        ]
        self.test_engine.reset_with_candles(candles)
        self.test_engine.active_trade = SimulatedTrade(
            trade_id="trade1b",
            status="OPEN",
            direction="LONG_STOCK",
            instrument_mode="stock",
            instrument_label="SBIN",
            price_mode="cash",
            trade_security_id="3045",
            quote_exchange_segment="NSE_EQ",
            option_type="CE",
            strike=0,
            symbol="SBIN EQ",
            option_security_id=None,
            quantity=65,
            open_quantity=33,
            closed_quantity=32,
            entry_time=datetime.fromisoformat("2026-05-14T09:15:00"),
            entry_price=808,
            entry_spot_price=808,
            entry_option_price=808,
            entry_quote_source="simulated",
            entry_quote_time=datetime.fromisoformat("2026-05-14T09:15:00"),
            current_price=815.5,
            current_option_price=815.5,
            current_quote_source="simulated",
            current_quote_time=datetime.fromisoformat("2026-05-14T09:17:00"),
            stop_price=804,
            stop_option_price=804,
            target_price=818,
            target_option_price=818,
            invalidation_level=804,
            target_spot_price=818,
            first_target_price=812,
            partial_exit_count=1,
            last_partial_exit_time=datetime.fromisoformat("2026-05-14T09:16:00"),
            exit_time=None,
            exit_price=None,
            exit_option_price=None,
            pnl=0.0,
        )
        self.test_engine.current_index = len(candles) - 1

        decision = self.test_engine.heuristic_decision(self.test_engine.build_context())

        self.assertEqual(decision.action, TradeAction.update_stop)
        self.assertGreaterEqual(decision.invalidation_level or 0, 808)
        self.assertIn("partial profits", decision.reason.lower())

    def test_heuristic_v2_skips_stock_partial_exit_when_setting_disabled(self) -> None:
        self.test_engine.set_instrument_mode("stock")
        self.temp_store.save(stock_partial_profit_enabled=False)
        candles = [
            Candle(timestamp="2026-05-13T09:15:00", open=805, high=810, low=803, close=808, volume=1000),
            Candle(timestamp="2026-05-14T09:15:00", open=808, high=812, low=807, close=811.5, volume=1200),
            Candle(timestamp="2026-05-14T09:16:00", open=811.5, high=815, low=811, close=814.2, volume=1400),
        ]
        self.test_engine.reset_with_candles(candles)
        self.test_engine.active_trade = SimulatedTrade(
            trade_id="trade1c",
            status="OPEN",
            direction="LONG_STOCK",
            instrument_mode="stock",
            instrument_label="SBIN",
            price_mode="cash",
            trade_security_id="3045",
            quote_exchange_segment="NSE_EQ",
            option_type="CE",
            strike=0,
            symbol="SBIN EQ",
            option_security_id=None,
            quantity=65,
            open_quantity=65,
            entry_time=datetime.fromisoformat("2026-05-14T09:15:00"),
            entry_price=808,
            entry_spot_price=808,
            entry_option_price=808,
            entry_quote_source="simulated",
            entry_quote_time=datetime.fromisoformat("2026-05-14T09:15:00"),
            current_price=814.2,
            current_option_price=814.2,
            current_quote_source="simulated",
            current_quote_time=datetime.fromisoformat("2026-05-14T09:16:00"),
            stop_price=804,
            stop_option_price=804,
            target_price=818,
            target_option_price=818,
            invalidation_level=804,
            target_spot_price=818,
            first_target_price=812,
            exit_time=None,
            exit_price=None,
            exit_option_price=None,
            pnl=0.0,
        )
        self.test_engine.current_index = len(candles) - 1

        decision = self.test_engine.heuristic_decision(self.test_engine.build_context())

        self.assertNotEqual(decision.action, TradeAction.partial_exit)

    def test_heuristic_v2_skips_stock_trailing_when_setting_disabled(self) -> None:
        self.test_engine.set_instrument_mode("stock")
        self.temp_store.save(stock_trailing_stop_enabled=False)
        candles = [
            Candle(timestamp="2026-05-13T09:15:00", open=805, high=810, low=803, close=808, volume=1000),
            Candle(timestamp="2026-05-14T09:15:00", open=808, high=812, low=807, close=811.5, volume=1200),
            Candle(timestamp="2026-05-14T09:16:00", open=811.5, high=812.2, low=810.2, close=810.8, volume=1400),
            Candle(timestamp="2026-05-14T09:17:00", open=810.8, high=811.2, low=809.6, close=810.5, volume=1300),
        ]
        self.test_engine.reset_with_candles(candles)
        self.test_engine.active_trade = SimulatedTrade(
            trade_id="trade1d",
            status="OPEN",
            direction="LONG_STOCK",
            instrument_mode="stock",
            instrument_label="SBIN",
            price_mode="cash",
            trade_security_id="3045",
            quote_exchange_segment="NSE_EQ",
            option_type="CE",
            strike=0,
            symbol="SBIN EQ",
            option_security_id=None,
            quantity=65,
            open_quantity=33,
            closed_quantity=32,
            entry_time=datetime.fromisoformat("2026-05-14T09:15:00"),
            entry_price=808,
            entry_spot_price=808,
            entry_option_price=808,
            entry_quote_source="simulated",
            entry_quote_time=datetime.fromisoformat("2026-05-14T09:15:00"),
            current_price=810.5,
            current_option_price=810.5,
            current_quote_source="simulated",
            current_quote_time=datetime.fromisoformat("2026-05-14T09:17:00"),
            stop_price=804,
            stop_option_price=804,
            target_price=818,
            target_option_price=818,
            invalidation_level=804,
            target_spot_price=818,
            first_target_price=812,
            partial_exit_count=1,
            last_partial_exit_time=datetime.fromisoformat("2026-05-14T09:16:00"),
            exit_time=None,
            exit_price=None,
            exit_option_price=None,
            pnl=0.0,
        )
        self.test_engine.current_index = len(candles) - 1

        decision = self.test_engine.heuristic_decision(self.test_engine.build_context())

        self.assertNotEqual(decision.action, TradeAction.update_stop)

    def test_heuristic_v2_skips_stock_one_r_trailing_when_setting_disabled(self) -> None:
        self.test_engine.set_instrument_mode("stock")
        self.temp_store.save(stock_trailing_stop_enabled=False)
        candles = [
            Candle(timestamp="2026-05-13T09:15:00", open=805, high=810, low=803, close=808, volume=1000),
            Candle(timestamp="2026-05-14T09:15:00", open=808, high=814.5, low=807.8, close=813.2, volume=1200),
            Candle(timestamp="2026-05-14T09:16:00", open=813.2, high=814.2, low=812.4, close=813.6, volume=1300),
        ]
        self.test_engine.reset_with_candles(candles)
        self.test_engine.active_trade = SimulatedTrade(
            trade_id="trade1d_1r",
            status="OPEN",
            direction="LONG_STOCK",
            instrument_mode="stock",
            instrument_label="SBIN",
            price_mode="cash",
            trade_security_id="3045",
            quote_exchange_segment="NSE_EQ",
            option_type="CE",
            strike=0,
            symbol="SBIN EQ",
            option_security_id=None,
            quantity=65,
            open_quantity=65,
            entry_time=datetime.fromisoformat("2026-05-14T09:15:00"),
            entry_price=808,
            entry_spot_price=808,
            entry_option_price=808,
            entry_quote_source="simulated",
            entry_quote_time=datetime.fromisoformat("2026-05-14T09:15:00"),
            current_price=813.6,
            current_option_price=813.6,
            current_quote_source="simulated",
            current_quote_time=datetime.fromisoformat("2026-05-14T09:16:00"),
            stop_price=804,
            stop_option_price=804,
            target_price=818,
            target_option_price=818,
            invalidation_level=804,
            target_spot_price=818,
            first_target_price=812,
            exit_time=None,
            exit_price=None,
            exit_option_price=None,
            pnl=0.0,
        )
        self.test_engine.current_index = len(candles) - 1

        decision = self.test_engine.heuristic_decision(self.test_engine.build_context())

        self.assertNotEqual(decision.action, TradeAction.update_stop)

    def test_heuristic_v2_skips_stock_opposite_setup_exit_when_setting_disabled(self) -> None:
        self.test_engine.set_instrument_mode("stock")
        self.temp_store.save(stock_heuristic_early_exit_enabled=False)
        candles = [
            Candle(timestamp="2026-05-13T09:15:00", open=805, high=810, low=803, close=808, volume=1000),
            Candle(timestamp="2026-05-14T09:15:00", open=808, high=811, low=807, close=809.5, volume=1200),
            Candle(timestamp="2026-05-14T09:16:00", open=809.5, high=810.5, low=808.5, close=809.8, volume=1400),
        ]
        self.test_engine.reset_with_candles(candles)
        self.test_engine.active_trade = SimulatedTrade(
            trade_id="trade1e",
            status="OPEN",
            direction="LONG_STOCK",
            instrument_mode="stock",
            instrument_label="SBIN",
            price_mode="cash",
            trade_security_id="3045",
            quote_exchange_segment="NSE_EQ",
            option_type="CE",
            strike=0,
            symbol="SBIN EQ",
            option_security_id=None,
            quantity=65,
            open_quantity=65,
            entry_time=datetime.fromisoformat("2026-05-14T09:15:00"),
            entry_price=808,
            entry_spot_price=808,
            entry_option_price=808,
            entry_quote_source="simulated",
            entry_quote_time=datetime.fromisoformat("2026-05-14T09:15:00"),
            current_price=809.8,
            current_option_price=809.8,
            current_quote_source="simulated",
            current_quote_time=datetime.fromisoformat("2026-05-14T09:16:00"),
            stop_price=804,
            stop_option_price=804,
            target_price=818,
            target_option_price=818,
            invalidation_level=804,
            target_spot_price=818,
            first_target_price=812,
            setup_type="stock_first_pullback_trend_long",
            exit_time=None,
            exit_price=None,
            exit_option_price=None,
            pnl=0.0,
        )
        self.test_engine.current_index = len(candles) - 1
        opposite_candidate = SetupCandidate(
            setup_type="bearish_rejection_watch",
            direction="short",
            option_type="PE",
            trigger_basis="close_below",
            trigger_price=809.0,
            invalidation_level=811.0,
            defended_level=810.5,
            target_spot_price=804.0,
            first_target_price=806.0,
            score=82.0,
            ready_to_enter=True,
            notes=["Strong opposite setup"],
            rule_ids=["R25", "R45"],
            event=SweepEvent(
                side="buy",
                level_label="Round Number 810",
                level_price=810.0,
                sweep_index=1,
                reclaim_index=2,
                trigger_index=2,
                sweep_price=810.5,
                defended_level=810.5,
                trigger_price=809.0,
                invalidation_level=811.0,
                primary=True,
                quality="tradable",
            ),
        )

        with patch.object(self.test_engine.heuristic_engine, "build_candidates", return_value=[opposite_candidate]):
            decision = self.test_engine.heuristic_decision(self.test_engine.build_context())

        self.assertNotEqual(decision.action, TradeAction.exit)
        self.assertNotIn("opposite sl-hunting setup", decision.reason.lower())

    def test_heuristic_v2_trails_stop_for_nifty_mode(self) -> None:
        self.test_engine.set_instrument_mode("nifty")
        candles = [
            Candle(timestamp="2026-05-13T09:15:00", open=23490, high=23510, low=23480, close=23500, volume=1000),
            Candle(timestamp="2026-05-14T09:15:00", open=23500, high=23530, low=23495, close=23518, volume=1200),
            Candle(timestamp="2026-05-14T09:16:00", open=23518, high=23542, low=23510, close=23536, volume=1400),
        ]
        self.test_engine.reset_with_candles(candles)
        self.test_engine.active_trade = SimulatedTrade(
            trade_id="trade2",
            status="OPEN",
            direction="LONG_CALL",
            instrument_mode="nifty",
            instrument_label="Nifty 50",
            price_mode="option",
            trade_security_id="13",
            quote_exchange_segment="IDX_I",
            option_type="CE",
            strike=23500,
            symbol="NIFTY 23500 CE",
            option_security_id="opt-1",
            quantity=65,
            open_quantity=65,
            entry_time=datetime.fromisoformat("2026-05-14T09:15:00"),
            entry_price=120,
            entry_spot_price=23500,
            entry_option_price=120,
            entry_quote_source="simulated",
            entry_quote_time=datetime.fromisoformat("2026-05-14T09:15:00"),
            current_price=144,
            current_option_price=144,
            current_quote_source="simulated",
            current_quote_time=datetime.fromisoformat("2026-05-14T09:16:00"),
            stop_price=108,
            stop_option_price=108,
            target_price=150,
            target_option_price=150,
            invalidation_level=23480,
            target_spot_price=None,
            first_target_price=23520,
            exit_time=None,
            exit_price=None,
            exit_option_price=None,
            pnl=0.0,
        )
        self.test_engine.current_index = len(candles) - 1

        decision = self.test_engine.heuristic_decision(self.test_engine.build_context())

        self.assertEqual(decision.action, TradeAction.update_stop)
        self.assertGreaterEqual(decision.invalidation_level or 0, 23500)

        self.temp_store.save(nifty_trailing_stop_enabled=False)
        disabled_decision = self.test_engine.heuristic_decision(self.test_engine.build_context())

        self.assertNotEqual(disabled_decision.action, TradeAction.update_stop)

    def test_nifty_cost_sl_control_moves_stop_to_entry_without_trailing_toggle(self) -> None:
        candles = [
            Candle(timestamp="2026-05-13T09:15:00", open=23100, high=23120, low=23080, close=23100, volume=1000),
            Candle(timestamp="2026-05-14T09:15:00", open=23192, high=23205, low=23180, close=23196, volume=1200),
            Candle(timestamp="2026-05-14T09:16:00", open=23196, high=23228, low=23194, close=23220, volume=1400),
        ]
        trade = self._build_trade(
            entry_time=candles[1].timestamp,
            entry_spot_price=23192.0,
            invalidation_level=23140.0,
            setup_type="bullish_reclaim_watch",
        )
        context = self._build_context(candles, active_trade=trade).model_copy(
            update={
                "nifty_trailing_stop_enabled": False,
                "nifty_cost_sl_enabled": True,
                "nifty_cost_sl_points": 35.0,
                "nifty_target_enabled": False,
            }
        )
        observation = self._build_observation(atr=25.0)

        decision = self.test_engine.heuristic_engine.manage_active_trade(context, observation, current_trade_price=11.0)

        self.assertEqual(decision.action, TradeAction.update_stop)
        self.assertEqual(decision.invalidation_level, 23192.0)
        self.assertIn("cost-sl", decision.reason.lower())

    def test_nifty_fixed_target_control_exits_on_spot_points(self) -> None:
        candles = [
            Candle(timestamp="2026-05-13T09:15:00", open=23100, high=23120, low=23080, close=23100, volume=1000),
            Candle(timestamp="2026-05-14T09:15:00", open=23192, high=23205, low=23180, close=23196, volume=1200),
            Candle(timestamp="2026-05-14T09:16:00", open=23196, high=23282, low=23194, close=23270, volume=1400),
        ]
        trade = self._build_trade(
            entry_time=candles[1].timestamp,
            entry_spot_price=23192.0,
            invalidation_level=23140.0,
            setup_type="bullish_reclaim_watch",
        )
        context = self._build_context(candles, active_trade=trade).model_copy(
            update={
                "nifty_heuristic_early_exit_enabled": False,
                "nifty_target_enabled": True,
                "nifty_target_points": 90.0,
            }
        )
        observation = self._build_observation(atr=25.0)

        decision = self.test_engine.heuristic_engine.manage_active_trade(context, observation, current_trade_price=12.0)

        self.assertEqual(decision.action, TradeAction.exit)
        self.assertEqual(decision.target_spot_price, 23282.0)
        self.assertIn("fixed target", decision.reason.lower())

    def test_nifty_square_offs_near_next_100_point_round_shelf(self) -> None:
        candles = [
            Candle(timestamp="2026-05-13T09:15:00", open=24120, high=24170, low=24100, close=24150, volume=1000),
            Candle(timestamp="2026-05-14T09:15:00", open=24240, high=24260, low=24230, close=24252, volume=1200),
            Candle(timestamp="2026-05-14T09:16:00", open=24252, high=24282, low=24248, close=24276, volume=1400),
            Candle(timestamp="2026-05-14T09:17:00", open=24276, high=24278, low=24255, close=24258, volume=1450),
            Candle(timestamp="2026-05-14T09:18:00", open=24258, high=24260, low=24240, close=24244, volume=1500),
        ]
        trade = self._build_trade(
            entry_time=candles[1].timestamp,
            entry_spot_price=24252.0,
            invalidation_level=24220.0,
            setup_type="bullish_reclaim_watch",
        )
        context = self._build_context(candles, active_trade=trade).model_copy(
            update={
                "nifty_heuristic_early_exit_enabled": False,
                "nifty_target_enabled": False,
            }
        )
        observation = self._build_observation(atr=25.0)

        decision = self.test_engine.heuristic_engine.manage_active_trade(context, observation, current_trade_price=12.0)

        self.assertEqual(decision.action, TradeAction.exit)
        self.assertEqual(decision.target_spot_price, 24244.0)
        self.assertIn("next 100-point round shelf 24300.00", decision.reason)
        self.assertIn("reversal structure", decision.reason)

    def test_nifty_does_not_square_off_on_blind_next_round_band_tag(self) -> None:
        candles = [
            Candle(timestamp="2026-05-13T09:15:00", open=24120, high=24170, low=24100, close=24150, volume=1000),
            Candle(timestamp="2026-05-14T09:15:00", open=24240, high=24260, low=24230, close=24252, volume=1200),
            Candle(timestamp="2026-05-14T09:16:00", open=24252, high=24282, low=24248, close=24276, volume=1400),
        ]
        trade = self._build_trade(
            entry_time=candles[1].timestamp,
            entry_spot_price=24252.0,
            invalidation_level=24220.0,
            setup_type="bullish_reclaim_watch",
        )
        context = self._build_context(candles, active_trade=trade).model_copy(
            update={
                "nifty_heuristic_early_exit_enabled": False,
                "nifty_target_enabled": False,
            }
        )
        observation = self._build_observation(atr=25.0)

        decision = self.test_engine.heuristic_engine.manage_active_trade(context, observation, current_trade_price=12.0)

        self.assertNotEqual(decision.action, TradeAction.exit)

    def test_heuristic_v2_skips_nifty_opposite_setup_exit_when_setting_disabled(self) -> None:
        candles = [
            self._make_candle(0, 100, 101, 99, 100),
            self._make_candle(1, 100, 102, 99.5, 101),
            self._make_candle(2, 101, 101.5, 100.5, 101.2),
        ]
        trade = self._build_trade(
            entry_time=candles[0].timestamp,
            entry_spot_price=100.0,
            invalidation_level=95.0,
            setup_type="bullish_reclaim_watch",
        )
        context = self._build_context(candles, active_trade=trade)
        observation = self._build_observation(atr=2.0)
        opposite_candidate = SetupCandidate(
            setup_type="bearish_rejection_watch",
            direction="LONG_PUT",
            option_type="PE",
            trigger_basis="close_below",
            trigger_price=100.8,
            invalidation_level=102.0,
            defended_level=101.5,
            target_spot_price=96.0,
            first_target_price=99.0,
            score=82.0,
            ready_to_enter=True,
            notes=["Strong opposite Nifty setup"],
            rule_ids=["R26", "R45"],
            event=SweepEvent(
                side="buy",
                level_label="Round Number 100",
                level_price=100.0,
                sweep_index=1,
                reclaim_index=2,
                trigger_index=2,
                sweep_price=101.5,
                defended_level=101.5,
                trigger_price=100.8,
                invalidation_level=102.0,
                primary=True,
                quality="tradable",
            ),
        )

        with patch.object(self.test_engine.heuristic_engine, "build_candidates", return_value=[opposite_candidate]):
            enabled = self.test_engine.heuristic_engine.manage_active_trade(context, observation, current_trade_price=12.0)
            disabled_context = context.model_copy(update={"nifty_heuristic_early_exit_enabled": False})
            disabled = self.test_engine.heuristic_engine.manage_active_trade(disabled_context, observation, current_trade_price=12.0)
            replay_cash_context = context.model_copy(
                update={
                    "active_trade": trade.model_copy(update={"price_mode": "cash"}),
                    "nifty_heuristic_early_exit_enabled": False,
                }
            )
            replay_cash_disabled = self.test_engine.heuristic_engine.manage_active_trade(
                replay_cash_context,
                observation,
                current_trade_price=101.2,
            )

        self.assertEqual(enabled.action, TradeAction.exit)
        self.assertNotEqual(disabled.action, TradeAction.exit)
        self.assertNotEqual(replay_cash_disabled.action, TradeAction.exit)

    def test_nifty_exits_and_arms_opposite_trade_on_confirmed_liquidity_reversal(self) -> None:
        candles = [
            self._make_candle(0, 24120, 24130, 24100, 24120),
            self._make_candle(1, 24120, 24155, 24115, 24145),
            self._make_candle(2, 24145, 24162, 24092, 24096),
        ]
        trade = self._build_trade(
            entry_time=candles[0].timestamp,
            entry_spot_price=24120.0,
            invalidation_level=24080.0,
            setup_type="bullish_reclaim_watch",
        )
        context = self._build_context(candles, active_trade=trade)
        observation = self._build_observation(atr=25.0)
        opposite_candidate = SetupCandidate(
            setup_type="bearish_rejection_watch",
            direction="LONG_PUT",
            option_type="PE",
            trigger_basis="close_below",
            trigger_price=24100.0,
            invalidation_level=24140.0,
            defended_level=24100.0,
            target_spot_price=24020.0,
            first_target_price=24070.0,
            score=70.0,
            ready_to_enter=True,
            notes=["Previous day high swept and rejected cleanly."],
            rule_ids=["R26", "R28", "R45"],
            event=SweepEvent(
                side="buy",
                level_label="Previous Day High",
                level_price=24100.0,
                sweep_index=1,
                reclaim_index=2,
                trigger_index=2,
                sweep_price=24162.0,
                defended_level=24100.0,
                trigger_price=24100.0,
                invalidation_level=24140.0,
                primary=True,
                quality="tradable",
            ),
        )

        with patch.object(self.test_engine.heuristic_engine, "build_candidates", return_value=[opposite_candidate]):
            decision = self.test_engine.heuristic_engine.manage_active_trade(context, observation, current_trade_price=12.0)

        self.assertEqual(decision.action, TradeAction.exit)
        self.assertEqual(decision.pending_setup_action, "ARM")
        self.assertEqual(decision.pending_setup_option_type, "PE")
        self.assertIn("opposite liquidity sweep", decision.reason.lower())

    def test_nifty_short_exits_and_arms_long_on_opening_low_reclaim(self) -> None:
        candles = [
            Candle(timestamp="2026-04-15T09:15:00", open=24163.8, high=24280.9, low=24162.7, close=24232.9, volume=1000),
            Candle(timestamp="2026-04-15T09:30:00", open=24193.5, high=24194.7, low=24176.35, close=24183.25, volume=1300),
            Candle(timestamp="2026-04-15T09:38:00", open=24171.8, high=24175.75, low=24147.5, close=24147.95, volume=1500),
            Candle(timestamp="2026-04-15T09:39:00", open=24147.7, high=24158.2, low=24145.8, close=24151.35, volume=1550),
            Candle(timestamp="2026-04-15T09:40:00", open=24151.05, high=24175.4, low=24149.7, close=24174.85, volume=1800),
        ]
        trade = self._build_trade(
            entry_time=candles[1].timestamp,
            entry_spot_price=24183.25,
            invalidation_level=24223.25,
            setup_type="bearish_rejection_watch",
        ).model_copy(update={"direction": "LONG_PUT", "option_type": "PE", "target_spot_price": 24121.92})
        context = self._build_context(candles, active_trade=trade)
        observation = self._build_observation(atr=30.0, nifty_mid_noise=False)
        opposite_candidate = SetupCandidate(
            setup_type="bullish_reclaim_watch",
            direction="LONG_CALL",
            option_type="CE",
            trigger_basis="close_above",
            trigger_price=24162.7,
            invalidation_level=24142.7,
            defended_level=24162.7,
            target_spot_price=24240.0,
            first_target_price=24205.0,
            score=74.0,
            ready_to_enter=True,
            notes=["Opening range low was swept and reclaimed with a strong recovery close."],
            rule_ids=["R25", "R27", "R45", "R86"],
            event=SweepEvent(
                side="sell",
                level_label="Opening Range Low",
                level_price=24162.7,
                sweep_index=2,
                reclaim_index=4,
                trigger_index=4,
                sweep_price=24147.5,
                defended_level=24162.7,
                trigger_price=24162.7,
                invalidation_level=24142.7,
                primary=True,
                quality="tradable",
                notes=["Day/opening low liquidity was swept before reclaim."],
            ),
        )

        with patch.object(self.test_engine.heuristic_engine, "build_candidates", return_value=[opposite_candidate]):
            decision = self.test_engine.heuristic_engine.manage_active_trade(context, observation, current_trade_price=12.0)

        self.assertEqual(decision.action, TradeAction.exit)
        self.assertEqual(decision.pending_setup_action, "ARM")
        self.assertEqual(decision.pending_setup_option_type, "CE")
        self.assertEqual(decision.pending_setup_trigger_price, 24162.7)
        self.assertIn("opposite liquidity sweep", decision.reason.lower())

    def test_nifty_enters_immediately_on_strong_opening_low_reclaim_despite_noise(self) -> None:
        candles = [
            Candle(timestamp="2026-04-15T09:15:00", open=24163.8, high=24280.9, low=24162.7, close=24232.9, volume=12307017),
            Candle(timestamp="2026-04-15T09:16:00", open=24225.5, high=24238.8, low=24205.2, close=24232.0, volume=6597189),
            Candle(timestamp="2026-04-15T09:17:00", open=24231.05, high=24237.7, low=24214.4, close=24228.8, volume=4617894),
            Candle(timestamp="2026-04-15T09:18:00", open=24227.6, high=24241.9, low=24222.6, close=24231.45, volume=3684318),
            Candle(timestamp="2026-04-15T09:19:00", open=24230.3, high=24235.3, low=24224.6, close=24230.85, volume=3014809),
            Candle(timestamp="2026-04-15T09:20:00", open=24231.6, high=24234.7, low=24226.55, close=24231.25, volume=3128109),
            Candle(timestamp="2026-04-15T09:21:00", open=24231.85, high=24233.3, low=24214.9, close=24219.65, volume=3053227),
            Candle(timestamp="2026-04-15T09:22:00", open=24217.7, high=24228.7, low=24211.5, close=24218.45, volume=3387949),
            Candle(timestamp="2026-04-15T09:23:00", open=24217.0, high=24225.75, low=24216.25, close=24222.95, volume=2825884),
            Candle(timestamp="2026-04-15T09:24:00", open=24222.8, high=24223.75, low=24206.75, close=24208.85, volume=2696511),
            Candle(timestamp="2026-04-15T09:25:00", open=24209.2, high=24212.9, low=24203.15, close=24212.65, volume=2661550),
            Candle(timestamp="2026-04-15T09:26:00", open=24212.75, high=24212.75, low=24204.85, close=24207.05, volume=2438865),
            Candle(timestamp="2026-04-15T09:27:00", open=24207.85, high=24210.85, low=24190.05, close=24190.05, volume=2449469),
            Candle(timestamp="2026-04-15T09:28:00", open=24190.05, high=24194.95, low=24184.1, close=24191.85, volume=2432314),
            Candle(timestamp="2026-04-15T09:29:00", open=24191.3, high=24195.45, low=24186.05, close=24192.45, volume=1776017),
            Candle(timestamp="2026-04-15T09:30:00", open=24193.5, high=24194.7, low=24176.35, close=24183.25, volume=2131653),
            Candle(timestamp="2026-04-15T09:31:00", open=24182.55, high=24183.2, low=24174.65, close=24179.05, volume=1563013),
            Candle(timestamp="2026-04-15T09:32:00", open=24178.9, high=24183.35, low=24172.8, close=24182.85, volume=1730147),
            Candle(timestamp="2026-04-15T09:33:00", open=24182.75, high=24187.15, low=24171.55, close=24175.85, volume=1535037),
            Candle(timestamp="2026-04-15T09:34:00", open=24177.1, high=24177.4, low=24164.15, close=24168.9, volume=1843517),
            Candle(timestamp="2026-04-15T09:35:00", open=24168.05, high=24174.05, low=24167.35, close=24171.5, volume=1557958),
            Candle(timestamp="2026-04-15T09:36:00", open=24170.0, high=24179.85, low=24170.0, close=24173.25, volume=1608519),
            Candle(timestamp="2026-04-15T09:37:00", open=24172.5, high=24176.7, low=24170.7, close=24173.15, volume=1485846),
            Candle(timestamp="2026-04-15T09:38:00", open=24171.8, high=24175.75, low=24147.5, close=24147.95, volume=1706733),
            Candle(timestamp="2026-04-15T09:39:00", open=24147.7, high=24158.2, low=24145.8, close=24151.35, volume=1651319),
            Candle(timestamp="2026-04-15T09:40:00", open=24151.05, high=24175.4, low=24149.7, close=24174.85, volume=1376109),
        ]
        context = StrategyContext(
            instrument=InstrumentState(),
            current_candle=candles[-1],
            recent_candles=candles[-10:],
            session_candles=candles,
            previous_day_candles=[
                Candle(timestamp="2026-04-14T15:28:00", open=24100, high=24200, low=24000, close=24120, volume=1000),
            ],
            previous_day=PreviousDayLevels(high=24200, low=24000, close=24120),
            liquidity_zones=[],
            operator_zones=[],
            signal_events=[],
            market_structure="",
            pending_setup=None,
            active_trade=None,
            recent_closed_trades=[],
            rulebook_markdown="",
        )

        observation = self.test_engine.heuristic_engine.observe(context)
        candidates = self.test_engine.heuristic_engine.build_candidates(context, observation)
        decision = self.test_engine.heuristic_engine.decide_entry(context, observation, candidates)

        self.assertTrue(observation.nifty_mid_noise)
        self.assertEqual(decision.action, TradeAction.enter_call)
        self.assertEqual(decision.option_type, "CE")
        self.assertEqual(decision.setup_type, "bullish_reclaim_watch")
        self.assertGreaterEqual(decision.setup_score or 0, 80)
        self.assertIn("Opening Range Low", decision.reason)

    def test_nifty_market_mechanics_profiles_previous_day_last_2h_context(self) -> None:
        previous_day_candles = []
        start = datetime(2026, 2, 16, 13, 0)
        for index in range(150):
            price = 24280.0 - index * 1.7
            previous_day_candles.append(
                Candle(
                    timestamp=start + timedelta(minutes=index),
                    open=price + 0.8,
                    high=price + 3.0,
                    low=price - 3.0,
                    close=price - 0.8,
                    volume=1000 + index,
                )
            )
        session = [
            Candle(timestamp=datetime(2026, 2, 17, 9, 15), open=24027.0, high=24042.0, low=24018.0, close=24031.0, volume=2000),
            Candle(timestamp=datetime(2026, 2, 17, 9, 16), open=24031.0, high=24036.0, low=24020.0, close=24024.0, volume=1800),
        ]
        context = StrategyContext(
            instrument=InstrumentState(),
            current_candle=session[-1],
            recent_candles=session,
            session_candles=session,
            previous_day_candles=previous_day_candles,
            previous_day=PreviousDayLevels(
                high=max(candle.high for candle in previous_day_candles),
                low=min(candle.low for candle in previous_day_candles),
                close=previous_day_candles[-1].close,
            ),
            liquidity_zones=[],
            operator_zones=[],
            signal_events=[],
            market_structure="",
            pending_setup=None,
            active_trade=None,
            recent_closed_trades=[],
            rulebook_markdown="",
        )

        profile = self.test_engine.heuristic_engine.nifty_market_mechanics_profile(context, atr=20.0)

        self.assertEqual(profile.previous_day_profile, "strong_bearish")
        self.assertEqual(profile.last_2h_flow, "selling_rally")
        self.assertEqual(profile.open_type, "flat")
        self.assertEqual(profile.trade_bias, "prefer_short")
        self.assertEqual(profile.risk_mode, "trap_first")
        self.assertIn("last 2h selling_rally", profile.summary)

    def test_nifty_liquidity_map_adds_previous_day_last_2h_swing_levels(self) -> None:
        previous_day_candles = []
        start = datetime(2026, 2, 16, 13, 0)
        for index in range(120):
            base = 24220.0 - index * 0.4
            high_spike = 18.0 if index in {20, 62, 98} else 2.0
            low_spike = 18.0 if index in {35, 78, 108} else 2.0
            previous_day_candles.append(
                Candle(
                    timestamp=start + timedelta(minutes=index),
                    open=base + 0.5,
                    high=base + high_spike,
                    low=base - low_spike,
                    close=base - 0.5,
                    volume=1000,
                )
            )
        session = [
            Candle(timestamp=datetime(2026, 2, 17, 9, 15), open=24180, high=24188, low=24170, close=24182, volume=2000),
            Candle(timestamp=datetime(2026, 2, 17, 9, 16), open=24182, high=24186, low=24174, close=24179, volume=1800),
            Candle(timestamp=datetime(2026, 2, 17, 9, 17), open=24179, high=24184, low=24176, close=24181, volume=1700),
        ]

        buy_levels, sell_levels = self.test_engine.heuristic_engine.build_liquidity_maps(
            session,
            previous_day_candles,
            PreviousDayLevels(
                high=max(candle.high for candle in previous_day_candles),
                low=min(candle.low for candle in previous_day_candles),
                close=previous_day_candles[-1].close,
            ),
            current_close=session[-1].close,
            atr=18.0,
            index_round_numbers=True,
        )

        self.assertTrue(any(label.startswith("Previous-Day Last-2h Swing High") for label, _, _ in buy_levels))
        self.assertTrue(any(label.startswith("Previous-Day Last-2h Swing Low") for label, _, _ in sell_levels))

    def test_nifty_market_mechanics_adjustment_boosts_matching_last_2h_liquidity(self) -> None:
        context = self._build_context(
            [
                Candle(timestamp=datetime(2026, 2, 17, 9, 15), open=24180, high=24195, low=24170, close=24188, volume=2000),
                Candle(timestamp=datetime(2026, 2, 17, 9, 16), open=24188, high=24202, low=24184, close=24186, volume=1800),
            ]
        )
        observation = self._build_observation(
            nifty_trade_bias="prefer_short",
            nifty_expected_behavior="trap_sellers_first_then_short",
            nifty_risk_mode="trap_first",
        )
        event = SweepEvent(
            side="buy",
            level_label="Previous-Day Last-2h Swing High 14:42",
            level_price=24200.0,
            sweep_index=1,
            reclaim_index=1,
            trigger_index=1,
            sweep_price=24204.0,
            defended_level=24200.0,
            trigger_price=24195.0,
            invalidation_level=24220.0,
            primary=True,
            quality="tradable",
        )

        adjustment, note, rules = self.test_engine.heuristic_engine._nifty_market_mechanics_score_adjustment(
            context,
            observation,
            event,
            option_type="PE",
        )

        self.assertGreaterEqual(adjustment, 15.0)
        self.assertIn("last-2h seller-stop liquidity", note or "")
        self.assertIn("R81", rules)

    def test_nifty_market_mechanics_fast_profit_exits_at_first_target_after_stop_protection(self) -> None:
        candles = [
            Candle(timestamp=datetime(2026, 5, 14, 9, 15), open=24000, high=24015, low=23990, close=24005, volume=1200),
            Candle(timestamp=datetime(2026, 5, 14, 9, 16), open=24008, high=24034, low=24006, close=24028, volume=1400),
        ]
        trade = self._build_trade(
            entry_time=candles[0].timestamp,
            entry_spot_price=24005.0,
            invalidation_level=24005.0,
            setup_type="bullish_reclaim_watch",
        )
        trade.first_target_price = 24030.0
        context = self._build_context(candles, active_trade=trade).model_copy(
            update={
                "nifty_heuristic_early_exit_enabled": True,
                "nifty_trailing_stop_enabled": True,
                "nifty_target_enabled": False,
                "nifty_cost_sl_enabled": False,
            }
        )
        observation = self._build_observation(atr=20.0, nifty_risk_mode="fast_profit")

        decision = self.test_engine.heuristic_engine.manage_active_trade(context, observation, current_trade_price=12.0)

        self.assertEqual(decision.action, TradeAction.exit)
        self.assertEqual(decision.target_spot_price, 24030.0)
        self.assertIn("fast-profit", decision.reason)

    def test_reversal_exit_can_enter_opposite_pending_setup_after_square_off(self) -> None:
        exit_candle = Candle(timestamp="2026-05-14T09:42:00", open=24105, high=24108, low=24062, close=24070, volume=1600)
        old_trade = self._build_trade(
            entry_time=datetime(2026, 5, 14, 9, 30),
            entry_spot_price=24120.0,
            invalidation_level=24080.0,
            setup_type="bullish_reclaim_watch",
        )
        old_trade.price_mode = "cash"
        self.test_engine.active_trade = old_trade
        self.test_engine.trade_history = [old_trade]
        decision = TradeDecision(
            action=TradeAction.exit,
            confidence=0.82,
            reason="Opposite liquidity reversal confirmed.",
            option_type="CE",
            target_spot_price=24020.0,
            pending_setup_action="ARM",
            pending_setup_type="bearish_rejection_watch",
            pending_setup_direction="LONG_PUT",
            pending_setup_option_type="PE",
            pending_setup_trigger_price=24100.0,
            pending_setup_invalidation_level=24140.0,
            pending_setup_trigger_basis="close_below",
            pending_setup_notes="Bearish setup is ready after liquidity sweep.",
        )

        self.test_engine.apply_pending_setup_decision(exit_candle, decision)
        self.test_engine.apply_trade_logic(exit_candle, decision, source="replay")

        self.assertEqual(old_trade.status, "CLOSED")
        self.assertIsNotNone(self.test_engine.active_trade)
        assert self.test_engine.active_trade is not None
        self.assertNotEqual(self.test_engine.active_trade.trade_id, old_trade.trade_id)
        self.assertEqual(self.test_engine.active_trade.option_type, "PE")
        self.assertEqual(self.test_engine.active_trade.setup_type, "bearish_rejection_watch")
        self.assertEqual(old_trade.exit_price, 24070.0)

    def test_heuristic_replaces_stale_long_setup_with_short_when_market_keeps_falling(self) -> None:
        candles = [
            Candle(timestamp="2026-05-13T09:15:00", open=100, high=101, low=99, close=100, volume=1000),
            Candle(timestamp="2026-05-14T09:15:00", open=103, high=103.4, low=102.7, close=103.1, volume=1200),
            Candle(timestamp="2026-05-14T09:16:00", open=103.1, high=103.2, low=101.4, close=101.6, volume=1300),
            Candle(timestamp="2026-05-14T09:17:00", open=101.6, high=101.8, low=99.7, close=99.8, volume=1400),
            Candle(timestamp="2026-05-14T09:18:00", open=99.8, high=100.0, low=98.6, close=98.7, volume=1600),
        ]
        self.test_engine.reset_with_candles(candles)
        arm_decision = TradeDecision(
            action=TradeAction.no_trade,
            confidence=0.71,
            reason="Arm bullish reclaim above 104.",
            pending_setup_action="ARM",
            pending_setup_type="bullish_reclaim_watch",
            pending_setup_direction="LONG_CALL",
            pending_setup_option_type="CE",
            pending_setup_trigger_price=104.0,
            pending_setup_invalidation_level=97.0,
            pending_setup_trigger_basis="close_above",
        )
        self.test_engine.apply_pending_setup_decision(candles[0], arm_decision)
        self.test_engine.current_index = len(candles) - 1

        decision = self.test_engine.heuristic_decision(self.test_engine.build_context())

        self.assertEqual(decision.pending_setup_action, "REPLACE")
        self.assertEqual(decision.pending_setup_option_type, "PE")
        self.assertGreaterEqual(decision.setup_score or 0, 58)

    def test_heuristic_detects_previous_close_reclaim_long_setup(self) -> None:
        self.test_engine.set_instrument_mode("stock")
        candles = [
            Candle(timestamp="2026-05-13T09:15:00", open=100, high=101, low=99.2, close=100, volume=1000),
            Candle(timestamp="2026-05-14T09:15:00", open=103, high=103.3, low=102.5, close=102.7, volume=1100),
            Candle(timestamp="2026-05-14T09:16:00", open=102.7, high=102.9, low=99.7, close=100.2, volume=1300),
            Candle(timestamp="2026-05-14T09:17:00", open=100.2, high=100.3, low=99.9, close=100.1, volume=1350),
            Candle(timestamp="2026-05-14T09:18:00", open=100.1, high=100.6, low=100.0, close=100.4, volume=1380),
            Candle(timestamp="2026-05-14T09:19:00", open=100.4, high=100.9, low=100.3, close=100.7, volume=1500),
        ]
        self.test_engine.reset_with_candles(candles)
        self.test_engine.current_index = len(candles) - 1

        decision = self.test_engine.heuristic_decision(self.test_engine.build_context())

        self.assertEqual(decision.action, TradeAction.enter_call)
        self.assertEqual(decision.setup_type, "previous_close_reclaim_long")

    def test_heuristic_observe_classifies_expanding_range_state(self) -> None:
        candles = [Candle(timestamp="2026-05-13T09:15:00", open=100, high=101.2, low=99.2, close=100.4, volume=1000)]
        recent_day = [
            (100.4, 101.0, 99.9, 100.8),
            (100.8, 101.1, 100.2, 100.5),
            (100.5, 101.2, 100.1, 100.9),
            (100.9, 101.4, 100.5, 101.2),
            (101.2, 101.5, 100.8, 101.0),
            (101.0, 101.6, 100.7, 101.4),
            (101.4, 101.8, 101.0, 101.6),
            (101.6, 101.9, 101.1, 101.3),
            (101.3, 101.9, 101.2, 101.8),
            (101.9, 102.6, 101.8, 102.5),
            (102.5, 103.2, 102.4, 103.1),
            (102.1, 102.2, 101.4, 101.6),
            (102.0, 102.8, 101.9, 102.7),
            (102.7, 103.5, 102.6, 103.4),
            (102.0, 102.1, 101.2, 101.4),
            (101.8, 104.0, 101.7, 103.8),
        ]
        for minute, (open_price, high, low, close) in enumerate(recent_day, start=15):
            candles.append(
                Candle(
                    timestamp=f"2026-05-14T09:{minute:02d}:00",
                    open=open_price,
                    high=high,
                    low=low,
                    close=close,
                    volume=1100 + minute,
                )
            )

        self.test_engine.reset_with_candles(candles)
        self.test_engine.current_index = len(candles) - 1

        observation = self.test_engine.heuristic_engine.observe(self.test_engine.build_context())

        self.assertEqual(observation.range_state, "expanding")
        self.assertEqual(observation.participation_state, "two_sided_active")
        self.assertGreater(observation.regime_quality, 0.0)

    def test_heuristic_observe_classifies_fair_value_churn_participation(self) -> None:
        candles = [Candle(timestamp="2026-05-13T09:15:00", open=100, high=101.0, low=99.0, close=100.0, volume=1000)]
        recent_day = [
            (100.0, 100.6, 99.5, 100.2),
            (100.2, 100.7, 99.8, 100.0),
            (100.0, 100.5, 99.6, 100.1),
            (100.1, 100.6, 99.7, 99.9),
            (99.9, 100.4, 99.5, 100.0),
            (100.0, 100.5, 99.6, 100.2),
            (100.2, 100.7, 99.8, 100.0),
            (100.0, 100.4, 99.6, 99.9),
            (99.9, 100.5, 99.6, 100.1),
            (100.1, 100.6, 99.7, 99.95),
            (99.95, 100.5, 99.6, 100.05),
            (100.05, 100.55, 99.7, 99.9),
            (99.9, 100.45, 99.55, 100.1),
            (100.1, 100.6, 99.8, 100.0),
            (100.0, 100.4, 99.65, 99.95),
            (99.95, 100.5, 99.7, 100.0),
        ]
        for minute, (open_price, high, low, close) in enumerate(recent_day, start=15):
            candles.append(
                Candle(
                    timestamp=f"2026-05-14T09:{minute:02d}:00",
                    open=open_price,
                    high=high,
                    low=low,
                    close=close,
                    volume=1050 + minute,
                )
            )

        self.test_engine.reset_with_candles(candles)
        self.test_engine.current_index = len(candles) - 1

        observation = self.test_engine.heuristic_engine.observe(self.test_engine.build_context())

        self.assertEqual(observation.participation_state, "fair_value_churn")
        self.assertLess(observation.regime_quality, 0.0)

    def test_heuristic_maps_round_number_equal_clusters_and_swings(self) -> None:
        self.test_engine.set_instrument_mode("stock")
        candles = [
            Candle(timestamp="2026-05-13T09:15:00", open=23710, high=23742, low=23688, close=23720, volume=1000),
            Candle(timestamp="2026-05-14T09:15:00", open=23720, high=23732, low=23718, close=23728, volume=1100),
            Candle(timestamp="2026-05-14T09:16:00", open=23728, high=23749.8, low=23726, close=23746, volume=1200),
            Candle(timestamp="2026-05-14T09:17:00", open=23746, high=23750.2, low=23740, close=23744, volume=1300),
            Candle(timestamp="2026-05-14T09:18:00", open=23744, high=23749.9, low=23734, close=23738, volume=1250),
            Candle(timestamp="2026-05-14T09:19:00", open=23738, high=23742, low=23711.1, close=23715, volume=1400),
            Candle(timestamp="2026-05-14T09:20:00", open=23715, high=23721, low=23700.1, close=23708, volume=1450),
            Candle(timestamp="2026-05-14T09:21:00", open=23708, high=23719, low=23700.4, close=23716, volume=1420),
            Candle(timestamp="2026-05-14T09:22:00", open=23716, high=23760, low=23714, close=23758, volume=1500),
        ]
        self.test_engine.reset_with_candles(candles)
        self.test_engine.current_index = len(candles) - 1

        context = self.test_engine.build_context()
        observation = self.test_engine.heuristic_engine.observe(context)
        zone_labels = [zone.label for zone in self.test_engine.find_liquidity_zones(context.session_candles, context.previous_day)]

        self.assertTrue(any("Round Number" in label for label, _, _ in observation.mapped_buy_liquidity + observation.mapped_sell_liquidity))
        self.assertTrue(any("Equal High Cluster" in label for label, _, _ in observation.mapped_buy_liquidity))
        self.assertTrue(any("Equal Low Cluster" in label for label, _, _ in observation.mapped_sell_liquidity))
        self.assertTrue(any("Same-Day Swing High" in label for label, _, _ in observation.mapped_buy_liquidity))
        self.assertTrue(any("Same-Day Swing Low" in label for label, _, _ in observation.mapped_sell_liquidity))
        self.assertTrue(any("Round Number" in label for label in zone_labels))
        self.assertTrue(any("Equal High Cluster" in label or "Equal Low Cluster" in label for label in zone_labels))

    def test_nifty_round_number_liquidity_uses_100_point_strikes_and_front_run_zones(self) -> None:
        candles = [
            Candle(timestamp="2026-05-13T09:15:00", open=23960, high=24040, low=23910, close=23980, volume=1000),
            Candle(timestamp="2026-05-14T09:15:00", open=23940, high=23970, low=23920, close=23955, volume=1100),
            Candle(timestamp="2026-05-14T09:16:00", open=23955, high=23982, low=23940, close=23946, volume=1200),
            Candle(timestamp="2026-05-14T09:17:00", open=23946, high=23960, low=23918, close=23935, volume=1300),
            Candle(timestamp="2026-05-14T09:18:00", open=23935, high=23958, low=23924, close=23950, volume=1250),
        ]
        self.test_engine.reset_with_candles(candles)
        self.test_engine.current_index = len(candles) - 1

        observation = self.test_engine.heuristic_engine.observe(self.test_engine.build_context())
        buy_labels = [label for label, _, _ in observation.mapped_buy_liquidity]
        sell_labels = [label for label, _, _ in observation.mapped_sell_liquidity]
        all_round_labels = [label for label in buy_labels + sell_labels if "Round Number" in label]

        self.assertTrue(any("Round Number 24000.00 Premature Reversal Zone" in label for label in buy_labels))
        self.assertTrue(any("Round Number 23900.00 Premature Reversal Zone" in label for label in sell_labels))
        self.assertFalse(any("23950" in label or "24050" in label for label in all_round_labels))

    def test_nifty_ignores_round_number_inside_first_session_candle(self) -> None:
        candles = [
            Candle(timestamp="2026-05-13T09:15:00", open=23120, high=23170, low=23100, close=23150, volume=1000),
            Candle(timestamp="2026-05-14T09:15:00", open=23192, high=23250, low=23192, close=23230, volume=1100),
            Candle(timestamp="2026-05-14T09:16:00", open=23230, high=23245, low=23205, close=23210, volume=1200),
            Candle(timestamp="2026-05-14T09:17:00", open=23210, high=23235, low=23202, close=23228, volume=1300),
        ]
        self.test_engine.reset_with_candles(candles)
        self.test_engine.current_index = len(candles) - 1

        observation = self.test_engine.heuristic_engine.observe(self.test_engine.build_context())
        all_round_labels = [
            label
            for label, _, _ in observation.mapped_buy_liquidity + observation.mapped_sell_liquidity
            if "Round Number" in label
        ]

        self.assertFalse(any("23200" in label for label in all_round_labels))

    def test_nifty_liquidity_filter_keeps_intraday_and_pivot_levels(self) -> None:
        context = self._build_context(
            [
                Candle(timestamp="2026-05-14T09:15:00", open=24000, high=24030, low=23970, close=24010, volume=1000),
                Candle(timestamp="2026-05-14T09:16:00", open=24010, high=24040, low=23990, close=24020, volume=1200),
            ],
            previous_close=24000.0,
        )
        allowed = self.test_engine.heuristic_engine._allowed_liquidity_families_for_context(context)
        levels = [
            ("Opening Range High", 24030.0, True),
            ("First 15m Low", 23970.0, True),
            ("Prior Hour High", 24040.0, False),
            ("Same-Day Swing High 09:16", 24040.0, False),
            ("Pivot R1", 24100.0, True),
            ("Previous-Day Swing Low 15:12", 23900.0, False),
        ]

        filtered = self.test_engine.heuristic_engine._filter_liquidity_levels(levels, allowed)

        self.assertEqual([label for label, _, _ in filtered], [label for label, _, _ in levels])

    def test_heuristic_scores_equal_high_round_number_sweep_more_aggressively(self) -> None:
        self.test_engine.set_instrument_mode("stock")
        candles = [
            Candle(timestamp="2026-05-13T09:15:00", open=23710, high=23820, low=23688, close=23720, volume=1000),
            Candle(timestamp="2026-05-14T09:15:00", open=23720, high=23734, low=23718, close=23730, volume=1100),
            Candle(timestamp="2026-05-14T09:16:00", open=23730, high=23749.7, low=23726, close=23746, volume=1200),
            Candle(timestamp="2026-05-14T09:17:00", open=23746, high=23750.1, low=23740, close=23747, volume=1220),
            Candle(timestamp="2026-05-14T09:18:00", open=23747, high=23749.8, low=23742, close=23745, volume=1180),
            Candle(timestamp="2026-05-14T09:19:00", open=23745, high=23758.2, low=23740, close=23743, volume=1500),
            Candle(timestamp="2026-05-14T09:20:00", open=23743, high=23744, low=23724, close=23730, volume=1650),
        ]
        self.test_engine.reset_with_candles(candles)
        self.test_engine.current_index = len(candles) - 1

        context = self.test_engine.build_context()
        observation = self.test_engine.heuristic_engine.observe(context)
        candidates = self.test_engine.heuristic_engine.build_candidates(context, observation)
        bearish_candidate = max(
            (
                candidate
                for candidate in candidates
                if candidate.option_type == "PE" and candidate.setup_type == "bearish_rejection_watch"
            ),
            key=lambda candidate: candidate.score,
        )

        self.assertGreaterEqual(bearish_candidate.score, 35.0)
        self.assertTrue(any("round-number" in note.lower() for note in bearish_candidate.notes))
        self.assertTrue(any("equal-high" in note.lower() for note in bearish_candidate.notes))

    def test_heuristic_regime_filter_downgrades_candidate_score_without_delaying_entry(self) -> None:
        self.test_engine.set_instrument_mode("stock")
        candles = [
            Candle(timestamp="2026-05-13T09:15:00", open=23710, high=23820, low=23688, close=23720, volume=1000),
            Candle(timestamp="2026-05-14T09:15:00", open=23720, high=23734, low=23718, close=23730, volume=1100),
            Candle(timestamp="2026-05-14T09:16:00", open=23730, high=23749.7, low=23726, close=23746, volume=1200),
            Candle(timestamp="2026-05-14T09:17:00", open=23746, high=23750.1, low=23740, close=23747, volume=1220),
            Candle(timestamp="2026-05-14T09:18:00", open=23747, high=23749.8, low=23742, close=23745, volume=1180),
            Candle(timestamp="2026-05-14T09:19:00", open=23745, high=23758.2, low=23740, close=23743, volume=1500),
            Candle(timestamp="2026-05-14T09:20:00", open=23743, high=23744, low=23724, close=23730, volume=1650),
        ]
        self.test_engine.reset_with_candles(candles)
        self.test_engine.current_index = len(candles) - 1

        context = self.test_engine.build_context()
        observation = self.test_engine.heuristic_engine.observe(context)
        candidates = self.test_engine.heuristic_engine.build_candidates(context, observation)
        bearish_candidate = max(
            (
                candidate
                for candidate in candidates
                if candidate.option_type == "PE" and candidate.setup_type == "bearish_rejection_watch"
            ),
            key=lambda candidate: candidate.score,
        )

        baseline_score = bearish_candidate.score
        baseline_ready = bearish_candidate.ready_to_enter
        observation.range_state = "compressing"
        observation.participation_state = "fair_value_churn"
        observation.regime_quality = self.test_engine.heuristic_engine.regime_quality_score(
            observation.range_state,
            observation.participation_state,
        )

        downgraded_candidate = self.test_engine.heuristic_engine.build_candidate_from_event(
            context,
            observation,
            bearish_candidate.event,
            option_type="PE",
            direction="LONG_PUT",
        )

        self.assertIsNotNone(downgraded_candidate)
        assert downgraded_candidate is not None
        self.assertLess(downgraded_candidate.score, baseline_score)
        self.assertEqual(downgraded_candidate.ready_to_enter, baseline_ready)
        self.assertTrue(any("fair-value churn" in note.lower() for note in downgraded_candidate.notes))

    def test_state_includes_heuristic_trace_and_narrative_entries(self) -> None:
        candles = [
            Candle(timestamp="2026-05-13T09:15:00", open=100, high=101, low=99, close=100, volume=1000),
            Candle(timestamp="2026-05-14T09:15:00", open=103, high=103.3, low=102.5, close=102.7, volume=1100),
            Candle(timestamp="2026-05-14T09:16:00", open=102.7, high=102.9, low=99.7, close=100.2, volume=1300),
            Candle(timestamp="2026-05-14T09:17:00", open=100.2, high=101.1, low=100.0, close=100.9, volume=1350),
            Candle(timestamp="2026-05-14T09:18:00", open=100.9, high=101.4, low=100.6, close=101.2, volume=1380),
            Candle(timestamp="2026-05-14T09:19:00", open=101.2, high=102.6, low=101.1, close=102.2, volume=1500),
        ]
        self.test_engine.reset_with_candles(candles)
        self.test_engine.current_index = len(candles) - 1
        self.test_engine.evaluate_current_candle()

        state = self.test_engine.get_state()

        self.assertTrue(state.heuristic_trace)
        self.assertTrue(state.heuristic_narrative)
        self.assertTrue(state.heuristic_trace[0].candle_refs)
        self.assertTrue(state.heuristic_narrative[0].candle_refs)

    def test_heuristic_trace_records_threshold_failure_details(self) -> None:
        self.test_engine.set_instrument_mode("stock")
        candles = [
            Candle(timestamp="2026-05-13T09:15:00", open=23910, high=23920, low=23888, close=23900, volume=1000),
            Candle(timestamp="2026-05-14T09:15:00", open=23720, high=23734, low=23718, close=23730, volume=1100),
            Candle(timestamp="2026-05-14T09:16:00", open=23730, high=23749.7, low=23726, close=23746, volume=1200),
            Candle(timestamp="2026-05-14T09:17:00", open=23746, high=23750.1, low=23740, close=23747, volume=1220),
            Candle(timestamp="2026-05-14T09:18:00", open=23747, high=23749.8, low=23742, close=23745, volume=1180),
            Candle(timestamp="2026-05-14T09:19:00", open=23745, high=23752.0, low=23740, close=23749.5, volume=1500),
            Candle(timestamp="2026-05-14T09:20:00", open=23749.5, high=23750.0, low=23738, close=23742.0, volume=1650),
        ]
        self.test_engine.reset_with_candles(candles)
        self.test_engine.current_index = len(candles) - 1
        self.test_engine.evaluate_current_candle()

        state = self.test_engine.get_state()
        entry = next((item for item in state.heuristic_trace if item.status == "failed_threshold"), None)

        self.assertIsNotNone(entry)
        self.assertIsNotNone(entry.setup_type)
        self.assertIsNotNone(entry.block_reason)
        self.assertIsNotNone(entry.trigger_price)
        self.assertTrue(entry.candle_refs)
        self.assertIsNotNone(entry.matched_level_label)
        self.assertIsNotNone(entry.matched_level_price)

    def test_heuristic_events_include_matching_candle_roles(self) -> None:
        candles = [
            Candle(timestamp="2026-05-13T09:15:00", open=100, high=101, low=99, close=100, volume=1000),
            Candle(timestamp="2026-05-14T09:15:00", open=103, high=103.3, low=102.5, close=102.7, volume=1100),
            Candle(timestamp="2026-05-14T09:16:00", open=102.7, high=102.9, low=99.7, close=100.2, volume=1300),
            Candle(timestamp="2026-05-14T09:17:00", open=100.2, high=101.1, low=100.0, close=100.9, volume=1350),
            Candle(timestamp="2026-05-14T09:18:00", open=100.9, high=101.4, low=100.6, close=101.2, volume=1380),
            Candle(timestamp="2026-05-14T09:19:00", open=101.2, high=102.6, low=101.1, close=102.2, volume=1500),
        ]
        self.test_engine.reset_with_candles(candles)
        self.test_engine.current_index = len(candles) - 1
        self.test_engine.evaluate_current_candle()

        state = self.test_engine.get_state()
        matched_narrative = next((event for event in state.heuristic_narrative if event.matched_level_label), None)
        trace_entry = next((entry for entry in state.heuristic_trace if entry.matched_level_label), None)

        self.assertIsNotNone(matched_narrative)
        self.assertIsNotNone(trace_entry)
        assert matched_narrative is not None
        assert trace_entry is not None
        self.assertTrue(any("Decision candle" in candle_ref.label for candle_ref in matched_narrative.candle_refs))
        self.assertTrue(any("Decision candle" in candle_ref.label for candle_ref in trace_entry.candle_refs))
        self.assertGreaterEqual(len(trace_entry.candle_refs), 2)

    def test_invalidated_pending_setup_is_not_reinvalidated_every_candle(self) -> None:
        candles = [
            Candle(timestamp="2026-05-13T09:15:00", open=100, high=101, low=99, close=100, volume=1000),
            Candle(timestamp="2026-05-14T09:15:00", open=103, high=103.3, low=102.5, close=102.7, volume=1100),
            Candle(timestamp="2026-05-14T09:16:00", open=102.7, high=102.9, low=99.7, close=100.2, volume=1300),
            Candle(timestamp="2026-05-14T09:17:00", open=100.2, high=101.1, low=100.0, close=100.9, volume=1350),
            Candle(timestamp="2026-05-14T09:18:00", open=100.9, high=101.4, low=100.6, close=101.2, volume=1380),
            Candle(timestamp="2026-05-14T09:19:00", open=101.2, high=102.6, low=101.1, close=102.2, volume=1500),
            Candle(timestamp="2026-05-14T09:20:00", open=102.2, high=102.8, low=101.8, close=102.4, volume=1550),
        ]
        self.test_engine.reset_with_candles(candles)
        arm_decision = TradeDecision(
            action=TradeAction.no_trade,
            confidence=0.72,
            reason="Arm previous close reclaim.",
            pending_setup_action="ARM",
            pending_setup_type="previous_close_reclaim_long",
            pending_setup_direction="LONG_CALL",
            pending_setup_option_type="CE",
            pending_setup_trigger_price=103.0,
            pending_setup_invalidation_level=99.5,
            pending_setup_trigger_basis="close_above",
        )
        self.test_engine.apply_pending_setup_decision(candles[1], arm_decision)
        assert self.test_engine.pending_setup is not None
        self.test_engine.pending_setup.status = "invalidated"
        self.test_engine.pending_setup.invalidated_at = candles[2].timestamp
        self.test_engine.pending_setup.status_reason = "Already invalidated."

        self.test_engine.current_index = len(candles) - 1
        self.test_engine.evaluate_current_candle()
        state = self.test_engine.get_state()

        self.assertFalse(any(entry.status == "setup_invalidated" for entry in state.heuristic_trace))

    def test_gap_fill_narrative_is_not_repeated_every_candle(self) -> None:
        candles = [
            Candle(timestamp="2026-05-13T09:15:00", open=100, high=101, low=99.2, close=100, volume=1000),
            Candle(timestamp="2026-05-14T09:15:00", open=103, high=103.3, low=102.5, close=102.7, volume=1100),
            Candle(timestamp="2026-05-14T09:16:00", open=102.7, high=102.9, low=99.7, close=100.2, volume=1300),
            Candle(timestamp="2026-05-14T09:17:00", open=100.2, high=101.1, low=100.0, close=100.9, volume=1350),
            Candle(timestamp="2026-05-14T09:18:00", open=100.9, high=101.4, low=100.6, close=101.2, volume=1380),
            Candle(timestamp="2026-05-14T09:19:00", open=101.2, high=102.6, low=101.1, close=102.2, volume=1500),
            Candle(timestamp="2026-05-14T09:20:00", open=102.2, high=102.7, low=101.9, close=102.4, volume=1520),
        ]
        self.test_engine.reset_with_candles(candles)
        for index in range(1, len(candles)):
            self.test_engine.current_index = index
            self.test_engine.evaluate_current_candle()

        state = self.test_engine.get_state()
        gap_fill_entries = [event for event in state.heuristic_narrative if event.event_type == "gap-fill"]

        self.assertEqual(len(gap_fill_entries), 1)

    def test_get_state_clears_stale_hold_when_no_active_trade_exists(self) -> None:
        self.test_engine.decision = TradeDecision(
            action=TradeAction.hold,
            confidence=0.58,
            reason="Hold the open trade.",
            decision_source="heuristic",
        )
        self.test_engine.active_trade = None

        state = self.test_engine.get_state()

        self.assertIsNotNone(state.decision)
        self.assertEqual(state.decision.action, TradeAction.no_trade)
        self.assertIn("No active paper trade", state.decision.reason)

    def test_consumed_pending_setup_is_hidden_from_live_pending_card_state(self) -> None:
        candles = [
            Candle(timestamp="2026-05-13T09:15:00", open=100, high=101, low=99.2, close=100, volume=1000),
            Candle(timestamp="2026-05-14T09:15:00", open=103, high=103.3, low=102.5, close=102.7, volume=1100),
        ]
        self.test_engine.reset_with_candles(candles)
        arm_decision = TradeDecision(
            action=TradeAction.no_trade,
            confidence=0.72,
            reason="Arm setup.",
            pending_setup_action="ARM",
            pending_setup_type="previous_close_reclaim_long",
            pending_setup_direction="LONG_CALL",
            pending_setup_option_type="CE",
            pending_setup_trigger_price=103.0,
            pending_setup_invalidation_level=99.5,
            pending_setup_trigger_basis="close_above",
        )
        self.test_engine.apply_pending_setup_decision(candles[1], arm_decision)
        assert self.test_engine.pending_setup is not None
        self.test_engine.pending_setup.status = "consumed"
        self.test_engine.pending_setup.consumed_at = candles[1].timestamp

        state = self.test_engine.get_state()

        self.assertIsNone(state.pending_setup)

    def test_neutral_previous_close_signal_is_deduplicated(self) -> None:
        candles = [
            Candle(timestamp="2026-05-13T09:15:00", open=100, high=101, low=99, close=100, volume=1000),
            Candle(timestamp="2026-05-14T09:15:00", open=100.5, high=100.8, low=99.9, close=100.2, volume=1100),
            Candle(timestamp="2026-05-14T09:16:00", open=100.2, high=100.6, low=99.8, close=100.1, volume=1150),
            Candle(timestamp="2026-05-14T09:17:00", open=100.1, high=100.7, low=99.9, close=100.3, volume=1200),
            Candle(timestamp="2026-05-14T09:18:00", open=100.3, high=100.5, low=99.7, close=100.0, volume=1210),
        ]
        self.test_engine.reset_with_candles(candles)
        for index in range(1, len(candles)):
            self.test_engine.current_index = index
            self.test_engine.evaluate_current_candle()

        state = self.test_engine.get_state()
        close_near_entries = [event for event in state.signal_history if event.title == "Close near previous day close"]

        self.assertEqual(len(close_near_entries), 1)


if __name__ == "__main__":
    unittest.main()
