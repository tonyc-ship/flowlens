"""Site-aware structured extraction tools."""

from __future__ import annotations

import json
from pathlib import Path

from ...core.bridge import ExtensionBridge, TabBridge
from ...knowledge.loader import detect_site
from ...perception.media import MediaProcessor
from ...platforms.xhs import XHSSiteAdapter, plan_for_level
from ..tool import Tool, ToolContext


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
                    "description": "Extraction depth. Use deep only when the extra cost is justified.",
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
                    "description": "Override whether note extraction runs OCR / vision / transcription.",
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
        )

        entity_type = params["entity_type"]
        level = params.get("level", "lite")

        if entity_type == "search_cards":
            cards = await adapter.extract_search_cards()
            payload = {
                "site": site_name,
                "entity_type": entity_type,
                "count": len(cards),
                "cards": [card.to_tool_dict() for card in cards],
            }
            return _dump_payload(payload)

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
            return _dump_payload(payload)

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
            return _dump_payload(payload)

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
        "- search_notes: submit a search query and return visible cards\n"
        "- open_search_tab: switch 全部/图文/视频/用户 and return visible cards\n"
        "- open_note: open a card by index or note_id\n"
        "- read_note: open a note and extract normalized info in one call\n"
        "- close_note: close the open note modal\n"
        "Prefer read_note over manually chaining click_card + wait + extract_site_entity(note)."
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
                    "description": "For read_note: extraction depth.",
                    "default": "lite",
                },
                "wait_seconds": {
                    "type": "number",
                    "description": "Wait after search/open actions.",
                    "default": 3,
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
                    "description": "For read_note: override OCR / vision / transcription.",
                },
            },
            "required": ["action"],
        }

    async def execute(self, params: dict, ctx: ToolContext) -> str:
        site_name = await _detect_current_site(self._bridge)
        if site_name != "xiaohongshu":
            return (
                f"Unsupported site for run_site_action: {site_name or 'unknown'} "
                f"(url={await _current_url(self._bridge)})"
            )

        adapter = _make_xhs_adapter(self._bridge, self._ext_bridge, self._media, ctx)
        action = str(params["action"])
        wait_seconds = float(params.get("wait_seconds", 3) or 0)

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
            return _dump_payload(payload)

        if action == "open_search_tab":
            label = str(params.get("tab_label", "")).strip() or "全部"
            payload = await adapter.open_search_tab(label, wait_seconds=max(wait_seconds, 0))
            payload.update({"site": site_name, "action": action})
            return _dump_payload(payload)

        if action == "open_note":
            payload = await adapter.open_note(
                index=params.get("index"),
                note_id=str(params.get("note_id", "")),
                wait_seconds=max(wait_seconds, 0),
            )
            return _dump_payload({"site": site_name, "action": action, "result": payload})

        if action == "close_note":
            payload = await adapter.close_note()
            return _dump_payload({"site": site_name, "action": action, "result": payload})

        if action == "read_note":
            level = str(params.get("level", "lite"))
            screenshot_path = ctx.next_screenshot_path("note_detail")
            note = await adapter.read_note(
                index=params.get("index"),
                note_id=str(params.get("note_id", "")),
                level=level,
                max_comments=int(params.get("max_comments", 4)),
                max_images=int(params.get("max_images", 6)),
                max_video_frames=int(params.get("max_video_frames", 4)),
                include_comments=params.get("include_comments"),
                include_media=params.get("include_media"),
                open_wait_seconds=max(wait_seconds, 0),
                close_after=bool(params.get("close_after", False)),
            )
            saved = await self._bridge.save_screenshot(screenshot_path)
            note.screenshot_path = Path(saved).name if saved else ""
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
                    include_media=params.get("include_media"),
                ).to_dict(),
                "entity": note.to_tool_dict(),
                "timing": adapter.timing.summary(),
            }
            return _dump_payload(payload)

        return f"Unsupported action: {action}"


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
    )


async def _current_url(bridge: ExtensionBridge | TabBridge) -> str:
    info = await bridge.get_tab_info()
    return str(info.get("url") or "")


async def _detect_current_site(bridge: ExtensionBridge | TabBridge) -> str | None:
    return detect_site(await _current_url(bridge))


def _dump_payload(payload: dict, max_chars: int = 16_000) -> str:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if len(text) <= max_chars:
        return text

    entity = payload.get("entity")
    if isinstance(entity, dict):
        video = entity.get("video")
        if isinstance(video, dict):
            excerpt = str(video.get("transcript_excerpt") or "")
            if len(excerpt) > 500:
                video["transcript_excerpt"] = excerpt[:500] + "... [truncated]"
        comments = entity.get("top_comments")
        if isinstance(comments, list) and len(comments) > 5:
            entity["top_comments"] = comments[:5]
        images = entity.get("images")
        if isinstance(images, list) and len(images) > 6:
            entity["images"] = images[:6]
        content = str(entity.get("content") or "")
        if len(content) > 1200:
            entity["content"] = content[:1200] + "... [truncated]"

    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n... [truncated]"
