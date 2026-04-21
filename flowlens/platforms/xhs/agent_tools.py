"""Xiaohongshu-specific agent tools."""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path

from ...agent.tool import Tool, ToolContext
from ...core.bridge import ExtensionBridge, TabBridge
from ...knowledge.loader import detect_site
from ...perception.media import MediaProcessor
from .capabilities import plan_for_level
from .entities import NoteCard, NoteEntity, extract_key_points, is_meaningful_note_content
from .processor import XHSSiteAdapter, rank_note_card


def _make_xhs_progress_logger(ctx: ToolContext):
    """Emit long-running stage progress both to stdout and a JSONL file.

    The XHS macros run entirely inside a single agent tool call, so the agent
    loop sees silence between ``tool_call`` and ``tool_result``. This logger
    gives the human (and anyone tailing ``stage_progress.jsonl``) a live
    heartbeat of which media stage is active.
    """
    progress_path = ctx.run_dir / "stage_progress.jsonl"

    def log(action: str, detail: str = "", duration: float | None = None) -> None:
        entry = {
            "timestamp": time.time(),
            "action": action,
            "detail": detail,
        }
        if duration is not None:
            entry["duration_s"] = round(float(duration), 2)
        try:
            with open(progress_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass
        suffix = f" ({duration:.1f}s)" if duration is not None else ""
        detail_str = f": {detail}" if detail else ""
        print(f"  [xhs] {action}{detail_str}{suffix}", flush=True)

    return log


def _short_text(text: str, max_chars: int = 320) -> str:
    text = str(text or "").strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "... [truncated]"


def _write_payload_artifact(ctx: ToolContext, label: str, payload: dict) -> str:
    summary = str(
        payload.get("summary")
        or payload.get("message")
        or payload.get("action")
        or payload.get("entity_type")
        or label
    )
    return ctx.write_json_artifact(
        label,
        payload,
        subdir="site_results",
        artifact_kind="site_result",
        summary=summary,
        metadata={"site": "xiaohongshu"},
    )


def _card_preview(card: dict, processed_notes: dict | None = None) -> dict:
    note_id = card.get("note_id", "")
    preview = {
        "note_id": note_id,
        "title": card.get("title", ""),
        "author": card.get("author", ""),
        "likes": card.get("likes", ""),
        "likes_value": card.get("likes_value"),
        "type": card.get("type", ""),
        "position": card.get("position"),
        "link": card.get("link", ""),
    }
    if note_id and processed_notes and note_id in processed_notes:
        info = processed_notes[note_id]
        preview["already_analyzed"] = True
        preview["prior_artifact"] = info.get("artifact", "")
    return preview


def _summarize_note_entity(entity: dict) -> dict:
    video = entity.get("video") or {}
    media_text = "\n".join(
        str(image.get("ocr_text", ""))
        for image in entity.get("images", [])
        if isinstance(image, dict) and image.get("ocr_text")
    )
    return {
        "note_id": entity.get("note_id", ""),
        "url": entity.get("url", ""),
        "title": entity.get("title", ""),
        "author": entity.get("author", ""),
        "type": entity.get("type", ""),
        "likes": entity.get("likes", ""),
        "likes_value": entity.get("likes_value"),
        "favorites": entity.get("favorites", ""),
        "favorites_value": entity.get("favorites_value"),
        "comments_count": entity.get("comments_count", ""),
        "comments_count_value": entity.get("comments_count_value"),
        "hashtags": list(entity.get("hashtags", [])[:8]),
        "content_summary": _short_text(entity.get("content", ""), 420),
        "content_source": entity.get("content_source", ""),
        "content_key_points": list(entity.get("key_points", [])[:6]),
        "media_ocr_key_points": list(entity.get("media_key_points", [])[:6])
        or extract_key_points(media_text, limit=6),
        "key_points": list(entity.get("key_points", [])[:6]),
        "top_comments": list(entity.get("top_comments", [])[:3]),
        "cover_description": _short_text(entity.get("cover_description", ""), 220),
        "transcript_summary": _short_text(video.get("transcript_summary", ""), 220),
        "visual_summary": _short_text(video.get("visual_summary", ""), 220),
        "screenshot": entity.get("screenshot", ""),
        "completeness_score": entity.get("completeness_score"),
        "applied_capabilities": list(entity.get("applied_capabilities", [])),
        "stale_warning": entity.get("stale_warning", ""),
    }


def _summarize_author_entity(entity: dict) -> dict:
    return {
        "name": entity.get("name", ""),
        "xhs_id": entity.get("xhs_id", ""),
        "bio": _short_text(entity.get("bio", ""), 240),
        "verified": entity.get("verified", False),
        "followers": entity.get("followers", ""),
        "followers_value": entity.get("followers_value"),
        "following": entity.get("following", ""),
        "following_value": entity.get("following_value"),
        "total_likes": entity.get("total_likes", ""),
        "total_likes_value": entity.get("total_likes_value"),
        "screenshot": entity.get("screenshot", ""),
        "top_note_cards": [_card_preview(card) for card in entity.get("note_cards", [])[:6]],
    }


def _is_note_ocr_stop_line(line: str) -> bool:
    return bool(
        re.search(r"(?:猜你想搜|说点什么)", line)
        or re.search(r"^(?:共\s*\d*\s*条评论|展开|收起|-?\s*THE END\s*-?)$", line, re.I)
        or re.search(r"^\d{4}-\d{1,2}-\d{1,2}(?:\s+\S+)?$", line)
        or re.search(r"^\d{1,2}-\d{1,2}(?:\s+\S+)?$", line)
        or re.search(r"^(?:刚刚|\d+\s*(?:秒|分钟|小时|天)前|昨天|前天|编辑于\s*.+)$", line)
        or re.search(r"^(?:加载中|赞|收藏|评论|分享|发送|取消)$", line)
    )


def _content_from_note_ocr(ocr_text: str, *, title: str = "", author: str = "") -> str:
    lines = [
        re.sub(r"\s+", " ", line).strip()
        for line in str(ocr_text or "").splitlines()
    ]
    lines = [line for line in lines if line]
    if not lines:
        return ""

    title = re.sub(r"\s+", " ", title or "").strip()
    author = re.sub(r"\s+", " ", author or "").strip()
    start = -1
    if title:
        for idx, line in enumerate(lines):
            if line == title or title in line or line in title:
                start = idx
                break
    if start < 0 and author:
        for idx, line in enumerate(lines):
            if line == author:
                start = idx
                break
    if start < 0:
        return ""

    body: list[str] = []
    for line in lines[start + 1:]:
        if line in {title, author}:
            continue
        if _is_note_ocr_stop_line(line):
            break
        if re.search(r"^(?:已关注|关注|作者|\.{2,}|…)$", line):
            continue
        body.append(line)

    content = "\n".join(body).strip()
    return content if is_meaningful_note_content(content) else ""


def _fill_missing_note_content_from_screenshot(
    note: NoteEntity,
    screenshot_path: Path,
    media: MediaProcessor,
) -> None:
    if note.content or not screenshot_path.exists():
        return
    try:
        ocr_text = media.ocr_image(screenshot_path.read_bytes())
    except Exception:
        return
    content = _content_from_note_ocr(
        ocr_text,
        title=note.title,
        author=note.author_name,
    )
    if not content:
        return
    note.content = content
    if "xhs.note.screenshot_ocr_content" not in note.applied_capabilities:
        note.applied_capabilities.append("xhs.note.screenshot_ocr_content")
    debug = dict(note.extraction_debug or {})
    debug["content_source"] = "screenshot_ocr"
    debug["screenshot_ocr_excerpt"] = content[:800]
    note.extraction_debug = debug
    note.refresh_derived_fields()


def _payload_summary(payload: dict, *, artifact_path: str, processed_notes: dict | None = None) -> dict:
    summary = {
        "site": payload.get("site", ""),
        "artifact_file": artifact_path,
        "artifact_file_note": "Local disk path relative to run_dir. NOT a URL. Do not navigate() or fetch() it; the full payload is already summarized in this response.",
    }
    if "action" in payload:
        summary["action"] = payload.get("action")
    if "entity_type" in payload:
        summary["entity_type"] = payload.get("entity_type")
    if "level" in payload:
        summary["level"] = payload.get("level")
    if "query" in payload:
        summary["query"] = payload.get("query")
    if "ok" in payload:
        summary["ok"] = payload.get("ok")
    if "state" in payload:
        summary["state"] = payload.get("state")
    if "reason" in payload and payload.get("reason"):
        summary["reason"] = payload.get("reason")

    cards = payload.get("cards")
    if isinstance(cards, list):
        summary["count"] = payload.get("count", len(cards))
        summary["top_cards"] = [_card_preview(card, processed_notes) for card in cards[:5]]

    entity = payload.get("entity")
    if isinstance(entity, dict):
        if payload.get("entity_type") == "author_profile":
            summary["entity"] = _summarize_author_entity(entity)
        else:
            summary["entity"] = _summarize_note_entity(entity)

    notes = payload.get("notes")
    if isinstance(notes, list):
        summary["note_count"] = len(notes)
        summary["notes"] = [
            {
                "level": note.get("scan_level", note.get("level", "")),
                "position": note.get("source_position"),
                "entity": _summarize_note_entity(note.get("entity", {})),
            }
            for note in notes[:8]
        ]
        if notes and payload.get("action") == "xhs_topic_scan":
            summary["sampling_status"] = {
                "searched": True,
                "sampled_count": len(notes),
                "sampled_note_ids": [
                    str(((note.get("entity") or {}).get("note_id")) or "").strip()
                    for note in notes
                    if isinstance(note, dict) and isinstance(note.get("entity"), dict)
                    and str(((note.get("entity") or {}).get("note_id")) or "").strip()
                ][:12],
            }

    timing = payload.get("timing")
    if isinstance(timing, dict) and timing:
        summary["timing"] = timing

    search = payload.get("search")
    if isinstance(search, dict):
        summary["search"] = {
            "ok": search.get("ok"),
            "count": search.get("count"),
            "state": search.get("state"),
            "reason": search.get("reason", ""),
            "top_cards": [_card_preview(card, processed_notes) for card in search.get("cards", [])[:5]],
        }

    selected = payload.get("selected_cards")
    if isinstance(selected, list):
        summary["selected_cards"] = [_card_preview(card, processed_notes) for card in selected[:6]]

    result = payload.get("result")
    if isinstance(result, dict):
        summary["result"] = {
            key: value
            for key, value in result.items()
            if key in {"ok", "state", "url", "note_id", "title", "message", "error"}
        }

    if processed_notes:
        summary["already_analyzed_notes"] = [
            {
                "note_id": nid,
                "title": info.get("title", ""),
                "artifact": info.get("artifact", ""),
            }
            for nid, info in list(processed_notes.items())[-20:]
        ]

    return summary


_LEVEL_ORDER = {"card": 0, "lite": 1, "deep": 2}


def _level_rank(level: str) -> int:
    return _LEVEL_ORDER.get(str(level or "").strip().lower(), -1)


def _dedup_short_circuit(
    ctx: ToolContext,
    *,
    note_id: str,
    requested_level: str,
    force: bool,
) -> str | None:
    """Return a short-circuit tool response when the note was already analyzed.

    Called before opening/extracting a note. If the note_id is already in
    ``ctx.processed_notes`` at a level greater-than-or-equal to the requested
    level, and the caller did not pass ``force=true``, we refuse to re-extract
    and point the agent at the prior artifact instead. Lite->deep upgrades are
    still allowed because a prior lite extraction lacks media.
    """
    if force or not note_id:
        return None
    processed = getattr(ctx, "processed_notes", None) or {}
    info = processed.get(str(note_id).strip())
    if not isinstance(info, dict):
        return None
    prior_level = info.get("level") or ""
    if _level_rank(prior_level) < _level_rank(requested_level):
        return None
    return json.dumps(
        {
            "site": "xiaohongshu",
            "ok": True,
            "skipped": True,
            "reason": "already_analyzed",
            "note_id": note_id,
            "title": info.get("title", ""),
            "prior_level": prior_level,
            "requested_level": requested_level,
            "prior_artifact": info.get("artifact", ""),
            "artifact_file_note": (
                "This note_id was already extracted at the same or deeper level. "
                "Reuse the prior artifact instead of re-opening. "
                "Pass force=true if a fresh re-extraction is genuinely needed."
            ),
        },
        ensure_ascii=False,
        indent=2,
    )


def _record_processed_note(ctx: ToolContext, note: NoteEntity, artifact: str) -> None:
    """Remember a note as already analyzed so future tool results can flag it."""
    note_id = str(getattr(note, "note_id", "") or "").strip()
    if not note_id:
        return
    new_level = str(getattr(note, "extraction_level", "") or "")
    prior = ctx.processed_notes.get(note_id) or {}
    if prior and _level_rank(prior.get("level", "")) > _level_rank(new_level):
        prior["artifact"] = artifact
        ctx.processed_notes[note_id] = prior
        return
    ctx.processed_notes[note_id] = {
        "title": str(getattr(note, "title", "") or "").strip()
        or prior.get("title", ""),
        "artifact": artifact,
        "level": new_level,
    }


def _emit_payload(ctx: ToolContext, label: str, payload: dict) -> str:
    artifact = _write_payload_artifact(ctx, label, payload)
    return json.dumps(
        _payload_summary(
            payload,
            artifact_path=artifact,
            processed_notes=getattr(ctx, "processed_notes", None),
        ),
        ensure_ascii=False,
        indent=2,
    )


class ExtractSiteEntityTool(Tool):
    """Extract normalized entities from supported sites."""

    def __init__(
        self,
        bridge: ExtensionBridge | TabBridge,
        *,
        ext_bridge: ExtensionBridge,
        media: MediaProcessor,
    ):
        self._bridge = bridge
        self._ext_bridge = ext_bridge
        self._media = media

    name = "extract_site_entity"
    description = (
        "Extract a structured entity from the CURRENT page on a supported site. "
        "Prefer this over raw extract_page_data when you need a normalized XHS "
        "note/profile with comments, count normalization, OCR, image vision, or "
        "video transcription. Currently supports Xiaohongshu entities:\n"
        "- search_cards: visible note cards on a search/profile grid\n"
        "- note: the OPEN note modal/detail currently on screen\n"
        "- author_profile: the currently open profile page\n"
        "Levels:\n"
        "- card: cheapest metadata only\n"
        "- lite: normalized note + hot comments\n"
        "- deep: add image OCR/vision and video transcript/frame understanding\n"
        "Typical XHS flow:\n"
        "- after search: extract_site_entity(search_cards)\n"
        "- after opening a note: extract_site_entity(note, level='lite' or 'deep')\n"
        "- on a profile page: extract_site_entity(author_profile)\n"
        "Use raw extract_page_data for low-level actions like click_card / close_note / submit_search_query."
    )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "entity_type": {
                    "type": "string",
                    "enum": ["search_cards", "note", "author_profile"],
                    "description": "What to extract from the current page.",
                },
                "level": {
                    "type": "string",
                    "enum": ["card", "lite", "deep"],
                    "description": (
                        "Extraction depth. Use lite for body/comments/likes. "
                        "Use deep only when the extra media cost is justified."
                    ),
                    "default": "lite",
                },
                "max_comments": {
                    "type": "integer",
                    "description": "Max comments to collect for note extraction.",
                    "default": 4,
                },
                "max_images": {
                    "type": "integer",
                    "description": "Max images to enrich for image notes.",
                    "default": 6,
                },
                "max_video_frames": {
                    "type": "integer",
                    "description": "Max frames to analyze for video notes.",
                    "default": 4,
                },
                "include_comments": {
                    "type": "boolean",
                    "description": "Override whether note extraction collects comments.",
                },
                "include_media": {
                    "type": "boolean",
                    "description": (
                        "Override whether note extraction runs OCR / vision / transcription. "
                        "Set false unless image/video analysis is explicitly needed."
                    ),
                },
                "include_notes": {
                    "type": "boolean",
                    "description": "For author_profile, whether to also extract the visible note cards.",
                    "default": True,
                },
            },
            "required": ["entity_type"],
        }

    async def execute(self, params: dict, ctx: ToolContext) -> str:
        info = await self._bridge.get_tab_info()
        current_url = str(info.get("url") or "")
        site_name = detect_site(current_url)
        if site_name != "xiaohongshu":
            return (
                f"Unsupported site for extract_site_entity: {site_name or 'unknown'} "
                f"(url={current_url})"
            )

        adapter = XHSSiteAdapter(
            self._bridge,
            ext_bridge=self._ext_bridge,
            media=self._media,
            run_dir=ctx.run_dir,
            log_fn=_make_xhs_progress_logger(ctx),
        )

        entity_type = params["entity_type"]
        level = params.get("level", "lite")

        if entity_type == "search_cards":
            cards = await adapter.extract_search_cards()
            payload = {
                "site": site_name,
                "entity_type": entity_type,
                "ok": True,
                "count": len(cards),
                "cards": [card.to_tool_dict() for card in cards],
            }
            return _emit_payload(ctx, "xhs_search_cards", payload)

        if entity_type == "author_profile":
            author = await adapter.extract_author_profile(
                include_notes=params.get("include_notes", True),
            )
            screenshot_path = ctx.next_screenshot_path("author_profile")
            saved = await self._bridge.save_screenshot(screenshot_path)
            author.screenshot_path = Path(saved).name if saved else ""
            payload = {
                "site": site_name,
                "entity_type": entity_type,
                "entity": author.to_tool_dict(),
                "timing": adapter.timing.summary(),
            }
            return _emit_payload(ctx, "xhs_author_profile", payload)

        if entity_type == "note":
            screenshot_path = ctx.next_screenshot_path("note_detail")
            saved = await self._bridge.save_screenshot(screenshot_path)
            note = await adapter.extract_note(
                level=level,
                max_comments=int(params.get("max_comments", 4)),
                max_images=int(params.get("max_images", 6)),
                max_video_frames=int(params.get("max_video_frames", 4)),
                include_comments=params.get("include_comments"),
                include_media=params.get("include_media"),
            )
            note.screenshot_path = Path(saved).name if saved else ""
            _fill_missing_note_content_from_screenshot(note, screenshot_path, self._media)
            payload = {
                "site": site_name,
                "entity_type": entity_type,
                "level": level,
                "plan": plan_for_level(
                    level,
                    max_comments=int(params.get("max_comments", 4)),
                    max_images=int(params.get("max_images", 6)),
                    max_video_frames=int(params.get("max_video_frames", 4)),
                    include_comments=params.get("include_comments"),
                    include_media=params.get("include_media"),
                ).to_dict(),
                "entity": note.to_tool_dict(),
                "timing": adapter.timing.summary(),
            }
            note_label = f"xhs_note_{note.note_id or level}"
            artifact_rel = _write_payload_artifact(ctx, note_label, payload)
            _record_processed_note(ctx, note, artifact_rel)
            return json.dumps(
                _payload_summary(
                    payload,
                    artifact_path=artifact_rel,
                    processed_notes=ctx.processed_notes,
                ),
                ensure_ascii=False,
                indent=2,
            )

        return f"Unsupported entity_type: {entity_type}"


