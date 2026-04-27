"""Hybrid conversation parsing for the WeChat desktop client."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from difflib import SequenceMatcher

from PIL import Image

from ...core.ocr_layout import OCRPage, NormalizedRegion
from ...perception.llm import VisionLLM, VisionRequestConfig
from .app import (
    WECHAT_FULL_TIMELINE_REGION,
    WECHAT_FULL_TITLE_REGION,
    WECHAT_HEADER_REGION,
    WECHAT_TIMELINE_REGION,
    normalize_wechat_title,
    _title_sort_key,
    _usable_title_text,
)
from .models import WeChatMessage, WeChatParsedCapture
from .vision_profiles import WECHAT_LAYOUT_PARSE_2B, WECHAT_PARSE_FALLBACK

_UI_NOISE_RE = re.compile(
    r"(?:go to the latest message|official accounts|service accounts|search|搜索)",
    re.IGNORECASE,
)
_ICON_ONLY_RE = re.compile(r"^[\W_©•·…∞~。，、]+$")


def _compact_text(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).strip()


def _normalize_date_marker(text: str) -> str:
    candidate = str(text or "").strip()
    candidate = re.sub(r"\s+", " ", candidate)
    compact = _compact_text(candidate)
    if re.match(r"^\d{1,2}[/.月-]\d{1,2}(?:日)?\d{1,2}:\d{2}$", compact):
        candidate = f"{compact[:-5]} {compact[-5:]}"
    elif re.match(r"^\d{4}[/.年-]\d{1,2}[/.月-]\d{1,2}(?:日)?\d{1,2}:\d{2}$", compact):
        candidate = f"{compact[:-5]} {compact[-5:]}"
    elif re.match(r"^(?:today|yesterday)\d{1,2}:\d{2}$", compact, re.IGNORECASE):
        candidate = f"{compact[:-5]} {compact[-5:]}"
    return candidate.strip()


def _looks_like_timestamp(text: str) -> bool:
    compact = _compact_text(text).casefold()
    if not compact:
        return False
    patterns = (
        r"^(?:today|yesterday)\d{1,2}:\d{2}$",
        r"^\d{1,2}[/.月-]\d{1,2}(?:日)?\d{1,2}:\d{2}$",
        r"^\d{4}[/.年-]\d{1,2}[/.月-]\d{1,2}(?:日)?\d{1,2}:\d{2}$",
        r"^(?:today|yesterday)$",
        r"^\d{1,2}[/.月-]\d{1,2}(?:日)?$",
        r"^\d{4}[/.年-]\d{1,2}[/.月-]\d{1,2}(?:日)?$",
        r"^(?:星期[一二三四五六日天]|周[一二三四五六日天])(?:\d{1,2}:\d{2})?$",
    )
    return any(re.match(pattern, compact, re.IGNORECASE) for pattern in patterns)


def _looks_like_speaker_label(text: str) -> bool:
    candidate = str(text or "").strip()
    if not candidate:
        return False
    if _looks_like_timestamp(candidate) or _UI_NOISE_RE.search(candidate):
        return False
    if len(candidate) > 18:
        return False
    if any(token in candidate for token in ("：", ":", "。", "，", ",")):
        return False
    return not _ICON_ONLY_RE.match(candidate)


def _looks_like_ui_noise(text: str) -> bool:
    candidate = str(text or "").strip()
    if not candidate:
        return True
    if _UI_NOISE_RE.search(candidate):
        return True
    return bool(_ICON_ONLY_RE.match(candidate))


def _prefer_conversation_title(primary: str, candidate: str) -> str:
    primary_text = str(primary or "").strip()
    candidate_text = str(candidate or "").strip()
    primary_normalized = normalize_wechat_title(primary_text)
    candidate_normalized = normalize_wechat_title(candidate_text)
    if not primary_normalized:
        return candidate_text
    if not candidate_normalized:
        return primary_text
    if len(candidate_normalized) > len(primary_normalized):
        return candidate_text
    return primary_text


def _generic_speaker_name(text: str) -> bool:
    return str(text or "").strip().casefold() in {"", "other", "unknown", "left", "right", "speaker", "左侧", "右侧"}


def _parse_json_payload(raw: str) -> dict:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    if cleaned.startswith("json\n"):
        cleaned = cleaned.split("\n", 1)[1].strip()
    if not cleaned.startswith("{") and "{" in cleaned and "}" in cleaned:
        cleaned = cleaned[cleaned.find("{"): cleaned.rfind("}") + 1]
    return json.loads(cleaned)


@dataclass
class _MessageBuffer:
    side: str
    speaker: str
    timestamp: str
    lines: list[str] = field(default_factory=list)
    top_y: float = 0.0
    last_y: float = 0.0

    def append(self, text: str, y_norm: float) -> None:
        self.lines.append(text)
        self.last_y = y_norm

    def build(self, *, capture_index: int) -> WeChatMessage | None:
        text = "\n".join(line for line in self.lines if line.strip()).strip()
        if not text:
            return None
        return WeChatMessage(
            speaker=self.speaker,
            side=self.side,
            text=text,
            timestamp=self.timestamp,
            source_capture=capture_index,
            y_norm=self.top_y,
        )


class WeChatConversationParser:
    """Parse OCR lines into structured messages using layout-aware local vision."""

    def __init__(self, *, vision: VisionLLM | None = None):
        self.vision = vision

    def parse_capture(
        self,
        *,
        capture_index: int,
        screenshot_path: str | Path,
        image: Image.Image,
        ocr_page: OCRPage,
        allow_vision: bool = True,
        allow_9b_fallback: bool = True,
    ) -> WeChatParsedCapture:
        conversation_title = self._read_title(ocr_page)
        ocr_capture = self._build_ocr_capture(
            capture_index=capture_index,
            screenshot_path=screenshot_path,
            conversation_title=conversation_title,
            ocr_page=ocr_page,
        )
        candidates = [ocr_capture]

        if self.vision is not None and allow_vision:
            vision_2b = self._parse_with_vision(
                capture_index=capture_index,
                screenshot_path=screenshot_path,
                image=image,
                conversation_title=conversation_title,
                ocr_page=ocr_page,
                ocr_capture=ocr_capture,
                config=WECHAT_LAYOUT_PARSE_2B,
                parser_mode="vision_layout_2b",
            )
            if vision_2b is not None:
                candidates.append(vision_2b)

            best = self._choose_best_candidate(candidates, conversation_title=conversation_title)
            if allow_9b_fallback and self._needs_9b_fallback(
                best,
                ocr_capture,
                conversation_title=conversation_title,
            ):
                vision_9b = self._parse_with_vision(
                    capture_index=capture_index,
                    screenshot_path=screenshot_path,
                    image=image,
                    conversation_title=conversation_title,
                    ocr_page=ocr_page,
                    ocr_capture=ocr_capture,
                    config=WECHAT_PARSE_FALLBACK,
                    parser_mode="vision_fallback_9b",
                )
                if vision_9b is not None:
                    candidates = [candidate for candidate in candidates if candidate.parser_mode != "vision_layout_2b"]
                    candidates.append(vision_9b)

        chosen = self._choose_best_candidate(candidates, conversation_title=conversation_title)
        merged_dates = self._merge_dates(chosen.date_markers, ocr_capture.date_markers)
        notes = list(chosen.notes)
        if chosen.parser_mode != ocr_capture.parser_mode and ocr_capture.date_markers and not chosen.date_markers:
            notes.append("Date markers inherited from OCR because the chosen layout parser omitted them.")

        return WeChatParsedCapture(
            capture_index=chosen.capture_index,
            screenshot_path=chosen.screenshot_path,
            conversation_title=_prefer_conversation_title(conversation_title, chosen.conversation_title),
            parser_mode=chosen.parser_mode,
            ocr_line_count=chosen.ocr_line_count,
            page_signature=chosen.page_signature,
            date_markers=merged_dates,
            messages=chosen.messages,
            notes=notes,
        )

    def _read_title(self, page: OCRPage) -> str:
        for region in (WECHAT_HEADER_REGION, WECHAT_FULL_TITLE_REGION):
            candidates = sorted(page.within(region), key=_title_sort_key, reverse=True)
            for item in candidates:
                if _usable_title_text(item.text) and not _looks_like_ui_noise(item.text):
                    return item.text
        return ""

    def _timeline_region(self, page: OCRPage) -> NormalizedRegion:
        return WECHAT_TIMELINE_REGION if page.within(WECHAT_HEADER_REGION) else WECHAT_FULL_TIMELINE_REGION

    def _build_ocr_capture(
        self,
        *,
        capture_index: int,
        screenshot_path: str | Path,
        conversation_title: str,
        ocr_page: OCRPage,
    ) -> WeChatParsedCapture:
        messages, date_markers, notes, speaker_labels = self._parse_from_ocr(
            capture_index=capture_index,
            conversation_title=conversation_title,
            ocr_page=ocr_page,
        )
        if speaker_labels == 0 and any(item.side == "left" for item in messages):
            notes.append("OCR did not recover explicit left-speaker labels.")
        return WeChatParsedCapture(
            capture_index=capture_index,
            screenshot_path=str(screenshot_path),
            conversation_title=conversation_title,
            parser_mode="ocr_layout",
            ocr_line_count=len(ocr_page.lines),
            page_signature=ocr_page.text_signature(region=self._timeline_region(ocr_page)),
            date_markers=date_markers,
            messages=messages,
            notes=notes,
        )

    def _ocr_speaker_labels(self, page: OCRPage) -> list[str]:
        timeline_region = self._timeline_region(page)
        pane_left = timeline_region.left
        pane_width = timeline_region.right - timeline_region.left
        labels: list[str] = []
        for line in sorted(page.within(timeline_region), key=lambda item: (-item.center_y, item.left)):
            text = line.text.strip()
            if not text or _looks_like_ui_noise(text):
                continue
            left_rel = (line.left - pane_left) / pane_width
            width_rel = line.w / pane_width
            if left_rel <= 0.11 and width_rel <= 0.18 and _looks_like_speaker_label(text):
                labels.append(text)
        return labels

    @staticmethod
    def _match_ocr_speaker(raw_speaker: str, labels: list[str]) -> str | None:
        candidate = str(raw_speaker or "").strip()
        if _generic_speaker_name(candidate):
            return None
        best_label = ""
        best_score = 0.0
        for label in labels:
            score = SequenceMatcher(None, normalize_wechat_title(candidate), normalize_wechat_title(label)).ratio()
            if score > best_score:
                best_label = label
                best_score = score
        return best_label if best_score >= 0.72 else None

    def _refine_vision_messages(
        self,
        messages: list[WeChatMessage],
        *,
        ocr_capture: WeChatParsedCapture,
        ocr_page: OCRPage,
    ) -> list[WeChatMessage]:
        labels = self._ocr_speaker_labels(ocr_page)
        refined: list[WeChatMessage] = []
        label_index = 0
        previous_left = ""
        for message in sorted(messages, key=lambda item: -item.y_norm):
            speaker = message.speaker
            side = message.side
            if side == "right":
                speaker = "self"
            elif side == "left":
                matched = self._match_ocr_speaker(speaker, labels)
                if matched:
                    speaker = matched
                    previous_left = matched
                elif _generic_speaker_name(speaker):
                    if label_index < len(labels):
                        speaker = labels[label_index]
                        previous_left = speaker
                        label_index += 1
                    elif previous_left:
                        speaker = previous_left
                    else:
                        speaker = "other"
            refined.append(
                WeChatMessage(
                    speaker=speaker,
                    side=side,
                    text=message.text,
                    timestamp=message.timestamp,
                    kind=message.kind,
                    source_capture=message.source_capture,
                    y_norm=message.y_norm,
                )
            )
        return sorted(refined, key=lambda item: -item.y_norm)

    def _parse_from_ocr(
        self,
        *,
        capture_index: int,
        conversation_title: str,
        ocr_page: OCRPage,
    ) -> tuple[list[WeChatMessage], list[str], list[str], int]:
        timeline_region = self._timeline_region(ocr_page)
        pane_left = timeline_region.left
        pane_width = timeline_region.right - timeline_region.left
        timeline_lines = ocr_page.within(timeline_region)
        ordered = sorted(timeline_lines, key=lambda item: (-item.center_y, item.left))

        messages: list[WeChatMessage] = []
        date_markers: list[str] = []
        notes: list[str] = []
        current_date = ""
        pending_left_speaker = ""
        speaker_labels = 0
        buffer: _MessageBuffer | None = None

        def flush() -> None:
            nonlocal buffer
            if buffer is None:
                return
            built = buffer.build(capture_index=capture_index)
            if built is not None and not _looks_like_ui_noise(built.text):
                messages.append(built)
            buffer = None

        for line in ordered:
            text = line.text.strip()
            if not text:
                continue
            if line.confidence < 0.35 and len(text) <= 2:
                continue
            if _looks_like_ui_noise(text):
                continue

            left_rel = (line.left - pane_left) / pane_width
            right_rel = (line.right - pane_left) / pane_width
            center_rel = (line.center_x - pane_left) / pane_width
            width_rel = line.w / pane_width

            if (
                _looks_like_timestamp(text)
                and 0.28 <= center_rel <= 0.72
                and width_rel <= 0.30
            ):
                flush()
                current_date = _normalize_date_marker(text)
                if current_date not in date_markers:
                    date_markers.append(current_date)
                pending_left_speaker = ""
                continue

            if (
                left_rel <= 0.11
                and width_rel <= 0.18
                and _looks_like_speaker_label(text)
            ):
                flush()
                pending_left_speaker = text
                speaker_labels += 1
                continue

            side = self._classify_side(left_rel, right_rel)
            if side == "center":
                continue

            if (
                buffer is not None
                and buffer.side == side
                and abs(buffer.last_y - line.center_y) <= 0.055
            ):
                buffer.append(text, line.center_y)
                continue

            flush()
            speaker = "self" if side == "right" else (pending_left_speaker or conversation_title or "other")
            buffer = _MessageBuffer(
                side=side,
                speaker=speaker,
                timestamp=current_date,
                lines=[text],
                top_y=line.center_y,
                last_y=line.center_y,
            )
            if side == "left":
                pending_left_speaker = ""

        flush()

        if not messages:
            notes.append("No OCR messages parsed from the timeline region.")
        return messages, date_markers, notes, speaker_labels

    @staticmethod
    def _classify_side(left_rel: float, right_rel: float) -> str:
        if left_rel >= 0.32:
            return "right"
        if right_rel <= 0.58:
            return "left"
        return "center"

    @staticmethod
    def _merge_dates(primary: list[str], secondary: list[str]) -> list[str]:
        merged: list[str] = []
        seen: set[str] = set()
        for item in [*primary, *secondary]:
            candidate = _normalize_date_marker(item)
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            merged.append(candidate)
        return merged

    @staticmethod
    def _crop_timeline(image: Image.Image, region: NormalizedRegion) -> Image.Image:
        width, height = image.size
        box = (
            int(width * region.left),
            int(height * (1 - region.top)),
            int(width * region.right),
            int(height * (1 - region.bottom)),
        )
        return image.crop(box)

    def _vision_prompt(self, conversation_title: str, config: VisionRequestConfig) -> str:
        mode = "2B UI layout understanding" if config.local_model_name == WECHAT_LAYOUT_PARSE_2B.local_model_name else "9B hard-case fallback"
        return f"""你在看微信 macOS 客户端聊天窗口截图，只抽取当前可见聊天内容。

