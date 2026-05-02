"""Generic CDP page/session primitives.

These helpers intentionally stay platform-agnostic: they know how to create a
Chrome page target, attach to it, evaluate JavaScript, dispatch basic input, and
capture screenshots. Product/site diagnostics live in higher-level modules.
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RuntimeEvaluation:
    """Compact representation of a ``Runtime.evaluate`` result."""

    value: Any = None
    description: str | None = None
    subtype: str | None = None
    type: str | None = None


class CDPPage:
    """A small wrapper around a flattened Chrome target session."""

    def __init__(self, client: Any, session_id: str, target_id: str):
        self.client = client
        self.session_id = session_id
        self.target_id = target_id

    async def send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send a raw CDP command scoped to this page session."""

        return await self.client.send_raw(method, params or {}, session_id=self.session_id)

    async def enable_default_domains(self) -> None:
        """Enable domains used by Socai's current page primitives."""

        for domain in ("Page", "Runtime", "DOM"):
            await self.send(f"{domain}.enable")

    async def navigate(self, url: str, wait_seconds: float = 1.2) -> None:
        """Navigate the page and wait briefly for browser work to settle."""

        await self.send("Page.navigate", {"url": url})
        if wait_seconds > 0:
            import asyncio

            await asyncio.sleep(wait_seconds)

    async def evaluate_js(self, expression: str) -> RuntimeEvaluation:
        """Evaluate JavaScript and return its by-value result when possible."""

        result = await self.send(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
            },
        )
        if "exceptionDetails" in result:
            raise RuntimeError(json.dumps(result["exceptionDetails"], ensure_ascii=False))

        payload = result.get("result") or {}
        return RuntimeEvaluation(
            value=payload.get("value"),
            description=payload.get("description"),
            subtype=payload.get("subtype"),
            type=payload.get("type"),
        )

    async def set_title_prefix(self, prefix: str, fallback_title: str = "Controlled Tab") -> str:
        """Prefix the current document title and return the resulting title."""

        expression = f"""
(() => {{
  const prefix = {json.dumps(prefix)};
  const fallback = {json.dumps(fallback_title)};
  if (!document.title.startsWith(prefix)) document.title = `${{prefix}} — ${{document.title || fallback}}`;
  return document.title;
}})()
"""
        result = await self.evaluate_js(expression)
        return str(result.value or result.description or "")

    async def capture_screenshot(self, path: Path) -> None:
        """Capture the current viewport to ``path`` as PNG."""

        result = await self.send("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
        data = result.get("data")
        if not data:
            raise RuntimeError("Page.captureScreenshot did not return image data")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(data))

    async def scroll(self, delta_y: int) -> int:
        """Scroll vertically and return the resulting rounded ``window.scrollY``."""

        result = await self.evaluate_js(f"window.scrollBy(0, {int(delta_y)}); Math.round(window.scrollY)")
        return int(result.value or 0)

    async def click(self, x: float, y: float) -> None:
        """Dispatch a left mouse click at viewport coordinates."""

        params = {"x": float(x), "y": float(y), "button": "left", "clickCount": 1}
        await self.send("Input.dispatchMouseEvent", {**params, "type": "mousePressed"})
        await self.send("Input.dispatchMouseEvent", {**params, "type": "mouseReleased"})

    async def type_text(self, text: str) -> None:
        """Insert text at the focused element."""

        await self.send("Input.insertText", {"text": text})

    async def press_key(self, key: str) -> None:
        """Dispatch a key press for common named keys or a single text key."""

        key_map = {
            "Enter": {"windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13, "code": "Enter", "key": "Enter"},
            "Escape": {"windowsVirtualKeyCode": 27, "nativeVirtualKeyCode": 27, "code": "Escape", "key": "Escape"},
            "Tab": {"windowsVirtualKeyCode": 9, "nativeVirtualKeyCode": 9, "code": "Tab", "key": "Tab"},
        }
        payload = key_map.get(key, {"key": key, "text": key})
        await self.send("Input.dispatchKeyEvent", {"type": "keyDown", **payload})
        await self.send("Input.dispatchKeyEvent", {"type": "keyUp", **payload})


async def create_page_target(client: Any, initial_url: str = "about:blank") -> CDPPage:
    """Create a new page target, attach with flattened sessions, and enable domains."""

    created = await client.send_raw("Target.createTarget", {"url": initial_url})
    target_id = created["targetId"]
    attached = await client.send_raw("Target.attachToTarget", {"targetId": target_id, "flatten": True})
    page = CDPPage(client=client, session_id=attached["sessionId"], target_id=target_id)
    await page.enable_default_domains()
    return page
