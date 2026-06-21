from __future__ import annotations

import base64
import json
from datetime import datetime
from pathlib import Path

from app.config import Settings
from app.schemas import CredentialSummary, FullAIProvider, InstrumentMode, OperatingMode


class CredentialStore:
    def __init__(self, path: str | Path | None = None) -> None:
        if path is None:
            path = Path(__file__).resolve().parents[1] / "data" / "credentials.json"
        self.path = Path(path)

    def load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def save(
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
        heuristic_advance_timeframe_minutes: int | None = None,
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
        stock_cost_sl_after_pyramid_enabled: bool | None = None,
        nifty_point_pyramiding_enabled: bool | None = None,
        nifty_point_pyramiding_points: float | None = None,
        nifty_trade_bias: str | None = None,
        nifty_option_trade_mode: str | None = None,
        global_mtm_square_off_enabled: bool | None = None,
        global_mtm_square_off_threshold: float | None = None,
    ) -> None:
        payload = self.load()
        updated = False

        if client_id and client_id.strip():
            normalized = client_id.strip()
            if payload.get("client_id") != normalized:
                payload["client_id"] = normalized
                updated = True
        if access_token and access_token.strip():
            normalized = access_token.strip()
            if payload.get("access_token") != normalized:
                payload["access_token"] = normalized
                updated = True
            embedded_client_id = self._extract_dhan_client_id_from_token(normalized)
            if embedded_client_id and payload.get("client_id") != embedded_client_id:
                payload["client_id"] = embedded_client_id
                updated = True
        if broker_provider and broker_provider.strip():
            normalized = self._normalize_broker_provider(broker_provider)
            if payload.get("broker_provider") != normalized:
                payload["broker_provider"] = normalized
                updated = True
        if zerodha_api_key and zerodha_api_key.strip():
            normalized = zerodha_api_key.strip()
            if payload.get("zerodha_api_key") != normalized:
                payload["zerodha_api_key"] = normalized
                updated = True
        if zerodha_api_secret and zerodha_api_secret.strip():
            normalized = zerodha_api_secret.strip()
            if payload.get("zerodha_api_secret") != normalized:
                payload["zerodha_api_secret"] = normalized
                updated = True
        if zerodha_access_token and zerodha_access_token.strip():
            normalized = zerodha_access_token.strip()
            if payload.get("zerodha_access_token") != normalized:
                payload["zerodha_access_token"] = normalized
                updated = True
        if openai_api_key and openai_api_key.strip():
            normalized = openai_api_key.strip()
            if payload.get("openai_api_key") != normalized:
                payload["openai_api_key"] = normalized
                updated = True
        if openai_model and openai_model.strip():
            normalized = openai_model.strip()
            if payload.get("openai_model") != normalized:
                payload["openai_model"] = normalized
                updated = True
        if deepseek_api_key and deepseek_api_key.strip():
            normalized = deepseek_api_key.strip()
            if payload.get("deepseek_api_key") != normalized:
                payload["deepseek_api_key"] = normalized
                updated = True
        if deepseek_model and deepseek_model.strip():
            normalized = deepseek_model.strip()
            if payload.get("deepseek_model") != normalized:
                payload["deepseek_model"] = normalized
                updated = True
        if full_ai_provider and full_ai_provider.strip():
            normalized = full_ai_provider.strip().lower()
            if payload.get("full_ai_provider") != normalized:
                payload["full_ai_provider"] = normalized
                updated = True
        if operating_mode and operating_mode.strip():
            normalized = operating_mode.strip()
            if payload.get("operating_mode") != normalized:
                payload["operating_mode"] = normalized
                updated = True
        if nifty_order_lots is not None:
            normalized = max(int(nifty_order_lots), 1)
            if payload.get("nifty_order_lots") != normalized:
                payload["nifty_order_lots"] = normalized
                updated = True
        if stock_trade_capital is not None:
            normalized = round(max(float(stock_trade_capital), 1.0), 2)
            if payload.get("stock_trade_capital") != normalized:
                payload["stock_trade_capital"] = normalized
                updated = True
        if stock_execution_mode and stock_execution_mode.strip():
            normalized = self._normalize_stock_execution_mode(stock_execution_mode)
            if payload.get("stock_execution_mode") != normalized:
                payload["stock_execution_mode"] = normalized
                updated = True
        if stock_future_lots is not None:
            normalized = max(int(stock_future_lots), 1)
            if payload.get("stock_future_lots") != normalized:
                payload["stock_future_lots"] = normalized
                updated = True
        if stock_option_lots is not None:
            normalized = max(int(stock_option_lots), 1)
            if payload.get("stock_option_lots") != normalized:
                payload["stock_option_lots"] = normalized
                updated = True
        if heuristic_advance_timeframe_minutes is not None:
            normalized = min(max(int(heuristic_advance_timeframe_minutes), 1), 60)
            if payload.get("heuristic_advance_timeframe_minutes") != normalized:
                payload["heuristic_advance_timeframe_minutes"] = normalized
                updated = True
        if nifty_expiry_preference and nifty_expiry_preference.strip():
            normalized = nifty_expiry_preference.strip().lower()
            if payload.get("nifty_expiry_preference") != normalized:
                payload["nifty_expiry_preference"] = normalized
                updated = True
        if stock_partial_profit_enabled is not None:
            normalized = bool(stock_partial_profit_enabled)
            if payload.get("stock_partial_profit_enabled") != normalized:
                payload["stock_partial_profit_enabled"] = normalized
                updated = True
        if stock_trailing_stop_enabled is not None:
            normalized = bool(stock_trailing_stop_enabled)
            if payload.get("stock_trailing_stop_enabled") != normalized:
                payload["stock_trailing_stop_enabled"] = normalized
                updated = True
        if stock_heuristic_early_exit_enabled is not None:
            normalized = bool(stock_heuristic_early_exit_enabled)
            if payload.get("stock_heuristic_early_exit_enabled") != normalized:
                payload["stock_heuristic_early_exit_enabled"] = normalized
                updated = True
        if nifty_trailing_stop_enabled is not None:
            normalized = bool(nifty_trailing_stop_enabled)
            if payload.get("nifty_trailing_stop_enabled") != normalized:
                payload["nifty_trailing_stop_enabled"] = normalized
                updated = True
        if nifty_heuristic_early_exit_enabled is not None:
            normalized = bool(nifty_heuristic_early_exit_enabled)
            if payload.get("nifty_heuristic_early_exit_enabled") != normalized:
                payload["nifty_heuristic_early_exit_enabled"] = normalized
                updated = True
        if nifty_cost_sl_enabled is not None:
            normalized = bool(nifty_cost_sl_enabled)
            if payload.get("nifty_cost_sl_enabled") != normalized:
                payload["nifty_cost_sl_enabled"] = normalized
                updated = True
        if nifty_cost_sl_points is not None:
            normalized = round(max(float(nifty_cost_sl_points), 0.0), 2)
            if payload.get("nifty_cost_sl_points") != normalized:
                payload["nifty_cost_sl_points"] = normalized
                updated = True
        if nifty_min_sl_points is not None:
            normalized = round(max(float(nifty_min_sl_points), 0.0), 2)
            if payload.get("nifty_min_sl_points") != normalized:
                payload["nifty_min_sl_points"] = normalized
                updated = True
        if nifty_max_sl_points is not None:
            normalized = round(max(float(nifty_max_sl_points), 0.0), 2)
            if payload.get("nifty_max_sl_points") != normalized:
                payload["nifty_max_sl_points"] = normalized
                updated = True
        if nifty_target_enabled is not None:
            normalized = bool(nifty_target_enabled)
            if payload.get("nifty_target_enabled") != normalized:
                payload["nifty_target_enabled"] = normalized
                updated = True
        if nifty_target_points is not None:
            normalized = round(max(float(nifty_target_points), 0.0), 2)
            if payload.get("nifty_target_points") != normalized:
                payload["nifty_target_points"] = normalized
                updated = True
        if nifty_daily_max_loss_enabled is not None:
            normalized = bool(nifty_daily_max_loss_enabled)
            if payload.get("nifty_daily_max_loss_enabled") != normalized:
                payload["nifty_daily_max_loss_enabled"] = normalized
                updated = True
        if nifty_daily_max_loss is not None:
            normalized = round(max(float(nifty_daily_max_loss), 0.0), 2)
            if payload.get("nifty_daily_max_loss") != normalized:
                payload["nifty_daily_max_loss"] = normalized
                updated = True
        if pyramiding_enabled is not None:
            normalized = bool(pyramiding_enabled)
            if payload.get("pyramiding_enabled") != normalized:
                payload["pyramiding_enabled"] = normalized
                updated = True
        if intelligent_pyramiding_enabled is not None:
            normalized = bool(intelligent_pyramiding_enabled)
            if payload.get("intelligent_pyramiding_enabled") != normalized:
                payload["intelligent_pyramiding_enabled"] = normalized
                updated = True
        if stock_percent_pyramiding_enabled is not None:
            normalized = bool(stock_percent_pyramiding_enabled)
            if payload.get("stock_percent_pyramiding_enabled") != normalized:
                payload["stock_percent_pyramiding_enabled"] = normalized
                updated = True
        if stock_percent_pyramiding_step is not None:
            normalized = round(max(float(stock_percent_pyramiding_step), 0.0), 2)
            if payload.get("stock_percent_pyramiding_step") != normalized:
                payload["stock_percent_pyramiding_step"] = normalized
                updated = True
        if stock_cost_sl_after_pyramid_enabled is not None:
            normalized = bool(stock_cost_sl_after_pyramid_enabled)
            if payload.get("stock_cost_sl_after_pyramid_enabled") != normalized:
                payload["stock_cost_sl_after_pyramid_enabled"] = normalized
                updated = True
        if nifty_point_pyramiding_enabled is not None:
            normalized = bool(nifty_point_pyramiding_enabled)
            if payload.get("nifty_point_pyramiding_enabled") != normalized:
                payload["nifty_point_pyramiding_enabled"] = normalized
                updated = True
        if nifty_point_pyramiding_points is not None:
            normalized = round(max(float(nifty_point_pyramiding_points), 0.0), 2)
            if payload.get("nifty_point_pyramiding_points") != normalized:
                payload["nifty_point_pyramiding_points"] = normalized
                updated = True
        if nifty_trade_bias and nifty_trade_bias.strip():
            normalized = self._normalize_nifty_trade_bias(nifty_trade_bias)
            if payload.get("nifty_trade_bias") != normalized:
                payload["nifty_trade_bias"] = normalized
                updated = True
        if nifty_option_trade_mode and nifty_option_trade_mode.strip():
            normalized = self._normalize_nifty_option_trade_mode(nifty_option_trade_mode)
            if payload.get("nifty_option_trade_mode") != normalized:
                payload["nifty_option_trade_mode"] = normalized
                updated = True
        if global_mtm_square_off_enabled is not None:
            normalized = bool(global_mtm_square_off_enabled)
            if payload.get("global_mtm_square_off_enabled") != normalized:
                payload["global_mtm_square_off_enabled"] = normalized
                updated = True
        if global_mtm_square_off_threshold is not None:
            normalized = round(float(global_mtm_square_off_threshold), 2)
            if payload.get("global_mtm_square_off_threshold") != normalized:
                payload["global_mtm_square_off_threshold"] = normalized
                updated = True

        if not updated:
            return

        payload["last_updated"] = datetime.now().isoformat()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def save_ui_preferences(
        self,
        *,
        instrument_mode: str | InstrumentMode | None = None,
        selected_stock_symbol: str | None = None,
        stock_watchlist_symbols: list[str] | tuple[str, ...] | None = None,
    ) -> None:
        payload = self.load()
        updated = False

        if instrument_mode is not None:
            normalized_mode = (
                instrument_mode.value if isinstance(instrument_mode, InstrumentMode) else str(instrument_mode).strip().lower()
            )
            if normalized_mode in {InstrumentMode.nifty.value, InstrumentMode.stock.value}:
                if payload.get("instrument_mode") != normalized_mode:
                    payload["instrument_mode"] = normalized_mode
                    updated = True

        if selected_stock_symbol is not None:
            normalized_symbol = str(selected_stock_symbol).strip().upper()
            if payload.get("selected_stock_symbol") != normalized_symbol:
                payload["selected_stock_symbol"] = normalized_symbol
                updated = True

        if stock_watchlist_symbols is not None:
            normalized_watchlist = [
                symbol.strip().upper()
                for symbol in stock_watchlist_symbols
                if str(symbol).strip()
            ]
            normalized_watchlist = list(dict.fromkeys(normalized_watchlist))
            if payload.get("stock_watchlist_symbols") != normalized_watchlist:
                payload["stock_watchlist_symbols"] = normalized_watchlist
                updated = True

        if not updated:
            return

        payload["last_updated"] = datetime.now().isoformat()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def get_dhan_credentials(self, settings: Settings) -> tuple[str | None, str | None]:
        payload = self.load()
        client_id = payload.get("client_id") or settings.dhan_client_id
        access_token = payload.get("access_token") or settings.dhan_access_token
        resolved_client_id, resolved_token, _ = self.resolve_dhan_credentials(client_id, access_token)
        return resolved_client_id, resolved_token

    def get_broker_provider(self, settings: Settings) -> str:
        payload = self.load()
        return self._normalize_broker_provider(payload.get("broker_provider") or settings.broker_provider)

    def get_zerodha_credentials(self, settings: Settings) -> tuple[str | None, str | None, str | None]:
        payload = self.load()
        api_key = payload.get("zerodha_api_key") or settings.zerodha_api_key
        api_secret = payload.get("zerodha_api_secret") or settings.zerodha_api_secret
        access_token = payload.get("zerodha_access_token") or settings.zerodha_access_token
        return (
            str(api_key).strip() if api_key else None,
            str(api_secret).strip() if api_secret else None,
            str(access_token).strip() if access_token else None,
        )

    def save_zerodha_access_token(self, access_token: str) -> None:
        self.save(zerodha_access_token=access_token)

    def resolve_dhan_credentials(
        self,
        client_id: str | None,
        access_token: str | None,
    ) -> tuple[str | None, str | None, str | None]:
        normalized_client_id = (client_id or "").strip() or None
        normalized_token = (access_token or "").strip() or None
        if not normalized_token:
            return normalized_client_id, normalized_token, None
        embedded_client_id = self._extract_dhan_client_id_from_token(normalized_token)
        if embedded_client_id and embedded_client_id != normalized_client_id:
            return (
                embedded_client_id,
                normalized_token,
                "Dhan access token belongs to a different client ID. The app is using the client ID embedded inside the token.",
            )
        return normalized_client_id, normalized_token, None

    def get_openai_settings(self, settings: Settings) -> tuple[str | None, str]:
        payload = self.load()
        api_key = payload.get("openai_api_key") or settings.openai_api_key
        model = payload.get("openai_model") or settings.openai_model
        return api_key, model

    def get_deepseek_settings(self, settings: Settings) -> tuple[str | None, str]:
        payload = self.load()
        api_key = payload.get("deepseek_api_key") or settings.deepseek_api_key
        model = payload.get("deepseek_model") or settings.deepseek_model
        return api_key, model

    def get_full_ai_provider(self, settings: Settings) -> FullAIProvider:
        payload = self.load()
        raw = str(payload.get("full_ai_provider") or settings.full_ai_provider or FullAIProvider.openai.value).strip().lower()
        if raw == FullAIProvider.deepseek.value:
            return FullAIProvider.deepseek
        return FullAIProvider.openai

    def get_operating_mode(self, settings: Settings) -> OperatingMode:
        payload = self.load()
        raw = str(payload.get("operating_mode") or settings.operating_mode or OperatingMode.full_ai.value).strip().lower()
        if raw == OperatingMode.heuristic.value:
            return OperatingMode.heuristic
        if raw == OperatingMode.heuristic_advance.value:
            return OperatingMode.heuristic_advance
        return OperatingMode.full_ai

    def get_nifty_order_lots(self, settings: Settings) -> int:
        payload = self.load()
        raw = payload.get("nifty_order_lots", settings.nifty_order_lots)
        try:
            return max(int(raw), 1)
        except (TypeError, ValueError):
            return max(int(settings.nifty_order_lots), 1)

    def get_stock_trade_capital(self, settings: Settings) -> float:
        payload = self.load()
        raw = payload.get("stock_trade_capital", settings.stock_trade_capital)
        try:
            return max(float(raw), 1.0)
        except (TypeError, ValueError):
            return max(float(settings.stock_trade_capital), 1.0)

    def get_stock_execution_mode(self, settings: Settings) -> str:
        payload = self.load()
        return self._normalize_stock_execution_mode(payload.get("stock_execution_mode") or settings.stock_execution_mode)

    def get_stock_future_lots(self, settings: Settings) -> int:
        payload = self.load()
        raw = payload.get("stock_future_lots", settings.stock_future_lots)
        try:
            return max(int(raw), 1)
        except (TypeError, ValueError):
            return max(int(settings.stock_future_lots), 1)

    def get_stock_option_lots(self, settings: Settings) -> int:
        payload = self.load()
        raw = payload.get("stock_option_lots", settings.stock_option_lots)
        try:
            return max(int(raw), 1)
        except (TypeError, ValueError):
            return max(int(settings.stock_option_lots), 1)

    def get_heuristic_advance_timeframe_minutes(self, settings: Settings) -> int:
        payload = self.load()
        raw = payload.get("heuristic_advance_timeframe_minutes", settings.heuristic_advance_timeframe_minutes)
        try:
            return min(max(int(raw), 1), 60)
        except (TypeError, ValueError):
            return min(max(int(settings.heuristic_advance_timeframe_minutes), 1), 60)

    def get_nifty_expiry_preference(self, settings: Settings) -> str:
        payload = self.load()
        raw = str(payload.get("nifty_expiry_preference") or settings.nifty_expiry_preference or "current-weekly").strip().lower()
        if raw == "next-weekly":
            return "next-weekly"
        return "current-weekly"

    def get_stock_partial_profit_enabled(self, settings: Settings) -> bool:
        payload = self.load()
        return self._coerce_bool(payload.get("stock_partial_profit_enabled"), True)

    def get_stock_trailing_stop_enabled(self, settings: Settings) -> bool:
        payload = self.load()
        return self._coerce_bool(payload.get("stock_trailing_stop_enabled"), True)

    def get_stock_heuristic_early_exit_enabled(self, settings: Settings) -> bool:
        payload = self.load()
        return self._coerce_bool(payload.get("stock_heuristic_early_exit_enabled"), True)

    def get_nifty_trailing_stop_enabled(self, settings: Settings) -> bool:
        payload = self.load()
        return self._coerce_bool(payload.get("nifty_trailing_stop_enabled"), bool(settings.nifty_trailing_stop_enabled))

    def get_nifty_heuristic_early_exit_enabled(self, settings: Settings) -> bool:
        payload = self.load()
        return self._coerce_bool(
            payload.get("nifty_heuristic_early_exit_enabled"),
            bool(settings.nifty_heuristic_early_exit_enabled),
        )

    def get_nifty_cost_sl_enabled(self, settings: Settings) -> bool:
        payload = self.load()
        return self._coerce_bool(payload.get("nifty_cost_sl_enabled"), bool(settings.nifty_cost_sl_enabled))

    def get_nifty_cost_sl_points(self, settings: Settings) -> float:
        payload = self.load()
        return self._coerce_float(payload.get("nifty_cost_sl_points"), float(settings.nifty_cost_sl_points), minimum=0.0)

    def get_nifty_min_sl_points(self, settings: Settings) -> float:
        payload = self.load()
        return self._coerce_float(payload.get("nifty_min_sl_points"), float(settings.nifty_min_sl_points), minimum=0.0)

    def get_nifty_max_sl_points(self, settings: Settings) -> float:
        payload = self.load()
        minimum = self.get_nifty_min_sl_points(settings)
        value = self._coerce_float(payload.get("nifty_max_sl_points"), float(settings.nifty_max_sl_points), minimum=0.0)
        return max(value, minimum)

    def get_nifty_target_enabled(self, settings: Settings) -> bool:
        payload = self.load()
        return self._coerce_bool(payload.get("nifty_target_enabled"), bool(settings.nifty_target_enabled))

    def get_nifty_target_points(self, settings: Settings) -> float:
        payload = self.load()
        return self._coerce_float(payload.get("nifty_target_points"), float(settings.nifty_target_points), minimum=0.0)

    def get_nifty_daily_max_loss_enabled(self, settings: Settings) -> bool:
        payload = self.load()
        return self._coerce_bool(
            payload.get("nifty_daily_max_loss_enabled"),
            bool(settings.nifty_daily_max_loss_enabled),
        )

    def get_nifty_daily_max_loss(self, settings: Settings) -> float:
        payload = self.load()
        return self._coerce_float(
            payload.get("nifty_daily_max_loss"),
            float(settings.nifty_daily_max_loss),
            minimum=0.0,
        )

    def get_pyramiding_enabled(self, settings: Settings) -> bool:
        payload = self.load()
        return self._coerce_bool(payload.get("pyramiding_enabled"), bool(settings.pyramiding_enabled))

    def get_intelligent_pyramiding_enabled(self, settings: Settings) -> bool:
        payload = self.load()
        return self._coerce_bool(
            payload.get("intelligent_pyramiding_enabled"),
            bool(settings.intelligent_pyramiding_enabled),
        )

    def get_stock_percent_pyramiding_enabled(self, settings: Settings) -> bool:
        payload = self.load()
        return self._coerce_bool(
            payload.get("stock_percent_pyramiding_enabled"),
            bool(settings.stock_percent_pyramiding_enabled),
        )

    def get_stock_percent_pyramiding_step(self, settings: Settings) -> float:
        payload = self.load()
        return self._coerce_float(
            payload.get("stock_percent_pyramiding_step"),
            float(settings.stock_percent_pyramiding_step),
            minimum=0.0,
        )

    def get_stock_cost_sl_after_pyramid_enabled(self, settings: Settings) -> bool:
        payload = self.load()
        return self._coerce_bool(
            payload.get("stock_cost_sl_after_pyramid_enabled"),
            bool(settings.stock_cost_sl_after_pyramid_enabled),
        )

    def get_nifty_option_trade_mode(self, settings: Settings) -> str:
        payload = self.load()
        return self._normalize_nifty_option_trade_mode(payload.get("nifty_option_trade_mode") or settings.nifty_option_trade_mode)

    def get_nifty_point_pyramiding_enabled(self, settings: Settings) -> bool:
        payload = self.load()
        return self._coerce_bool(
            payload.get("nifty_point_pyramiding_enabled"),
            bool(settings.nifty_point_pyramiding_enabled),
        )

    def get_nifty_point_pyramiding_points(self, settings: Settings) -> float:
        payload = self.load()
        return self._coerce_float(
            payload.get("nifty_point_pyramiding_points"),
            float(settings.nifty_point_pyramiding_points),
            minimum=0.0,
        )

    def get_nifty_trade_bias(self, settings: Settings) -> str:
        payload = self.load()
        return self._normalize_nifty_trade_bias(payload.get("nifty_trade_bias") or settings.nifty_trade_bias)

    def get_global_mtm_square_off_enabled(self, settings: Settings) -> bool:
        payload = self.load()
        return self._coerce_bool(
            payload.get("global_mtm_square_off_enabled"),
            bool(settings.global_mtm_square_off_enabled),
        )

    def get_global_mtm_square_off_threshold(self, settings: Settings) -> float:
        payload = self.load()
        return self._coerce_float(
            payload.get("global_mtm_square_off_threshold"),
            float(settings.global_mtm_square_off_threshold),
        )

    def get_ui_preferences(self) -> tuple[InstrumentMode, str | None, list[str]]:
        payload = self.load()
        raw_mode = str(payload.get("instrument_mode") or InstrumentMode.nifty.value).strip().lower()
        instrument_mode = InstrumentMode.stock if raw_mode == InstrumentMode.stock.value else InstrumentMode.nifty
        selected_stock_symbol = str(payload.get("selected_stock_symbol") or "").strip().upper() or None
        raw_watchlist = payload.get("stock_watchlist_symbols")
        if isinstance(raw_watchlist, list):
            stock_watchlist_symbols = [
                str(symbol).strip().upper()
                for symbol in raw_watchlist
                if str(symbol).strip()
            ]
            stock_watchlist_symbols = list(dict.fromkeys(stock_watchlist_symbols))
        else:
            stock_watchlist_symbols = []
        return instrument_mode, selected_stock_symbol, stock_watchlist_symbols

    def summary(self, settings: Settings) -> CredentialSummary:
        payload = self.load()
        last_updated = None
        if payload.get("last_updated"):
            try:
                last_updated = datetime.fromisoformat(payload["last_updated"])
            except ValueError:
                last_updated = None
        return CredentialSummary(
            client_id=payload.get("client_id") or settings.dhan_client_id,
            resolved_client_id=self.resolve_dhan_credentials(
                payload.get("client_id") or settings.dhan_client_id,
                payload.get("access_token") or settings.dhan_access_token,
            )[0],
            dhan_access_token_saved=bool(payload.get("access_token") or settings.dhan_access_token),
            broker_provider=self.get_broker_provider(settings),
            zerodha_api_key=payload.get("zerodha_api_key") or settings.zerodha_api_key,
            zerodha_api_secret_saved=bool(payload.get("zerodha_api_secret") or settings.zerodha_api_secret),
            zerodha_access_token_saved=bool(payload.get("zerodha_access_token") or settings.zerodha_access_token),
            zerodha_login_url=self._zerodha_login_url(payload.get("zerodha_api_key") or settings.zerodha_api_key),
            openai_api_key_saved=bool(payload.get("openai_api_key") or settings.openai_api_key),
            openai_model=payload.get("openai_model") or settings.openai_model,
            deepseek_api_key_saved=bool(payload.get("deepseek_api_key") or settings.deepseek_api_key),
            deepseek_model=payload.get("deepseek_model") or settings.deepseek_model,
            full_ai_provider=self.get_full_ai_provider(settings),
            operating_mode=self.get_operating_mode(settings),
            nifty_order_lots=self.get_nifty_order_lots(settings),
            stock_trade_capital=self.get_stock_trade_capital(settings),
            stock_execution_mode=self.get_stock_execution_mode(settings),
            stock_future_lots=self.get_stock_future_lots(settings),
            stock_option_lots=self.get_stock_option_lots(settings),
            heuristic_advance_timeframe_minutes=self.get_heuristic_advance_timeframe_minutes(settings),
            nifty_expiry_preference=self.get_nifty_expiry_preference(settings),
            stock_partial_profit_enabled=self.get_stock_partial_profit_enabled(settings),
            stock_trailing_stop_enabled=self.get_stock_trailing_stop_enabled(settings),
            stock_heuristic_early_exit_enabled=self.get_stock_heuristic_early_exit_enabled(settings),
            nifty_trailing_stop_enabled=self.get_nifty_trailing_stop_enabled(settings),
            nifty_heuristic_early_exit_enabled=self.get_nifty_heuristic_early_exit_enabled(settings),
            nifty_cost_sl_enabled=self.get_nifty_cost_sl_enabled(settings),
            nifty_cost_sl_points=self.get_nifty_cost_sl_points(settings),
            nifty_min_sl_points=self.get_nifty_min_sl_points(settings),
            nifty_max_sl_points=self.get_nifty_max_sl_points(settings),
            nifty_target_enabled=self.get_nifty_target_enabled(settings),
            nifty_target_points=self.get_nifty_target_points(settings),
            nifty_daily_max_loss_enabled=self.get_nifty_daily_max_loss_enabled(settings),
            nifty_daily_max_loss=self.get_nifty_daily_max_loss(settings),
            pyramiding_enabled=self.get_pyramiding_enabled(settings),
            intelligent_pyramiding_enabled=self.get_intelligent_pyramiding_enabled(settings),
            stock_percent_pyramiding_enabled=self.get_stock_percent_pyramiding_enabled(settings),
            stock_percent_pyramiding_step=self.get_stock_percent_pyramiding_step(settings),
            stock_cost_sl_after_pyramid_enabled=self.get_stock_cost_sl_after_pyramid_enabled(settings),
            nifty_point_pyramiding_enabled=self.get_nifty_point_pyramiding_enabled(settings),
            nifty_point_pyramiding_points=self.get_nifty_point_pyramiding_points(settings),
            nifty_trade_bias=self.get_nifty_trade_bias(settings),
            nifty_option_trade_mode=self.get_nifty_option_trade_mode(settings),
            global_mtm_square_off_enabled=self.get_global_mtm_square_off_enabled(settings),
            global_mtm_square_off_threshold=self.get_global_mtm_square_off_threshold(settings),
            dhan_credential_message=self.resolve_dhan_credentials(
                payload.get("client_id") or settings.dhan_client_id,
                payload.get("access_token") or settings.dhan_access_token,
            )[2],
            storage_path=str(self.path.resolve()),
            last_updated=last_updated,
        )

    def _extract_dhan_client_id_from_token(self, access_token: str | None) -> str | None:
        token = (access_token or "").strip()
        if not token:
            return None
        parts = token.split(".")
        if len(parts) < 2:
            return None
        payload = parts[1]
        padding = "=" * (-len(payload) % 4)
        try:
            decoded = base64.urlsafe_b64decode((payload + padding).encode("utf-8")).decode("utf-8")
            data = json.loads(decoded)
        except Exception:
            return None
        client_id = str(data.get("dhanClientId") or "").strip()
        return client_id or None

    @staticmethod
    def _normalize_broker_provider(value: object) -> str:
        normalized = str(value or "dhan").strip().lower()
        if normalized in {"zerodha", "kite", "kiteconnect", "kite-connect"}:
            return "zerodha"
        return "dhan"

    @staticmethod
    def _zerodha_login_url(api_key: object) -> str | None:
        key = str(api_key or "").strip()
        if not key:
            return None
        return f"https://kite.zerodha.com/connect/login?v=3&api_key={key}"

    @staticmethod
    def _normalize_nifty_option_trade_mode(value: object) -> str:
        normalized = str(value or "selling").strip().lower()
        if normalized in {"buy", "buying", "option-buying"}:
            return "buying"
        return "selling"

    @staticmethod
    def _normalize_stock_execution_mode(value: object) -> str:
        normalized = str(value or "cash").strip().lower()
        if normalized in {"option", "options", "stock-option", "stock_option"}:
            return "option"
        if normalized in {"future", "futures", "stock-future", "stock_future"}:
            return "future"
        return "cash"

    @staticmethod
    def _normalize_nifty_trade_bias(value: object) -> str:
        normalized = str(value or "both").strip().lower()
        if normalized in {"long", "buy", "bullish", "ce", "call"}:
            return "long"
        if normalized in {"short", "sell", "bearish", "pe", "put"}:
            return "short"
        return "both"

    @staticmethod
    def _coerce_bool(value: object, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        normalized = str(value).strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        return default

    @staticmethod
    def _coerce_float(value: object, default: float, *, minimum: float | None = None) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            parsed = float(default)
        if minimum is not None:
            parsed = max(parsed, minimum)
        return round(parsed, 2)
