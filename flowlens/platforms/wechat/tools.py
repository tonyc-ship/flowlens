"""WeChat-specific agent tools built on the desktop app adapter."""

from __future__ import annotations

import io
import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from PIL import Image

from ...agent.tool import Tool, ToolContext
from ...core.ocr_layout import OCRPage
from ...perception.llm import VisionLLM
from .app import WeChatDesktopApp, normalize_wechat_title
from .models import WeChatMessage, WeChatParsedCapture
from .parser import WeChatConversationParser


def _merge_messages(captures: list[WeChatParsedCapture]) -> list[WeChatMessage]:
    merged: list[WeChatMessage] = []
    seen: set[str] = set()
    for capture in reversed(captures):
        for message in sorted(capture.messages, key=lambda item: -item.y_norm):
            key = message.dedupe_key()
            if key in seen:
                continue
            seen.add(key)
            merged.append(message)
    return merged


def _merge_date_markers(captures: list[WeChatParsedCapture]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for capture in reversed(captures):
        for item in capture.date_markers:
            candidate = str(item or "").strip()
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            merged.append(candidate)
    return merged


def _best_conversation_title(captures: list[WeChatParsedCapture], fallback: str = "") -> str:
    candidates = [str(fallback or "").strip()]
    candidates.extend(str(item.conversation_title or "").strip() for item in captures)
    usable = [item for item in candidates if normalize_wechat_title(item)]
    if not usable:
        return fallback or "未识别会话"
    return max(usable, key=lambda item: (len(normalize_wechat_title(item)), len(item)))


def _speaker_counts(messages: list[WeChatMessage], *, limit: int = 12) -> list[dict]:
    counter: Counter[str] = Counter()
    for message in messages:
        speaker = str(message.speaker or "").strip()
        if not speaker:
            speaker = "self" if message.side == "right" else "other"
        counter[speaker] += 1
    return [
        {"speaker": speaker, "message_count": count}
        for speaker, count in counter.most_common(limit)
    ]


def _message_excerpt(
    messages: list[WeChatMessage],
    *,
    order: str = "oldest_first",
    offset: int = 0,
    limit: int = 12,
    speaker: str = "",
    query: str = "",
) -> list[dict]:
    speaker_filter = str(speaker or "").strip().casefold()
    query_filter = str(query or "").strip().casefold()

    filtered = []
    for message in messages:
        if speaker_filter and str(message.speaker or "").strip().casefold() != speaker_filter:
            continue
        if query_filter and query_filter not in str(message.text or "").casefold():
            continue
        filtered.append(message)

    ordered = filtered if order != "newest_first" else list(reversed(filtered))
    window = ordered[max(0, offset): max(0, offset) + max(1, limit)]
    return [message.to_dict() for message in window]


def _history_summary_payload(
    conversation_title: str,
    *,
    open_result: dict,
    stop_reason: str,
    captures: list[WeChatParsedCapture],
    merged_messages: list[WeChatMessage],
    merged_dates: list[str],
    screenshot_files: list[str],
) -> dict:
    top_speakers = _speaker_counts(merged_messages)
    oldest_preview = _message_excerpt(merged_messages, order="oldest_first", limit=8)
    newest_preview = _message_excerpt(merged_messages, order="newest_first", limit=8)
    content_summary = (
        f"{len(merged_messages)} unique messages across {len(captures)} captures"
        + (f"; visible date markers: {', '.join(merged_dates[:4])}" if merged_dates else "")
    )
    return {
        "entity_type": "wechat_conversation",
        "title": conversation_title,
        "summary": content_summary,
        "content_summary": content_summary,
        "conversation": conversation_title,
        "open_result": open_result,
        "stop_reason": stop_reason,
        "capture_count": len(captures),
        "unique_message_count": len(merged_messages),
        "date_markers": merged_dates,
        "top_speakers": top_speakers,
        "screenshots": screenshot_files,
        "message_samples": {
            "oldest_first": oldest_preview,
            "newest_first": newest_preview,
        },
        "captures": [capture.to_dict() for capture in captures],
        "messages": [asdict(message) for message in merged_messages],
    }


def _tool_preview(parsed: WeChatParsedCapture) -> dict:
    return {
        "capture_index": parsed.capture_index,
        "conversation_title": parsed.conversation_title,
        "parser_mode": parsed.parser_mode,
        "date_markers": parsed.date_markers[:6],
        "message_count": len(parsed.messages),
        "messages_preview": [message.to_dict() for message in parsed.messages[:8]],
        "notes": parsed.notes[:6],
    }


def _vision_backend(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"anthropic", "sonnet"}:
        return "sonnet"
    if normalized in {"openai", "kimi", "qwen", "qwen-local"}:
        return normalized
    return "sonnet"


class _WeChatToolRuntime:
    def __init__(self, *, llm_backend: str):
        self.vision = VisionLLM(backend=_vision_backend(llm_backend))
        self.app = WeChatDesktopApp(vision=self.vision)
        self.parser = WeChatConversationParser(vision=self.vision)


class _WeChatTool(Tool):
    capability_pack = "wechat"

    def __init__(self, *, llm_backend: str = "sonnet") -> None:
        self._llm_backend = llm_backend
        self._runtime: _WeChatToolRuntime | None = None

    @property
    def runtime(self) -> _WeChatToolRuntime:
        if self._runtime is None:
            self._runtime = _WeChatToolRuntime(llm_backend=self._llm_backend)
        return self._runtime

    def _ensure_open_conversation(self, conversation: str = "") -> dict:
        app = self.runtime.app
        target = str(conversation or "").strip()
        if target:
            return app.open_conversation(target)
        _, image, page = app.capture_state()
        if app.conversation_visible(image, page):
            return {"opened": True, "method": "current_conversation", "match": app.read_open_conversation_title(page)}
        return app.open_first_visible_conversation()

    def _capture_and_parse(
        self,
        ctx: ToolContext,
        *,
        capture_index: int,
        label: str,
        allow_9b_fallback: bool,
    ) -> tuple[Path, WeChatParsedCapture]:
        app = self.runtime.app
        parser = self.runtime.parser
        screenshot_path = ctx.next_screenshot_path(label).with_suffix(".jpg")
        _, image, ocr_page = app.capture_state()
        image.save(screenshot_path, quality=95)
        parsed = parser.parse_capture(
            capture_index=capture_index,
            screenshot_path=screenshot_path,
            image=image,
            ocr_page=ocr_page,
            allow_9b_fallback=allow_9b_fallback,
        )
        return screenshot_path, parsed

    def _ocr_page_for_image(self, image: Image.Image) -> OCRPage:
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        results = self.runtime.app.session.ocr.recognize(buffer.getvalue())
        return OCRPage.from_results(results, size_px=image.size)

    def _resolve_history_artifact_path(self, ctx: ToolContext, artifact_path: str = "") -> Path:
        candidate = str(artifact_path or "").strip()
        if candidate:
            path = Path(candidate)
            if not path.is_absolute():
                path = ctx.run_dir / candidate
            if path.is_file():
                return path
            raise FileNotFoundError(candidate)

        site_results = ctx.run_dir / "site_results"
        matches = sorted(site_results.glob("*wechat_history*.json"))
        if matches:
            return matches[-1]
        raise FileNotFoundError("No WeChat history artifact found in site_results/")

    @staticmethod
    def _relative_artifact_path(ctx: ToolContext, path: Path) -> str:
        try:
            return str(path.relative_to(ctx.run_dir))
        except ValueError:
            return str(path)


class WeChatOpenConversationTool(_WeChatTool):
    name = "wechat_open_conversation"
    description = "Open a WeChat conversation by name, or ensure the current visible conversation is ready."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "conversation": {"type": "string"},
            },
        }

    async def execute(self, params: dict, ctx: ToolContext) -> str:
        conversation = str(params.get("conversation") or "").strip()
        result = self._ensure_open_conversation(conversation)
        _, image, page = self.runtime.app.capture_state()
        return json.dumps(
            {
                "ok": bool(result.get("opened")),
                "conversation_requested": conversation,
                "open_result": result,
                "current_title": self.runtime.app.read_open_conversation_title(page),
                "conversation_visible": self.runtime.app.conversation_visible(image, page),
            },
            ensure_ascii=False,
            indent=2,
        )