class RunSiteActionTool(Tool):
    """Execute a higher-level site action on supported sites."""

    def __init__(
        self,
        bridge: ExtensionBridge | TabBridge,
        *,
        ext_bridge: ExtensionBridge,
        media: MediaProcessor,
    ):
        self._bridge = bridge
        self._ext_bridge = ext_bridge
        self._media = media

    name = "run_site_action"
    description = (
        "Run a higher-level site action on the CURRENT supported site. "
        "Use this to reduce planning overhead on Xiaohongshu when the next step "
        "is obvious. Supported actions:\n"
        "- search_notes: submit a search query, self-heal once if needed, and return a compact card summary\n"
        "- open_search_tab: switch 全部/图文/视频/用户 and return compact visible cards\n"
        "- open_note: open a card by index or note_id\n"
        "- read_note: open a note and extract normalized info in one call (full entity is written to disk)\n"
        "- close_note: close the open note modal\n"
        "Prefer read_note over manually chaining click_card + wait + extract_site_entity(note). "
        "Prefer targeting notes by `note_id` (stable) over `index` (position-dependent and can change between searches). "
        "Every card in tool results includes `note_id`, `title`, and an `already_analyzed` flag; "
        "skip notes already marked `already_analyzed: true` unless you need a fresh re-extraction. "
        "read_note will short-circuit with skipped=true when the note_id was already extracted at "
        "the same or deeper level earlier in this run; pass force=true only when a re-extraction "
        "is actually needed."
    )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["search_notes", "open_search_tab", "open_note", "read_note", "close_note"],
                    "description": "The site-level action to run.",
                },
                "query": {
                    "type": "string",
                    "description": "For search_notes: the search query.",
                },
                "tab_label": {
                    "type": "string",
                    "description": "For open_search_tab/search_notes: 全部, 图文, 视频, 用户, or English aliases.",
                },
                "index": {
                    "type": "integer",
                    "description": "For open_note/read_note: visible card index to open.",
                },
                "note_id": {
                    "type": "string",
                    "description": "For open_note/read_note: specific XHS note id.",
                },
                "level": {
                    "type": "string",
                    "enum": ["card", "lite", "deep"],
                    "description": (
                        "For read_note: extraction depth. Use lite for body/comments/likes. "
                        "Deep may run expensive image/video enrichment unless include_media=false."
                    ),
                    "default": "lite",
                },
                "wait_seconds": {
                    "type": "number",
                    "description": "Wait after search/open actions. Keep this <= 2 unless retrying.",
                    "default": 1.5,
                },
                "close_after": {
                    "type": "boolean",
                    "description": "For read_note: close the note after extraction.",
                    "default": False,
                },
                "max_comments": {
                    "type": "integer",
                    "description": "For read_note: max comments to collect.",
                    "default": 4,
                },
                "max_images": {
                    "type": "integer",
                    "description": "For read_note: max images to enrich.",
                    "default": 6,
                },
                "max_video_frames": {
                    "type": "integer",
                    "description": "For read_note: max video frames to analyze.",
                    "default": 4,
                },
                "include_comments": {
                    "type": "boolean",
                    "description": "For read_note: override comment collection.",
                },
                "include_media": {
                    "type": "boolean",
                    "description": (
                        "For read_note: override OCR / vision / transcription. "
                        "Set false unless image/video analysis is explicitly needed."
                    ),
                },
                "force": {
                    "type": "boolean",
                    "description": (
                        "For read_note: bypass the already_analyzed short-circuit and "
                        "re-open the note even if it was extracted at the same/deeper "
                        "level earlier in this run. Default false."
                    ),
                    "default": False,
                },
            },
            "required": ["action"],
        }

    async def execute(self, params: dict, ctx: ToolContext) -> str:
        action = str(params["action"])
        site_name = await _detect_current_site(self._bridge)
        if site_name != "xiaohongshu" and action == "search_notes":
            await self._bridge.navigate("https://www.xiaohongshu.com/explore", wait_ms=1500)
            site_name = await _detect_current_site(self._bridge)
        if site_name != "xiaohongshu":
            return (
                f"Unsupported site for run_site_action: {site_name or 'unknown'} "
                f"(url={await _current_url(self._bridge)})"
            )

        adapter = _make_xhs_adapter(self._bridge, self._ext_bridge, self._media, ctx)
        wait_seconds = min(max(float(params.get("wait_seconds", 1.5) or 0), 0.0), 2.0)

        if action == "search_notes":
            query = str(params.get("query", "")).strip()
            if not query:
                return "run_site_action(search_notes) requires query."
            payload = await adapter.search_notes(
                query,
                tab_label=params.get("tab_label"),
                wait_seconds=max(wait_seconds, 0),
            )
            payload.update({"site": site_name, "action": action})
            return _emit_payload(ctx, "xhs_search_notes", payload)

        if action == "open_search_tab":
            label = str(params.get("tab_label", "")).strip() or "全部"
            payload = await adapter.open_search_tab(label, wait_seconds=max(wait_seconds, 0))
            payload.update({"site": site_name, "action": action})
            return _emit_payload(ctx, f"xhs_search_tab_{label}", payload)

        if action == "open_note":
            payload = await adapter.open_note(
                index=params.get("index"),
                note_id=str(params.get("note_id", "")),
                wait_seconds=max(wait_seconds, 0),
            )
            return _emit_payload(ctx, "xhs_open_note", {"site": site_name, "action": action, "result": payload})

        if action == "close_note":
            payload = await adapter.close_note()
            return _emit_payload(ctx, "xhs_close_note", {"site": site_name, "action": action, "result": payload})

        if action == "read_note":
            level = str(params.get("level", "lite"))
            include_media = params.get("include_media")
            if include_media is None:
                include_media = False
            short_circuit = _dedup_short_circuit(
                ctx,
                note_id=str(params.get("note_id", "")).strip(),
                requested_level=level,
                force=bool(params.get("force", False)),
            )
            if short_circuit is not None:
                return short_circuit
            screenshot_path = ctx.next_screenshot_path("note_detail")
            note = await adapter.read_note(
                index=params.get("index"),
                note_id=str(params.get("note_id", "")),
                level=level,
                max_comments=int(params.get("max_comments", 4)),
                max_images=int(params.get("max_images", 6)),
                max_video_frames=int(params.get("max_video_frames", 4)),
                include_comments=params.get("include_comments"),
                include_media=include_media,
                open_wait_seconds=max(wait_seconds, 0),
                close_after=False,
            )
            saved = await self._bridge.save_screenshot(screenshot_path)
            note.screenshot_path = Path(saved).name if saved else ""
            _fill_missing_note_content_from_screenshot(note, screenshot_path, self._media)
            if bool(params.get("close_after", False)):
                try:
                    await adapter.close_note()
                except Exception:
                    pass
            payload = {
                "site": site_name,
                "action": action,
                "level": level,
                "plan": plan_for_level(
                    level,
                    max_comments=int(params.get("max_comments", 4)),
                    max_images=int(params.get("max_images", 6)),
                    max_video_frames=int(params.get("max_video_frames", 4)),
                    include_comments=params.get("include_comments"),
                    include_media=include_media,
                ).to_dict(),
                "entity": note.to_tool_dict(),
                "timing": adapter.timing.summary(),
            }
            note_label = f"xhs_read_note_{note.note_id or level}"
            artifact_rel = _write_payload_artifact(ctx, note_label, payload)
            _record_processed_note(ctx, note, artifact_rel)
            return json.dumps(
                _payload_summary(
                    payload,
                    artifact_path=artifact_rel,
                    processed_notes=ctx.processed_notes,
                ),
                ensure_ascii=False,
                indent=2,
            )

        return f"Unsupported action: {action}"


