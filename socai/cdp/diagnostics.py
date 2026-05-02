"""Generic CDP diagnostics used by the Socai desktop runtime."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .discovery import INSPECT_URL
from .errors import CDPDependencyError, add_chrome_permission_hint, exception_message
from .page import CDPPage, create_page_target
from .session import ExistingChromeConfig, connect_existing_chrome

SOCAI_TITLE_PREFIX = "🟢 Socai"

TEST_PAGE_HTML = """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Socai Primitive Test</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, sans-serif; margin: 40px; min-height: 1400px; }
    button, input { font-size: 18px; padding: 10px 14px; }
    #spacer { height: 900px; background: linear-gradient(#f8fbff, #e6f0ff); margin-top: 30px; }
  </style>
</head>
<body>
  <h1>Socai Primitive Test</h1>
  <p>This page is created by the Socai desktop runtime diagnostic.</p>
  <button id="click-target" onclick="document.body.dataset.clicked='yes'; this.textContent='Clicked by Socai';">Click me</button>
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


@dataclass(frozen=True)
class ControlledTabConfig:
    """Configuration for the generic controlled-tab proof."""

    url: str | None = None
    screenshot_path: Path | None = None
    timeout: float = 10.0
    load_wait: float = 1.2
    open_inspect_if_needed: bool = False


def default_test_url() -> str:
    """Return a data URL for the local primitive-test page."""

    return "data:text/html;charset=utf-8," + urllib.parse.quote(TEST_PAGE_HTML)


def default_screenshot_path() -> Path:
    """Return a timestamped temp screenshot path."""

    stamp = time.strftime("%Y%m%d-%H%M%S")
    return Path(tempfile.gettempdir()) / "socai" / f"controlled_tab_{stamp}.png"


async def create_controlled_tab(client: Any, initial_url: str) -> CDPPage:
    """Backward-compatible alias for creating a controlled page target."""

    return await create_page_target(client, initial_url)


async def run_controlled_tab_diagnostic(config: ControlledTabConfig | None = None) -> dict[str, Any]:
    """Create a marked tab and verify core CDP page primitives."""

    config = config or ControlledTabConfig()
    client = None
    discovery: dict[str, Any] | None = None

    try:
        client, discovery, setup_result = await connect_existing_chrome(
            ExistingChromeConfig(
                timeout=config.timeout,
                open_inspect_if_needed=config.open_inspect_if_needed,
            )
        )
        if setup_result:
            return setup_result

        assert client is not None  # for type checkers
        assert discovery is not None
        endpoint = discovery["endpoint"]
        screenshot_path = config.screenshot_path or default_screenshot_path()

        page = await create_page_target(client, config.url or default_test_url())
        await asyncio.sleep(config.load_wait)
        marked_title = await page.set_title_prefix(SOCAI_TITLE_PREFIX)

        initial_state = await page.evaluate_js(
            "({title: document.title, url: location.href, scrollY: Math.round(window.scrollY)})"
        )
        clicked = await exercise_click(page)
        typed, enter_pressed = await exercise_typing(page)
        # Capture while the semantic click/type targets are still visible; the
        # subsequent scroll primitive is still verified in final_state.
        await page.capture_screenshot(screenshot_path)
        scroll_y = await page.scroll(350)
        final_state = await read_final_test_state(page)

        return {
            "status": "controlled_tab_ready",
            "reason": "Created and exercised a marked Socai-controlled Chrome tab.",
            "endpoint": endpoint,
            "target_id": page.target_id,
            "session_id": page.session_id,
            "marked_title": marked_title,
            "initial_state": initial_state.value,
            "final_state": final_state,
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
    except CDPDependencyError as exc:
        return {
            "status": "dependency_missing",
            "reason": exception_message(exc),
        }
    except Exception as exc:  # noqa: BLE001 - diagnostic boundary
        message = add_chrome_permission_hint(exception_message(exc), INSPECT_URL)
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


async def exercise_click(page: CDPPage) -> bool:
    """Click the semantic test button and verify its state changed."""

    button_rect = await page.evaluate_js(
        """
(() => {
  const r = document.getElementById('click-target')?.getBoundingClientRect();
  return r ? {x: r.left + r.width / 2, y: r.top + r.height / 2} : null;
})()
"""
    )
    if not button_rect.value:
        return False

    await page.click(button_rect.value["x"], button_rect.value["y"])
    await asyncio.sleep(0.2)
    clicked_state = await page.evaluate_js("document.body.dataset.clicked")
    return clicked_state.value == "yes"


async def exercise_typing(page: CDPPage) -> tuple[bool, bool]:
    """Focus the semantic test input, type text, and press Enter."""

    await page.evaluate_js("document.getElementById('type-target')?.focus(); true")
    await page.type_text("socai")
    await page.press_key("Enter")
    await asyncio.sleep(0.2)
    input_state = await page.evaluate_js(
        "({value: document.getElementById('type-target')?.value || '', enterPressed: document.body.dataset.enterPressed})"
    )
    if not isinstance(input_state.value, dict):
        return False, False
    return input_state.value.get("value") == "socai", input_state.value.get("enterPressed") == "yes"


async def read_final_test_state(page: CDPPage) -> Any:
    """Read the local test page state after primitives have run."""

    result = await page.evaluate_js(
        "({title: document.title, url: location.href, scrollY: Math.round(window.scrollY), clicked: document.body.dataset.clicked, inputValue: document.getElementById('type-target')?.value || '', enterPressed: document.body.dataset.enterPressed})"
    )
    return result.value


def print_controlled_tab_human(result: dict[str, Any]) -> None:
    """Print a human-readable controlled-tab diagnostic summary."""

    print(f"Socai controlled-tab status: {result['status']}")
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


def parse_controlled_tab_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create and verify a Socai-controlled Chrome CDP tab.")
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


def controlled_tab_main(argv: list[str] | None = None) -> int:
    """CLI entry point for the controlled-tab diagnostic."""

    args = parse_controlled_tab_args(argv or sys.argv[1:])
    result = asyncio.run(
        run_controlled_tab_diagnostic(
            ControlledTabConfig(
                url=args.url,
                screenshot_path=Path(args.screenshot) if args.screenshot else None,
                timeout=args.timeout,
                load_wait=args.load_wait,
                open_inspect_if_needed=args.open_inspect,
            )
        )
    )

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_controlled_tab_human(result)

    return 0 if result["status"] in {"controlled_tab_ready", "setup_required"} else 1
