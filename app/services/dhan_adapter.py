from __future__ import annotations

import asyncio
import inspect
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

try:  # pragma: no cover - depends on installed SDK version
    from dhanhq import DhanContext, MarketFeed
except Exception:  # pragma: no cover - optional runtime dependency path
    DhanContext = None
    MarketFeed = None

try:  # pragma: no cover - depends on installed SDK version
    from dhanhq import marketfeed as legacy_marketfeed
except Exception:  # pragma: no cover - optional runtime dependency path
    legacy_marketfeed = None

try:  # pragma: no cover - depends on installed SDK version
    from dhanhq import DhanFeed as LegacyDhanFeed
except Exception:  # pragma: no cover - optional runtime dependency path
    LegacyDhanFeed = None


@dataclass(slots=True)
class FeedStatusEvent:
    status: str
    message: str | None = None
    retry_attempt: int = 0
    next_retry_at: datetime | None = None


class DhanMarketFeedAdapter:
    """Background-thread wrapper around the Dhan live market feed SDK."""

    def __init__(
        self,
        client_id: str,
        access_token: str,
        instruments: list[tuple[Any, str, Any]],
        reconnect_delay: float = 3.0,
        max_reconnect_delay: float = 300.0,
        rate_limit_delay: float = 60.0,
    ) -> None:
        self.client_id = client_id
        self.access_token = access_token
        self.reconnect_delay = reconnect_delay
        self.max_reconnect_delay = max_reconnect_delay
        self.rate_limit_delay = rate_limit_delay
        self.connected = False
        self.status = "disconnected"
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._packet_callback: Callable[[dict[str, Any]], None] | None = None
        self._status_callback: Callable[[str, str | None, int, datetime | None], None] | None = None
        self._feed_lock = threading.Lock()
        self._feed: Any = None
        self._instrument_lock = threading.Lock()
        self.instruments = list(instruments)
        self._retry_attempt = 0
        self._connected_notified = False

    def start(
        self,
        packet_callback: Callable[[dict[str, Any]], None],
        status_callback: Callable[[str, str | None, int, datetime | None], None] | None = None,
    ) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._packet_callback = packet_callback
        self._status_callback = status_callback
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="dhan-live-feed", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._close_active_feed()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        self.connected = False
        self.status = "disconnected"
        self._connected_notified = False
        self._notify_status("disconnected", None)

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and not self._stop_event.is_set()

    def subscribe_symbols(self, instruments: list[tuple[Any, str, Any]]) -> None:
        if not instruments:
            return
        with self._instrument_lock:
            self.instruments = _merge_instruments(self.instruments, instruments)
        with self._feed_lock:
            feed = self._feed
        if not feed:
            return
        subscribe = getattr(feed, "subscribe_symbols", None)
        if callable(subscribe):
            self._call_feed_method(subscribe, instruments)

    def unsubscribe_symbols(self, instruments: list[tuple[Any, str, Any]]) -> None:
        if not instruments:
            return
        with self._instrument_lock:
            self.instruments = _remove_instruments(self.instruments, instruments)
        with self._feed_lock:
            feed = self._feed
        if not feed:
            return
        unsubscribe = getattr(feed, "unsubscribe_symbols", None)
        if callable(unsubscribe):
            self._call_feed_method(unsubscribe, instruments)

    def _call_feed_method(self, method: Callable[[list[tuple[Any, str, Any]]], Any], instruments: list[tuple[Any, str, Any]]) -> None:
        if not instruments:
            return
        try:
            result = method(instruments)
        except Exception as exc:
            self._notify_status("connected", f"Dhan live feed subscription warning: {exc}")
            return
        if not inspect.isawaitable(result):
            return
        loop = self._loop
        if loop is not None and loop.is_running():
            asyncio.run_coroutine_threadsafe(result, loop)
            return
        try:
            asyncio.run(result)
        except RuntimeError:
            try:
                result.close()
            except Exception:
                pass

    def _notify_status(
        self,
        status: str,
        message: str | None,
        retry_attempt: int = 0,
        next_retry_at: datetime | None = None,
    ) -> None:
        self.status = status
        if self._status_callback:
            self._status_callback(status, message, retry_attempt, next_retry_at)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            try:
                self._connected_notified = False
                connect_status = "connecting" if self._retry_attempt == 0 else "reconnecting"
                connect_message = "Connecting to Dhan market feed." if self._retry_attempt == 0 else "Reconnecting to Dhan market feed."
                self._notify_status(connect_status, connect_message, self._retry_attempt)
                feed = self._create_feed()
                with self._feed_lock:
                    self._feed = feed
                runner = getattr(feed, "run", None)
                if callable(runner):
                    runner()
                    if not self._stop_event.is_set():
                        raise RuntimeError("Dhan market feed loop exited unexpectedly.")
                else:
                    feed.run_forever()
                    self._notify_connected()
                    while not self._stop_event.is_set():
                        packet = feed.get_data()
                        if getattr(feed, "on_close", False):
                            raise RuntimeError("Dhan market feed reported a server-side disconnection.")
                        self._dispatch_sdk_message(packet)
            except Exception as exc:
                self.connected = False
                if self._stop_event.is_set():
                    break
                self._retry_attempt += 1
                status_event = self._classify_error(exc, self._retry_attempt)
                self._notify_status(
                    status_event.status,
                    status_event.message,
                    status_event.retry_attempt,
                    status_event.next_retry_at,
                )
                if status_event.status == "error":
                    break
                delay_seconds = (
                    max(0.0, (status_event.next_retry_at - _utcnow()).total_seconds())
                    if status_event.next_retry_at is not None
                    else self.reconnect_delay
                )
                if self._stop_event.wait(delay_seconds):
                    break
            finally:
                self._safe_disconnect(loop)
                with self._feed_lock:
                    self._feed = None
                self.connected = False
                try:
                    loop.close()
                except Exception:
                    pass
                self._loop = None
        self._notify_status("disconnected", None)

    def _create_feed(self):
        with self._instrument_lock:
            instruments = list(self.instruments)
        if MarketFeed is not None and DhanContext is not None:
            context = DhanContext(self.client_id, self.access_token)
            return MarketFeed(
                context,
                instruments,
                version="v2",
                on_connect=self._handle_sdk_connect,
                on_message=self._handle_sdk_message,
                on_error=self._handle_sdk_error,
                on_close=self._handle_sdk_close,
            )
        if LegacyDhanFeed is not None:
            feed = LegacyDhanFeed(
                self.client_id,
                self.access_token,
                instruments,
                version="v2",
            )
            self._bind_feed_callbacks(feed)
            return feed
        if legacy_marketfeed is not None:
            feed = legacy_marketfeed.DhanFeed(
                self.client_id,
                self.access_token,
                instruments,
                version="v2",
            )
            self._bind_feed_callbacks(feed)
            return feed
        raise RuntimeError("dhanhq package is not available in this environment")

    def _bind_feed_callbacks(self, feed: Any) -> None:
        for attr_name, callback in (
            ("on_connect", self._handle_sdk_connect),
            ("on_message", self._handle_sdk_message),
            ("on_error", self._handle_sdk_error),
            ("on_close", self._handle_sdk_close),
        ):
            try:
                setattr(feed, attr_name, callback)
            except Exception:
                continue

    def _notify_connected(self) -> None:
        if self._connected_notified:
            return
        self.connected = True
        self._retry_attempt = 0
        self._connected_notified = True
        self._notify_status("connected", "Live feed connected.")

    def _handle_sdk_connect(self, *_args) -> None:
        self._notify_connected()

    def _handle_sdk_message(self, *_args) -> None:
        if not _args:
            return
        payload = _args[-1]
        self._dispatch_sdk_message(payload)

    def _dispatch_sdk_message(self, payload: Any) -> None:
        if isinstance(payload, str):
            self._notify_connected()
            self._notify_status("connected", payload)
            return
        if isinstance(payload, dict) and self._packet_callback:
            self._notify_connected()
            self._packet_callback(payload)

    def _handle_sdk_error(self, *_args) -> None:
        if not _args:
            return
        maybe_error = _args[-1]
        if isinstance(maybe_error, Exception):
            self._notify_status("connected", f"Live feed warning: {maybe_error}")

    def _handle_sdk_close(self, *_args) -> None:
        return

    def _classify_error(self, exc: Exception, retry_attempt: int) -> FeedStatusEvent:
        message = str(exc).strip() or exc.__class__.__name__
        lowered = message.lower()
        if _is_terminal_auth_error(lowered):
            return FeedStatusEvent(status="error", message=message)
        delay = _retry_delay_seconds(
            retry_attempt=retry_attempt,
            base_delay=self.reconnect_delay,
            max_delay=self.max_reconnect_delay,
            rate_limit_delay=self.rate_limit_delay,
            message=lowered,
        )
        if "http 429" in lowered or "too many requests" in lowered:
            detail = f"Dhan websocket rate-limited the connection (HTTP 429). Retrying in {int(delay)}s."
        else:
            detail = f"{message}. Retrying in {int(delay)}s."
        return FeedStatusEvent(
            status="reconnecting",
            message=detail,
            retry_attempt=retry_attempt,
            next_retry_at=_utcnow() + timedelta(seconds=delay),
        )

    def _safe_disconnect(self, loop: asyncio.AbstractEventLoop) -> None:
        with self._feed_lock:
            feed = self._feed
        if not feed:
            return
        close_connection = getattr(feed, "close_connection", None)
        if callable(close_connection):
            try:
                close_connection()
            except Exception:
                pass
        disconnect = getattr(feed, "disconnect", None)
        if not callable(disconnect):
            return
        try:
            result = disconnect()
            if inspect.isawaitable(result):
                loop.run_until_complete(result)
        except Exception:
            pass

    def _close_active_feed(self) -> None:
        loop = self._loop
        if loop is None:
            return
        self._safe_disconnect(loop)