当前会话标题参考：{conversation_title or "未知"}
当前模式：{mode}

只返回 JSON：
{{
  "conversation_title": "string",
  "date_markers": ["可见时间分隔线"],
  "messages": [
    {{
      "speaker": "左侧真实说话人名，右侧固定 self；不确定就留空",
      "side": "left|right|center",
      "kind": "text|image|video|sticker|system",
      "text": "仅聊天正文；图片/视频可留空",
      "confidence": "high|medium|low"
    }}
  ],
  "quality": "good|uncertain",
  "notes": ["简短备注"]
}}

规则：
- 忽略顶部群名、顶部菜单图标、搜索框、右下角 “Go to the latest message” 浮层。
- 忽略图片/视频缩略图内部或底部叠字，不要把它们当作 speaker。
- 中央日期/时间分隔线只能放进 date_markers，不能当普通消息。
- 左侧只有在名字明确位于消息上方时才填 speaker。
- 右侧任何消息 speaker 都必须是 self。
- 只保留你高度确定的项；不确定宁可省略。
"""

    def _parse_with_vision(
        self,
        *,
        capture_index: int,
        screenshot_path: str | Path,
        image: Image.Image,
        conversation_title: str,
        ocr_page: OCRPage,
        ocr_capture: WeChatParsedCapture,
        config: VisionRequestConfig,
        parser_mode: str,
    ) -> WeChatParsedCapture | None:
        if self.vision is None:
            return None
        timeline_region = self._timeline_region(ocr_page)
        timeline_crop = self._crop_timeline(image, timeline_region)
        raw = self.vision.analyze_page(
            timeline_crop,
            self._vision_prompt(conversation_title, config),
            config=config,
        )
        try:
            data = _parse_json_payload(raw)
        except Exception:
            return None

        messages: list[WeChatMessage] = []
        date_markers = self._merge_dates(
            [str(item) for item in data.get("date_markers", []) if str(item).strip()],
            [],
        )
        raw_messages = data.get("messages") or data.get("items") or []
        for index, item in enumerate(raw_messages if isinstance(raw_messages, list) else []):
            side = str(item.get("side") or "").strip().lower()
            if side not in {"left", "right", "center"}:
                continue
            text = str(item.get("text") or "").strip()
            speaker = str(item.get("speaker") or "").strip()
            kind = str(item.get("kind") or "text").strip().lower()
            if side == "center":
                if text and _looks_like_timestamp(text):
                    date_markers = self._merge_dates(date_markers, [text])
                continue
            if side == "right":
                speaker = "self"
            if _looks_like_ui_noise(text) or _looks_like_ui_noise(speaker):
                continue
            if side == "left" and not speaker:
                speaker = "other"
            if not text and kind == "text":
                continue
            messages.append(
                WeChatMessage(
                    speaker=speaker or ("self" if side == "right" else "other"),
                    side=side,
                    text=text,
                    timestamp="",
                    kind=kind if kind in {"text", "image", "video", "sticker", "system"} else "text",
                    source_capture=capture_index,
                    y_norm=max(0.0, 0.95 - index * 0.06),
                )
            )

        notes = [str(item) for item in data.get("notes", []) if str(item).strip()]
        quality = str(data.get("quality") or "").strip().lower()
        if quality and quality != "good":
            notes.append(f"Vision quality={quality}")

        refined_messages = self._refine_vision_messages(
            messages,
            ocr_capture=ocr_capture,
            ocr_page=ocr_page,
        )

        return WeChatParsedCapture(
            capture_index=capture_index,
            screenshot_path=str(screenshot_path),
            conversation_title=str(data.get("conversation_title") or conversation_title or ""),
            parser_mode=parser_mode,
            ocr_line_count=len(ocr_page.lines),
            page_signature=ocr_page.text_signature(region=timeline_region),
            date_markers=date_markers,
            messages=refined_messages,
            notes=notes,
        )

    def _candidate_score(self, parsed: WeChatParsedCapture, *, conversation_title: str) -> int:
        score = len(parsed.messages) * 7 + len(parsed.date_markers) * 2
        if any(item.side == "left" for item in parsed.messages) and any(item.side == "right" for item in parsed.messages):
            score += 4
        normalized_title = normalize_wechat_title(conversation_title)
        for item in parsed.messages:
            if item.side == "right" and item.speaker != "self":
                score -= 8
            if item.text and _looks_like_timestamp(item.text):
                score -= 10
            if _looks_like_ui_noise(item.text) or _looks_like_ui_noise(item.speaker):
                score -= 20
            if normalized_title and item.side == "left" and normalize_wechat_title(item.speaker) == normalized_title:
                score -= 12
        if parsed.parser_mode == "vision_layout_2b":
            score += 2
        if parsed.parser_mode == "vision_fallback_9b":
            score += 3
        return score

    def _choose_best_candidate(
        self,
        candidates: list[WeChatParsedCapture],
        *,
        conversation_title: str,
    ) -> WeChatParsedCapture:
        return max(
            candidates,
            key=lambda item: (
                self._candidate_score(item, conversation_title=conversation_title),
                len(item.messages),
                len(item.date_markers),
            ),
        )

    def _needs_9b_fallback(
        self,
        best: WeChatParsedCapture,
        ocr_capture: WeChatParsedCapture,
        *,
        conversation_title: str,
    ) -> bool:
        if best.parser_mode == "vision_fallback_9b":
            return False
        if not best.messages:
            return True
        if best.parser_mode == "vision_layout_2b":
            if any(_generic_speaker_name(item.speaker) for item in best.messages if item.side == "left"):
                return True
            best_right = sum(1 for item in best.messages if item.side == "right")
            ocr_right = sum(1 for item in ocr_capture.messages if item.side == "right")
            if best_right != ocr_right:
                return True
        if any("quality=uncertain" in note.casefold() for note in best.notes):
            return True
        if self._candidate_score(best, conversation_title=conversation_title) < 18:
            return True
        if (
            best.parser_mode == "ocr_layout"
            and self._candidate_score(ocr_capture, conversation_title=conversation_title) <= self._candidate_score(best, conversation_title=conversation_title)
        ):
            return True
        return False
