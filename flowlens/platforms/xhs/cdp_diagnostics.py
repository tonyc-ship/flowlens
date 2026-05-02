"""Xiaohongshu CDP diagnostics for the FlowLens desktop runtime.

This module is intentionally site-specific. Generic CDP primitives live in
``flowlens.cdp``; this file only describes how to probe XHS page reachability,
login prompts, and security-verification states.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from flowlens.cdp.diagnostics import FLOWLENS_TITLE_PREFIX
from flowlens.cdp.discovery import INSPECT_URL
from flowlens.cdp.errors import CDPDependencyError, add_chrome_permission_hint, exception_message
from flowlens.cdp.page import CDPPage, create_page_target
from flowlens.cdp.session import ExistingChromeConfig, connect_existing_chrome

DEFAULT_XHS_URL = "https://www.xiaohongshu.com/explore"
XHS_SUCCESS_STATUSES = {
    "xhs_probe_ready",
    "xhs_probe_inconclusive",
    "xhs_login_required",
    "xhs_security_verification",
    "setup_required",
}


@dataclass(frozen=True)
class XHSCdpProbeConfig:
    """Configuration for the XHS reachability probe."""

    url: str = DEFAULT_XHS_URL
    output_dir: Path | None = None
    timeout: float = 10.0
    load_wait: float = 6.0
    login_wait: float = 0.0
    login_poll_interval: float = 2.0
    scroll_delta: int = 650
    after_scroll_wait: float = 1.5
    open_inspect_if_needed: bool = False


def default_output_dir() -> Path:
    """Return a timestamped temp output directory for XHS probe artifacts."""

    stamp = time.strftime("%Y%m%d-%H%M%S")
    return Path(tempfile.gettempdir()) / "flowlens" / f"xhs_probe_{stamp}"


async def read_page_probe(page: CDPPage) -> dict[str, Any]:
    """Read page state signals relevant to XHS reachability."""

    result = await page.evaluate_js(
        """
