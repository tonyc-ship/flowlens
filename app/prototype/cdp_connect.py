"""Shared CDP connection helper with retry logic for the Chrome Allow dialog.

Chrome shows one "Allow remote debugging?" dialog per WebSocket connection
attempt. Each attempt has a ~10s handshake timeout inside cdp-use/websockets.
If the user doesn't click Allow fast enough, the attempt times out and a new
one must be made (which shows the dialog again).

This module wraps that into a retry loop so the user has multiple chances to
click Allow before the overall operation gives up.
"""
from __future__ import annotations

import asyncio
import sys
from typing import Any


async def connect_cdp_with_retry(
    browser_ws_url: str,
    max_attempts: int = 4,
    per_attempt_timeout: float = 12.0,
    pause_between: float = 1.0,
) -> Any:
    """Connect to Chrome CDP with retries for the Allow dialog.

    Returns a started CDPClient. Caller must stop() it when done.

    Raises RuntimeError with a diagnostic message if all attempts fail.
    """
    from cdp_use.client import CDPClient

    last_error: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        client = CDPClient(browser_ws_url)
        try:
            await asyncio.wait_for(client.start(), timeout=per_attempt_timeout)
            return client
        except Exception as exc:
            last_error = exc
            message = str(exc).strip() or exc.__class__.__name__
            print(
                f"[socai] CDP connect attempt {attempt}/{max_attempts} failed: {message}",
                file=sys.stderr,
            )
            # Clean up the failed client before retrying.
            try:
                await asyncio.wait_for(client.stop(), timeout=2)
            except Exception:
                pass
            if attempt < max_attempts:
                print(
                    f"[socai] Retrying in {pause_between}s — click Allow in Chrome if prompted.",
                    file=sys.stderr,
                )
                await asyncio.sleep(pause_between)

    raise RuntimeError(
        f"CDP connection failed after {max_attempts} attempts. "
        f"Last error: {last_error}. "
        "Open chrome://inspect/#remote-debugging, approve Chrome remote-debugging "
        "permission, then retry."
    )