class WeChatCaptureConversationTool(_WeChatTool):
    name = "wechat_capture_conversation"
    description = (
        "Capture the current visible WeChat conversation, parse the visible chat messages, and save a screenshot plus JSON artifact."
    )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "conversation": {"type": "string"},
                "label": {"type": "string", "default": "wechat_capture"},
                "deep_parse": {"type": "boolean", "default": False},
            },
        }

    async def execute(self, params: dict, ctx: ToolContext) -> str:
        conversation = str(params.get("conversation") or "").strip()
        label = str(params.get("label") or "wechat_capture").strip() or "wechat_capture"
        open_result = self._ensure_open_conversation(conversation)
        screenshot_path, parsed = self._capture_and_parse(
            ctx,
            capture_index=0,
            label=label,
            allow_9b_fallback=bool(params.get("deep_parse", False)),
        )
        artifact_path = ctx.write_json_artifact(
            f"{label}_parsed",
            parsed.to_dict(),
            subdir="site_results",
            artifact_kind="wechat_capture",
            summary=f"WeChat capture {parsed.conversation_title or conversation or 'current'}",
            metadata={"site": "wechat", "screenshot_file": screenshot_path.name},
        )
        return json.dumps(
            {
                "ok": True,
                "open_result": open_result,
                "screenshot_file": screenshot_path.name,
                "artifact_path": artifact_path,
                "parsed": _tool_preview(parsed),
            },
            ensure_ascii=False,
            indent=2,
        )


