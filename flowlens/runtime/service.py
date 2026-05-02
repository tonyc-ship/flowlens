"""Service methods for the FlowLens Python runtime sidecar.

The Tauri app talks to this module through JSON-RPC over stdio. Runtime actions
call importable FlowLens packages directly; the old app-local prototype path is
deprecated and no longer part of runtime dispatch.
"""
from __future__ import annotations

import asyncio
import json
import platform
import subprocess
import sys
from importlib import metadata
from typing import Any

from flowlens.cdp.diagnostics import ControlledTabConfig, run_controlled_tab_diagnostic
from flowlens.cdp.discovery import INSPECT_URL, discover_chrome_cdp
from flowlens.cdp.targets import TargetListConfig, list_chrome_targets
from flowlens.platforms.xhs.cdp_diagnostics import XHS_SUCCESS_STATUSES, XHSCdpProbeConfig, run_xhs_cdp_probe


class RuntimeMethodError(RuntimeError):
    """Raised when a runtime method cannot complete."""


def package_version() -> str:
    try:
        return metadata.version("flowlens")
    except metadata.PackageNotFoundError:
        return "0.1.0"


def health() -> dict[str, Any]:
    return {
        "appName": "FlowLens",
        "version": package_version(),
        "os": platform.system().lower() or sys.platform,
        "arch": platform.machine() or platform.processor(),
        "backendMode": "Tauri + FlowLens Python runtime",
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


def run_action(action: str) -> dict[str, Any]:
    if action == "connect_chrome":
        return runtime_result_from_json(
            action,
            discover_chrome_cdp(),
            success_statuses={"cdp_available", "setup_required"},
        )
    if action == "list_targets":
        result = asyncio.run(list_chrome_targets(TargetListConfig(timeout=30.0)))
        return runtime_result_from_json(
            action,
            result,
            success_statuses={"connected", "setup_required"},
        )
    if action in {"controlled_tab", "capture_test_screenshot"}:
        result = asyncio.run(run_controlled_tab_diagnostic(ControlledTabConfig(timeout=30.0)))
        return runtime_result_from_json(
            action,
            result,
            success_statuses={"controlled_tab_ready", "setup_required"},
        )
    if action == "xhs_probe":
        result = asyncio.run(run_xhs_cdp_probe(XHSCdpProbeConfig(timeout=30.0)))
        return runtime_result_from_json(action, result, success_statuses=XHS_SUCCESS_STATUSES)
    if action == "xhs_connection_test":
        result = asyncio.run(
            run_xhs_cdp_probe(
                XHSCdpProbeConfig(
                    timeout=30.0,
                    load_wait=8.0,
                    login_wait=90.0,
                    login_poll_interval=2.0,
                )
            )
        )
        return runtime_result_from_json(action, result, success_statuses=XHS_SUCCESS_STATUSES)

    raise RuntimeMethodError(f"Unknown FlowLens runtime action: {action}")


def runtime_result_from_json(
    action: str,
    result: dict[str, Any],
    *,
    success_statuses: set[str],
) -> dict[str, Any]:
    ok = result.get("status") in success_statuses
    return {
        "action": action,
        "ok": ok,
        "exitCode": 0 if ok else 1,
        "stdout": json.dumps(result, ensure_ascii=False, indent=2),
        "stderr": "",
        "json": result,
    }


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

    raise RuntimeMethodError(f"Unknown FlowLens desktop runtime method: {method}")
