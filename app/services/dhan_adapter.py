from __future__ import annotations

import asyncio
import inspect
import logging
import threading
import time
from concurrent.futures import TimeoutError as FutureTimeoutError
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

try:  # pragma: no cover - depends on installed SDK version
    from dhanhq import DhanContext, MarketFeed
except Exception:  # pragma: no cover - optional runtime dependency path
    DhanContext = None
    MarketFeed = None

try:  # pragma: no cover - dhanhq 2.0.2 exports marketfeed / DhanFeed
    from dhanhq import marketfeed as legacy_marketfeed
except Exception:  # pragma: no cover - optional runtime dependency path
    legacy_marketfeed = None

try:  # pragma: no cover - dhanhq 2.0.2 exports DhanFeed at package root
    from dhanhq import DhanFeed as LegacyDhanFeed
except Exception:  # pragma: no cover - optional runtime dependency path
    LegacyDhanFeed = None

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class FeedStatusEvent:
    status: str
    message: str | None = None
    retry_attempt: int = 0
    next_retry_at: datetime | None = None


class DhanMarketFeedAdapter:
    """Background-thread wrapper around the Dhan live market feed SDK."""

    _cooldown_lock = threading.Lock()
    _rate_limit_until_by_client: dict[str, datetime] = {}
    _active_client_lock = threading.Lock()
    _active_clients: set[str] = set()

    def __init__(
        self,
        client_id: str,
        access_token: str,
        instruments: list[tuple[Any, str, Any]],
        reconnect_delay: float = 3.0,
        max_reconnect_delay: float = 300.0,
        rate_limit_delay: float = 60.0,
        stale_packet_timeout: float = 45.0,
        connect_timeout: float = 20.0,
    ) -> None:
        self.client_id = client_id
        self.access_token = access_token
        self.reconnect_delay = reconnect_delay
        self.max_reconnect_delay = max_reconnect_delay
        self.rate_limit_delay = rate_limit_delay
        self.stale_packet_timeout = stale_packet_timeout
        self.connect_timeout = connect_timeout
        self.connected = False
        self.status = "disconnected"
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._packet_callback: Callable[[dict[str, Any]], None] | None = None
        self._status_callback: Callable[[str, str | None, int, datetime | None], None] | None = None
        self._feed_lock = threading.Lock()
        self._feed: Any = None
        self._sdk_call_lock = threading.Lock()
        self._instrument_lock = threading.Lock()
        self.instruments = _merge_instruments([], instruments)
        self._retry_attempt = 0
        self._connected_notified = False
        self._sdk_closed = False
        self._last_sdk_error: Exception | None = None
        self._last_packet_monotonic: float | None = None
        self._connection_attempt_started_at: datetime | None = None
        self._feed_class_name: str | None = None

    def start(
        self,
        packet_callback: Callable[[dict[str, Any]], None],
        status_callback: Callable[[str, str | None, int, datetime | None], None] | None = None,
    ) -> None:
        if self._thread and self._thread.is_alive():
            return
        with self._active_client_lock:
            if self.client_id in self._active_clients:
                self._packet_callback = packet_callback
                self._status_callback = status_callback
                self._notify_status(
                    "cooldown",
                    "A Dhan market feed is already active for this client in this app process. Reuse or disconnect it before opening another socket.",
                )
                return
            self._active_clients.add(self.client_id)
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
        with self._active_client_lock:
            self._active_clients.discard(self.client_id)
        self._notify_status("disconnected", None)

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and not self._stop_event.is_set()

    @classmethod
    def cooldown_until_for_client(cls, client_id: str) -> datetime | None:
        with cls._cooldown_lock:
            cooldown_until = cls._rate_limit_until_by_client.get(str(client_id))
        if cooldown_until is None or cooldown_until <= _utcnow():
            return None
        return cooldown_until

    def subscribe_symbols(self, instruments: list[tuple[Any, str, Any]]) -> None:
        if not instruments:
            return
        with self._instrument_lock:
            self.instruments = _merge_instruments(self.instruments, instruments)
        with self._feed_lock:
            feed = self._feed
        if not feed or not self.connected:
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
        if not feed or not self.connected:
            return
        unsubscribe = getattr(feed, "unsubscribe_symbols", None)
        if callable(unsubscribe):
            self._call_feed_method(unsubscribe, instruments)

    def _call_feed_method(self, method: Callable[[list[tuple[Any, str, Any]]], Any], instruments: list[tuple[Any, str, Any]]) -> None:
        if not instruments:
            return
        for batch in _chunk_instruments(instruments, 100):
            try:
                with self._sdk_call_lock:
                    result = method(batch)
                    if inspect.isawaitable(result):
                        with self._feed_lock:
                            feed = self._feed
                        self._run_feed_awaitable(result, feed=feed, loop=self._loop)
            except Exception as exc:
                self._notify_status("connected", f"Dhan live feed subscription warning: {exc}")

    def _notify_status(
        self,
        status: str,
        message: str | None,
        retry_attempt: int = 0,
        next_retry_at: datetime | None = None,
    ) -> None:
        message = self._append_debug_detail(status, message, retry_attempt, next_retry_at)
        self.status = status
        if self._status_callback:
            self._status_callback(status, message, retry_attempt, next_retry_at)

    def _append_debug_detail(
        self,
        status: str,
        message: str | None,
        retry_attempt: int,
        next_retry_at: datetime | None,
    ) -> str | None:
        if status not in {"connecting", "reconnecting", "cooldown", "error"}:
            return message
        parts = self._debug_detail_parts(retry_attempt, next_retry_at)
        if not parts:
            return message
        detail = "Debug: " + "; ".join(parts) + "."
        return f"{message}\n{detail}" if message else detail

    def _debug_detail_parts(self, retry_attempt: int, next_retry_at: datetime | None) -> list[str]:
        with self._instrument_lock:
            instruments = list(self.instruments)
        first_ids = [str(item[1]) for item in instruments[:8]]
        with self._active_client_lock:
            active_client_count = len(self._active_clients)
        feed_class = self._feed_class_name or self._available_sdk_label()
        parts = [
            f"sdk={feed_class}",
            f"url={self._market_feed_url()}",
            f"initial_subscriptions={len(instruments)}",
            f"first_security_ids={','.join(first_ids) if first_ids else '-'}",
            f"retry_attempt={retry_attempt}",
            f"connect_timeout={int(self.connect_timeout)}s",
            f"active_clients_in_process={active_client_count}",
        ]
        if self._connection_attempt_started_at is not None:
            parts.append(f"attempt_started={self._connection_attempt_started_at.isoformat()}")
            parts.append(f"attempt_started_ist={_format_ist(self._connection_attempt_started_at)}")
        if next_retry_at is not None:
            parts.append(f"next_retry_at={next_retry_at.isoformat()}")
            parts.append(f"next_retry_at_ist={_format_ist(next_retry_at)}")
        return parts

    def _available_sdk_label(self) -> str:
        if MarketFeed is not None and DhanContext is not None:
            return "MarketFeed"
        if LegacyDhanFeed is not None:
            return "LegacyDhanFeed"
        if legacy_marketfeed is not None:
            return "legacy_marketfeed.DhanFeed"
        return "missing"

    def _market_feed_url(self) -> str:
        for candidate in (MarketFeed, LegacyDhanFeed, getattr(legacy_marketfeed, "DhanFeed", None) if legacy_marketfeed else None):
            if candidate is None:
                continue
            url = getattr(candidate, "market_feed_wss", None)
            if url:
                return str(url)
        return "unknown"

    def _run(self) -> None:
        while not self._stop_event.is_set():
            cooldown_until = self._rate_limit_cooldown_until()
            if cooldown_until is not None:
                delay_seconds = max(0.0, (cooldown_until - _utcnow()).total_seconds())
                if delay_seconds > 0:
                    self._notify_status(
                        "cooldown",
                        f"Dhan websocket is cooling down after rate limit. Retrying in {int(delay_seconds)}s.",
                        self._retry_attempt,
                        cooldown_until,
                    )
                    if self._stop_event.wait(delay_seconds):
                        break
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            try:
                self._connected_notified = False
                self._sdk_closed = False
                self._last_sdk_error = None
                self._connection_attempt_started_at = _utcnow()
                connect_status = "connecting" if self._retry_attempt == 0 else "reconnecting"
                connect_message = "Connecting to Dhan market feed." if self._retry_attempt == 0 else "Reconnecting to Dhan market feed."
                self._notify_status(connect_status, connect_message, self._retry_attempt)
                feed = self._create_feed()
                if hasattr(feed, "loop"):
                    feed.loop = loop
                with self._feed_lock:
                    self._feed = feed
                self._start_feed_on_loop(feed, loop)
                if self._last_sdk_error is not None:
                    raise self._last_sdk_error
                self._notify_connected()
                self._last_packet_monotonic = time.monotonic()
                while not self._stop_event.is_set():
                    packet = self._receive_feed_packet(feed)
                    if self._feed_reported_close():
                        raise RuntimeError("Dhan market feed reported a server-side disconnection.")
                    self._dispatch_sdk_message(packet)
                    self._last_packet_monotonic = time.monotonic()
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
        with self._active_client_lock:
            self._active_clients.discard(self.client_id)

    def _create_feed(self):
        with self._instrument_lock:
            instruments = list(self.instruments)
        logger.info(
            "Creating Dhan market feed. Instruments=%d Retry=%d",
            len(instruments),
            self._retry_attempt,
        )
        if MarketFeed is not None and DhanContext is not None:
            context = DhanContext(self.client_id, self.access_token)
            feed = MarketFeed(
                context,
                instruments,
                version="v2",
            )
            self._feed_class_name = "MarketFeed"
            self._bind_feed_callbacks(feed)
            return feed
        if LegacyDhanFeed is not None:
            feed = LegacyDhanFeed(
                self.client_id,
                self.access_token,
                instruments,
                version="v2",
            )
            self._feed_class_name = "LegacyDhanFeed"
            self._bind_feed_callbacks(feed)
            return feed
        if legacy_marketfeed is not None:
            feed = legacy_marketfeed.DhanFeed(
                self.client_id,
                self.access_token,
                instruments,
                version="v2",
            )
            self._feed_class_name = "legacy_marketfeed.DhanFeed"
            self._bind_feed_callbacks(feed)
            return feed
        raise RuntimeError("dhanhq>=2.0.2,<2.3 with MarketFeed or DhanFeed is required for Dhan live feed.")

    def _bind_feed_callbacks(self, feed: Any) -> None:
        for attr_name, callback in (
            ("on_connect", self._handle_sdk_connect),
            ("on_message", self._handle_sdk_message),
            ("on_error", self._handle_sdk_error),
            ("on_close", self._handle_sdk_close),
            ("on_ticks", self._handle_sdk_message),
        ):
            try:
                setattr(feed, attr_name, callback)
            except Exception:
                continue

    def _start_feed_on_loop(self, feed: Any, loop: asyncio.AbstractEventLoop) -> None:
        connect = getattr(feed, "connect", None)
        if callable(connect) and inspect.iscoroutinefunction(connect):
            with self._sdk_call_lock:
                loop.run_until_complete(asyncio.wait_for(connect(), timeout=self.connect_timeout))
            return
        run_forever = getattr(feed, "run_forever", None)
        if callable(run_forever):
            with self._sdk_call_lock:
                run_forever()
            return
        if callable(connect):
            with self._sdk_call_lock:
                result = connect()
                if inspect.isawaitable(result):
                    loop.run_until_complete(asyncio.wait_for(result, timeout=self.connect_timeout))
            return
        raise RuntimeError("Dhan feed object does not expose run_forever() or connect().")

    def _receive_feed_packet(self, feed: Any) -> Any:
        get_data = getattr(feed, "get_data", None)
        get_instrument_data = getattr(feed, "get_instrument_data", None)
        if callable(get_data):
            if callable(get_instrument_data):
                loop = self._loop or getattr(feed, "loop", None)
                if loop is not None:
                    with self._sdk_call_lock:
                        result = get_instrument_data()
                        if inspect.isawaitable(result):
                            return loop.run_until_complete(
                                asyncio.wait_for(result, timeout=self.stale_packet_timeout)
                            )
                        return result
            with self._sdk_call_lock:
                return get_data()
        if callable(get_instrument_data):
            with self._sdk_call_lock:
                result = get_instrument_data()
                if inspect.isawaitable(result):
                    loop = self._loop or getattr(feed, "loop", None)
                    if loop is None:
                        return asyncio.run(asyncio.wait_for(result, timeout=self.stale_packet_timeout))
                    return loop.run_until_complete(asyncio.wait_for(result, timeout=self.stale_packet_timeout))
                return result
        raise RuntimeError("Dhan feed object does not expose get_instrument_data() or get_data().")

    def _notify_connected(self) -> None:
        if self._connected_notified:
            return
        self.connected = True
        self._retry_attempt = 0
        self._connected_notified = True
        logger.info("Dhan websocket connected.")
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
            message = str(maybe_error).strip() or maybe_error.__class__.__name__
            lowered = message.lower()
            if _should_stop_sdk_retry_loop(lowered):
                self._last_sdk_error = maybe_error
                self._request_sdk_loop_stop()
                return
            self._notify_status("connected", f"Live feed warning: {maybe_error}")

    def _request_sdk_loop_stop(self) -> None:
        with self._feed_lock:
            feed = self._feed
        if not feed:
            return
        try:
            setattr(feed, "_running", False)
        except Exception:
            pass
        # Do not call the SDK's async disconnect from inside its error callback.
        # The outer retry loop owns cleanup in _safe_disconnect(); calling it here
        # with wait=False can leave MarketFeed.disconnect() pending when the loop
        # is closed after a 429/error handshake.

    def _handle_sdk_close(self, *_args) -> None:
        self._sdk_closed = True
        self._request_sdk_loop_stop()

    def _feed_reported_close(self) -> bool:
        return self._sdk_closed

    def _classify_error(self, exc: Exception, retry_attempt: int) -> FeedStatusEvent:
        message = str(exc).strip() or exc.__class__.__name__
        lowered = message.lower()
        if _is_terminal_auth_error(lowered):
            return FeedStatusEvent(status="error", message=message)
        if isinstance(exc, TimeoutError) or lowered in {"timeouterror", "asyncio.timeouterror"}:
            message = f"No Dhan market-feed packet arrived for {int(self.stale_packet_timeout)}s"
            lowered = message.lower()
        delay = _retry_delay_seconds(
            retry_attempt=retry_attempt,
            base_delay=self.reconnect_delay,
            max_delay=self.max_reconnect_delay,
            rate_limit_delay=self.rate_limit_delay,
            message=lowered,
        )
        if "http 429" in lowered or "too many requests" in lowered:
            detail = f"Dhan websocket rate-limited the connection (HTTP 429). Retrying in {int(delay)}s."
            self._set_rate_limit_cooldown(_utcnow() + timedelta(seconds=delay))
        else:
            detail = f"{message}. Retrying in {int(delay)}s."
        logger.warning("Dhan websocket reconnect scheduled: %s", detail)
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
        self._disconnect_feed(feed, loop=loop, wait=not loop.is_running())

    def _disconnect_feed(
        self,
        feed: Any,
        *,
        loop: asyncio.AbstractEventLoop | None = None,
        wait: bool = False,
    ) -> None:
        disconnect = getattr(feed, "disconnect", None)
        if callable(disconnect):
            try:
                result = disconnect()
                if inspect.isawaitable(result):
                    self._run_feed_awaitable(result, feed=feed, loop=loop, wait=wait)
                return
            except Exception:
                return
        close_connection = getattr(feed, "close_connection", None)
        if not callable(close_connection):
            return
        try:
            result = close_connection()
            if inspect.isawaitable(result):
                self._run_feed_awaitable(result, feed=feed, loop=loop, wait=wait)
        except Exception:
            pass

    def _run_feed_awaitable(
        self,
        awaitable: Any,
        *,
        feed: Any,
        loop: asyncio.AbstractEventLoop | None = None,
        wait: bool = False,
    ) -> None:
        target_loop = loop or getattr(feed, "loop", None)
        if target_loop is not None and target_loop.is_running():
            future = asyncio.run_coroutine_threadsafe(awaitable, target_loop)
            if wait:
                try:
                    future.result(timeout=2)
                except FutureTimeoutError:
                    future.cancel()
            return
        if target_loop is not None:
            target_loop.run_until_complete(awaitable)
            return
        asyncio.run(awaitable)

    def _close_active_feed(self) -> None:
        loop = self._loop
        if loop is None:
            return
        with self._feed_lock:
            feed = self._feed
        if not feed:
            return
        self._disconnect_feed(feed, loop=loop, wait=True)

    def _rate_limit_cooldown_until(self) -> datetime | None:
        with self._cooldown_lock:
            cooldown_until = self._rate_limit_until_by_client.get(self.client_id)
        if cooldown_until is None or cooldown_until <= _utcnow():
            return None
        return cooldown_until

    def _set_rate_limit_cooldown(self, cooldown_until: datetime) -> None:
        with self._cooldown_lock:
            current = self._rate_limit_until_by_client.get(self.client_id)
            if current is None or cooldown_until > current:
                self._rate_limit_until_by_client[self.client_id] = cooldown_until


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
    raise RuntimeError("dhanhq>=2.0.2,<2.3 with MarketFeed or DhanFeed is required for Dhan live feed.")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _format_ist(value: datetime) -> str:
    return value.astimezone(timezone(timedelta(hours=5, minutes=30))).strftime("%Y-%m-%d %H:%M:%S IST")


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


def _should_stop_sdk_retry_loop(message: str) -> bool:
    return (
        "http 429" in message
        or "too many requests" in message
        or _is_terminal_auth_error(message)
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


def _chunk_instruments(
    instruments: list[tuple[Any, str, Any]],
    size: int,
) -> list[list[tuple[Any, str, Any]]]:
    if size <= 0:
        return [instruments]
    return [instruments[index:index + size] for index in range(0, len(instruments), size)]