class WeChatScrollHistoryTool(_WeChatTool):
    name = "wechat_scroll_history"
    description = "Scroll upward inside the open WeChat conversation to reveal older history."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "repeats": {"type": "integer", "minimum": 1, "maximum": 40, "default": 10},
                "line_delta": {"type": "integer", "default": 12},
            },
        }

    async def execute(self, params: dict, ctx: ToolContext) -> str:
        repeats = max(1, int(params.get("repeats", 10)))
        line_delta = int(params.get("line_delta", 12))
        screen_x, screen_y = self.runtime.app.scroll_history_up(repeats=repeats, line_delta=line_delta)
        return json.dumps(
            {
                "ok": True,
                "repeats": repeats,
                "line_delta": line_delta,
                "scroll_anchor": {"x": screen_x, "y": screen_y},
            },
            ensure_ascii=False,
            indent=2,
        )


class WeChatCollectHistoryTool(_WeChatTool):
    name = "wechat_collect_history"
    description = (
        "Collect structured WeChat conversation history across multiple screens. "
        "This opens the target conversation if needed, captures visible chat screens, scrolls upward, "
        "and saves a merged history artifact for later summarization."
    )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "conversation": {"type": "string"},
                "target_screens": {"type": "integer", "minimum": 1, "maximum": 16, "default": 6},
                "max_scroll_rounds": {"type": "integer", "minimum": 1, "maximum": 20, "default": 10},
                "min_capture_rounds": {"type": "integer", "minimum": 1, "maximum": 8, "default": 3},
                "target_messages": {"type": "integer", "minimum": 8, "maximum": 200, "default": 45},
                "target_date_markers": {"type": "integer", "minimum": 0, "maximum": 8, "default": 2},
                "stale_limit": {"type": "integer", "minimum": 1, "maximum": 4, "default": 2},
                "scroll_repeats": {"type": "integer", "minimum": 1, "maximum": 40, "default": 10},
                "scroll_line_delta": {"type": "integer", "minimum": 1, "maximum": 24, "default": 12},
                "fast_mode": {"type": "boolean", "default": True},
            },
        }

    async def execute(self, params: dict, ctx: ToolContext) -> str:
        conversation = str(params.get("conversation") or "").strip()
        target_screens = max(1, int(params.get("target_screens", 6)))
        max_scroll_rounds = max(1, int(params.get("max_scroll_rounds", 10)))
        min_capture_rounds = max(1, int(params.get("min_capture_rounds", 3)))
        target_messages = max(8, int(params.get("target_messages", 45)))
        target_date_markers = max(0, int(params.get("target_date_markers", 2)))
        stale_limit = max(1, int(params.get("stale_limit", 2)))
        scroll_repeats = max(1, int(params.get("scroll_repeats", 10)))
        scroll_line_delta = max(1, int(params.get("scroll_line_delta", 12)))
        fast_mode = bool(params.get("fast_mode", True))

        open_result = self._ensure_open_conversation(conversation)
        captures: list[WeChatParsedCapture] = []
        screenshot_files: list[str] = []
        page_signatures: set[str] = set()
        known_message_keys: set[str] = set()
        consecutive_stale = 0
        stop_reason = "max_scroll_rounds_reached"

        for capture_index in range(max_scroll_rounds):
            allow_9b = (not fast_mode) or capture_index < 2 or capture_index % 3 == 0
            screenshot_path, parsed = self._capture_and_parse(
                ctx,
                capture_index=capture_index,
                label=f"wechat_history_{capture_index:02d}",
                allow_9b_fallback=allow_9b,
            )
            captures.append(parsed)
            screenshot_files.append(screenshot_path.name)

            new_messages = 0
            for message in parsed.messages:
                key = message.dedupe_key()
                if key in known_message_keys:
                    continue
                known_message_keys.add(key)
                new_messages += 1

            if parsed.page_signature in page_signatures or new_messages == 0:
                consecutive_stale += 1
            else:
                consecutive_stale = 0
                page_signatures.add(parsed.page_signature)

            merged_messages = _merge_messages(captures)
            merged_dates = _merge_date_markers(captures)
            enough_messages = len(merged_messages) >= target_messages
            enough_dates = len(merged_dates) >= target_date_markers
            enough_screens = len(captures) >= target_screens

            if consecutive_stale >= stale_limit:
                stop_reason = "stale_capture_limit"
                break
            if len(captures) >= min_capture_rounds and enough_messages and enough_dates and new_messages <= 2:
                stop_reason = "coverage_threshold_met"
                break
            if len(captures) >= max(target_screens, min_capture_rounds) and enough_screens and new_messages <= 1:
                stop_reason = "target_screens_reached"
                break

            self.runtime.app.scroll_history_up(
                repeats=scroll_repeats if consecutive_stale == 0 else max(6, scroll_repeats - 2),
                line_delta=scroll_line_delta,
            )

        merged_messages = _merge_messages(captures)
        merged_dates = _merge_date_markers(captures)
        conversation_title = _best_conversation_title(captures, conversation or "")
        artifact_payload = _history_summary_payload(
            conversation_title,
            open_result=open_result,
            stop_reason=stop_reason,
            captures=captures,
            merged_messages=merged_messages,
            merged_dates=merged_dates,
            screenshot_files=screenshot_files,
        )
        artifact_path = ctx.write_json_artifact(
            "wechat_history",
            artifact_payload,
            subdir="site_results",
            artifact_kind="wechat_history",
            summary=f"WeChat history {conversation_title or conversation or 'current'}",
            metadata={"site": "wechat"},
        )
        return json.dumps(
            {
                "ok": True,
                "artifact_path": artifact_path,
                "conversation": conversation_title,
                "stop_reason": stop_reason,
                "capture_count": len(captures),
                "unique_message_count": len(merged_messages),
                "date_markers": merged_dates[:8],
                "top_speakers": artifact_payload["top_speakers"][:8],
                "screenshots": screenshot_files,
                "capture_preview": [_tool_preview(capture) for capture in captures[:4]],
            },
            ensure_ascii=False,
            indent=2,
        )


