from __future__ import annotations

import asyncio
import contextlib
import json
import threading
from collections.abc import Callable

try:  # pragma: no cover - depends on installed SDK version
    import websockets
except Exception:  # pragma: no cover - optional runtime dependency path
    websockets = None


class DhanOrderUpdateAdapter:
    """Background-thread wrapper around the official DhanHQ-py order update client."""

    order_feed_wss = "wss://api-order-update.dhan.co"

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
        if websockets is None:
            self._notify_status("error", "The websockets package is required for Dhan order updates.")
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
                self._notify_status("connecting", "Connecting to Dhan order updates.")
                self._listen_task = asyncio.create_task(self._connect_and_listen())
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

    async def _connect_and_listen(self) -> None:
        assert websockets is not None
        async with websockets.connect(self.order_feed_wss) as websocket:
            auth_message = {
                "LoginReq": {
                    "MsgCode": 42,
                    "ClientId": str(self.client_id),
                    "Token": str(self.access_token),
                },
                "UserType": "SELF",
            }
            await websocket.send(json.dumps(auth_message))
            self.connected = True
            self._notify_status("connected", "Dhan order update websocket connected.")
            async for message in websocket:
                if self._stop_event.is_set():
                    break
                packets = self._parse_order_update_message(message)
                if not packets:
                    self._notify_status("connected", "Skipped a malformed Dhan order update message.")
                    continue
                for packet in packets:
                    self._handle_sdk_update(packet)

    def _parse_order_update_message(self, message) -> list[dict]:
        if isinstance(message, bytes):
            message = message.decode("utf-8", errors="ignore")
        if not isinstance(message, str):
            return [message] if isinstance(message, dict) else []
        text = message.strip()
        if not text:
            return []

        decoder = json.JSONDecoder()
        packets: list[dict] = []
        index = 0
        while index < len(text):
            while index < len(text) and text[index].isspace():
                index += 1
            if index >= len(text):
                break
            try:
                value, next_index = decoder.raw_decode(text, index)
            except json.JSONDecodeError:
                line_end = text.find("\n", index)
                if line_end == -1:
                    return packets
                index = line_end + 1
                continue
            if isinstance(value, dict):
                packets.append(value)
            elif isinstance(value, list):
                packets.extend(item for item in value if isinstance(item, dict))
            index = next_index
        return packets

    def _handle_sdk_update(self, packet: dict) -> None:
        self.connected = True
        if self._update_callback:
            self._update_callback(packet)
