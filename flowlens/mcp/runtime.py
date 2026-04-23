"""Shared bridge lifecycle for FlowLens MCP tools.

The MCP server is spawned once per stdio session. All site tool modules
share a single ExtensionBridge (and the ToolContext / MediaProcessor
that depend on it), started lazily on the first tool call so startup
does not block before the host actually asks for work.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from ..agent.tool import ToolContext
from ..core.bridge import ExtensionBridge
from ..perception.media import MediaProcessor


_bridge: ExtensionBridge | None = None
_ctx: ToolContext | None = None
_media: MediaProcessor | None = None
_start_lock = asyncio.Lock()


def _bridge_log_to_stderr(action: str, detail: str = "") -> None:
    try:
        print(f"[bridge] {action}: {detail}", file=sys.stderr, flush=True)
    except Exception:
        pass


def _default_port() -> int:
    raw = os.environ.get("FLOWLENS_MCP_PORT") or os.environ.get("FLOWLENS_BRIDGE_PORT") or "8765"
    try:
        return int(raw)
    except ValueError:
        return 8765


def _make_progress_logger(run_dir: Path):
    log_path = run_dir / "stage_progress.jsonl"

    def log(action: str, detail: str = "", duration: float | None = None) -> None:
        try:
            with open(log_path, "a", encoding="utf-8") as handle:
                entry: dict[str, Any] = {"action": action, "detail": detail}
                if duration is not None:
                    entry["duration_s"] = round(float(duration), 2)
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass

    return log


async def ensure_runtime() -> tuple[ExtensionBridge, ToolContext, MediaProcessor]:
    """Start the bridge and companion state on first use, then reuse."""
    global _bridge, _ctx, _media
    async with _start_lock:
        if _bridge is not None and _ctx is not None and _media is not None:
            return _bridge, _ctx, _media

        port = _default_port()
        bridge = ExtensionBridge(port=port)
        bridge.on_log(_bridge_log_to_stderr)
        await bridge.start()
        await bridge.wait_for_connection(timeout=120)

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_dir = Path(tempfile.gettempdir()) / f"flowlens_mcp_{stamp}"
        run_dir.mkdir(parents=True, exist_ok=True)
        ctx = ToolContext(run_dir=run_dir)
        media = MediaProcessor()

        _bridge = bridge
        _ctx = ctx
        _media = media
        return bridge, ctx, media


def compact_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def progress_logger_for(run_dir: Path):
    return _make_progress_logger(run_dir)
