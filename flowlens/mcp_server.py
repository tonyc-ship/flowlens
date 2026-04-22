"""FastMCP server exposing low-level FlowLens Xiaohongshu tools.

Claude Desktop / Cursor / Claude Code spawn this server via stdio. The
server owns a local WebSocket bridge that the FlowLens Chrome extension
connects to from the user's logged-in Chrome.

In this MCP layer FlowLens does NOT run its own LLM agent — the host
model (Claude) plans and decides, FlowLens only exposes browser-level
capabilities that survive XHS anti-bot because they run inside the
user's real logged-in Chrome.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from .agent.tool import ToolContext
from .core.bridge import ExtensionBridge
from .perception.media import MediaProcessor
from .platforms.xhs.processor import XHSSiteAdapter


_bridge: ExtensionBridge | None = None
_adapter: XHSSiteAdapter | None = None
_ctx: ToolContext | None = None
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
    def log(action: str, detail: str = "", duration: float | None = None) -> None:
        # Keep stderr clean for MCP stdio — log only to file.
        try:
            with open(run_dir / "stage_progress.jsonl", "a", encoding="utf-8") as handle:
                entry: dict[str, Any] = {"action": action, "detail": detail}
                if duration is not None:
                    entry["duration_s"] = round(float(duration), 2)
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass

    return log


async def _ensure_ready() -> tuple[ExtensionBridge, XHSSiteAdapter, ToolContext]:
    global _bridge, _adapter, _ctx
    async with _start_lock:
        if _bridge is not None and _adapter is not None and _ctx is not None:
            return _bridge, _adapter, _ctx

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
        adapter = XHSSiteAdapter(
            bridge,
            ext_bridge=bridge,
            media=media,
            run_dir=run_dir,
            log_fn=_make_progress_logger(run_dir),
        )

        _bridge = bridge
        _adapter = adapter
        _ctx = ctx
        return bridge, adapter, ctx


def _compact_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


mcp = FastMCP(
    name="flowlens-xhs",
    instructions=(
        "FlowLens Xiaohongshu MCP.\n\n"
        "This server drives the user's real, logged-in Chrome (not a bundled "
        "Chromium) via a companion Chrome extension. Because actions run inside "
        "the user's actual session, they survive anti-bot measures that block "
        "headless scrapers.\n\n"
        "Anti-bot priors (follow these):\n"
        "- Prefer UI entry to note detail via xhs_open_note / xhs_read_note\n"
        "  over direct /explore/<id> navigation.\n"
        "- Close notes with xhs_close_note (clicks X / Esc) rather than reloading.\n"
        "- If you see error_page / security_verification / 'scan on phone',\n"
        "  STOP. Tell the user XHS is challenging; do not retry in a loop.\n"
        "- Space out searches. Do not burst more than ~3 searches per minute."
    ),
)


@mcp.tool()
async def xhs_tab_info() -> str:
    """Return the current Chrome tab's url and title.

    Call this once at the start of a session to confirm that the bridge is
    connected and the user is already on xiaohongshu.com (or to decide whether
    you need xhs_navigate first).
    """
    bridge, _, _ = await _ensure_ready()
    info = await bridge.get_tab_info()
    return _compact_json({
        "url": info.get("url", ""),
        "title": info.get("title", ""),
        "tab_id": info.get("tabId"),
    })


@mcp.tool()
async def xhs_navigate(url: str = "https://www.xiaohongshu.com/explore", wait_ms: int = 3000) -> str:
    """Navigate the active Chrome tab to a URL.

    Default target is the Xiaohongshu explore page. For note detail you should
    usually NOT navigate directly — use xhs_open_note or xhs_read_note instead,
    because direct /explore/<id> often triggers XHS anti-bot.
    """
    bridge, _, _ = await _ensure_ready()
    await bridge.navigate(url, wait_ms=wait_ms)
    info = await bridge.get_tab_info()
    return _compact_json({
        "ok": True,
        "url": info.get("url", url),
        "title": info.get("title", ""),
    })


@mcp.tool()
async def xhs_search_notes(query: str, tab_label: str = "全部", wait_seconds: float = 1.5) -> str:
    """Submit a Xiaohongshu search query and return the visible result cards.

    The submit is human-like (click + type + Enter in the page's search input),
    NOT a URL navigation — this is the reliable way to reach search results.

    Returns a JSON payload with `cards` (note_id, title, author, likes,
    position, link). Use `note_id` when calling xhs_read_note/xhs_open_note —
    it is stable across re-renders, unlike `index`.

    tab_label: 全部 / 图文 / 视频 / 用户 (or English: all / image / video / user).
    """
    _, adapter, _ = await _ensure_ready()
    payload = await adapter.search_notes(
        query,
        tab_label=tab_label or None,
        wait_seconds=max(0.0, min(float(wait_seconds), 2.0)),
    )
    payload["site"] = "xiaohongshu"
    payload["action"] = "search_notes"
    return _compact_json(payload)


@mcp.tool()
async def xhs_open_search_tab(tab_label: str, wait_seconds: float = 1.5) -> str:
    """Switch between search-result tabs (全部 / 图文 / 视频 / 用户).

    Returns the visible cards on the new tab. Call this after xhs_search_notes
    to explore a different slice of the results — do NOT re-issue the same
    search query just to change tabs.
    """
    _, adapter, _ = await _ensure_ready()
    label = (tab_label or "全部").strip()
    payload = await adapter.open_search_tab(label, wait_seconds=max(0.0, min(float(wait_seconds), 2.0)))
    payload["site"] = "xiaohongshu"
    payload["action"] = "open_search_tab"
    return _compact_json(payload)


@mcp.tool()
async def xhs_open_note(note_id: str = "", index: int | None = None, wait_seconds: float = 1.5) -> str:
    """Open a note detail modal by clicking a visible card.

    Prefer `note_id` (stable) over `index` (depends on scroll position and can
    shift between searches). The click simulates a real user click, which is
    the path that reliably opens the note modal on XHS.

    Does NOT extract the note content. Call xhs_read_note for that, or combine
    in one step by calling xhs_read_note directly with the note_id.
    """
    _, adapter, _ = await _ensure_ready()
    result = await adapter.open_note(
        index=index,
        note_id=(note_id or "").strip(),
        wait_seconds=max(0.0, min(float(wait_seconds), 2.0)),
    )
    return _compact_json({"site": "xiaohongshu", "action": "open_note", "result": result})


@mcp.tool()
async def xhs_close_note() -> str:
    """Close the currently open note detail modal (clicks X or presses Esc).

    Always prefer this over reloading the page or navigating back — reloads
    cost time, reorder the search results, and add request pressure.
    """
    _, adapter, _ = await _ensure_ready()
    result = await adapter.close_note()
    return _compact_json({"site": "xiaohongshu", "action": "close_note", "result": result})


@mcp.tool()
async def xhs_read_note(
    note_id: str = "",
    index: int | None = None,
    level: str = "lite",
    max_comments: int = 4,
    max_images: int = 0,
    max_video_frames: int = 0,
    include_comments: bool = True,
    include_media: bool = False,
    wait_seconds: float = 1.5,
    close_after: bool = False,
) -> str:
    """Open a note and extract normalized info in one call.

    level:
    - "card": card-level only (no body extraction, very cheap)
    - "lite": body, comments, likes — no image/video enrichment (recommended default)
    - "deep": lite + optional image OCR / video frame analysis if include_media=true

    include_media: set True only when image / video content matters to the user
    query. Media enrichment is expensive (OCR, vision calls). Default False.

    Target a note via note_id (stable, preferred) or index (position-dependent).
    Returns a compact entity payload with content_summary, hashtags, likes,
    top_comments, etc. The full artifact is written to the server's run_dir.
    """
    _, adapter, _ = await _ensure_ready()
    note = await adapter.read_note(
        index=index,
        note_id=(note_id or "").strip(),
        level=str(level or "lite"),
        max_comments=int(max_comments),
        max_images=int(max_images),
        max_video_frames=int(max_video_frames),
        include_comments=include_comments,
        include_media=include_media,
        open_wait_seconds=max(0.0, min(float(wait_seconds), 2.0)),
        close_after=bool(close_after),
    )
    return _compact_json({
        "site": "xiaohongshu",
        "action": "read_note",
        "level": str(level or "lite"),
        "entity": note.to_tool_dict(),
    })


@mcp.tool()
async def xhs_screenshot() -> str:
    """Capture the current Chrome tab as a PNG/JPEG saved on disk.

    Returns the absolute file path and the current url/title. You can read
    the file contents yourself if you want visual inspection — this tool does
    NOT return the image bytes inline, to keep MCP tool results small.
    """
    bridge, _, ctx = await _ensure_ready()
    data_url = await bridge.capture_screenshot()
    if not data_url or "," not in data_url:
        return _compact_json({"ok": False, "error": "Empty screenshot"})
    header, b64 = data_url.split(",", 1)
    import base64
    img_bytes = base64.b64decode(b64)
    ext = ".jpg" if "jpeg" in header else ".png"
    path = ctx.next_screenshot_path("mcp_screenshot").with_suffix(ext)
    path.write_bytes(img_bytes)
    info = await bridge.get_tab_info()
    return _compact_json({
        "ok": True,
        "path": str(path),
        "url": info.get("url", ""),
        "title": info.get("title", ""),
        "bytes": len(img_bytes),
    })


def main() -> None:
    mcp.run("stdio")


if __name__ == "__main__":
    main()