(() => {
  const text = document.body ? document.body.innerText || '' : '';
  const lower = text.toLowerCase();
  const hasLoginText = text.includes('登录') || text.includes('扫码') || text.includes('二维码') ||
    text.includes('请扫码') || text.includes('手机') || lower.includes('login') || lower.includes('sign in');
  return {
    title: document.title,
    url: location.href,
    readyState: document.readyState,
    scrollY: Math.round(window.scrollY),
    bodyTextLength: text.length,
    hasXiaohongshuText: text.includes('小红书') || lower.includes('xiaohongshu'),
    possibleSecurityVerification: text.includes('安全验证') || text.includes('验证') || lower.includes('verify') || lower.includes('captcha'),
    possibleLoginPrompt: hasLoginText
  };
})()
"""
    )
    return result.value if isinstance(result.value, dict) else {}


async def run_xhs_cdp_probe(config: XHSCdpProbeConfig | None = None) -> dict[str, Any]:
    """Open XHS in a controlled tab and return reachability diagnostics."""

    config = config or XHSCdpProbeConfig()
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

        assert client is not None
        assert discovery is not None
        endpoint = discovery["endpoint"]
        run_dir = config.output_dir or default_output_dir()
        before_screenshot = run_dir / "xhs_before_scroll.png"
        after_screenshot = run_dir / "xhs_after_scroll.png"

        page = await create_page_target(client, "about:blank")
        await page.set_title_prefix(f"{FLOWLENS_TITLE_PREFIX} — XHS")
        await page.navigate(config.url, wait_seconds=config.load_wait)
        marked_title = await page.set_title_prefix(f"{FLOWLENS_TITLE_PREFIX} — XHS")

        before_state = await read_page_probe(page)
        await page.capture_screenshot(before_screenshot)
        before_state, login_prompt_initial, login_waited_seconds = await wait_for_login_clearance(
            page,
            before_state,
            config.login_wait,
            config.login_poll_interval,
        )

        scroll_y = await page.scroll(config.scroll_delta)
        await asyncio.sleep(config.after_scroll_wait)
        after_state = await read_page_probe(page)
        await page.capture_screenshot(after_screenshot)

        diagnostics = build_xhs_diagnostics(
            config=config,
            before_state=before_state,
            after_state=after_state,
            scroll_y=scroll_y,
            login_prompt_initial=login_prompt_initial,
            login_waited_seconds=login_waited_seconds,
        )
        status, reason = classify_xhs_probe(diagnostics, after_screenshot)

        return {
            "status": status,
            "reason": reason,
            "endpoint": endpoint,
            "target_id": page.target_id,
            "session_id": page.session_id,
            "marked_title": marked_title,
            "before_state": before_state,
            "after_state": after_state,
            "diagnostics": diagnostics,
            "screenshots": {
                "before_scroll": str(before_screenshot),
                "after_scroll": str(after_screenshot),
            },
            "run_dir": str(run_dir),
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


async def wait_for_login_clearance(
    page: CDPPage,
    before_state: dict[str, Any],
    login_wait: float,
    poll_interval: float,
) -> tuple[dict[str, Any], bool, float]:
    """Optionally wait for an XHS login prompt to clear."""

    login_prompt_initial = bool(before_state.get("possibleLoginPrompt"))
    if not login_prompt_initial or login_wait <= 0:
        return before_state, login_prompt_initial, 0.0

    started = time.monotonic()
    deadline = started + login_wait
    while time.monotonic() < deadline:
        await asyncio.sleep(poll_interval)
        before_state = await read_page_probe(page)
        if not before_state.get("possibleLoginPrompt"):
            break
    return before_state, login_prompt_initial, round(time.monotonic() - started, 2)


def build_xhs_diagnostics(
    *,
    config: XHSCdpProbeConfig,
    before_state: dict[str, Any],
    after_state: dict[str, Any],
    scroll_y: int,
    login_prompt_initial: bool,
    login_waited_seconds: float,
) -> dict[str, Any]:
    """Build the stable diagnostics object returned to the app."""

    return {
        "opened_requested_url": config.url,
        "landed_url": after_state.get("url") or before_state.get("url"),
        "title": after_state.get("title") or before_state.get("title"),
        "readyState": after_state.get("readyState") or before_state.get("readyState"),
        "scrollY": scroll_y,
        "bodyTextLength": after_state.get("bodyTextLength", 0),
        "hasXiaohongshuText": bool(after_state.get("hasXiaohongshuText") or before_state.get("hasXiaohongshuText")),
        "possibleSecurityVerification": bool(
            after_state.get("possibleSecurityVerification") or before_state.get("possibleSecurityVerification")
        ),
        "possibleLoginPrompt": bool(after_state.get("possibleLoginPrompt") or before_state.get("possibleLoginPrompt")),
        "loginPromptInitial": login_prompt_initial,
        "loginWaitSeconds": float(config.login_wait),
        "loginWaitedSeconds": login_waited_seconds,
    }


def classify_xhs_probe(diagnostics: dict[str, Any], after_screenshot: Path) -> tuple[str, str]:
    """Classify XHS page state from diagnostics and artifact presence."""

    landed_url = diagnostics.get("landed_url") or ""
    operated = bool("xiaohongshu.com" in landed_url and after_screenshot.exists())
    login_required = operated and diagnostics["possibleLoginPrompt"]
    security_verification = operated and diagnostics["possibleSecurityVerification"]

    if not operated:
        return "xhs_probe_inconclusive", "Could not confirm XHS operation from URL/screenshot diagnostics."
    if security_verification:
        return "xhs_security_verification", "Opened Xiaohongshu, but the page appears to be in a security verification state."
    if login_required:
        return "xhs_login_required", "Opened Xiaohongshu in a FlowLens-controlled tab, but the user still needs to log in."
    return "xhs_probe_ready", "Opened Xiaohongshu in a FlowLens-controlled Chrome tab and confirmed the page is reachable."


def print_xhs_probe_human(result: dict[str, Any]) -> None:
    """Print a human-readable XHS probe summary."""

    print(f"FlowLens XHS probe status: {result['status']}")
    print(f"Reason: {result.get('reason')}")

    if result["status"] == "setup_required":
        print(f"Inspect URL: {INSPECT_URL}")
        if result.get("opened_inspect_url"):
            print("Opened Chrome inspect permission page.")
        return

    if result["status"] not in XHS_SUCCESS_STATUSES:
        if result.get("inspect_url"):
            print(f"Inspect URL: {result['inspect_url']}")
        return

    diagnostics = result.get("diagnostics") or {}
    print(f"Target ID: {result.get('target_id')}")
    print(f"Marked title: {result.get('marked_title')}")
    print(f"Landed URL: {diagnostics.get('landed_url')}")
    print(f"Ready state: {diagnostics.get('readyState')}")
    print(f"Scrolled to Y: {diagnostics.get('scrollY')}")
    print(f"Security verification detected: {diagnostics.get('possibleSecurityVerification')}")
    print(f"Login prompt detected: {diagnostics.get('possibleLoginPrompt')}")
    print(f"Login wait: {diagnostics.get('loginWaitedSeconds')} / {diagnostics.get('loginWaitSeconds')} seconds")
    print("Screenshots:")
    for name, path in (result.get("screenshots") or {}).items():
        print(f"- {name}: {path}")


def parse_xhs_probe_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Open Xiaohongshu in a FlowLens-controlled Chrome CDP tab.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON only")
    parser.add_argument("--url", default=DEFAULT_XHS_URL, help="Xiaohongshu URL to open")
    parser.add_argument("--output-dir", help="Directory for probe screenshots")
    parser.add_argument(
        "--open-inspect",
        action="store_true",
        help=f"Open {INSPECT_URL} in Google Chrome if setup is required",
    )
    parser.add_argument("--timeout", type=float, default=10.0, help="CDP connection timeout in seconds")
    parser.add_argument("--load-wait", type=float, default=6.0, help="Seconds to wait after navigating to XHS")
    parser.add_argument(
        "--login-wait",
        type=float,
        default=0.0,
        help="Seconds to wait for the user to scan/login if XHS asks for login",
    )
    parser.add_argument("--login-poll-interval", type=float, default=2.0, help="Seconds between XHS login-state checks")
    parser.add_argument("--scroll-delta", type=int, default=650, help="Pixels to scroll during the proof")
    parser.add_argument("--after-scroll-wait", type=float, default=1.5, help="Seconds to wait after scrolling")
    return parser.parse_args(argv)


def xhs_probe_main(argv: list[str] | None = None) -> int:
    """CLI entry point for XHS CDP diagnostics."""

    args = parse_xhs_probe_args(argv or sys.argv[1:])
    result = asyncio.run(
        run_xhs_cdp_probe(
            XHSCdpProbeConfig(
                url=args.url,
                output_dir=Path(args.output_dir) if args.output_dir else None,
                timeout=args.timeout,
                load_wait=args.load_wait,
                login_wait=args.login_wait,
                login_poll_interval=args.login_poll_interval,
                scroll_delta=args.scroll_delta,
                after_scroll_wait=args.after_scroll_wait,
                open_inspect_if_needed=args.open_inspect,
            )
        )
    )

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_xhs_probe_human(result)

    return 0 if result["status"] in XHS_SUCCESS_STATUSES else 1
