"""Xiaohongshu MCP tools.

Only high-level, site-knowledge-aware tools are exposed here. Low-level
actions (navigate, click, screenshot, open/close modal, tab switch) are
deliberately NOT exposed — the host model would otherwise waste turns on
UI mechanics that FlowLens's adapter already handles correctly.

Tool set:
    xhs_session_check  — is the bridge connected, is the user logged in?
    xhs_search_notes   — one call returns ranked note cards for a query
    xhs_read_note      — open + extract a full note by note_id or url
    xhs_topic_scan     — macro: search + rank + sample deep/lite + summarize
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from mcp.server.fastmcp import FastMCP

from ...platforms.xhs.agent_tools import XHSTopicScanTool
from ...platforms.xhs.processor import XHSSiteAdapter
from ..runtime import compact_json, ensure_runtime, progress_logger_for


_XHS_HOST_RE = re.compile(r"(^|\.)xiaohongshu\.com$", re.IGNORECASE)


async def _adapter() -> XHSSiteAdapter:
    bridge, ctx, media = await ensure_runtime()
    return XHSSiteAdapter(
        bridge,
        ext_bridge=bridge,
        media=media,
        run_dir=ctx.run_dir,
        log_fn=progress_logger_for(ctx.run_dir),
    )


def _note_id_from_url(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if "/" not in value:
        return value
    try:
        parsed = urlparse(value if value.startswith("http") else f"https://{value}")
    except Exception:
        return ""
    parts = [segment for segment in (parsed.path or "").split("/") if segment]
    for segment in reversed(parts):
        if re.fullmatch(r"[0-9a-f]{16,32}", segment, re.IGNORECASE):
            return segment
    return parts[-1] if parts else ""


def _host_is_xhs(url: str) -> bool:
    try:
        return bool(_XHS_HOST_RE.search(urlparse(url or "").hostname or ""))
    except Exception:
        return False


def register(mcp: FastMCP) -> None:
    """Register all Xiaohongshu tools on the given FastMCP app."""

    @mcp.tool()
    async def xhs_session_check() -> str:
        """Check whether FlowLens can drive Xiaohongshu in this session.

        Returns:
          - connected: bridge <-> extension connection is live
          - on_xhs: current Chrome tab is on xiaohongshu.com
          - logged_in: best-effort signal (true if the 我 / profile menu is
            visible on the page). If false, ask the user to log in manually
            in the same Chrome tab before calling other xhs_* tools.

        Call this before anything else at the start of a session. If
        connected=false, the user has not loaded the FlowLens extension
        or has not opened Chrome yet.
        """
        bridge, _, _ = await ensure_runtime()
        info = await bridge.get_tab_info()
        url = info.get("url", "")
        on_xhs = _host_is_xhs(url)
        logged_in = None
        if on_xhs:
            try:
                probe = await bridge.run_js(
                    "return (function(){\n"
                    "  const sel = '[data-testid=\"user\"], a[href*=\"/user/profile\"], .side-bar .user, .reds-avatar';\n"
                    "  return !!document.querySelector(sel);\n"
                    "})()"
                )
                logged_in = bool(probe.get("value"))
            except Exception:
                logged_in = None
        return compact_json({
            "connected": True,
            "on_xhs": on_xhs,
            "logged_in": logged_in,
            "url": url,
            "title": info.get("title", ""),
        })

    @mcp.tool()
    async def xhs_search_notes(
        query: str,
        tab_label: str = "全部",
        wait_seconds: float = 3.0,
    ) -> str:
        """Search Xiaohongshu and return ranked result cards in one call.

        Submits the query through the real search box (human-like click +
        type + Enter), which is the path that reliably transitions to the
        search_results page without tripping anti-bot.

        tab_label: 全部 / 图文 / 视频 / 用户 (English aliases accepted).

        Returns a compact JSON list of `cards` — each has note_id, title,
        author, likes, position, link. Use `note_id` (stable) when calling
        xhs_read_note, not `position` (which can shift between searches).
        """
        adapter = await _adapter()
        bridge, _, _ = await ensure_runtime()
        info = await bridge.get_tab_info()
        if not _host_is_xhs(info.get("url", "")):
            await bridge.navigate("https://www.xiaohongshu.com/explore", wait_ms=3000)
        payload = await adapter.search_notes(
            query,
            tab_label=tab_label or None,
            wait_seconds=max(0.0, min(float(wait_seconds), 6.0)),
        )
        return compact_json({
            "site": "xiaohongshu",
            "action": "search_notes",
            "query": query,
            "ok": payload.get("ok"),
            "state": payload.get("state", ""),
            "count": payload.get("count", 0),
            "cards": payload.get("cards", []),
            "reason": payload.get("reason", ""),
        })

    @mcp.tool()
    async def xhs_read_note(
        note_id: str = "",
        url: str = "",
        include_media: bool = False,
        comment_limit: int = 6,
        wait_seconds: float = 2.0,
    ) -> str:
        """Open a Xiaohongshu note and return its full content.

        Accepts either `note_id` (16-32 hex chars, from xhs_search_notes
        results) or `url` (a full note URL — the id is parsed from the
        path). Opens the note via a real UI click from the current search
        or profile page rather than direct /explore/<id> navigation,
        because direct navigation frequently hits XHS anti-bot.

        Returns a normalized entity with title, author, content_summary,
        hashtags, top_comments, likes, etc.

        include_media: set True only when the image/video content matters
        to the user task. Media enrichment runs OCR / vision calls and is
        noticeably slower. Default False keeps reads cheap.
        """
        adapter = await _adapter()
        nid = (note_id or "").strip() or _note_id_from_url(url)
        if not nid:
            return compact_json({
                "ok": False,
                "error": "xhs_read_note requires note_id or a valid note url.",
            })
        note = await adapter.read_note(
            index=None,
            note_id=nid,
            level="lite" if not include_media else "deep",
            max_comments=int(comment_limit),
            max_images=6 if include_media else 0,
            max_video_frames=4 if include_media else 0,
            include_comments=True,
            include_media=include_media,
            open_wait_seconds=max(0.5, min(float(wait_seconds), 4.0)),
            close_after=True,
        )
        return compact_json({
            "site": "xiaohongshu",
            "action": "read_note",
            "note_id": nid,
            "include_media": include_media,
            "entity": note.to_tool_dict(),
        })

    @mcp.tool()
    async def xhs_topic_scan(
        query: str,
        tab_label: str = "",
        max_deep_notes: int = 2,
        max_lite_notes: int = 4,
        deep_comment_count: int = 10,
        lite_comment_count: int = 4,
        include_media: bool = False,
        max_images: int = 4,
        wait_seconds: float = 2.0,
    ) -> str:
        """High-value macro: research a topic on Xiaohongshu end-to-end.

        Runs: search(query) → rank cards by topic relevance → sample the
        top N as (deep/lite) reads → collect body + comments → return a
        compact summary plus pointers to full entity artifacts on disk.

        Prefer this over driving xhs_search_notes + multiple xhs_read_note
        calls yourself — this tool handles ranking, de-duplication, the
        deep/lite tradeoff, and XHS pacing between reads.

        Use `max_deep_notes` for notes that deserve full read (body +
        many comments + optional media), and `max_lite_notes` for quick
        body-only reads. Keep include_media=False unless the user asked
        for image/video analysis — it is expensive.
        """
        bridge, ctx, media = await ensure_runtime()
        tool = XHSTopicScanTool(bridge, ext_bridge=bridge, media=media)
        params = {
            "query": query,
            "tab_label": tab_label or None,
            "max_deep_notes": int(max_deep_notes),
            "max_lite_notes": int(max_lite_notes),
            "deep_comment_count": int(deep_comment_count),
            "lite_comment_count": int(lite_comment_count),
            "include_media": bool(include_media),
            "max_images": int(max_images),
            "wait_seconds": max(0.0, min(float(wait_seconds), 6.0)),
        }
        result = await tool.execute(params, ctx)
        return result if isinstance(result, str) else compact_json(result)
