from __future__ import annotations

import tempfile
import time
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

import app.main as main_module
from app.config import get_settings
from app.schemas import Candle, LiveFeedState, OperatingMode, SimulatedTrade, TradeAction, TradeDecision
from app.services.credential_store import CredentialStore
from app.services.dhan_history import DhanSessionBundle
from app.services.dhan_options import OptionContract, OptionQuote
from app.services.simulation import SimulationEngine


class AppIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.original_engine = main_module.engine
        self.test_engine = SimulationEngine(get_settings())
        self.test_engine.credential_store = CredentialStore(Path(self.tempdir.name) / "credentials.json")
        self.test_engine.ai_service.enabled = False
        self.test_engine.live_feed = self.test_engine._build_live_feed_state()
        main_module.engine = self.test_engine
        self.client = TestClient(main_module.app)

    def tearDown(self) -> None:
        self.test_engine.disconnect_live_feed()
        main_module.engine = self.original_engine
        self.tempdir.cleanup()

    def test_dashboard_and_health_routes_render(self) -> None:
        with patch.object(self.test_engine.ai_service, "health", return_value={"reachable": True, "model_available": True, "model": "gpt-5.4-mini", "message": "ok"}):
            dashboard = self.client.get("/")
            health = self.client.get("/api/health/ai")

        self.assertEqual(dashboard.status_code, 200)
        self.assertIn("SL Hunting Paper Trader", dashboard.text)
        self.assertEqual(health.status_code, 200)
        self.assertTrue(health.json()["reachable"])

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
        self.assertTrue(Path(summary["storage_path"]).exists())

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

    def test_switching_to_stock_mode_updates_state(self) -> None:
        response = self.client.post("/api/instrument-mode", data={"instrument_mode": "stock"})

        self.assertEqual(response.status_code, 200)
        state = response.json()["state"]
        self.assertEqual(state["instrument"]["mode"], "stock")
        self.assertEqual(state["instrument"]["label"], "SBIN")
        self.assertEqual(state["instrument"]["security_id"], "3045")
        self.assertFalse(state["instrument"]["supports_options"])

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

    def test_heuristic_v2_enters_stock_long_after_multi_candle_reclaim(self) -> None:
        self.test_engine.set_instrument_mode("stock")
        candles = [
            Candle(timestamp="2026-05-13T09:15:00", open=810, high=812, low=806, close=808, volume=1000),
            Candle(timestamp="2026-05-13T09:16:00", open=808, high=811, low=804, close=805, volume=1100),
            Candle(timestamp="2026-05-14T09:15:00", open=806, high=807, low=803, close=805.5, volume=1300),
            Candle(timestamp="2026-05-14T09:16:00", open=805.5, high=809, low=803.8, close=808.4, volume=1500),
            Candle(timestamp="2026-05-14T09:17:00", open=808.4, high=810.2, low=807.9, close=809.8, volume=1450),
            Candle(timestamp="2026-05-14T09:18:00", open=809.8, high=811.4, low=809.2, close=810.9, volume=1480),
            Candle(timestamp="2026-05-14T09:19:00", open=810.9, high=812.2, low=810.1, close=811.8, volume=1520),
            Candle(timestamp="2026-05-14T09:20:00", open=811.8, high=813.1, low=811.0, close=812.6, volume=1550),
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

    def test_heuristic_v2_requests_partial_exit_after_first_target(self) -> None:
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
        self.assertGreaterEqual(decision.partial_exit_quantity or 0, 1)

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
        candles = [
            Candle(timestamp="2026-05-13T09:15:00", open=100, high=101, low=99.2, close=100, volume=1000),
            Candle(timestamp="2026-05-14T09:15:00", open=103, high=103.3, low=102.5, close=102.7, volume=1100),
            Candle(timestamp="2026-05-14T09:16:00", open=102.7, high=102.9, low=99.7, close=100.2, volume=1300),
            Candle(timestamp="2026-05-14T09:17:00", open=100.2, high=101.1, low=100.0, close=100.9, volume=1350),
            Candle(timestamp="2026-05-14T09:18:00", open=100.9, high=101.4, low=100.6, close=101.2, volume=1380),
            Candle(timestamp="2026-05-14T09:19:00", open=101.2, high=102.6, low=101.1, close=102.2, volume=1500),
        ]
        self.test_engine.reset_with_candles(candles)
        self.test_engine.current_index = len(candles) - 1

        decision = self.test_engine.heuristic_decision(self.test_engine.build_context())

        self.assertEqual(decision.action, TradeAction.enter_call)
        self.assertEqual(decision.setup_type, "previous_close_reclaim_long")

    def test_heuristic_maps_round_number_equal_clusters_and_swings(self) -> None:
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

    def test_heuristic_scores_equal_high_round_number_sweep_more_aggressively(self) -> None:
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
        bearish_candidate = next(candidate for candidate in candidates if candidate.option_type == "PE")

        self.assertGreaterEqual(bearish_candidate.score, 35.0)
        self.assertTrue(any("round-number" in note.lower() for note in bearish_candidate.notes))
        self.assertTrue(any("equal-high" in note.lower() for note in bearish_candidate.notes))

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

    def test_heuristic_trace_records_threshold_failure_details(self) -> None:
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
