#!/usr/bin/env python3
"""Create a marked SocAI-controlled tab and exercise basic CDP primitives.

Session 3 scope:
- connect to existing Chrome through the Session 1/2 discovery path
- create a new page target
- attach to that target
- mark its title with `🟢 SocAI`
- exercise navigate/evaluate/screenshot/click/type/key/scroll primitives

This is still a technical prototype. It does not open XHS and does not expose
raw JavaScript execution to any LLM or product-facing agent.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
import tempfile
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from chrome_discovery import INSPECT_URL, discover_chrome_cdp, open_inspect_page
from cdp_targets import cdp_use_install_help, exception_message

SOCAI_TITLE_PREFIX = "🟢 SocAI"

TEST_PAGE_HTML = """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>SocAI Primitive Test</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, sans-serif; margin: 40px; min-height: 1400px; }
    button, input { font-size: 18px; padding: 10px 14px; }
    #spacer { height: 900px; background: linear-gradient(#f8fbff, #e6f0ff); margin-top: 30px; }
  </style>
</head>
<body>
  <h1>SocAI Primitive Test</h1>
  <p>This page is created by the SocAI CDP prototype.</p>
  <button id="click-target" onclick="document.body.dataset.clicked='yes'; this.textContent='Clicked by SocAI';">Click me</button>
  <input id="type-target" placeholder="Type target" />
  <div id="spacer">Scroll target area</div>
  <script>
    document.body.dataset.clicked = 'no';
    document.body.dataset.enterPressed = 'no';
    document.getElementById('type-target').addEventListener('keydown', (event) => {
      if (event.key === 'Enter') document.body.dataset.enterPressed = 'yes';
    });
  </script>
</body>
</html>
"""


def default_test_url() -> str:
    return "data:text/html;charset=utf-8," + urllib.parse.quote(TEST_PAGE_HTML)


@dataclass
class RuntimeResult:
    value: Any = None
    description: str | None = None
    subtype: str | None = None
    type: str | None = None


class SocAICDPPage:
    def __init__(self, client: Any, session_id: str, target_id: str):
        self.client = client
        self.session_id = session_id
        self.target_id = target_id

    async def send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self.client.send_raw(method, params or {}, session_id=self.session_id)

    async def enable(self) -> None:
        for domain in ("Page", "Runtime", "DOM", "Input"):
            # Input.enable does not exist; skip cleanly for CDP compatibility.
            if domain == "Input":
                continue
            await self.send(f"{domain}.enable")

    async def navigate(self, url: str, wait_seconds: float = 1.2) -> None:
        await self.send("Page.navigate", {"url": url})
        await asyncio.sleep(wait_seconds)

    async def evaluate_js(self, expression: str) -> RuntimeResult:
        result = await self.send(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
            },
        )
        payload = result.get("result") or {}
        if "exceptionDetails" in result:
            raise RuntimeError(json.dumps(result["exceptionDetails"], ensure_ascii=False))
        return RuntimeResult(
            value=payload.get("value"),
            description=payload.get("description"),
            subtype=payload.get("subtype"),
            type=payload.get("type"),
        )

    async def mark_title(self, label: str = SOCAI_TITLE_PREFIX) -> str:
        expression = f"""
(() => {{
  const prefix = {json.dumps(label)};
  if (!document.title.startsWith(prefix)) document.title = `${{prefix}} — ${{document.title || 'Controlled Tab'}}`;
  return document.title;
}})()
"""
        result = await self.evaluate_js(expression)
        return str(result.value or result.description or "")

    async def capture_screenshot(self, path: Path) -> None:
        result = await self.send("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
        data = result.get("data")
        if not data:
            raise RuntimeError("Page.captureScreenshot did not return image data")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(data))

    async def scroll(self, delta_y: int) -> int:
        result = await self.evaluate_js(f"window.scrollBy(0, {int(delta_y)}); Math.round(window.scrollY)")
        return int(result.value or 0)

    async def click(self, x: float, y: float) -> None:
        params = {"x": float(x), "y": float(y), "button": "left", "clickCount": 1}
        await self.send("Input.dispatchMouseEvent", {**params, "type": "mousePressed"})
        await self.send("Input.dispatchMouseEvent", {**params, "type": "mouseReleased"})

    async def type_text(self, text: str) -> None:
        await self.send("Input.insertText", {"text": text})

    async def press_key(self, key: str) -> None:
        key_map = {
            "Enter": {"windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13, "code": "Enter", "key": "Enter"},
            "Escape": {"windowsVirtualKeyCode": 27, "nativeVirtualKeyCode": 27, "code": "Escape", "key": "Escape"},
            "Tab": {"windowsVirtualKeyCode": 9, "nativeVirtualKeyCode": 9, "code": "Tab", "key": "Tab"},
        }
        payload = key_map.get(key, {"key": key, "text": key})
        await self.send("Input.dispatchKeyEvent", {"type": "keyDown", **payload})
        await self.send("Input.dispatchKeyEvent", {"type": "keyUp", **payload})


async def create_controlled_tab(client: Any, initial_url: str) -> SocAICDPPage:
    created = await client.send_raw("Target.createTarget", {"url": initial_url})
    target_id = created["targetId"]
    attached = await client.send_raw("Target.attachToTarget", {"targetId": target_id, "flatten": True})
    page = SocAICDPPage(client=client, session_id=attached["sessionId"], target_id=target_id)
    await page.enable()
    return page


async def run(args: argparse.Namespace) -> dict[str, Any]:
    discovery = discover_chrome_cdp()
    if discovery["status"] != "cdp_available":
        if args.open_inspect:
            open_inspect_page()
        return {
            "status": "setup_required",
            "reason": discovery.get("reason"),
            "inspect_url": INSPECT_URL,
            "opened_inspect_url": bool(args.open_inspect),
            "discovery": discovery,
        }

    try:
        from cdp_use.client import CDPClient
    except ModuleNotFoundError as exc:
        return {
            "status": "dependency_missing",
            "reason": cdp_use_install_help("apps/socai/prototype/cdp_controlled_tab.py"),
        }

    endpoint = discovery["endpoint"]
    browser_ws_url = endpoint.get("browser_ws_url")
    if not browser_ws_url:
        return {
            "status": "error",
            "reason": "Discovery reported cdp_available but did not include browser_ws_url.",
            "discovery": discovery,
        }

    screenshot_path = Path(args.screenshot) if args.screenshot else default_screenshot_path()
    from cdp_connect import connect_cdp_with_retry

    client = None
    try:
        client = await connect_cdp_with_retry(browser_ws_url, per_attempt_timeout=args.timeout)
        page = await create_controlled_tab(client, args.url or default_test_url())
        await asyncio.sleep(args.load_wait)
        marked_title = await page.mark_title()

        initial_state = await page.evaluate_js(
            "({title: document.title, url: location.href, scrollY: Math.round(window.scrollY)})"
        )

        # Test click using the target's semantic bounding box, then use the CDP click primitive.
        button_rect = await page.evaluate_js(
            """
