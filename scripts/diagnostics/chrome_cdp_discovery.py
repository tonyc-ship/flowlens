#!/usr/bin/env python3
"""Discover whether the user's existing Chrome profile exposes a CDP endpoint."""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from flowlens.cdp.discovery import INSPECT_URL, discover_chrome_cdp, open_inspect_page


def print_human(result: dict[str, Any]) -> None:
    print(f"FlowLens Chrome discovery status: {result['status']}")
    print(f"Reason: {result.get('reason')}")

    if result["status"] == "cdp_available":
        endpoint = result["endpoint"]
        print(f"Source: {endpoint.get('source')}")
        if endpoint.get("port"):
            print(f"Port: {endpoint['port']}")
        if endpoint.get("user_data_dir"):
            print(f"User data dir: {endpoint['user_data_dir']}")
        print(f"Browser WebSocket: {endpoint.get('browser_ws_url')}")
        if endpoint.get("version"):
            print(f"Chrome version: {endpoint['version'].get('Browser')}")
        print(f"Next: {result.get('next_step')}")
        return

    print("\nSetup instructions:")
    for index, instruction in enumerate(result.get("instructions", []), start=1):
        print(f"{index}. {instruction}")

    checked = result.get("profile_paths_checked") or []
    if checked:
        print("\nProfile paths checked:")
        for path in checked:
            print(f"- {path}")

    candidates = result.get("candidates") or []
    if candidates:
        print("\nDiagnostics:")
        for candidate in candidates:
            print(f"- {candidate.get('source')}: {candidate.get('error') or 'not live'}")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON only")
    parser.add_argument(
        "--open-inspect",
        action="store_true",
        help=f"Open {INSPECT_URL} in Google Chrome if setup is required",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    result = discover_chrome_cdp()

    if args.open_inspect and result["status"] != "cdp_available":
        open_inspect_page()
        result["opened_inspect_url"] = True
    else:
        result["opened_inspect_url"] = False

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_human(result)
        if result["opened_inspect_url"]:
            print("\nOpened Chrome inspect permission page.")

    return 0 if result["status"] in {"cdp_available", "setup_required"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