def resolve_default_quote_subscription(security_id: str) -> tuple[Any, str, Any]:
    return resolve_quote_subscription(security_id, "IDX_I")


def resolve_quote_subscription(security_id: str, exchange_segment: str) -> tuple[Any, str, Any]:
    segment_aliases = {
        "IDX_I": "IDX",
        "NSE_EQ": "NSE",
        "NSE_FNO": "NSE_FNO",
        "NSE_CURR": "NSE_CURR",
        "BSE_EQ": "BSE",
        "BSE_FNO": "BSE_FNO",
        "MCX_COMM": "MCX",
    }
    segment_name = segment_aliases.get(exchange_segment.strip().upper())
    if not segment_name:
        raise RuntimeError(f"Unsupported Dhan exchange segment for live feed subscription: {exchange_segment}")

    if MarketFeed is not None:
        return (getattr(MarketFeed, segment_name), security_id, getattr(MarketFeed, "Quote"))
    if legacy_marketfeed is not None:
        return (getattr(legacy_marketfeed, segment_name), security_id, getattr(legacy_marketfeed, "Quote"))
    if LegacyDhanFeed is not None:
        return (getattr(LegacyDhanFeed, segment_name), security_id, getattr(LegacyDhanFeed, "Quote"))
    raise RuntimeError("dhanhq package is not available in this environment")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _retry_delay_seconds(
    *,
    retry_attempt: int,
    base_delay: float,
    max_delay: float,
    rate_limit_delay: float,
    message: str,
) -> float:
    if "http 429" in message or "too many requests" in message:
        rate_delay = rate_limit_delay * (2 ** max(0, retry_attempt - 1))
        return min(max_delay, max(rate_limit_delay, rate_delay, base_delay * max(1, retry_attempt)))
    exponential_delay = base_delay * (2 ** max(0, retry_attempt - 1))
    return min(max_delay, exponential_delay)


def _is_terminal_auth_error(message: str) -> bool:
    return any(
        marker in message
        for marker in (
            "access token is invalid",
            "access token is expired",
            "authentication failed",
            "check client id",
            "401",
            "403",
            "forbidden",
        )
    )


def _instrument_key(instrument: tuple[Any, str, Any]) -> tuple[str, str, str]:
    exchange, security_id, packet_type = instrument
    return (str(exchange), str(security_id), str(packet_type))


def _merge_instruments(
    current: list[tuple[Any, str, Any]],
    updates: list[tuple[Any, str, Any]],
) -> list[tuple[Any, str, Any]]:
    merged: dict[tuple[str, str, str], tuple[Any, str, Any]] = {
        _instrument_key(instrument): instrument for instrument in current
    }
    for instrument in updates:
        merged[_instrument_key(instrument)] = instrument
    return list(merged.values())


def _remove_instruments(
    current: list[tuple[Any, str, Any]],
    removals: list[tuple[Any, str, Any]],
) -> list[tuple[Any, str, Any]]:
    removal_keys = {_instrument_key(instrument) for instrument in removals}
    return [instrument for instrument in current if _instrument_key(instrument) not in removal_keys]
