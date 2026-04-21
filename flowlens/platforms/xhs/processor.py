"""Site-aware Xiaohongshu extraction and enrichment.

This module restores the valuable parts of the old hardcoded workflow:
- deterministic normalization
- costed extraction plans
- multimodal note enrichment

It deliberately does NOT reintroduce a fixed task workflow. The generic agent
selects when to invoke these capabilities through a single site-aware tool.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from ...core.bridge import ExtensionBridge, TabBridge
from ...perception.media import BACKEND_QWEN_LOCAL, BACKEND_UI_TARS_LOCAL, MediaProcessor
from .capabilities import NoteExtractionPlan, plan_for_level
from .entities import AuthorEntity, Comment, ImageInfo, NoteCard, NoteEntity, NoteType, VideoInfo, parse_count_text

XHS_REFERER = "https://www.xiaohongshu.com"

_SEARCH_INPUT_JS = r"""
return (function() {
  const selectors = [
    'input#search-input',
    'input[type="search"]',
    'input[placeholder*="搜索"]',
    '.search-input input',
    '.search-container input'
  ];
  const input = selectors
    .map((selector) => document.querySelector(selector))
    .find((el) => el instanceof HTMLElement && el.getBoundingClientRect().width >= 120);
  if (!input) return JSON.stringify({ok: false, error: 'search_input_not_found'});

  const inputRect = input.getBoundingClientRect();
  const root = input.closest('form, header, .search-input, .search-container, .search-bar, .search-box') || document;
  const inputCenterY = inputRect.top + inputRect.height / 2;
  const rawSubmitCandidates = [
    ...root.querySelectorAll('button, [role="button"], a, div, span, svg, .search-icon, .search-btn, .icon-search'),
    ...document.querySelectorAll('button, [role="button"], a, div, span, svg, .search-icon, .search-btn, .icon-search'),
  ];
  const submitCandidates = [...new Set(rawSubmitCandidates)]
    .filter((el) => el instanceof HTMLElement || el instanceof SVGElement)
    .map((el) => {
      const clickable = el.closest?.('button, [role="button"], a, div, span') || el;
      const rect = clickable.getBoundingClientRect();
      const meta = [
        clickable.getAttribute?.('aria-label') || '',
        clickable.getAttribute?.('title') || '',
        clickable.className || '',
        el.getAttribute?.('aria-label') || '',
        el.getAttribute?.('title') || '',
        el.className || '',
      ].join(' ').toLowerCase();
      let score = 0;
      if (/search|搜索|find|query/.test(meta)) score += 100;
      if (/clear|close|cancel|remove|delete|清除|关闭|取消/.test(meta)) score -= 120;
      const centerY = rect.top + rect.height / 2;
      score -= Math.abs(rect.left - inputRect.right);
      score -= Math.abs(centerY - inputCenterY) * 0.6;
      if (rect.left >= inputRect.right - 8) score += 18;
      if (rect.left < inputRect.left - 24) score -= 60;
      if (root.contains(clickable)) score += 18;
      if (rect.left >= inputRect.left && rect.right <= inputRect.right) score -= 20;
      return { rect, score };
    })
    .filter(({ rect, score }) => rect.width >= 12 && rect.height >= 12 && rect.right >= inputRect.left && rect.left <= inputRect.right + 180 && score > -140)
    .sort((a, b) => b.score - a.score);

  const submit = submitCandidates[0] || null;
  return JSON.stringify({
    ok: true,
    input: {
      x: Math.round(inputRect.left + inputRect.width / 2),
      y: Math.round(inputRect.top + inputRect.height / 2),
    },
    submit: submit ? {
      x: Math.round(submit.rect.left + submit.rect.width / 2),
      y: Math.round(submit.rect.top + submit.rect.height / 2),
    } : null,
  });
})()
"""

def _set_search_input_js(query: str) -> str:
    encoded_query = json.dumps(str(query or ""), ensure_ascii=False)
    return f"""
