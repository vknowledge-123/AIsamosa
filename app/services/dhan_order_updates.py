from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from collections.abc import Callable

try:  # pragma: no cover - depends on installed SDK version
    from dhanhq import DhanContext, OrderUpdate
except Exception:  # pragma: no cover - optional runtime dependency path
    DhanContext = None
    OrderUpdate = None


class DhanOrderUpdateAdapter:
    """Background-thread wrapper around the official DhanHQ-py order update client."""

    def __init__(self, client_id: str, access_token: str, reconnect_delay: float = 3.0) -> None:
        self.client_id = client_id
        self.access_token = access_token
        self.reconnect_delay = reconnect_delay
        self.connected = False
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._listen_task: asyncio.Task | None = None
        self._stop_event = threading.Event()
        self._update_callback: Callable[[dict], None] | None = None
        self._status_callback: Callable[[str, str | None], None] | None = None

    def start(
        self,
        update_callback: Callable[[dict], None],
        status_callback: Callable[[str, str | None], None] | None = None,
    ) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._update_callback = update_callback
        self._status_callback = status_callback
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="dhan-order-updates", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._cancel_listen_task)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        self.connected = False
        self._notify_status("disconnected", None)

    def _cancel_listen_task(self) -> None:
        if self._listen_task and not self._listen_task.done():
            self._listen_task.cancel()

    def _notify_status(self, status: str, message: str | None) -> None:
        if self._status_callback:
            self._status_callback(status, message)

    def _run(self) -> None:
        if DhanContext is None or OrderUpdate is None:
            self._notify_status("error", "DhanHQ-py OrderUpdate client is not available in this environment.")
            return
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._listen_forever())
        finally:
            pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
            for task in pending:
                task.cancel()
            with contextlib.suppress(Exception):
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            with contextlib.suppress(Exception):
                loop.close()
            self._loop = None
            self._listen_task = None
            self.connected = False

    async def _listen_forever(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._notify_status("connecting", "Connecting to Dhan order updates using DhanHQ-py.")
                dhan_context = DhanContext(self.client_id, self.access_token)
                order_client = OrderUpdate(dhan_context)
                order_client.on_update = self._handle_sdk_update
                self.connected = True
                self._notify_status("connected", "DhanHQ-py order update websocket connected.")
                self._listen_task = asyncio.create_task(order_client.connect_order_update())
                await self._listen_task
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self.connected = False
                self._notify_status("error", str(exc))
                if self._stop_event.is_set():
                    break
                await asyncio.sleep(self.reconnect_delay)
            finally:
                self.connected = False
                self._listen_task = None
        self._notify_status("disconnected", None)

    def _handle_sdk_update(self, packet: dict) -> None:
        self.connected = True
        if self._update_callback:
            self._update_callback(packet)