class XHSTopicScanTool(Tool):
    """Macro action for Xiaohongshu topic research."""

    def __init__(
        self,
        bridge: ExtensionBridge | TabBridge,
        *,
        ext_bridge: ExtensionBridge,
        media: MediaProcessor,
    ):
        self._bridge = bridge
        self._ext_bridge = ext_bridge
        self._media = media

    name = "xhs_topic_scan"
    description = (
        "Run a Xiaohongshu topic-scan macro on the CURRENT site. This is the "
        "preferred starting point for topic research because it reduces turn "
        "count: search -> collect cards -> rank relevance -> read a small sample "
        "(default 2 deep + 4 lite) -> stop. Media vision is off by default to "
        "keep costs low. Full extracted notes are written to disk and the tool "
        "returns a compact summary."
    )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The topic or search query to scan on Xiaohongshu.",
                },
                "tab_label": {
                    "type": "string",
                    "description": "Optional tab after search: 全部, 图文, 视频, 用户.",
                },
                "max_deep_notes": {
                    "type": "integer",
                    "description": "How many high-value notes to read deeply.",
                    "default": 2,
                },
                "max_lite_notes": {
                    "type": "integer",
                    "description": "How many additional notes to sample lightly.",
                    "default": 4,
                },
                "lite_comment_count": {
                    "type": "integer",
                    "description": "Comments to collect for lite reads.",
                    "default": 4,
                },
                "deep_comment_count": {
                    "type": "integer",
                    "description": "Comments to collect for deep reads.",
                    "default": 10,
                },
                "max_images": {
                    "type": "integer",
                    "description": "Max images to enrich for deep image notes.",
                    "default": 4,
                },
                "include_media": {
                    "type": "boolean",
                    "description": (
                        "Whether deep reads should run OCR/vision/video enrichment. "
                        "Default false keeps topic scans cheap; set true only when "
                        "the user asks to analyze image or video contents."
                    ),
                    "default": False,
                },
                "max_video_frames": {
                    "type": "integer",
                    "description": "Max video frames to analyze for deep video notes.",
                    "default": 4,
                },
                "wait_seconds": {
                    "type": "number",
                    "description": "Wait after navigation/search/open actions. Keep this <= 2 unless retrying.",
                    "default": 1.5,
                },
            },
            "required": ["query"],
        }

    async def execute(self, params: dict, ctx: ToolContext) -> str:
        site_name = await _detect_current_site(self._bridge)
        if site_name != "xiaohongshu":
            await self._bridge.navigate("https://www.xiaohongshu.com/explore", wait_ms=1500)
            site_name = await _detect_current_site(self._bridge)
        if site_name != "xiaohongshu":
            return (
                f"Unsupported site for xhs_topic_scan: {site_name or 'unknown'} "
                f"(url={await _current_url(self._bridge)})"
            )

        query = str(params.get("query", "")).strip()
        if not query:
            return "xhs_topic_scan requires query."

        adapter = _make_xhs_adapter(self._bridge, self._ext_bridge, self._media, ctx)
        wait_seconds = min(max(float(params.get("wait_seconds", 1.5) or 0), 0.0), 2.0)
        max_deep = max(0, int(params.get("max_deep_notes", 2)))
        max_lite = max(0, int(params.get("max_lite_notes", 4)))
        include_media = bool(params.get("include_media", False))
        total_limit = max_deep + max_lite

        search = await adapter.search_notes(
            query,
            tab_label=params.get("tab_label"),
            wait_seconds=max(wait_seconds, 0),
        )

        ranked_cards = [
            NoteCard.from_dom_dict(card)
            for card in search.get("cards", [])
        ]
        ranked_cards = sorted(
            ranked_cards,
            key=lambda card: rank_note_card(card, query),
            reverse=True,
        )

        selected_cards: list[NoteCard] = []
        seen_ids: set[str] = set()
        for card in ranked_cards:
            dedupe_key = card.note_id or card.link or f"pos:{card.position}"
            if dedupe_key in seen_ids:
                continue
            seen_ids.add(dedupe_key)
            selected_cards.append(card)
            if len(selected_cards) >= total_limit:
                break

        progress_log = _make_xhs_progress_logger(ctx)
        progress_log(
            "topic_scan_plan",
            f"query='{query}' deep={max_deep} lite={max_lite} media={include_media} selected={len(selected_cards)}",
        )
        notes: list[dict] = []
        for idx, card in enumerate(selected_cards):
            level = "deep" if idx < max_deep else "lite"
            progress_log(
                "topic_scan_note_start",
                f"{idx + 1}/{len(selected_cards)} {level} note_id={card.note_id or '-'} title={(card.title or '')[:40]}",
            )
            comment_count = int(params.get("deep_comment_count", 10)) if level == "deep" else int(params.get("lite_comment_count", 4))
            try:
                note = await adapter.read_note(
                    index=None if card.note_id else card.position,
                    note_id=card.note_id,
                    level=level,
                    max_comments=comment_count,
                    max_images=int(params.get("max_images", 4)),
                    max_video_frames=int(params.get("max_video_frames", 4)),
                    include_media=include_media and level == "deep",
                    open_wait_seconds=max(wait_seconds, 0),
                    close_after=False,
                )
                screenshot_path = ctx.next_screenshot_path(f"topic_scan_{idx+1}_{level}")
                saved = await self._bridge.save_screenshot(screenshot_path)
                note.screenshot_path = Path(saved).name if saved else ""
                _fill_missing_note_content_from_screenshot(note, screenshot_path, self._media)
                notes.append({
                    "scan_level": level,
                    "source_position": card.position,
                    "entity": note.to_tool_dict(),
                })
                _record_processed_note(
                    ctx,
                    note,
                    f"topic_scan:{query} #{idx + 1} [{level}]",
                )
            except Exception as exc:
                notes.append({
                    "scan_level": level,
                    "source_position": card.position,
                    "entity": {
                        "title": card.title,
                        "note_id": card.note_id,
                        "url": card.link,
                    },
                    "error": str(exc),
                })
            finally:
                try:
                    await adapter.close_note()
                except Exception:
                    pass
                progress_log("topic_scan_note_end", f"{idx + 1}/{len(selected_cards)}")
                if wait_seconds > 0:
                    await asyncio.sleep(min(wait_seconds, 2.0))

        payload = {
            "site": site_name,
            "action": self.name,
            "query": query,
            "ok": bool(search.get("ok")),
            "state": search.get("state", ""),
            "search": search,
            "selected_cards": [card.to_tool_dict() for card in selected_cards],
            "notes": notes,
            "sampling": {
                "max_deep_notes": max_deep,
                "max_lite_notes": max_lite,
            },
            "timing": adapter.timing.summary(),
        }
        return _emit_payload(ctx, f"xhs_topic_scan_{query}", payload)


def _make_xhs_adapter(
    bridge: ExtensionBridge | TabBridge,
    ext_bridge: ExtensionBridge,
    media: MediaProcessor,
    ctx: ToolContext,
) -> XHSSiteAdapter:
    return XHSSiteAdapter(
        bridge,
        ext_bridge=ext_bridge,
        media=media,
        run_dir=ctx.run_dir,
        log_fn=_make_xhs_progress_logger(ctx),
    )


async def _current_url(bridge: ExtensionBridge | TabBridge) -> str:
    info = await bridge.get_tab_info()
    return str(info.get("url") or "")


async def _detect_current_site(bridge: ExtensionBridge | TabBridge) -> str | None:
    return detect_site(await _current_url(bridge))