return (function() {{
  const targetValue = {encoded_query};
  const selectors = [
    'input#search-input',
    'input[type="search"]',
    'input[placeholder*="搜索"]',
    '.search-input input',
    '.search-container input'
  ];
  const input = selectors
    .map((selector) => document.querySelector(selector))
    .find((el) => el instanceof HTMLElement && el.getBoundingClientRect().width >= 120);
  if (!input) return JSON.stringify({{ok: false, error: 'search_input_not_found'}});

  input.focus();
  if (input instanceof HTMLInputElement || input instanceof HTMLTextAreaElement) {{
    const proto = input instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype
      : HTMLInputElement.prototype;
    const descriptor = Object.getOwnPropertyDescriptor(proto, 'value');
    if (descriptor && descriptor.set) descriptor.set.call(input, targetValue);
    else input.value = targetValue;
  }} else if (input.isContentEditable) {{
    input.textContent = targetValue;
  }} else {{
    return JSON.stringify({{ok: false, error: 'unsupported_search_input'}});
  }}

  input.dispatchEvent(new InputEvent('input', {{
    bubbles: true,
    inputType: 'insertReplacementText',
    data: targetValue,
  }}));
  input.dispatchEvent(new Event('change', {{ bubbles: true }}));

  const actualValue = input instanceof HTMLInputElement || input instanceof HTMLTextAreaElement
    ? input.value
    : input.textContent;
  return JSON.stringify({{
    ok: String(actualValue || '').trim() === String(targetValue || '').trim(),
    value: String(actualValue || '').trim(),
  }});
}})()
"""


def _topic_terms(topic: str) -> list[str]:
    raw = str(topic or "").strip().lower()
    if not raw:
        return []
    terms = re.findall(r"[a-z0-9][a-z0-9_\-\.]*|[\u4e00-\u9fff]{2,}", raw)
    seen: list[str] = []
    for term in [raw, *terms]:
        if term and term not in seen:
            seen.append(term)
    return seen[:8]


def rank_note_card(card: NoteCard, topic: str) -> float:
    text = f"{card.title} {card.author_name}".lower()
    score = float(parse_count_text(card.likes) or 0) / 1000.0
    for idx, term in enumerate(_topic_terms(topic)):
        if term in text:
            score += 8.0 if idx == 0 else 3.0
    if card.note_type == NoteType.VIDEO:
        score += 0.4
    return score


@dataclass
class ProcessorConfig:
    max_images: int = 10
    use_ocr: bool = True
    use_vision: bool = True
    use_whisper: bool = True
    vision_concurrency: int = 3
    max_transcription_seconds: int = 90
    transcription_timeout_s: int = 300
    max_video_frame_seconds: int = 60
    max_video_frame_samples: int = 4
    cache_video_locally: bool = False
    image_vision_max_tokens: int = 180
    video_poster_max_tokens: int = 180
    video_frame_max_tokens: int = 180
    transcript_summary_max_tokens: int = 160
    video_visual_summary_max_tokens: int = 180
    image_prompt_template: str = (
        "你在抽取小红书帖子《{title}》里的单张图片信息。"
        "请严格按下面3行输出：\n"
        "1. 场景/主体：\n"
        "2. 关键可见文字或界面：\n"
        "3. 这张图对帖子主题提供了什么信息：\n"
        "要求：每行一句；具体描述可见内容；不要空泛修饰；不要猜测看不见的内容。"
    )
    video_poster_prompt: str = (
        "你在抽取小红书视频《{title}》的封面信息。"
        "请严格按下面3行输出：\n"
        "1. 封面主体：\n"
        "2. 关键可见文字或界面：\n"
        "3. 这张封面对理解视频主题有什么帮助：\n"
        "要求：每行一句；只写可见内容；不要猜测视频里未出现的内容。"
    )
    video_frame_prompt_template: str = (
        "你在抽取小红书视频《{title}》的第{index}帧信息。"
        "请严格按下面3行输出：\n"
        "1. 画面里正在发生什么：\n"
        "2. 可见设备/界面/物品：\n"
        "3. 这帧对理解整条视频有什么帮助：\n"
        "要求：每行一句；只写肉眼可见内容；不要推测用途、因果或后续步骤；看不出来就写“无法判断”。"
    )
    transcript_summary_prompt: str = (
        "你在抽取小红书视频《{title}》的语音转录内容。下面的转录可能包含口语噪声。\n\n"
        "请只基于转录里明确说出的信息，严格按下面3行输出：\n"
        "1. 视频主旨：\n"
        "2. 关键步骤/操作：\n"
        "3. 关键结论/产品/名词：\n"
        "要求：信息具体，压缩表达，不要套话，不要补充转录里没明确提到的背景推断。\n\n"
        "转录文本：\n{transcript}\n"
    )
    video_visual_summary_prompt: str = (
        "以下是一个小红书视频《{title}》的封面描述和若干关键帧描述。\n\n"
        "封面描述：{poster}\n\n"
        "关键帧描述：\n{frames}\n\n"
        "请严格按下面3行输出：\n"
        "1. 画面主线：\n"
        "2. 关键设备/界面/物体：\n"
        "3. 对理解视频内容最有帮助的视觉线索：\n"
        "要求：只基于给定描述；每行一句；不要补充未出现的推断。"
    )


@dataclass
class TimingRecord:
    counts: dict[str, int] = field(default_factory=dict)
    totals: dict[str, float] = field(default_factory=dict)

    def record(self, op: str, duration: float) -> None:
        self.counts[op] = self.counts.get(op, 0) + 1
        self.totals[op] = self.totals.get(op, 0.0) + duration

    def summary(self) -> dict[str, dict]:
        result: dict[str, dict] = {}
        for op in sorted(self.totals):
            total = self.totals[op]
            count = self.counts.get(op, 0)
            result[op] = {
                "count": count,
                "total_s": round(total, 2),
                "avg_s": round(total / count, 2) if count else 0,
            }
        return result


class XHSSiteAdapter:
    """Bridge + media powered XHS entity extractor."""

    def __init__(
        self,
        bridge: ExtensionBridge | TabBridge,
        *,
        ext_bridge: ExtensionBridge,
        media: MediaProcessor,
        run_dir: str | Path,
        config: ProcessorConfig | None = None,
        log_fn=None,
    ):
        self.bridge = bridge
        self.ext_bridge = ext_bridge
        self.media = media
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.config = config or ProcessorConfig()
        self.timing = TimingRecord()
        self._log_fn = log_fn

    def _log(self, action: str, detail: str = "", duration: float | None = None) -> None:
        if self._log_fn:
            self._log_fn(action, detail, duration)

    async def _current_url(self) -> str:
        try:
            info = await self.bridge.get_tab_info()
        except Exception:
            return ""
        return str(info.get("url") or "")

    def _uses_local_vision(self) -> bool:
        return self.media.backend in {BACKEND_QWEN_LOCAL, BACKEND_UI_TARS_LOCAL}

    def _vision_concurrency(self) -> int:
        if self._uses_local_vision():
            return 1
        return max(1, int(self.config.vision_concurrency or 1))

    async def extract_search_cards(self) -> list[NoteCard]:
        result = await self.ext_bridge.send_command("extract_search_cards")
        cards = [NoteCard.from_dom_dict(item) for item in result.get("cards", [])]
        return cards

    async def get_search_page_state(self) -> dict:
        return await self.ext_bridge.send_command("get_search_page_state")

    async def detect_state(self) -> dict:
        return await self.ext_bridge.send_command("detect_state")

    async def _run_js_json(self, code: str) -> dict:
        raw = await self.bridge.run_js(code)
        value = raw.get("value", raw.get("result", ""))
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                return json.loads(value)
            except Exception:
                return {"ok": False, "error": value}
        return {"ok": False, "error": f"Unexpected JS result: {value!r}"}

    @staticmethod
    def _search_transition_ok(state: dict, query: str) -> bool:
        page_state = str(state.get("page_state") or state.get("state") or "")
        keyword = str(query or "").strip().lower()
        visible_keyword = str(state.get("input_keyword") or "").strip().lower()
        url_keyword = str(state.get("url_keyword") or "").strip().lower()
        if page_state != "search_results":
            return False
        if keyword:
            if visible_keyword:
                if visible_keyword != keyword:
                    return False
            elif url_keyword and url_keyword != keyword:
                return False
        if state.get("loading"):
            return False
        return bool(state.get("tabs") or state.get("card_count") or state.get("has_no_results"))

    async def _wait_for_search_transition(
        self,
        query: str,
        *,
        timeout_s: float = 2.0,
        poll_s: float = 0.15,
    ) -> dict:
        deadline = time.monotonic() + max(0.2, float(timeout_s))
        latest: dict = {}
        while time.monotonic() < deadline:
            latest = await self.get_search_page_state()
            if self._search_transition_ok(latest, query):
                return latest
            await asyncio.sleep(max(0.05, poll_s))
        latest = await self.get_search_page_state()
        return latest

    async def _manual_search_submit(self, query: str, *, wait_seconds: float = 2.0) -> dict:
        loc = await self._run_js_json(_SEARCH_INPUT_JS)
        if not loc.get("ok"):
            return {"ok": False, "strategy": "manual_fallback_unavailable", "error": loc.get("error", "")}

        input_pos = loc.get("input") or {}
        await self.bridge.click_at(int(input_pos.get("x", 0)), int(input_pos.get("y", 0)))
        await asyncio.sleep(0.3)
        input_result = await self._run_js_json(_set_search_input_js(query))
        if not input_result.get("ok"):
            return {
                "ok": False,
                "strategy": "manual_input_failed",
                "input": input_result,
                "error": "Search input did not accept the requested keyword",
            }
        await asyncio.sleep(0.2)
        await self.bridge.press_key("Enter")
        state = await self._wait_for_search_transition(query, timeout_s=max(1.2, min(wait_seconds, 2.0)))
        if self._search_transition_ok(state, query):
            return {
                "ok": True,
                "strategy": "manual_click_type_enter",
                "state": state.get("page_state", ""),
                "searchState": state,
                "url": await self._current_url(),
            }

        submit_pos = loc.get("submit") or {}
        if submit_pos.get("x") and submit_pos.get("y"):
            await self.bridge.click_at(int(submit_pos["x"]), int(submit_pos["y"]))
            state = await self._wait_for_search_transition(query, timeout_s=max(1.2, min(wait_seconds, 2.0)))
            if self._search_transition_ok(state, query):
                return {
                    "ok": True,
                    "strategy": "manual_click_search_button",
                    "state": state.get("page_state", ""),
                    "searchState": state,
                    "url": await self._current_url(),
                }

        return {
            "ok": False,
            "strategy": "manual_submit_failed",
            "state": state.get("page_state", ""),
            "searchState": state,
            "url": await self._current_url(),
            "error": "Manual search fallback did not transition to search_results",
        }

    async def search_notes(
        self,
        query: str,
        *,
        tab_label: str | None = None,
        wait_seconds: float = 4.0,
    ) -> dict:
        effective_wait = max(0.8, min(float(wait_seconds or 0), 2.0))

        t0 = time.time()
        submit = await self._manual_search_submit(query, wait_seconds=effective_wait)
        self.timing.record("search_submit", time.time() - t0)
        state = submit.get("searchState") or await self._wait_for_search_transition(
            query,
            timeout_s=max(0.6, effective_wait),
        )
        ok = self._search_transition_ok(state, query)
        active_submit = submit
        recovery = None

        if not ok:
            t0 = time.time()
            recovery = await self.ext_bridge.send_command("submit_search_query", {"keyword": query})
            self.timing.record("search_recovery", time.time() - t0)
            state = recovery.get("searchState") or await self._wait_for_search_transition(
                query,
                timeout_s=max(0.6, effective_wait),
            )
            ok = self._search_transition_ok(state, query)
            active_submit = recovery if recovery and recovery.get("ok") else submit
            if recovery and not ok:
                active_submit = recovery

        tab_result = None
        if ok and tab_label and _normalize_search_tab(tab_label) != "全部":
            tab_result = await self.open_search_tab(tab_label, wait_seconds=min(effective_wait, 1.2))
            state = await self.get_search_page_state()

        cards = await self.extract_search_cards() if ok else []
        return {
            "ok": ok,
            "query": query,
            "submit": active_submit,
            "initial_submit": submit,
            "recovery": recovery,
            "tab": tab_result,
            "page_state": state,
            "cards": [card.to_tool_dict() for card in cards],
            "count": len(cards),
            "state": state.get("page_state", ""),
            "reason": "" if ok else str((recovery or submit).get("error") or "search_submit_failed"),
            "manual_fallback_allowed": not ok,
        }

    async def open_search_tab(self, label: str, *, wait_seconds: float = 1.5) -> dict:
        normalized = _normalize_search_tab(label)
        t0 = time.time()
        result = await self.ext_bridge.send_command("click_search_tab", {"label": normalized})
        self.timing.record("search_tab_switch", time.time() - t0)
        if wait_seconds > 0:
            await asyncio.sleep(wait_seconds)
        cards = await self.extract_search_cards()
        return {
            "label": normalized,
            "result": result,
            "count": len(cards),
            "cards": [card.to_tool_dict() for card in cards],
        }

    async def open_note(
        self,
        *,
        index: int | None = None,
        note_id: str = "",
        wait_seconds: float = 0.0,
    ) -> dict:
        if note_id:
            result = await self.ext_bridge.send_command("click_note_by_id", {"note_id": note_id})
        elif index is not None:
            result = await self.ext_bridge.send_command("click_card", {"index": int(index)})
        else:
            raise ValueError("open_note requires index or note_id")
        if wait_seconds > 0:
            deadline = time.monotonic() + float(wait_seconds)
            while time.monotonic() < deadline:
                state = await self.detect_state()
                if str(state.get("state") or "") == "note_detail":
                    break
                await asyncio.sleep(0.12)
        return result

    async def close_note(self) -> dict:
        return await self.ext_bridge.send_command("close_note")

    async def extract_author_profile(
        self, *, include_notes: bool = True, max_notes: int = 20
    ) -> AuthorEntity:
        info = await self.ext_bridge.send_command("extract_profile_info")
        author = AuthorEntity.from_dom_dict(info.get("profile", {}))
        author.profile_url = await self._current_url()
        if include_notes:
            notes = await self.ext_bridge.send_command("extract_profile_notes")
            cards = [NoteCard.from_dom_dict(item) for item in notes.get("notes", [])]
            # Scroll down to load more note cards until we reach max_notes
            scroll_attempts = 0
            while len(cards) < max_notes and scroll_attempts < 10:
                await self.ext_bridge.send_command("scroll_page", {"pixels": 800})
                await asyncio.sleep(1.5)
                notes = await self.ext_bridge.send_command("extract_profile_notes")
                new_cards = [NoteCard.from_dom_dict(item) for item in notes.get("notes", [])]
                if len(new_cards) <= len(cards):
                    break  # no new cards loaded
                cards = new_cards
                scroll_attempts += 1
            author.note_cards = cards[:max_notes]
        return author

    async def extract_note(
        self,
        *,
        level: str = "lite",
        max_comments: int = 4,
        max_images: int = 6,
        max_video_frames: int = 4,
        include_comments: bool | None = None,
        include_media: bool | None = None,
    ) -> NoteEntity:
        plan = plan_for_level(
            level,
            max_comments=max_comments,
            max_images=max_images,
            max_video_frames=max_video_frames,
            include_comments=include_comments,
            include_media=include_media,
        )

        raw = await self.ext_bridge.send_command("extract_note_content")
        if raw.get("error"):
            raise RuntimeError(raw.get("message") or raw["error"])

        note = NoteEntity.from_dom_dict(raw.get("note", {}))
        if note.images:
            note.images = _unique_image_infos(note.images)
            note.image_count = max(int(note.image_count or 0), len(note.images))
        note.extraction_level = plan.level.value
        note.requested_sections = plan.requested_sections
        note.applied_capabilities = ["xhs.note.open_basic"]

        comments_task = None
        media_task = None

        if plan.use_media:
            if note.note_type == NoteType.VIDEO:
                media_task = asyncio.create_task(self._process_video(note, plan))
            else:
                await self._ensure_all_images(note, plan.max_images)
                await self._ensure_cover_image_ocr(note)
                media_task = asyncio.create_task(self._enrich_images(note, plan))
        elif self.config.use_ocr and note.images:
            await self._ensure_cover_image_ocr(note)

        if plan.include_comments:
            comments_task = asyncio.create_task(self._collect_note_comments(plan))

        if comments_task is not None:
            note.comments = await comments_task
            note.applied_capabilities.append("xhs.note.sample_comments")
        if media_task is not None:
            await media_task

        note.refresh_derived_fields()
        return note

    async def read_note(
        self,
        *,
        index: int | None = None,
        note_id: str = "",
        level: str = "lite",
        max_comments: int = 4,
        max_images: int = 6,
        max_video_frames: int = 4,
        include_comments: bool | None = None,
        include_media: bool | None = None,
        open_wait_seconds: float = 2.5,
        close_after: bool = False,
    ) -> NoteEntity:
        expected_note_id = note_id or await self._resolve_note_target_id(index=index)
        if index is not None or note_id:
            await self.open_note(index=index, note_id=note_id, wait_seconds=open_wait_seconds)

        note = await self.extract_note(
            level=level,
            max_comments=max_comments,
            max_images=max_images,
            max_video_frames=max_video_frames,
            include_comments=include_comments,
            include_media=include_media,
        )

        if expected_note_id and note.note_id and note.note_id != expected_note_id:
            raise RuntimeError(
                "Opened stale Xiaohongshu note: "
                f"expected note_id={expected_note_id}, got note_id={note.note_id}. "
                "The previous note detail likely remained open or the click did not switch cards."
            )

        if close_after:
            try:
                await self.close_note()
            except Exception:
                pass

        return note

    async def _resolve_note_target_id(self, *, index: int | None = None) -> str:
        if index is None:
            return ""
        try:
            state = await self.detect_state()
        except Exception:
            return ""

        page_state = str(state.get("state") or "")
        try:
            if page_state == "search_results":
                cards = await self.extract_search_cards()
            elif page_state == "profile_page":
                raw = await self.ext_bridge.send_command("extract_profile_notes")
                cards = [NoteCard.from_dom_dict(item) for item in raw.get("notes", [])]
            else:
                return ""
        except Exception:
            return ""

        if index < 0 or index >= len(cards):
            return ""
        return str(cards[index].note_id or "").strip()

    async def _collect_note_comments(self, plan: NoteExtractionPlan) -> list[Comment]:
        merged: list[Comment] = []
        for round_idx in range(plan.max_comment_scrolls + 1):
            raw = await self.ext_bridge.send_command(
                "extract_comments",
                {"max_comments": plan.max_comments, "prefer_hot": True},
            )
            current = [
                Comment.from_dom_dict(item)
                for item in raw.get("comments", [])
            ]
            merged = Comment.merge_many([*merged, *current])
            if round_idx >= plan.max_comment_scrolls:
                break
            try:
                scroll_result = await self.ext_bridge.send_command("scroll_note", {"pixels": 420})
            except Exception:
                break
            if not scroll_result.get("ok", True):
                break
            await asyncio.sleep(0.8)
        return merged[: plan.max_comments]

    async def _process_images(self, note: NoteEntity, plan: NoteExtractionPlan) -> None:
        await self._ensure_all_images(note, plan.max_images)
        await self._ensure_cover_image_ocr(note)
        await self._enrich_images(note, plan)

    async def _enrich_images(self, note: NoteEntity, plan: NoteExtractionPlan) -> None:
        if not note.images:
            return

        images = note.images[: min(self.config.max_images, plan.max_images)]
        t0 = time.time()
        downloads = await asyncio.gather(*[
            asyncio.to_thread(self.media.download_image, img.url, note.url or XHS_REFERER)
            for img in images
        ], return_exceptions=True)
        self.timing.record("image_download_batch", time.time() - t0)

        deduped: list[tuple[ImageInfo, bytes]] = []
        seen_hashes: dict[str, int] = {}
        for image, payload in zip(images, downloads):
            if not isinstance(payload, bytes) or not payload:
                continue
            digest = hashlib.md5(payload).hexdigest()
            if digest in seen_hashes:
                continue
            seen_hashes[digest] = image.index
            image.local_path = self._save_image_bytes(note.note_id or "note", image.index, payload)
            deduped.append((image, payload))

        if deduped:
            note.images = [img for img, _ in deduped]
            for idx, image in enumerate(note.images):
                image.index = idx
                image.is_cover = idx == 0
            note.image_count = len(note.images)

        semaphore = asyncio.Semaphore(self._vision_concurrency())

        async def enrich_single(img: ImageInfo, img_bytes: bytes) -> None:
            should_ocr = self.config.use_ocr and (img.is_cover or plan.use_image_ocr)
            if should_ocr and not img.ocr_text:
                t0_local = time.time()
                ocr_text = await asyncio.to_thread(self.media.ocr_image, img_bytes)
                self.timing.record("ocr_single", time.time() - t0_local)
                if ocr_text.strip():
                    img.ocr_text = ocr_text
            if plan.use_image_vision and self.config.use_vision:
                async with semaphore:
                    t0_local = time.time()
                    prompt = self.config.image_prompt_template.format(title=note.title or "未命名笔记")
                    img.vision_description = await asyncio.to_thread(
                        self.media.describe_image,
                        img_bytes,
                        prompt,
                        self.config.image_vision_max_tokens,
                    )
                    self.timing.record("vision_single", time.time() - t0_local)
            if img.is_cover and img.vision_description:
                note.cover_description = img.vision_description

        t0 = time.time()
        await asyncio.gather(*[
            enrich_single(img, payload)
            for img, payload in deduped
        ], return_exceptions=True)
        self.timing.record("image_process_batch", time.time() - t0)

    async def _ensure_cover_image_ocr(self, note: NoteEntity) -> None:
        if not self.config.use_ocr or not note.images:
            return
        cover = next((img for img in note.images if img.is_cover), note.images[0])
        if not str(cover.url or "").strip():
            return
        if cover.ocr_text:
            if note.note_type == NoteType.VIDEO and note.video and not note.video.poster_ocr:
                note.video.poster_ocr = cover.ocr_text
            return

        t0 = time.time()
        payload = await asyncio.to_thread(self.media.download_image, cover.url, note.url or XHS_REFERER)
        self.timing.record("cover_image_download", time.time() - t0)
        if not isinstance(payload, bytes) or not payload:
            return

        if not cover.local_path:
            cover.local_path = self._save_image_bytes(note.note_id or "note", cover.index, payload)

        t0 = time.time()
        ocr_text = await asyncio.to_thread(self.media.ocr_image, payload)
        self.timing.record("ocr_cover", time.time() - t0)
        if not ocr_text.strip():
            return

        cover.ocr_text = ocr_text
        if note.note_type == NoteType.VIDEO and note.video and not note.video.poster_ocr:
            note.video.poster_ocr = ocr_text

    async def _ensure_all_images(self, note: NoteEntity, max_images: int) -> None:
        dom_count = len(note.images)
        unique_urls = _unique_image_infos(note.images)
        indicator_total = max(note.image_count, len(unique_urls))
        if len(unique_urls) >= indicator_total and unique_urls:
            note.images = unique_urls[:max_images]
            note.image_count = len(note.images)
            return

        try:
            result = await self.ext_bridge.send_command(
                "collect_carousel_images",
                {"max_images": max(max_images, indicator_total)},
            )
        except Exception:
            note.images = unique_urls[:max_images]
            note.image_count = len(note.images)
            return

        urls = [str(url) for url in result.get("image_urls", []) if str(url).strip()]
        if urls and len(urls) > len(unique_urls):
            note.images = [
                ImageInfo(url=url, index=i, is_cover=(i == 0))
                for i, url in enumerate(urls[:max_images])
            ]
            note.image_count = len(note.images)
            return

        note.images = unique_urls[:max_images]
        note.image_count = len(note.images or []) or dom_count

    async def _process_video(self, note: NoteEntity, plan: NoteExtractionPlan) -> None:
        if note.video is None:
            note.video = VideoInfo()

        video = note.video
        poster_url = video.poster_url or (note.images[0].url if note.images else "")
        downloadable_url = video.best_download_url()
        video.resolved_url = downloadable_url or video.best_source_url()
        video.stream_type = self._infer_video_stream_type(video.resolved_url)
        referer = note.url or XHS_REFERER
        processing_source = downloadable_url

        if not downloadable_url:
            video.download_error = "no_downloadable_video_url"
        elif self.config.cache_video_locally and video.stream_type in {"mp4", "mov", "m4v"}:
            t0 = time.time()
            suffix = f".{video.stream_type}" if video.stream_type else ".mp4"
            local_path = await asyncio.to_thread(
                self.media.download_file,
                downloadable_url,
                referer,
                suffix,
            )
            self.timing.record("video_download", time.time() - t0)
            if local_path:
                video.download_path = local_path
                processing_source = local_path

        poster_bytes = None
        if poster_url:
            t0 = time.time()
            poster_bytes = await asyncio.to_thread(self.media.download_image, poster_url, referer)
            self.timing.record("poster_download", time.time() - t0)
            if isinstance(poster_bytes, bytes) and poster_bytes:
                saved = self._save_named_bytes(note.note_id or "video", "poster", poster_bytes)
                if note.images:
                    note.images[0].local_path = saved

        async def poster_vision() -> None:
            if not poster_bytes or not plan.use_image_vision or not self.config.use_vision:
                return
            t0_local = time.time()
            video.poster_description = await asyncio.to_thread(
                self.media.describe_image,
                poster_bytes,
                self.config.video_poster_prompt.format(title=note.title or "未命名视频"),
                self.config.video_poster_max_tokens,
            )
            note.cover_description = video.poster_description
            self.timing.record("vision_poster", time.time() - t0_local)

        async def poster_ocr() -> None:
            if note.images and note.images[0].ocr_text and not video.poster_ocr:
                video.poster_ocr = note.images[0].ocr_text
                return
            if not poster_bytes or not self.config.use_ocr or video.poster_ocr:
                return
            t0_local = time.time()
            video.poster_ocr = await asyncio.to_thread(self.media.ocr_image, poster_bytes)
            self.timing.record("ocr_poster", time.time() - t0_local)

        async def transcribe() -> None:
            if not processing_source or not plan.use_video_transcript or not self.config.use_whisper:
                return
            t0_local = time.time()
            transcript = await self.media.transcribe_video(
                processing_source,
                "zh",
                referer=referer if str(processing_source).startswith("http") else "",
                max_audio_seconds=self.config.max_transcription_seconds,
                timeout_s=self.config.transcription_timeout_s,
            )
            self.timing.record("whisper_transcribe", time.time() - t0_local)
            if transcript.strip():
                video.transcript = transcript
                t0_summary = time.time()
                video.transcript_summary = await asyncio.to_thread(
                    self.media.call_text,
                    self.config.transcript_summary_prompt.format(
                        title=note.title or "未命名视频",
                        transcript=transcript[:3000],
                    ),
                    self.config.transcript_summary_max_tokens,
                )
                self.timing.record("llm_transcript_summary", time.time() - t0_summary)
            elif not video.download_error:
                video.download_error = "empty_transcript"

        async def frame_understanding() -> None:
            if not processing_source or not plan.use_video_frames or not self.config.use_vision:
                return
            t0_local = time.time()
            frame_paths = await self.media.extract_video_frames(
                processing_source,
                referer=referer if str(processing_source).startswith("http") else "",
                max_seconds=self.config.max_video_frame_seconds,
                num_frames=min(self.config.max_video_frame_samples, plan.max_video_frames),
                timeout_s=self.config.transcription_timeout_s,
            )
            self.timing.record("video_frame_extract", time.time() - t0_local)
            if not frame_paths:
                return

            video.frame_paths = frame_paths
            semaphore = asyncio.Semaphore(self._vision_concurrency())
            frame_descriptions: list[str] = []

            async def describe_frame(index: int, frame_path: str) -> None:
                async with semaphore:
                    try:
                        frame_bytes = await asyncio.to_thread(Path(frame_path).read_bytes)
                    except Exception:
                        return
                    t0_frame = time.time()
                    description = await asyncio.to_thread(
                        self.media.describe_image,
                        frame_bytes,
                        self.config.video_frame_prompt_template.format(
                            title=note.title or "未命名视频",
                            index=index + 1,
                        ),
                        self.config.video_frame_max_tokens,
                    )
                    self.timing.record("vision_video_frame", time.time() - t0_frame)
                    if description:
                        frame_descriptions.append(description)

            await asyncio.gather(*[
                describe_frame(index, path)
                for index, path in enumerate(frame_paths)
            ], return_exceptions=True)
            video.frame_descriptions = frame_descriptions
            if frame_descriptions:
                t0_summary = time.time()
                video.visual_summary = await asyncio.to_thread(
                    self.media.call_text,
                    self.config.video_visual_summary_prompt.format(
                        title=note.title or "未命名视频",
                        poster=video.poster_description or video.poster_ocr or "无",
                        frames="\n".join(
                            f"{idx + 1}. {desc}"
                            for idx, desc in enumerate(frame_descriptions)
                        ),
                    ),
                    self.config.video_visual_summary_max_tokens,
                )
                self.timing.record("llm_video_visual_summary", time.time() - t0_summary)

        await asyncio.gather(
            poster_vision(),
            poster_ocr(),
            transcribe(),
            frame_understanding(),
            return_exceptions=True,
        )

    def _save_image_bytes(self, note_id: str, index: int, payload: bytes) -> str:
        media_type = self.media.detect_media_type(payload)
        ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}.get(media_type, ".img")
        path = self.run_dir / "site_media" / note_id / f"image_{index + 1:02d}{ext}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return str(path.relative_to(self.run_dir))

    def _save_named_bytes(self, note_id: str, stem: str, payload: bytes) -> str:
        media_type = self.media.detect_media_type(payload)
        ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}.get(media_type, ".img")
        path = self.run_dir / "site_media" / note_id / f"{stem}{ext}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return str(path.relative_to(self.run_dir))

    @staticmethod
    def _infer_video_stream_type(url: str) -> str:
        lower = (url or "").lower()
        if ".mp4" in lower:
            return "mp4"
        if ".m3u8" in lower:
            return "m3u8"
        if ".mov" in lower:
            return "mov"
        if ".m4v" in lower:
            return "m4v"
        if lower.startswith("blob:"):
            return "blob"
        return ""


def _unique_image_infos(images: list[ImageInfo]) -> list[ImageInfo]:
    seen: set[str] = set()
    result: list[ImageInfo] = []
    for image in images:
        url = str(image.url or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        image.index = len(result)
        image.is_cover = len(result) == 0
        result.append(image)
    return result


def _normalize_search_tab(label: str) -> str:
    normalized = str(label or "").strip().lower()
    mapping = {
        "all": "全部",
        "全部": "全部",
        "image": "图文",
        "images": "图文",
        "图文": "图文",
        "video": "视频",
        "videos": "视频",
        "视频": "视频",
        "user": "用户",
        "users": "用户",
        "用户": "用户",
    }
    return mapping.get(normalized, str(label or "全部").strip() or "全部")
