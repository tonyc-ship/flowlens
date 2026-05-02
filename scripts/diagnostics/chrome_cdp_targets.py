#!/usr/bin/env python3
"""Connect to existing Chrome over CDP and list page targets."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from flowlens.cdp.discovery import INSPECT_URL
from flowlens.cdp.targets import TargetListConfig, list_chrome_targets


def print_human(result: dict[str, Any]) -> None:
    print(f"FlowLens CDP target listing status: {result['status']}")
    print(f"Reason: {result.get('reason')}")

    if result["status"] == "setup_required":
        print("\nSetup required:")
        print(f"- Open {INSPECT_URL}")
        print("- Approve Chrome remote-debugging/inspect permission if prompted")
        print("- Re-run this diagnostic")
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
    result = asyncio.run(
        list_chrome_targets(
            TargetListConfig(
                timeout=args.timeout,
                show_internal=args.all,
                include_all_targets=args.all_json,
                open_inspect_if_needed=args.open_inspect,
            )
        )
    )

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_human(result)

    return 0 if result["status"] in {"connected", "setup_required"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
