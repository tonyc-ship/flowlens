#!/usr/bin/env python3
"""Discover whether the user's existing Chrome profile exposes a CDP endpoint.

Session 1 scope only:
- inspect known Google Chrome user-data locations
- read DevToolsActivePort when present
- optionally probe common local DevTools ports
- print either `cdp_available` or `setup_required`

This script does not attach to Chrome, create tabs, or control pages. CDP attach
starts in the next prototype session.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

INSPECT_URL = "chrome://inspect/#remote-debugging"
COMMON_DEVTOOLS_PORTS = (9222, 9223)


@dataclass
class Endpoint:
    source: str
    port: int | None = None
    browser_ws_url: str | None = None
    http_version_url: str | None = None
    user_data_dir: str | None = None
    version: dict[str, Any] | None = None


@dataclass
class Candidate:
    source: str
    user_data_dir: str | None = None
    devtools_active_port: str | None = None
    port: int | None = None
    browser_ws_url: str | None = None
    live: bool = False
    error: str | None = None


def chrome_user_data_dirs() -> list[Path]:
    """Return candidate Chrome user-data roots for this prototype.

    The prototype target is macOS + Google Chrome + existing default profile.
    `SOCAI_CHROME_USER_DATA_DIR` can override/add a custom root for tests or
    local development.
    """

    candidates: list[Path] = []
    if override := os.environ.get("SOCAI_CHROME_USER_DATA_DIR"):
        candidates.append(Path(override).expanduser())
        if os.environ.get("SOCAI_CHROME_USER_DATA_DIR_ONLY") == "1":
            return candidates

    home = Path.home()
    system = platform.system()
    if system == "Darwin":
        candidates.append(home / "Library/Application Support/Google/Chrome")
    elif system == "Windows":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            candidates.append(Path(local_app_data) / "Google/Chrome/User Data")
    else:
        candidates.extend(
            [
                home / ".config/google-chrome",
                home / ".config/chromium",
            ]
        )

    # Preserve order while removing duplicates.
    seen: set[str] = set()
    unique: list[Path] = []
    for path in candidates:
        key = str(path)
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def read_devtools_active_port(user_data_dir: Path) -> Candidate:
    marker = user_data_dir / "DevToolsActivePort"
    candidate = Candidate(
        source="devtools_active_port",
        user_data_dir=str(user_data_dir),
        devtools_active_port=str(marker),
    )

    try:
        lines = marker.read_text(encoding="utf-8").strip().splitlines()
    except FileNotFoundError:
        candidate.error = "DevToolsActivePort not found"
        return candidate
    except OSError as exc:
        candidate.error = f"Could not read DevToolsActivePort: {exc}"
        return candidate

    if len(lines) < 2:
        candidate.error = "DevToolsActivePort exists but does not contain port + browser path"
        return candidate

    try:
        port = int(lines[0].strip())
    except ValueError:
        candidate.error = f"Invalid DevToolsActivePort port: {lines[0]!r}"
        return candidate

    browser_path = lines[1].strip()
    candidate.port = port
    candidate.browser_ws_url = f"ws://127.0.0.1:{port}{browser_path}"
    candidate.live = tcp_port_live(port)
    if not candidate.live:
        candidate.error = f"DevToolsActivePort found, but 127.0.0.1:{port} is not accepting connections"
    return candidate


def tcp_port_live(port: int, timeout: float = 0.5) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def fetch_json(url: str, timeout: float = 1.5) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def endpoint_from_http_port(port: int, source: str) -> Endpoint | None:
    version_url = f"http://127.0.0.1:{port}/json/version"
    try:
        version = fetch_json(version_url)
    except (OSError, urllib.error.URLError, json.JSONDecodeError, TimeoutError):
        return None
    browser_ws_url = version.get("webSocketDebuggerUrl")
    if not browser_ws_url:
        return None
    return Endpoint(
        source=source,
        port=port,
        browser_ws_url=browser_ws_url,
        http_version_url=version_url,
        version={
            key: version.get(key)
            for key in ("Browser", "Protocol-Version", "User-Agent", "V8-Version")
            if key in version
        },
    )


def discover_chrome_cdp() -> dict[str, Any]:
    candidates: list[Candidate] = []

    # Explicit HTTP endpoint is useful for tests and local development, but the
    # product default remains the user's existing Chrome profile.
    if explicit_url := os.environ.get("SOCAI_CDP_URL"):
        version_url = explicit_url.rstrip("/") + "/json/version"
        try:
            version = fetch_json(version_url)
            endpoint = Endpoint(
                source="SOCAI_CDP_URL",
                port=None,
                browser_ws_url=version.get("webSocketDebuggerUrl"),
                http_version_url=version_url,
                version={
                    key: version.get(key)
                    for key in ("Browser", "Protocol-Version", "User-Agent", "V8-Version")
                    if key in version
                },
            )
            if endpoint.browser_ws_url:
                return result_available(endpoint, candidates)
        except Exception as exc:  # noqa: BLE001 - surfaced as diagnostics
            candidates.append(Candidate(source="SOCAI_CDP_URL", error=str(exc)))

    if explicit_ws := os.environ.get("SOCAI_CDP_WS"):
        endpoint = Endpoint(source="SOCAI_CDP_WS", browser_ws_url=explicit_ws)
        return result_available(endpoint, candidates)

    for user_data_dir in chrome_user_data_dirs():
        candidate = read_devtools_active_port(user_data_dir)
        candidates.append(candidate)
        if candidate.live and candidate.browser_ws_url:
            endpoint = endpoint_from_http_port(candidate.port, source="devtools_active_port") if candidate.port else None
            if endpoint:
                endpoint.user_data_dir = candidate.user_data_dir
            else:
                endpoint = Endpoint(
                    source="devtools_active_port",
                    port=candidate.port,
                    browser_ws_url=candidate.browser_ws_url,
                    http_version_url=(
                        f"http://127.0.0.1:{candidate.port}/json/version" if candidate.port else None
                    ),
                    user_data_dir=candidate.user_data_dir,
                )
            return result_available(endpoint, candidates)

    for port in COMMON_DEVTOOLS_PORTS:
        endpoint = endpoint_from_http_port(port, source="common_devtools_port")
        if endpoint:
            return result_available(endpoint, candidates)

    return {
        "status": "setup_required",
        "reason": "No live Chrome CDP endpoint was found for the existing Chrome profile.",
        "inspect_url": INSPECT_URL,
        "profile_paths_checked": [str(path) for path in chrome_user_data_dirs()],
        "candidates": [asdict(candidate) for candidate in candidates],
        "instructions": setup_instructions(),
    }


def result_available(endpoint: Endpoint, candidates: list[Candidate]) -> dict[str, Any]:
    return {
        "status": "cdp_available",
        "reason": "A local Chrome CDP endpoint is available.",
        "inspect_url": INSPECT_URL,
        "endpoint": asdict(endpoint),
        "profile_paths_checked": [str(path) for path in chrome_user_data_dirs()],
        "candidates": [asdict(candidate) for candidate in candidates],
        "next_step": "Session 2 can connect to this browser WebSocket and call Target.getTargets.",
    }


def setup_instructions() -> list[str]:
    return [
        "Open Google Chrome using the profile that is already logged in to Xiaohongshu.",
        f"Open {INSPECT_URL} in Chrome.",
        "If Chrome shows a remote-debugging permission prompt, approve it. Tick the checkbox if Chrome offers one.",
        "Re-run: python3 apps/socai/prototype/chrome_discovery.py",
    ]


def open_inspect_page() -> None:
    if platform.system() == "Darwin":
        subprocess.run(["open", "-a", "Google Chrome", INSPECT_URL], check=False)
        return

    # Fallback for non-macOS development machines.
    import webbrowser

    webbrowser.open(INSPECT_URL, new=2)


def print_human(result: dict[str, Any]) -> None:
    print(f"SocAI Chrome discovery status: {result['status']}")
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