class WeChatReadHistoryArtifactTool(_WeChatTool):
    name = "wechat_read_history_artifact"
    description = (
        "Read a saved WeChat history artifact as compact structured slices with speaker counts, "
        "date markers, and selected messages. Prefer this over raw read_saved_artifact for long chat histories."
    )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "artifact_path": {"type": "string"},
                "order": {"type": "string", "enum": ["oldest_first", "newest_first"], "default": "oldest_first"},
                "offset": {"type": "integer", "minimum": 0, "maximum": 1000, "default": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 80, "default": 20},
                "speaker": {"type": "string"},
                "query": {"type": "string"},
            },
        }

    async def execute(self, params: dict, ctx: ToolContext) -> str:
        path = self._resolve_history_artifact_path(ctx, str(params.get("artifact_path") or ""))
        payload = json.loads(path.read_text(encoding="utf-8"))
        raw_messages = list(payload.get("messages") or [])
        messages = [
            WeChatMessage(
                speaker=str(item.get("speaker") or ""),
                side=str(item.get("side") or ""),
                text=str(item.get("text") or ""),
                timestamp=str(item.get("timestamp") or ""),
                kind=str(item.get("kind") or "text"),
                source_capture=int(item.get("source_capture") or 0),
                y_norm=float(item.get("y_norm") or 0.0),
            )
            for item in raw_messages
            if isinstance(item, dict)
        ]
        order = str(params.get("order") or "oldest_first").strip() or "oldest_first"
        offset = max(0, int(params.get("offset", 0)))
        limit = max(1, int(params.get("limit", 20)))
        speaker = str(params.get("speaker") or "").strip()
        query = str(params.get("query") or "").strip()
        excerpt = _message_excerpt(
            messages,
            order=order,
            offset=offset,
            limit=limit,
            speaker=speaker,
            query=query,
        )
        filtered_messages = [
            message for message in messages
            if (not speaker or message.speaker == speaker)
            and (not query or query.casefold() in message.text.casefold())
        ]
        return json.dumps(
            {
                "ok": True,
                "artifact_path": self._relative_artifact_path(ctx, path),
                "conversation": str(payload.get("conversation") or payload.get("title") or ""),
                "capture_count": int(payload.get("capture_count") or len(payload.get("captures") or [])),
                "total_messages": len(messages),
                "filtered_messages": len(filtered_messages),
                "date_markers": list(payload.get("date_markers") or [])[:10],
                "top_speakers": _speaker_counts(filtered_messages or messages)[:10],
                "window": {
                    "order": order,
                    "offset": offset,
                    "limit": limit,
                    "speaker": speaker,
                    "query": query,
                },
                "messages": excerpt,
            },
            ensure_ascii=False,
            indent=2,
        )


