"""WebSocket bridge between Python agent and Chrome Extension.

The Python agent runs a WebSocket server. The Chrome Extension's
background.js connects as a client. Commands flow:

    Agent (Python) → WebSocket → Extension background.js
        → content.js (DOM ops) or chrome.tabs API (navigation/screenshots)
        → response back through WebSocket
"""

from __future__ import annotations

import asyncio
import base64
import json
import uuid
from pathlib import Path

import websockets
from websockets.asyncio.server import serve


class ExtensionBridge:
    """WebSocket server that communicates with the Chrome Extension."""

    def __init__(self, port: int = 8765):
        self.port = port
        self._ws = None
        self._server = None
        self._pending: dict[str, asyncio.Future] = {}
        self._connected = asyncio.Event()
        self._log_callback = None
        self._keepalive_task = None

    def on_log(self, callback):
        """Register a logging callback: callback(action, detail)"""
        self._log_callback = callback

    def _log(self, action: str, detail: str = ""):
        if self._log_callback:
            self._log_callback(action, detail)
        else:
            print(f"  [bridge] {action}: {detail}")

    async def start(self):
        """Start the WebSocket server and wait for extension to connect."""
        self._server = await serve(
            self._handle_connection,
            "localhost",
            self.port,
        )
        self._log("server_started", f"Listening on ws://localhost:{self.port}")

    async def wait_for_connection(self, timeout: float = 120):
        """Wait for the Chrome Extension to connect."""
        self._log("waiting", f"Waiting for extension to connect (timeout={timeout}s)...")
        try:
            await asyncio.wait_for(self._connected.wait(), timeout)
        except asyncio.TimeoutError:
            raise RuntimeError(
                f"Extension did not connect within {timeout}s. "
                "Make sure the extension is loaded and click 'Connect' in the popup."
            )
        self._log("extension_connected", "Chrome Extension connected")

    async def _handle_connection(self, ws):
        """Handle incoming WebSocket connection from extension."""
        # If we already have a connection, the old one is stale — replace it
        old_ws = self._ws
        self._ws = ws
        self._connected.set()

        # Cancel any pending futures from old connection (they'll never resolve)
        for msg_id, future in list(self._pending.items()):
            if not future.done():
                future.cancel()
        self._pending.clear()

        # Start keepalive pings to prevent MV3 service worker from sleeping
        if self._keepalive_task:
            self._keepalive_task.cancel()
        self._keepalive_task = asyncio.create_task(self._keepalive(ws))

        try:
            async for raw in ws:
                msg = json.loads(raw)

                if msg.get("type") == "event":
                    self._log("event", f"{msg.get('event')}: {json.dumps(msg.get('data', {}))}")
                    continue

                # Response to a pending command
                msg_id = msg.get("id")
                if msg_id and msg_id in self._pending:
                    future = self._pending[msg_id]
                    if not future.done():
                        future.set_result(msg)
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            # Only clear if this is still the active connection
            if self._ws is ws:
                self._ws = None
                self._connected.clear()
                self._log("disconnected", "Extension disconnected")

    async def _keepalive(self, ws):
        """Send periodic pings to keep the MV3 service worker alive."""
        try:
            while True:
                await asyncio.sleep(10)
                try:
                    pong = await ws.ping()
                    await asyncio.wait_for(pong, timeout=5)
                except Exception:
                    break
        except asyncio.CancelledError:
            pass

    async def _ensure_connected(self, timeout: float = 10):
        """Wait for connection if not currently connected."""
        if self._ws:
            return
        self._log("reconnecting", f"Waiting for extension reconnect ({timeout}s)...")
        try:
            await asyncio.wait_for(self._connected.wait(), timeout)
        except asyncio.TimeoutError:
            raise RuntimeError("Extension not connected and reconnect timed out")

    async def send_command(
        self, action: str, params: dict | None = None, timeout: float = 30,
        _retries: int = 3,
    ) -> dict:
        """Send a command to the extension and wait for response.

        Automatically retries on connection loss (MV3 service worker restarts).
        """
        last_error = None
        for attempt in range(_retries):
            try:
                await self._ensure_connected(timeout=15)

                msg_id = str(uuid.uuid4())[:8]
                loop = asyncio.get_running_loop()
                future = loop.create_future()
                self._pending[msg_id] = future

                await self._ws.send(json.dumps({
                    "id": msg_id,
                    "type": "command",
                    "action": action,
                    "params": params or {},
                }))

                result = await asyncio.wait_for(future, timeout)
                self._pending.pop(msg_id, None)

                if result.get("error"):
                    raise RuntimeError(f"Extension error on '{action}': {result['error']}")

                return result.get("data", {})

            except (asyncio.CancelledError, RuntimeError, Exception) as e:
                self._pending.pop(msg_id, None) if 'msg_id' in dir() else None
                last_error = e
                if attempt < _retries - 1:
                    self._log("retry", f"Command '{action}' failed ({e}), retry {attempt+1}...")
                    await asyncio.sleep(2)
                    continue

        raise RuntimeError(f"Command '{action}' failed after {_retries} attempts: {last_error}")

    # ── Convenience Methods ─────────────────────────────────────

    async def navigate(self, url: str, wait_ms: int = 5000) -> dict:
        """Navigate the active tab to a URL."""
        return await self.send_command("navigate", {"url": url, "wait": wait_ms})

    async def capture_screenshot(self) -> str:
        """Capture screenshot of visible tab. Returns base64 data URL."""
        result = await self.send_command("capture_screenshot")
        return result.get("screenshot", "")

    async def save_screenshot(self, path: str | Path) -> str:
        """Capture and save screenshot to file. Returns the path."""
        data_url = await self.capture_screenshot()
        if not data_url:
            return ""
        # Remove data:image/png;base64, prefix
        b64_data = data_url.split(",", 1)[1] if "," in data_url else data_url
        img_bytes = base64.b64decode(b64_data)
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(img_bytes)
        return str(path)

    async def get_tab_info(self) -> dict:
        """Get current tab URL and title."""
        return await self.send_command("get_tab_info")

    async def detect_state(self) -> dict:
        """Detect current page state via content script."""
        return await self.send_command("detect_state")

    async def extract_search_cards(self) -> list[dict]:
        """Extract search result cards from DOM."""
        result = await self.send_command("extract_search_cards")
        return result.get("cards", [])

    async def extract_note_content(self) -> dict:
        """Extract note content from DOM."""
        result = await self.send_command("extract_note_content")
        return result.get("note", {})

    async def extract_comments(self) -> list[dict]:
        """Extract comments from DOM (deduplicated)."""
        result = await self.send_command("extract_comments")
        return result.get("comments", [])

    async def click_card(self, index: int) -> dict:
        """Click a search result card by index."""
        return await self.send_command("click_card", {"index": index})

    async def click_note_link(self, url: str) -> dict:
        """Click a note by its link URL."""
        return await self.send_command("click_note_link", {"url": url})

    async def close_note(self) -> dict:
        """Close the note detail overlay."""
        return await self.send_command("close_note")

    async def scroll_note(self, pixels: int = 400) -> dict:
        """Scroll within the note detail panel."""
        return await self.send_command("scroll_note", {"pixels": pixels})

    async def scroll_page(self, pixels: int = 600) -> dict:
        """Scroll the page."""
        return await self.send_command("scroll_page", {"pixels": pixels})

    async def stop(self):
        """Stop the WebSocket server."""
        if self._keepalive_task:
            self._keepalive_task.cancel()
        if self._server:
            self._server.close()
            await self._server.wait_closed()
