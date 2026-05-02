"""CDP target listing helpers."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from .client import connect_cdp_with_retry
from .discovery import INSPECT_URL, discover_chrome_cdp, open_inspect_page
from .errors import CDPConnectionError, exception_message

INTERNAL_URL_PREFIXES = (
    "chrome://",
    "chrome-untrusted://",
    "devtools://",
    "chrome-extension://",
    "about:",
)


@dataclass(frozen=True)
class TargetListConfig:
    timeout: float = 10.0
    show_internal: bool = False
    include_all_targets: bool = False
    open_inspect_if_needed: bool = False


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
    """Connect to Chrome CDP and return raw target infos."""

    client = await connect_cdp_with_retry(browser_ws_url, per_attempt_timeout=timeout)
    try:
        result = await asyncio.wait_for(client.send_raw("Target.getTargets"), timeout=timeout)
        return result.get("targetInfos", [])
    except Exception as exc:  # noqa: BLE001 - surfaced to runtime diagnostics
        message = exception_message(exc)
        raise CDPConnectionError(f"CDP command failed: {message}") from exc
    finally:
        try:
            await asyncio.wait_for(client.stop(), timeout=2)
        except Exception:
            pass


async def list_chrome_targets(config: TargetListConfig | None = None) -> dict[str, Any]:
    """Discover Chrome, call Target.getTargets, and return compact diagnostics."""

    config = config or TargetListConfig()
    discovery = discover_chrome_cdp()
    if discovery["status"] != "cdp_available":
        if config.open_inspect_if_needed:
            open_inspect_page()
        return {
            "status": "setup_required",
            "reason": discovery.get("reason"),
            "inspect_url": INSPECT_URL,
            "opened_inspect_url": config.open_inspect_if_needed,
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
        targets = await get_targets(browser_ws_url, timeout=config.timeout)
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
        if config.show_internal or (target.get("type") == "page" and not is_internal_target(target))
    ]

    return {
        "status": "connected",
        "reason": "Connected to Chrome CDP and called Target.getTargets.",
        "endpoint": endpoint,
        "target_count": len(all_targets),
        "shown_target_count": len(visible_targets),
        "targets": visible_targets,
        "all_targets": all_targets if config.include_all_targets else None,
    }
