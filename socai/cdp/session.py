"""Helpers for connecting to the user's existing Chrome CDP endpoint."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .client import connect_cdp_with_retry
from .discovery import INSPECT_URL, discover_chrome_cdp, open_inspect_page


@dataclass(frozen=True)
class ExistingChromeConfig:
    """Configuration for connecting to an already-running Chrome instance."""

    timeout: float = 10.0
    open_inspect_if_needed: bool = False


async def connect_existing_chrome(
    config: ExistingChromeConfig | None = None,
) -> tuple[Any | None, dict[str, Any], dict[str, Any] | None]:
    """Discover and connect to existing Chrome.

    Returns ``(client, discovery, setup_result)``. If setup is required,
    ``client`` is ``None`` and ``setup_result`` contains the user-facing
    diagnostic dictionary. Callers own the returned client and must stop it.
    """

    config = config or ExistingChromeConfig()
    discovery = discover_chrome_cdp()
    if discovery["status"] != "cdp_available":
        if config.open_inspect_if_needed:
            open_inspect_page()
        return (
            None,
            discovery,
            {
                "status": "setup_required",
                "reason": discovery.get("reason"),
                "inspect_url": INSPECT_URL,
                "opened_inspect_url": config.open_inspect_if_needed,
                "discovery": discovery,
            },
        )

    endpoint = discovery.get("endpoint") or {}
    browser_ws_url = endpoint.get("browser_ws_url")
    if not browser_ws_url:
        return (
            None,
            discovery,
            {
                "status": "error",
                "reason": "Discovery reported cdp_available but did not include browser_ws_url.",
                "discovery": discovery,
            },
        )

    client = await connect_cdp_with_retry(browser_ws_url, per_attempt_timeout=config.timeout)
    return client, discovery, None