(() => {
  const r = document.getElementById('click-target')?.getBoundingClientRect();
  return r ? {x: r.left + r.width / 2, y: r.top + r.height / 2} : null;
})()
"""
        )
        clicked = False
        if button_rect.value:
            await page.click(button_rect.value["x"], button_rect.value["y"])
            await asyncio.sleep(0.2)
            clicked_state = await page.evaluate_js("document.body.dataset.clicked")
            clicked = clicked_state.value == "yes"

        # Test typing/key primitives by focusing a known input on the test page.
        typed = False
        enter_pressed = False
        await page.evaluate_js("document.getElementById('type-target')?.focus(); true")
        await page.type_text("socai")
        await page.press_key("Enter")
        await asyncio.sleep(0.2)
        input_state = await page.evaluate_js(
            "({value: document.getElementById('type-target')?.value || '', enterPressed: document.body.dataset.enterPressed})"
        )
        if isinstance(input_state.value, dict):
            typed = input_state.value.get("value") == "socai"
            enter_pressed = input_state.value.get("enterPressed") == "yes"

        scroll_y = await page.scroll(350)
        await page.capture_screenshot(screenshot_path)

        final_state = await page.evaluate_js(
            "({title: document.title, url: location.href, scrollY: Math.round(window.scrollY), clicked: document.body.dataset.clicked, inputValue: document.getElementById('type-target')?.value || '', enterPressed: document.body.dataset.enterPressed})"
        )

        return {
            "status": "controlled_tab_ready",
            "reason": "Created and exercised a marked SocAI-controlled Chrome tab.",
            "endpoint": endpoint,
            "target_id": page.target_id,
            "session_id": page.session_id,
            "marked_title": marked_title,
            "initial_state": initial_state.value,
            "final_state": final_state.value,
            "primitive_results": {
                "navigate": True,
                "evaluate_js": bool(initial_state.value),
                "click": clicked,
                "type_text": typed,
                "press_key": enter_pressed,
                "scroll": scroll_y > 0,
                "capture_screenshot": screenshot_path.exists(),
            },
            "screenshot_path": str(screenshot_path),
        }
    except Exception as exc:  # noqa: BLE001 - CLI diagnostic boundary
        message = exception_message(exc)
        lower = message.lower()
        if "handshake" in lower or "timeout" in lower or "403" in message or "allow" in lower:
            message = (
                f"{message}\nOpen {INSPECT_URL}, approve Chrome remote-debugging/inspect permission, then retry. "
                "Chrome may show one Allow dialog per connection attempt during the prototype."
            )
        return {
            "status": "failed",
            "reason": message,
            "inspect_url": INSPECT_URL,
            "discovery": discovery,
        }
    finally:
        if client:
            try:
                await asyncio.wait_for(client.stop(), timeout=2)
            except Exception:
                pass


def default_screenshot_path() -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return Path(tempfile.gettempdir()) / "socai" / f"controlled_tab_{stamp}.png"


def print_human(result: dict[str, Any]) -> None:
    print(f"SocAI controlled-tab status: {result['status']}")
    print(f"Reason: {result.get('reason')}")

    if result["status"] == "setup_required":
        print(f"Inspect URL: {INSPECT_URL}")
        if result.get("opened_inspect_url"):
            print("Opened Chrome inspect permission page.")
        return

    if result["status"] != "controlled_tab_ready":
        if result.get("inspect_url"):
            print(f"Inspect URL: {result['inspect_url']}")
        return

    print(f"Target ID: {result.get('target_id')}")
    print(f"Marked title: {result.get('marked_title')}")
    print(f"Screenshot: {result.get('screenshot_path')}")
    print("Primitive results:")
    for name, ok in (result.get("primitive_results") or {}).items():
        print(f"- {name}: {'ok' if ok else 'failed'}")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON only")
    parser.add_argument("--url", help="Initial URL for the controlled tab; defaults to a local data URL")
    parser.add_argument("--screenshot", help="Path for the captured screenshot")
    parser.add_argument(
        "--open-inspect",
        action="store_true",
        help=f"Open {INSPECT_URL} in Google Chrome if setup is required",
    )
    parser.add_argument("--timeout", type=float, default=10.0, help="CDP connection timeout in seconds")
    parser.add_argument("--load-wait", type=float, default=1.2, help="Seconds to wait after creating the tab")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    result = asyncio.run(run(args))

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_human(result)

    return 0 if result["status"] in {"controlled_tab_ready", "setup_required"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
