from __future__ import annotations

import asyncio
import inspect
import threading
import time
from collections.abc import Callable
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


class DhanMarketFeedAdapter:
    """Background-thread wrapper around the Dhan live market feed SDK."""

    def __init__(
        self,
        client_id: str,
        access_token: str,
        instruments: list[tuple[Any, str, Any]],
        reconnect_delay: float = 3.0,
    ) -> None:
        self.client_id = client_id
        self.access_token = access_token
        self.instruments = instruments
        self.reconnect_delay = reconnect_delay
        self.connected = False
        self._feed = None
        self._loop = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._packet_callback: Callable[[dict[str, Any]], None] | None = None
        self._status_callback: Callable[[str, str | None], None] | None = None

    def start(
        self,
        packet_callback: Callable[[dict[str, Any]], None],
        status_callback: Callable[[str, str | None], None] | None = None,
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
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        self.connected = False
        self._notify_status("disconnected", None)

    def subscribe_symbols(self, instruments: list[tuple[Any, str, Any]]) -> None:
        if not instruments or not self._feed:
            return
        subscribe = getattr(self._feed, "subscribe_symbols", None)
        if callable(subscribe):
            subscribe(instruments)

    def unsubscribe_symbols(self, instruments: list[tuple[Any, str, Any]]) -> None:
        if not instruments or not self._feed:
            return
        unsubscribe = getattr(self._feed, "unsubscribe_symbols", None)
        if callable(unsubscribe):
            unsubscribe(instruments)

    def _notify_status(self, status: str, message: str | None) -> None:
        if self._status_callback:
            self._status_callback(status, message)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            try:
                self._notify_status("connecting", "Connecting to Dhan market feed.")
                self._feed = self._create_feed()
                self._feed.run_forever()
                self.connected = True
                self._notify_status("connected", "Live feed connected.")
                while not self._stop_event.is_set():
                    packet = self._feed.get_data()
                    if getattr(self._feed, "on_close", False):
                        raise RuntimeError("Dhan market feed reported a server-side disconnection.")
                    if isinstance(packet, str):
                        self._notify_status("connected", packet)
                        continue
                    if packet and self._packet_callback:
                        self._packet_callback(packet)
            except Exception as exc:
                self.connected = False
                self._notify_status("error", str(exc))
                if self._stop_event.is_set():
                    break
                time.sleep(self.reconnect_delay)
            finally:
                self._safe_disconnect(loop)
                self._feed = None
                self.connected = False
                try:
                    loop.close()
                except Exception:
                    pass
                self._loop = None
        self._notify_status("disconnected", None)

    def _create_feed(self):
        if MarketFeed is not None and DhanContext is not None:
            context = DhanContext(self.client_id, self.access_token)
            return MarketFeed(context, self.instruments, version="v2")
        if LegacyDhanFeed is not None:
            return LegacyDhanFeed(
                self.client_id,
                self.access_token,
                self.instruments,
                version="v2",
            )
        if legacy_marketfeed is not None:
            return legacy_marketfeed.DhanFeed(
                self.client_id,
                self.access_token,
                self.instruments,
                version="v2",
            )
        raise RuntimeError("dhanhq package is not available in this environment")

    def _safe_disconnect(self, loop: asyncio.AbstractEventLoop) -> None:
        if not self._feed:
            return
        close_connection = getattr(self._feed, "close_connection", None)
        if callable(close_connection):
            try:
                close_connection()
            except Exception:
                pass
        disconnect = getattr(self._feed, "disconnect", None)
        if not callable(disconnect):
            return
        try:
            result = disconnect()
            if inspect.isawaitable(result):
                loop.run_until_complete(result)
        except Exception:
            pass


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
