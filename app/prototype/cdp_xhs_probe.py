#!/usr/bin/env python3
"""Open Xiaohongshu in a Socai-controlled tab and prove basic operation.

Session 4 scope:
- create a marked Socai-controlled tab in the user's existing Chrome profile
- navigate to Xiaohongshu
- capture screenshots
- scroll the page
- read URL/title/basic runtime state

This does not implement XHS extraction, product functions, or LLM planning.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from chrome_discovery import INSPECT_URL, discover_chrome_cdp, open_inspect_page
from cdp_controlled_tab import SOCAI_TITLE_PREFIX, SocaiCDPPage, create_controlled_tab
from cdp_targets import cdp_use_install_help, exception_message

DEFAULT_XHS_URL = "https://www.xiaohongshu.com/explore"


async def connect_client() -> tuple[Any, dict[str, Any]]:
    discovery = discover_chrome_cdp()
    if discovery["status"] != "cdp_available":
        raise RuntimeError(json.dumps({"setup_required": discovery}, ensure_ascii=False))

    try:
        from cdp_use.client import CDPClient
    except ModuleNotFoundError as exc:
        raise RuntimeError(cdp_use_install_help("app/prototype/cdp_xhs_probe.py")) from exc

    endpoint = discovery["endpoint"]
    browser_ws_url = endpoint.get("browser_ws_url")
    if not browser_ws_url:
        raise RuntimeError("Discovery reported cdp_available but did not include browser_ws_url.")

    return CDPClient(browser_ws_url), discovery


async def read_page_probe(page: SocaiCDPPage) -> dict[str, Any]:
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
    except ModuleNotFoundError:
        return {
            "status": "dependency_missing",
            "reason": cdp_use_install_help("app/prototype/cdp_xhs_probe.py"),
        }

    endpoint = discovery["endpoint"]
    browser_ws_url = endpoint.get("browser_ws_url")
    if not browser_ws_url:
        return {
            "status": "error",
            "reason": "Discovery reported cdp_available but did not include browser_ws_url.",
            "discovery": discovery,
        }

    run_dir = Path(args.output_dir) if args.output_dir else default_output_dir()
    before_screenshot = run_dir / "xhs_before_scroll.png"
    after_screenshot = run_dir / "xhs_after_scroll.png"

    from cdp_connect import connect_cdp_with_retry

    client = None
    try:
        client = await connect_cdp_with_retry(browser_ws_url, per_attempt_timeout=args.timeout)
        page = await create_controlled_tab(client, "about:blank")
        await page.mark_title(f"{SOCAI_TITLE_PREFIX} — XHS")
        await page.navigate(args.url, wait_seconds=args.load_wait)
        marked_title = await page.mark_title(f"{SOCAI_TITLE_PREFIX} — XHS")

        before_state = await read_page_probe(page)
        await page.capture_screenshot(before_screenshot)

        login_prompt_initial = bool(before_state.get("possibleLoginPrompt"))
        login_wait_started = time.monotonic()
        login_waited_seconds = 0.0
        if login_prompt_initial and args.login_wait > 0:
            deadline = time.monotonic() + args.login_wait
            while time.monotonic() < deadline:
                await asyncio.sleep(args.login_poll_interval)
                before_state = await read_page_probe(page)
                if not before_state.get("possibleLoginPrompt"):
                    break
            login_waited_seconds = round(time.monotonic() - login_wait_started, 2)

        scroll_y = await page.scroll(args.scroll_delta)
        await asyncio.sleep(args.after_scroll_wait)
        after_state = await read_page_probe(page)
        await page.capture_screenshot(after_screenshot)

        diagnostics = {
            "opened_requested_url": args.url,
            "landed_url": after_state.get("url") or before_state.get("url"),
            "title": after_state.get("title") or before_state.get("title"),
            "readyState": after_state.get("readyState") or before_state.get("readyState"),
            "scrollY": scroll_y,
            "bodyTextLength": after_state.get("bodyTextLength", 0),
            "hasXiaohongshuText": bool(after_state.get("hasXiaohongshuText") or before_state.get("hasXiaohongshuText")),
            "possibleSecurityVerification": bool(after_state.get("possibleSecurityVerification") or before_state.get("possibleSecurityVerification")),
            "possibleLoginPrompt": bool(after_state.get("possibleLoginPrompt") or before_state.get("possibleLoginPrompt")),
            "loginPromptInitial": login_prompt_initial,
            "loginWaitSeconds": float(args.login_wait),
            "loginWaitedSeconds": login_waited_seconds,
        }

        operated = bool(
            diagnostics["landed_url"]
            and "xiaohongshu.com" in diagnostics["landed_url"]
            and Path(after_screenshot).exists()
        )
        login_required = operated and diagnostics["possibleLoginPrompt"]
        security_verification = operated and diagnostics["possibleSecurityVerification"]

        if not operated:
            status = "xhs_probe_inconclusive"
            reason = "Could not confirm XHS operation from URL/screenshot diagnostics."
        elif security_verification:
            status = "xhs_security_verification"
            reason = "Opened Xiaohongshu, but the page appears to be in a security verification state."
        elif login_required:
            status = "xhs_login_required"
            reason = "Opened Xiaohongshu in a Socai-controlled tab, but the user still needs to log in."
        else:
            status = "xhs_probe_ready"
            reason = "Opened Xiaohongshu in a Socai-controlled Chrome tab and confirmed the page is reachable."

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


def default_output_dir() -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return Path(tempfile.gettempdir()) / "socai" / f"xhs_probe_{stamp}"


def print_human(result: dict[str, Any]) -> None:
    print(f"Socai XHS probe status: {result['status']}")
    print(f"Reason: {result.get('reason')}")

    if result["status"] == "setup_required":
        print(f"Inspect URL: {INSPECT_URL}")
        if result.get("opened_inspect_url"):
            print("Opened Chrome inspect permission page.")
        return

    if result["status"] not in {"xhs_probe_ready", "xhs_probe_inconclusive", "xhs_login_required", "xhs_security_verification"}:
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


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
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
    parser.add_argument("--login-wait", type=float, default=0.0, help="Seconds to wait for the user to scan/login if XHS asks for login")
    parser.add_argument("--login-poll-interval", type=float, default=2.0, help="Seconds between XHS login-state checks")
    parser.add_argument("--scroll-delta", type=int, default=650, help="Pixels to scroll during the proof")
    parser.add_argument("--after-scroll-wait", type=float, default=1.5, help="Seconds to wait after scrolling")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    result = asyncio.run(run(args))

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_human(result)

    return 0 if result["status"] in {"xhs_probe_ready", "xhs_probe_inconclusive", "xhs_login_required", "xhs_security_verification", "setup_required"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
