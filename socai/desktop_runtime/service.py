"""Service methods for the Socai desktop Python sidecar.

This module is intentionally dependency-light.  During the migration from the
old app-local diagnostic scripts to the unified desktop runtime, the runtime
keeps those scripts as implementation details and exposes a stable command
surface to the Tauri app.
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from importlib import metadata
from pathlib import Path
from typing import Any

INSPECT_URL = "chrome://inspect/#remote-debugging"
CDP_USE_VERSION = "1.4.5"


class RuntimeMethodError(RuntimeError):
    """Raised when a runtime method cannot complete."""


def package_version() -> str:
    try:
        return metadata.version("socai")
    except metadata.PackageNotFoundError:
        return "0.1.0"


def repo_root() -> Path:
    override = os.environ.get("SOCAI_REPO_ROOT") or os.environ.get("SOCAI_DESKTOP_REPO_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


def diagnostics_dir() -> Path:
    override = os.environ.get("SOCAI_DESKTOP_DIAGNOSTICS_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return repo_root() / "app" / "prototype"


def health() -> dict[str, Any]:
    return {
        "appName": "Socai",
        "version": package_version(),
        "os": platform.system().lower() or sys.platform,
        "arch": platform.machine() or platform.processor(),
        "backendMode": "Tauri + Socai Python runtime",
        "ready": True,
    }


def open_chrome_inspect() -> dict[str, Any]:
    if platform.system() == "Darwin":
        command = ["open", "-a", "Google Chrome", INSPECT_URL]
    elif platform.system() == "Windows":
        command = ["cmd", "/C", "start", INSPECT_URL]
    else:
        command = ["xdg-open", INSPECT_URL]

    completed = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeMethodError(
            "Failed to open Chrome inspect page: "
            f"exit={completed.returncode} stderr={completed.stderr.strip()}"
        )
    return {"opened": True, "inspectUrl": INSPECT_URL}


def action_specs() -> dict[str, tuple[str, list[str], bool]]:
    """Return action -> (script, args, needs_cdp_use) mapping."""

    return {
        "connect_chrome": ("chrome_discovery.py", ["--json"], False),
        "list_targets": ("cdp_targets.py", ["--json", "--timeout", "30"], True),
        "controlled_tab": ("cdp_controlled_tab.py", ["--json", "--timeout", "30"], True),
        "capture_test_screenshot": ("cdp_controlled_tab.py", ["--json", "--timeout", "30"], True),
        "xhs_probe": ("cdp_xhs_probe.py", ["--json", "--timeout", "30"], True),
        "xhs_connection_test": (
            "cdp_xhs_probe.py",
            [
                "--json",
                "--timeout",
                "30",
                "--load-wait",
                "8",
                "--login-wait",
                "90",
                "--login-poll-interval",
                "2",
            ],
            True,
        ),
    }


def run_action(action: str) -> dict[str, Any]:
    specs = action_specs()
    if action not in specs:
        raise RuntimeMethodError(f"Unknown Socai runtime action: {action}")

    script_name, extra_args, needs_cdp_use = specs[action]
    script_path = diagnostics_dir() / script_name
    if not script_path.exists():
        raise RuntimeMethodError(f"Desktop diagnostic script not found: {script_path}")

    output = run_script(script_path, extra_args, needs_cdp_use=needs_cdp_use)
    stdout = output.stdout or ""
    stderr = output.stderr or ""
    parsed_json = parse_json_stdout(stdout)

    return {
        "action": action,
        "ok": output.returncode == 0,
        "exitCode": output.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "json": parsed_json,
    }


def run_script(script_path: Path, args: list[str], *, needs_cdp_use: bool) -> subprocess.CompletedProcess[str]:
    use_uv = needs_cdp_use and should_use_uv_for_cdp()
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    env.setdefault("SOCAI_REPO_ROOT", str(repo_root()))

    if use_uv:
        command = [
            "uv",
            "run",
            "--no-project",
            "--with",
            f"cdp-use=={CDP_USE_VERSION}",
            "--python",
            "3.11",
            "python",
            str(script_path),
            *args,
        ]
    else:
        command = [sys.executable, str(script_path), *args]

    return subprocess.run(
        command,
        cwd=repo_root(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        env=env,
    )


def should_use_uv_for_cdp() -> bool:
    explicit = os.environ.get("SOCAI_DESKTOP_USE_UV_FOR_CDP")
    if explicit is not None:
        return explicit.strip().lower() not in {"0", "false", "no", "off"}

    # A bundled app runtime should already contain its Python dependencies.
    if os.environ.get("SOCAI_DESKTOP_BUNDLED_RUNTIME") == "1":
        return False

    try:
        __import__("cdp_use")
        return False
    except ModuleNotFoundError:
        return True


def parse_json_stdout(stdout: str) -> Any | None:
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return None


def handle_method(method: str, params: dict[str, Any] | None = None) -> Any:
    params = params or {}

    if method == "health":
        return health()
    if method == "open_chrome_inspect":
        return open_chrome_inspect()
    if method == "run_action":
        action = params.get("action")
        if not isinstance(action, str) or not action:
            raise RuntimeMethodError("run_action requires a non-empty string 'action' parameter")
        return run_action(action)
    if method == "shutdown":
        return {"shutdown": True}

    # Convenience methods keep the frontend/Rust command names stable while the
    # transport moves to a sidecar.
    direct_actions = {
        "connect_chrome": "connect_chrome",
        "list_chrome_targets": "list_targets",
        "create_controlled_tab": "controlled_tab",
        "open_xhs_probe": "xhs_probe",
        "xhs_connection_test": "xhs_connection_test",
        "capture_test_screenshot": "capture_test_screenshot",
    }
    if method in direct_actions:
        return run_action(direct_actions[method])

    raise RuntimeMethodError(f"Unknown Socai desktop runtime method: {method}")
