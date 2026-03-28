"""Generic WebSocket bridge between Python agent and Chrome Extension.

Platform-independent browser automation layer. Handles WebSocket transport,
navigation, screenshots (CDP), mouse clicks (CDP), and JS execution.
Site-specific DOM extraction is handled by platform modules (e.g. xhs/).

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
        self._watch_mode = False
        self._capabilities: dict[str, object] = {}

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

    async def wait_for_connection(self, timeout: float = 120, *, require_watch: bool = False):
        """Wait for the Chrome Extension to connect."""
        self._log("waiting", f"Waiting for extension to connect (timeout={timeout}s)...")
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            remaining = max(0.0, deadline - asyncio.get_running_loop().time())
            if remaining <= 0:
                raise RuntimeError(
                    f"Extension did not connect within {timeout}s. "
                    "Make sure the extension is loaded and click 'Connect' in the popup."
                )
            try:
                await asyncio.wait_for(self._connected.wait(), remaining)
            except asyncio.TimeoutError:
                raise RuntimeError(
                    f"Extension did not connect within {timeout}s. "
                    "Make sure the extension is loaded and click 'Connect' in the popup."
                )

            if not require_watch or self._capabilities.get("watch_mode") is True:
                break

            self._log("incompatible_connection", "Connected extension missing watch-mode capabilities; waiting for newer runtime")
            if self._ws is not None:
                try:
                    await self._ws.close()
                except Exception:
                    pass
            self._connected.clear()

        self._log("extension_connected", "Chrome Extension connected")

        # Warmup: wait for extension to set activeTabId (async in onopen callback)
        await asyncio.sleep(1)
        for _ in range(3):
            try:
                await self.get_tab_info()
                break
            except Exception:
                await asyncio.sleep(1)

    async def _handle_connection(self, ws):
        """Handle incoming WebSocket connection from extension."""
        old_ws = self._ws

        # Chrome MV3 may briefly establish overlapping websocket sessions during
        # service worker restarts. Keep the current primary connection whenever
        # it is already established to avoid a self-inflicted reconnect loop.
        if old_ws and old_ws is not ws:
            if self._pending:
                self._log("duplicate_connection", "Ignoring duplicate extension websocket during in-flight command")
                try:
                    await ws.close()
                except Exception:
                    pass
                return

            if self._connected.is_set():
                self._log("duplicate_connection", "Keeping existing primary extension websocket")
                try:
                    await ws.close()
                except Exception:
                    pass
                return

            self._log("duplicate_connection", "Replacing stale extension websocket before handshake")
            try:
                await old_ws.close()
            except Exception:
                pass

        self._ws = ws
        self._connected.clear()
        self._capabilities = {}

        # Start keepalive pings to prevent MV3 service worker from sleeping
        if self._keepalive_task:
            self._keepalive_task.cancel()
        self._keepalive_task = asyncio.create_task(self._keepalive(ws))

        try:
            async for raw in ws:
                msg = json.loads(raw)

                if msg.get("type") == "event":
                    if msg.get("event") == "connected":
                        data = msg.get("data", {}) or {}
                        caps = data.get("capabilities", {}) or {}
                        if isinstance(caps, dict):
                            self._capabilities = caps
                        else:
                            self._capabilities = {}
                        self._connected.set()
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
                self._capabilities = {}
                for msg_id, future in list(self._pending.items()):
                    if not future.done():
                        future.cancel()
                self._pending.clear()
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
        if self._ws and self._connected.is_set():
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

    # ── Generic Browser Operations ─────────────────────────────

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
        b64_data = data_url.split(",", 1)[1] if "," in data_url else data_url
        img_bytes = base64.b64decode(b64_data)
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(img_bytes)
        return str(path)

    async def get_tab_info(self) -> dict:
        """Get current tab URL and title."""
        return await self.send_command("get_tab_info")

    async def run_js(self, code: str) -> dict:
        """Execute JavaScript in the page's MAIN world context."""
        return await self.send_command("run_js", {"code": code})

    async def find_chat_input(self, selectors: list[str]) -> dict:
        """Find the best visible chatbot input candidate in the current tab."""
        return await self.send_command("find_chat_input", {"selectors": selectors})

    async def set_chat_input_text(self, selectors: list[str], text: str) -> dict:
        """Set chatbot input text directly through DOM-safe execution."""
        return await self.send_command("set_chat_input_text", {
            "selectors": selectors,
            "text": text,
        })

    async def click_chat_submit(self, selectors: list[str]) -> dict:
        """Click the best visible chatbot submit button in the current tab."""
        return await self.send_command("click_chat_submit", {"selectors": selectors})

    async def click_at(self, x: int, y: int) -> dict:
        """CDP-based real mouse click at viewport coordinates."""
        return await self.send_command("click_at", {"x": x, "y": y})

    async def mouse_move(self, x: int, y: int) -> dict:
        """CDP-based mouse move to viewport coordinates."""
        return await self.send_command("mouse_move", {"x": x, "y": y})

    async def create_background_window(
        self,
        url: str = "about:blank",
        minimized: bool = False,
        *,
        width: int | None = None,
        height: int | None = None,
        left: int | None = None,
        top: int | None = None,
        lock: bool = True,
        focused: bool = False,
    ) -> dict:
        """Create a new browser window in the background (same profile, shares login state)."""
        params: dict = {"url": url, "minimized": minimized, "lock": lock, "focused": focused}
        if width is not None:
            params["width"] = width
        if height is not None:
            params["height"] = height
        if left is not None:
            params["left"] = left
        if top is not None:
            params["top"] = top
        return await self.send_command("create_background_window", params)

    async def close_window(self, window_id: int) -> None:
        """Close a browser window by ID."""
        await self.send_command("close_window", {"windowId": window_id})

    async def lock_active_tab(self, tab_id: int | None = None) -> dict:
        """Pin automation to a specific tab so front-window browsing does not hijack it."""
        params = {}
        if tab_id is not None:
            params["tabId"] = tab_id
        return await self.send_command("lock_active_tab", params)

    async def release_active_tab(self) -> dict:
        """Release the pinned automation tab."""
        return await self.send_command("release_active_tab")

    async def press_key(self, key: str, *, code: str | None = None, windows_virtual_key_code: int | None = None) -> dict:
        """Dispatch a real key press via CDP."""
        params = {"key": key}
        if code is not None:
            params["code"] = code
        if windows_virtual_key_code is not None:
            params["windowsVirtualKeyCode"] = windows_virtual_key_code
        return await self.send_command("press_key", params)

    async def type_text(self, text: str) -> dict:
        """Insert text at the current cursor position via CDP Input.insertText.

        Works with textareas, contenteditable, ProseMirror, etc.
        Handles Unicode/CJK without IME simulation.
        """
        return await self.send_command("type_text", {"text": text})

    async def reload_extension(self) -> None:
        """Reload the Chrome Extension to pick up code changes.

        After reload, the WebSocket connection drops and extension auto-reconnects.
        We clear the connected state and wait for a fresh connection to ensure
        the NEW code (not the pre-reload connection) is what we talk to.
        """
        try:
            await self.send_command("reload_extension", timeout=5)
        except Exception:
            pass  # Expected — connection drops during reload

        # Clear connected state so we wait for the NEW connection after reload
        self._connected.clear()
        self._ws = None
        self._log("reload", "Extension reloading, waiting for reconnect...")
        await asyncio.sleep(3)  # Give chrome.runtime.reload() time to fire
        try:
            await self.wait_for_connection(timeout=30)
            self._log("reload", "Extension reconnected after reload")
        except RuntimeError:
            self._log("reload_failed", "Extension did not reconnect after reload")
            raise

    async def scroll_page(self, pixels: int = 600) -> dict:
        """Scroll the page."""
        return await self.send_command("scroll_page", {"pixels": pixels})

    # ── Watch Mode ─────────────────────────────────────────────

    @property
    def watch_mode(self) -> bool:
        return self._watch_mode

    async def create_watch_window(self, url: str = "about:blank") -> dict:
        """Enable watch mode on the current browser window and open the side panel."""
        result = await self.send_command("create_watch_window", {
            "url": url,
            "lock": True,
        })
        self._watch_mode = True
        self._log("watch_mode", "Watch side panel attached to current tab")
        return result

    async def enable_watch_mode(self) -> dict:
        """Enable watch mode on the current tab and open the side panel."""
        result = await self.send_command("enable_watch_mode")
        self._watch_mode = True
        return result

    async def disable_watch_mode(self) -> dict:
        """Disable watch mode and hide the activity sidebar."""
        result = await self.send_command("disable_watch_mode")
        self._watch_mode = False
        return result

    async def watch_log(
        self,
        level: str,
        message: str,
        *,
        phase: str = "",
        detail: str = "",
        observation: str = "",
        reasoning: str = "",
        decision: str = "",
        evidence: str = "",
        action_name: str = "",
        duration: float | None = None,
        x: int | None = None,
        y: int | None = None,
        target: str = "",
    ) -> None:
        """Send a log entry to the watch panel sidebar.

        Only sends if watch mode is active. Safe to call unconditionally.

        Args:
            level: Entry type — 'think', 'action', 'result', 'click', 'extract',
                   'warning', 'error', 'info', 'session'
            message: Primary message shown in the panel
            phase: Agent phase (e.g. 'keyword_generation', 'note_extraction')
            detail: Extended detail text
            observation: What the agent observed (for 'think' entries)
            reasoning: Agent reasoning (for 'think' entries)
            decision: What the agent decided (for 'think' entries)
            evidence: Supporting evidence (for 'think' entries)
            action_name: Name of the action (for 'action' entries)
            duration: Duration in seconds (for 'result' entries)
            x, y: Coordinates (for 'click' entries)
            target: Description of click target
        """
        if not self._watch_mode:
            return
        params: dict = {"level": level, "message": message}
        if phase:
            params["phase"] = phase
        if detail:
            params["detail"] = detail
        if observation:
            params["observation"] = observation
        if reasoning:
            params["reasoning"] = reasoning
        if decision:
            params["decision"] = decision
        if evidence:
            params["evidence"] = evidence
        if action_name:
            params["action_name"] = action_name
        if duration is not None:
            params["duration"] = duration
        if x is not None:
            params["x"] = x
        if y is not None:
            params["y"] = y
        if target:
            params["target"] = target
        try:
            await self.send_command("watch_log", params, timeout=5, _retries=1)
        except Exception:
            pass  # Non-critical — don't let watch logging break the workflow

    async def watch_highlight(
        self, *, x: int | None = None, y: int | None = None, selector: str = "",
    ) -> None:
        """Send a highlight command to the watch panel.

        Either provide (x, y) for coordinate highlight or selector for element highlight.
        """
        if not self._watch_mode:
            return
        params: dict = {}
        if x is not None and y is not None:
            params["mode"] = "coords"
            params["x"] = x
            params["y"] = y
        elif selector:
            params["mode"] = "selector"
            params["selector"] = selector
        try:
            await self.send_command("watch_highlight", params, timeout=5, _retries=1)
        except Exception:
            pass

    async def stop(self):
        """Stop the WebSocket server."""
        if self._keepalive_task:
            self._keepalive_task.cancel()
        if self._server:
            self._server.close()
            await self._server.wait_closed()
