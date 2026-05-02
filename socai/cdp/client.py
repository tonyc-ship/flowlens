"""CDP client connection helpers."""
from __future__ import annotations

import asyncio
import sys
from typing import Any

from .errors import CDPDependencyError, cdp_use_install_help


async def connect_cdp_with_retry(
    browser_ws_url: str,
    max_attempts: int = 4,
    per_attempt_timeout: float = 12.0,
    pause_between: float = 1.0,
) -> Any:
    """Connect to Chrome CDP with retries for Chrome's Allow dialog.

    Returns a started ``cdp_use.client.CDPClient``. Caller must stop it when the
    operation is done.
    """

    try:
        from cdp_use.client import CDPClient
    except ModuleNotFoundError as exc:
        raise CDPDependencyError(cdp_use_install_help()) from exc

    last_error: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        client = CDPClient(browser_ws_url)
        try:
            await asyncio.wait_for(client.start(), timeout=per_attempt_timeout)
            return client
        except Exception as exc:  # noqa: BLE001 - convert to diagnostic after retries
            last_error = exc
            message = str(exc).strip() or exc.__class__.__name__
            print(
                f"[socai] CDP connect attempt {attempt}/{max_attempts} failed: {message}",
                file=sys.stderr,
            )
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
