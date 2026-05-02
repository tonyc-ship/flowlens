"""Chrome CDP endpoint discovery.

The desktop app uses the user's existing Chrome profile. This module finds a
local Chrome DevTools Protocol endpoint by checking explicit environment
overrides, Chrome's ``DevToolsActivePort`` marker, and common local debug ports.
"""
from __future__ import annotations

import json
import os
import platform
import socket
import subprocess
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
    """Return candidate Chrome user-data roots.

    ``FLOWLENS_CHROME_USER_DATA_DIR`` can override/add a custom root for tests or
    local development. Set ``FLOWLENS_CHROME_USER_DATA_DIR_ONLY=1`` to skip default
    profile paths.
    """

    candidates: list[Path] = []
    if override := os.environ.get("FLOWLENS_CHROME_USER_DATA_DIR"):
        candidates.append(Path(override).expanduser())
        if os.environ.get("FLOWLENS_CHROME_USER_DATA_DIR_ONLY") == "1":
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
        candidates.extend([home / ".config/google-chrome", home / ".config/chromium"])

    seen: set[str] = set()
    unique: list[Path] = []
    for path in candidates:
        key = str(path)
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def read_devtools_active_port(user_data_dir: Path) -> Candidate:
    """Read Chrome's DevToolsActivePort marker for a user-data root."""

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
    """Discover a live Chrome CDP endpoint and return diagnostic metadata."""

    candidates: list[Candidate] = []

    if explicit_url := os.environ.get("FLOWLENS_CDP_URL"):
        version_url = explicit_url.rstrip("/") + "/json/version"
        try:
            version = fetch_json(version_url)
            endpoint = Endpoint(
                source="FLOWLENS_CDP_URL",
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
            candidates.append(Candidate(source="FLOWLENS_CDP_URL", error=str(exc)))

    if explicit_ws := os.environ.get("FLOWLENS_CDP_WS"):
        return result_available(Endpoint(source="FLOWLENS_CDP_WS", browser_ws_url=explicit_ws), candidates)

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
                    http_version_url=f"http://127.0.0.1:{candidate.port}/json/version" if candidate.port else None,
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
        "next_step": "FlowLens can connect to this browser WebSocket and call Target.getTargets.",
    }


def setup_instructions() -> list[str]:
    return [
        "Open Google Chrome using the profile that is already logged in to the target site.",
        f"Open {INSPECT_URL} in Chrome.",
        "If Chrome shows a remote-debugging permission prompt, approve it. Tick the checkbox if Chrome offers one.",
        "Re-run the FlowLens Chrome CDP diagnostic.",
    ]


def open_inspect_page() -> None:
    """Open Chrome's remote-debugging setup page."""

    if platform.system() == "Darwin":
        subprocess.run(["open", "-a", "Google Chrome", INSPECT_URL], check=False)
        return

    import webbrowser

    webbrowser.open(INSPECT_URL, new=2)
