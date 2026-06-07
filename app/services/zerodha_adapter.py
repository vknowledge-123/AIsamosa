from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import datetime
from typing import Any

from app.services.zerodha_execution import ZerodhaExecutionError


class ZerodhaMarketFeedAdapter:
    """Background wrapper around KiteTicker that emits app-normalized quote packets."""

    def __init__(
        self,
        api_key: str,
        access_token: str,
        instruments: list[tuple[int, str, str]],
        *,
        order_update_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.api_key = api_key
        self.access_token = access_token
        self.connected = False
        self.status = "disconnected"
        self._ticker: Any = None
        self._lock = threading.RLock()
        self._packet_callback: Callable[[dict[str, Any]], None] | None = None
        self._status_callback: Callable[[str, str | None, int, datetime | None], None] | None = None
        self._order_update_callback = order_update_callback
        self._stop_event = threading.Event()
        self._tokens: dict[int, str] = {}
        self._modes: dict[int, str] = {}
        self.instruments = list(instruments)
        self._merge_instruments_locked(self.instruments)

    def start(
        self,
        packet_callback: Callable[[dict[str, Any]], None],
        status_callback: Callable[[str, str | None, int, datetime | None], None] | None = None,
    ) -> None:
        self._packet_callback = packet_callback
        self._status_callback = status_callback
        self._stop_event.clear()
        self._notify_status("connecting", "Connecting to Zerodha Kite websocket.")
        try:
            from kiteconnect import KiteTicker
        except Exception as exc:
            raise ZerodhaExecutionError("kiteconnect package is not installed. Run pip install -r requirements.txt.") from exc

        ticker = KiteTicker(self.api_key, self.access_token)
        ticker.on_connect = self._handle_connect
        ticker.on_ticks = self._handle_ticks
        ticker.on_close = self._handle_close
        ticker.on_error = self._handle_error
        ticker.on_reconnect = self._handle_reconnect
        ticker.on_noreconnect = self._handle_no_reconnect
        ticker.on_order_update = self._handle_order_update
        with self._lock:
            self._ticker = ticker
        ticker.connect(threaded=True)

    def stop(self) -> None:
        self._stop_event.set()
        with self._lock:
            ticker = self._ticker
            self._ticker = None
        if ticker is not None:
            try:
                ticker.close()
            except Exception:
                try:
                    ticker.stop()
                except Exception:
                    pass
        self.connected = False
        self._notify_status("disconnected", None)

    def is_running(self) -> bool:
        with self._lock:
            ticker = self._ticker
        if ticker is None or self._stop_event.is_set():
            return False
        checker = getattr(ticker, "is_connected", None)
        if callable(checker):
            try:
                return bool(checker())
            except Exception:
                return self.status in {"connecting", "connected", "reconnecting"}
        return self.status in {"connecting", "connected", "reconnecting"}

    def subscribe_symbols(self, instruments: list[tuple[int, str, str]]) -> None:
        if not instruments:
            return
        tokens = self._merge_instruments_locked(instruments)
        with self._lock:
            ticker = self._ticker
        if ticker is None or not tokens:
            return
        try:
            ticker.subscribe(tokens)
            self._apply_modes(ticker, tokens)
        except Exception as exc:
            self._notify_status("connected", f"Zerodha subscribe warning: {exc}")

    def unsubscribe_symbols(self, instruments: list[tuple[int, str, str]]) -> None:
        if not instruments:
            return
        tokens = [int(item[0]) for item in instruments if item and item[0]]
        with self._lock:
            for token in tokens:
                self._tokens.pop(token, None)
                self._modes.pop(token, None)
                self.instruments = [item for item in self.instruments if int(item[0]) != token]
            ticker = self._ticker
        if ticker is None or not tokens:
            return
        try:
            ticker.unsubscribe(tokens)
        except Exception as exc:
            self._notify_status("connected", f"Zerodha unsubscribe warning: {exc}")

    def _merge_instruments_locked(self, instruments: list[tuple[int, str, str]]) -> list[int]:
        tokens: list[int] = []
        with self._lock:
            existing = {int(item[0]): item for item in self.instruments if item and item[0]}
            for token_raw, security_id, mode in instruments:
                token = int(token_raw)
                existing[token] = (token, str(security_id), str(mode or "quote").lower())
                self._tokens[token] = str(security_id)
                self._modes[token] = str(mode or "quote").lower()
                tokens.append(token)
            self.instruments = list(existing.values())
        return tokens

    def _handle_connect(self, ws, _response) -> None:
        with self._lock:
            tokens = list(self._tokens.keys())
        if tokens:
            ws.subscribe(tokens)
            self._apply_modes(ws, tokens)
        self.connected = True
        self._notify_status("connected", "Zerodha Kite websocket connected.")

    def _apply_modes(self, ws, tokens: list[int]) -> None:
        quote_tokens = [token for token in tokens if self._modes.get(token) != "ltp"]
        ltp_tokens = [token for token in tokens if self._modes.get(token) == "ltp"]
        if quote_tokens:
            ws.set_mode(ws.MODE_QUOTE, quote_tokens)
        if ltp_tokens:
            ws.set_mode(ws.MODE_LTP, ltp_tokens)

    def _handle_ticks(self, _ws, ticks: list[dict[str, Any]]) -> None:
        callback = self._packet_callback
        if callback is None:
            return
        for tick in ticks or []:
            packet = self._normalize_tick(tick)
            if packet is not None:
                callback(packet)

    def _normalize_tick(self, tick: dict[str, Any]) -> dict[str, Any] | None:
        token = tick.get("instrument_token")
        if token in (None, ""):
            return None
        token_int = int(token)
        with self._lock:
            security_id = self._tokens.get(token_int)
        if not security_id:
            return None
        last_price = tick.get("last_price")
        if last_price in (None, ""):
            return None
        timestamp = tick.get("exchange_timestamp") or tick.get("last_trade_time") or datetime.now()
        volume = tick.get("volume_traded") or tick.get("volume") or 0
        return {
            "type": "Quote",
            "source": "zerodha-websocket",
            "security_id": str(security_id),
            "instrument_token": token_int,
            "LTP": float(last_price),
            "volume": volume,
            "OI": tick.get("oi"),
            "timestamp": timestamp,
        }

    def _handle_order_update(self, _ws, data: dict[str, Any]) -> None:
        if self._order_update_callback is None:
            return
        order_id = data.get("order_id") or data.get("orderId")
        status = data.get("status") or data.get("order_status") or data.get("orderStatus")
        average_price = data.get("average_price") or data.get("averageTradedPrice")
        self._order_update_callback(
            {
                "Data": {
                    "orderId": order_id,
                    "status": status,
                    "averageTradedPrice": average_price,
                    "raw": data,
                }
            }
        )

    def _handle_close(self, _ws, code, reason) -> None:
        self.connected = False
        if self._stop_event.is_set():
            self._notify_status("disconnected", None)
            return
        self._notify_status("reconnecting", f"Zerodha websocket closed: {code} {reason}")

    def _handle_error(self, _ws, code, reason) -> None:
        self.connected = False
        self._notify_status("reconnecting", f"Zerodha websocket error: {code} {reason}")

    def _handle_reconnect(self, _ws, attempts_count) -> None:
        self.connected = False
        self._notify_status("reconnecting", f"Zerodha websocket reconnect attempt {attempts_count}.", int(attempts_count or 0))

    def _handle_no_reconnect(self, _ws) -> None:
        self.connected = False
        self._notify_status("error", "Zerodha websocket could not reconnect.")

    def _notify_status(
        self,
        status: str,
        message: str | None,
        retry_attempt: int = 0,
        next_retry_at: datetime | None = None,
    ) -> None:
        self.status = status
        if self._status_callback is not None:
            self._status_callback(status, message, retry_attempt, next_retry_at)
