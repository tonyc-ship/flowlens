#!/usr/bin/env python3
"""Connect to existing Chrome over CDP and list page targets.

Diagnostic scope:
- reuse Chrome discovery
- connect to the discovered browser WebSocket with cdp-use
- call Target.getTargets
- print current page targets

This script does not create tabs, attach to a target, navigate, or control pages.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from chrome_discovery import INSPECT_URL, discover_chrome_cdp, open_inspect_page

INTERNAL_URL_PREFIXES = (
    "chrome://",
    "chrome-untrusted://",
    "devtools://",
    "chrome-extension://",
    "about:",
)


def exception_message(exc: BaseException) -> str:
    message = str(exc).strip()
    if message:
        return message
    return exc.__class__.__name__


def cdp_use_install_help(script: str = "app/prototype/cdp_targets.py") -> str:
    return "\n".join(
        [
            "Missing Python package: cdp-use",
            "Run with uv without changing the project environment:",
            f"  uv run --no-project --with cdp-use==1.4.5 --python 3.11 python {script}",
            "Or install desktop diagnostic requirements into a Python 3.11 environment:",
            "  python3.11 -m pip install -r app/requirements.txt",
        ]
    )


def is_internal_target(target: dict[str, Any]) -> bool:
    url = target.get("url") or ""
    return url.startswith(INTERNAL_URL_PREFIXES)


def compact_target(target: dict[str, Any]) -> dict[str, Any]:
    return {
        "targetId": target.get("targetId"),
        "type": target.get("type"),
        "title": target.get("title"),
        "url": target.get("url"),
        "attached": target.get("attached"),
        "canAccessOpener": target.get("canAccessOpener"),
    }


async def get_targets(browser_ws_url: str, timeout: float) -> list[dict[str, Any]]:
    try:
        from cdp_connect import connect_cdp_with_retry
    except ModuleNotFoundError as exc:
        raise RuntimeError(cdp_use_install_help()) from exc

    client = await connect_cdp_with_retry(browser_ws_url, per_attempt_timeout=timeout)
    try:
        result = await asyncio.wait_for(client.send_raw("Target.getTargets"), timeout=timeout)
        return result.get("targetInfos", [])
    except Exception as exc:  # noqa: BLE001 - surfaced to CLI diagnostics
        message = exception_message(exc)
        raise RuntimeError(f"CDP command failed: {message}") from exc
    finally:
        try:
            await asyncio.wait_for(client.stop(), timeout=2)
        except Exception:
            pass


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

    endpoint = discovery["endpoint"]
    browser_ws_url = endpoint.get("browser_ws_url")
    if not browser_ws_url:
        return {
            "status": "error",
            "reason": "Discovery reported cdp_available but did not include browser_ws_url.",
            "discovery": discovery,
        }

    try:
        targets = await get_targets(browser_ws_url, timeout=args.timeout)
    except RuntimeError as exc:
        return {
            "status": "connection_failed",
            "reason": str(exc),
            "inspect_url": INSPECT_URL,
            "discovery": discovery,
        }

    all_targets = [compact_target(target) for target in targets]
    visible_targets = [
        target
        for target in all_targets
        if args.all or (target.get("type") == "page" and not is_internal_target(target))
    ]

    return {
        "status": "connected",
        "reason": "Connected to Chrome CDP and called Target.getTargets.",
        "endpoint": endpoint,
        "target_count": len(all_targets),
        "shown_target_count": len(visible_targets),
        "targets": visible_targets,
        "all_targets": all_targets if args.all_json else None,
    }


def print_human(result: dict[str, Any]) -> None:
    print(f"Socai CDP target listing status: {result['status']}")
    print(f"Reason: {result.get('reason')}")

    if result["status"] == "setup_required":
        print("\nSetup required:")
        print(f"- Open {INSPECT_URL}")
        print("- Approve Chrome remote-debugging/inspect permission if prompted")
        print("- Re-run this script")
        if result.get("opened_inspect_url"):
            print("\nOpened Chrome inspect permission page.")
        return

    if result["status"] != "connected":
        print("\nConnection did not complete.")
        if result.get("inspect_url"):
            print(f"Inspect URL: {result['inspect_url']}")
        return

    endpoint = result.get("endpoint") or {}
    if endpoint.get("port"):
        print(f"Port: {endpoint['port']}")
    if endpoint.get("user_data_dir"):
        print(f"User data dir: {endpoint['user_data_dir']}")
    print(f"Targets returned by Chrome: {result['target_count']}")
    print(f"Page targets shown: {result['shown_target_count']}")

    for index, target in enumerate(result.get("targets") or [], start=1):
        title = target.get("title") or "(untitled)"
        url = target.get("url") or "(no url)"
        target_type = target.get("type") or "unknown"
        print(f"\n{index}. [{target_type}] {title}")
        print(f"   {url}")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON only")
    parser.add_argument("--all", action="store_true", help="Show internal Chrome targets too")
    parser.add_argument(
        "--all-json",
        action="store_true",
        help="Include all raw compact targets in JSON output under all_targets",
    )
    parser.add_argument(
        "--open-inspect",
        action="store_true",
        help=f"Open {INSPECT_URL} in Google Chrome if setup is required",
    )
    parser.add_argument("--timeout", type=float, default=10.0, help="CDP operation timeout in seconds")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    result = asyncio.run(run(args))

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_human(result)

    return 0 if result["status"] in {"connected", "setup_required"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