class WeChatOcrConversationRegionTool(_WeChatTool):
    name = "wechat_ocr_conversation_region"
    description = (
        "OCR only the WeChat conversation pane from the current window or a saved screenshot. "
        "Use this when you need exact chat text faster than a full vision-model screenshot analysis."
    )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "conversation": {"type": "string"},
                "screenshot_file": {"type": "string"},
                "label": {"type": "string", "default": "wechat_conversation_ocr"},
                "max_chars": {"type": "integer", "minimum": 200, "maximum": 6000, "default": 1800},
            },
        }

    async def execute(self, params: dict, ctx: ToolContext) -> str:
        conversation = str(params.get("conversation") or "").strip()
        screenshot_file = str(params.get("screenshot_file") or "").strip()
        max_chars = max(200, int(params.get("max_chars", 1800)))

        screenshot_path: Path
        if screenshot_file:
            screenshot_path = ctx.run_dir / screenshot_file
            if not screenshot_path.is_file():
                raise FileNotFoundError(screenshot_file)
            image = Image.open(screenshot_path).convert("RGB")
            ocr_page = self._ocr_page_for_image(image)
        else:
            label = str(params.get("label") or "wechat_conversation_ocr").strip() or "wechat_conversation_ocr"
            self._ensure_open_conversation(conversation)
            screenshot_path = ctx.next_screenshot_path(label).with_suffix(".jpg")
            _, image, ocr_page = self.runtime.app.capture_state()
            image.save(screenshot_path, quality=95)

        region = self.runtime.app.conversation_region(ocr_page)
        ordered_lines = sorted(ocr_page.within(region), key=lambda item: (-item.center_y, item.left))
        text = "\n".join(item.text.strip() for item in ordered_lines if item.text.strip())
        title = self.runtime.app.read_open_conversation_title(ocr_page)
        return json.dumps(
            {
                "ok": True,
                "screenshot_file": screenshot_path.name,
                "conversation_title": title,
                "line_count": len(ordered_lines),
                "ocr_text": text[:max_chars],
                "truncated": len(text) > max_chars,
                "full_text_chars": len(text),
            },
            ensure_ascii=False,
            indent=2,
        )


def make_wechat_tools(*, llm_backend: str = "sonnet") -> list[Tool]:
    return [
        WeChatOpenConversationTool(llm_backend=llm_backend),
        WeChatCaptureConversationTool(llm_backend=llm_backend),
        WeChatScrollHistoryTool(llm_backend=llm_backend),
        WeChatCollectHistoryTool(llm_backend=llm_backend),
        WeChatReadHistoryArtifactTool(llm_backend=llm_backend),
        WeChatOcrConversationRegionTool(llm_backend=llm_backend),
    ]
