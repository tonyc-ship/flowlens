"""XHS Research Agent — topic research on Xiaohongshu.

Task-level orchestration only. Delegates note understanding to NoteProcessor.

Flow:
  1. Generate search keywords (Claude Text)
  2. Navigate to XHS search via XHSBrowser
  3. Extract NoteCards from DOM
  4. Pick best notes (Claude Text)
  5. Open each note → NoteProcessor.process_note() handles all media
  6. Vision fallback if DOM text extraction failed
  7. Scroll + extract comments
  8. Synthesize findings using note.to_summary()
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from ..bridge import ExtensionBridge
from ..media import MediaProcessor
from ...reporting import markdown_styles, render_markdown_block
from .capabilities import NoteExtractionPlan, deep_note_plan, lite_note_plan
from .browser import XHSBrowser
from .entities import Comment, NoteCard, NoteEntity, NoteType, parse_count_text
from .processor import NoteProcessor, ProcessorConfig, TimingRecord


@dataclass
class ResearchConfig:
    """Configuration for a research session."""
    max_cards_per_keyword: int = 12
    max_search_scroll_rounds: int = 4
    max_lite_notes: int = 10
    max_deep_notes: int = 4
    lite_comment_count: int = 4
    lite_comment_scrolls: int = 0
    max_comments_per_note: int = 20
    max_comment_scrolls: int = 2
    max_keywords: int = 4
    use_vision_fallback: bool = True
    inter_note_pause_s: float = 1.2
    anti_bot_backoff_s: float = 8.0
    screenshot_dir: str = "screenshots"
    # NoteProcessor config
    max_images_per_note: int = 10
    vision_concurrency: int = 3


@dataclass
class ResearchCandidate:
    keyword: str
    search_url: str
    card: NoteCard
    note: NoteEntity | None = None


class XHSResearchAgent:
    """Autonomous XHS research agent. Delegates media processing to NoteProcessor."""

    def __init__(
        self,
        output_dir: str = "research_output",
        port: int = 8765,
        config: ResearchConfig | None = None,
        browser: XHSBrowser | None = None,
        media: MediaProcessor | None = None,
        manage_bridge_lifecycle: bool | None = None,
    ):
        self.config = config or ResearchConfig()
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        (self.output_dir / self.config.screenshot_dir).mkdir(exist_ok=True)

        if browser:
            self.browser = browser
        else:
            bridge = ExtensionBridge(port=port)
            self.browser = XHSBrowser(bridge)
            bridge.on_log(self._log_step)
        self.manage_bridge_lifecycle = (
            browser is None if manage_bridge_lifecycle is None else manage_bridge_lifecycle
        )

        self.media = media or MediaProcessor()

        # NoteProcessor handles all media understanding
        proc_config = ProcessorConfig(
            max_images=self.config.max_images_per_note,
            vision_concurrency=self.config.vision_concurrency,
        )
        self.processor = NoteProcessor(
            self.browser, self.media, proc_config, log_fn=self._log_step,
        )

        self._step = 0
        self._log: list[dict] = []
        self._t0 = 0
        self._screenshots: list[str] = []
        self._watch = False

    @property
    def timing(self) -> TimingRecord:
        return self.processor.timing

    def _log_step(self, action: str, detail: str = "", duration: float | None = None):
        self._step += 1
        elapsed = time.time() - self._t0 if self._t0 else 0
        dur_str = f" ({duration:.2f}s)" if duration is not None else ""
        entry = {
            "step": self._step,
            "time": time.strftime("%H:%M:%S"),
            "elapsed_s": round(elapsed, 1),
            "action": action,
            "detail": detail[:200] + dur_str,
        }
        if duration is not None:
            entry["duration_s"] = round(duration, 2)
        self._log.append(entry)
        print(f"  [{self._step:03d} {elapsed:5.1f}s] {action}{dur_str}: {detail[:100]}")

    @contextmanager
    def _timed(self, op: str):
        t0 = time.time()
        yield
        self.timing.record(op, time.time() - t0)

    # ── LLM Decision Functions ──────────────────────────────────

    def generate_keywords(self, topic: str) -> list[str]:
        with self._timed("llm_keywords"):
            raw = self.media.call_text(
                f"I want to research '{topic}' on Xiaohongshu (小红书). "
                f"Generate {self.config.max_keywords} Chinese search keywords "
                f"that would find the most relevant and diverse results. "
                f"Return only a JSON array of strings.",
                256,
            )
        result = self.media.extract_json(raw)
        return result if isinstance(result, list) else [topic]

    def pick_candidates_for_lite(
        self,
        candidates: list[ResearchCandidate],
        topic: str,
        max_picks: int,
    ) -> list[ResearchCandidate]:
        if len(candidates) <= max_picks:
            return candidates

        card_dicts = [
            {
                "index": idx,
                "keyword": candidate.keyword,
                "title": candidate.card.title,
                "author": candidate.card.author_name,
                "likes": candidate.card.likes,
                "type": candidate.card.note_type.value,
            }
            for idx, candidate in enumerate(candidates)
        ]
        with self._timed("llm_pick_notes"):
            raw = self.media.call_text(
                f"I'm researching '{topic}' on Xiaohongshu.\n"
                f"Here are note cards collected across multiple keywords:\n"
                f"{json.dumps(card_dicts, ensure_ascii=False, indent=1)}\n\n"
                f"Pick {max_picks} cards for a LIGHTWEIGHT read. Prefer topic relevance, diversity, and strong signal. "
                f"Avoid near-duplicates. Return a JSON array of index integers.",
                768,
            )
        picks = self.media.extract_json(raw)
        if isinstance(picks, list):
            selected = [candidate for idx, candidate in enumerate(candidates) if idx in {int(v) for v in picks if isinstance(v, int)}]
            if selected:
                return selected[:max_picks]
        return self._fallback_pick_candidates(candidates, max_picks)

    def pick_candidates_for_deep(
        self,
        notes: list[ResearchCandidate],
        topic: str,
        max_picks: int,
    ) -> list[ResearchCandidate]:
        if len(notes) <= max_picks:
            return notes

        note_dicts = [
            {
                "index": idx,
                "keyword": candidate.keyword,
                "title": candidate.note.title if candidate.note else candidate.card.title,
                "author": candidate.note.author_name if candidate.note else candidate.card.author_name,
                "likes": candidate.note.likes if candidate.note else candidate.card.likes,
                "favorites": candidate.note.favorites if candidate.note else "",
                "comments": candidate.note.comments_count if candidate.note else "",
                "type": candidate.note.note_type.value if candidate.note else candidate.card.note_type.value,
                "content_preview": candidate.note.content[:240] if candidate.note else "",
                "top_comments": [comment.text[:80] for comment in candidate.note.hottest_comments(2)] if candidate.note else [],
                "format_hints": candidate.note.format_hints if candidate.note else [],
                "key_points": candidate.note.key_points[:3] if candidate.note else [],
            }
            for idx, candidate in enumerate(notes)
        ]
        with self._timed("llm_pick_deep_notes"):
            raw = self.media.call_text(
                f"You are planning DEEP multimodal reading for Xiaohongshu topic research: '{topic}'.\n"
                f"These notes already had a lightweight read:\n"
                f"{json.dumps(note_dicts, ensure_ascii=False, indent=1)}\n\n"
                f"Pick {max_picks} notes that most deserve expensive deep reading. "
                f"Prefer representative or unusually informative samples. Return a JSON array of index integers.",
                768,
            )
        picks = self.media.extract_json(raw)
        if isinstance(picks, list):
            selected = [candidate for idx, candidate in enumerate(notes) if idx in {int(v) for v in picks if isinstance(v, int)}]
            if selected:
                return selected[:max_picks]
        return self._fallback_pick_candidates(notes, max_picks, use_note=True)

    @staticmethod
    def _fallback_pick_candidates(
        candidates: list[ResearchCandidate],
        max_picks: int,
        *,
        use_note: bool = False,
    ) -> list[ResearchCandidate]:
        def signal(candidate: ResearchCandidate) -> int:
            if use_note and candidate.note:
                return parse_count_text(candidate.note.likes) + parse_count_text(candidate.note.favorites)
            return parse_count_text(candidate.card.likes)

        ordered = sorted(candidates, key=signal, reverse=True)
        seen_keys: set[str] = set()
        picked: list[ResearchCandidate] = []
        for candidate in ordered:
            title = candidate.note.title if use_note and candidate.note else candidate.card.title
            author = candidate.note.author_name if use_note and candidate.note else candidate.card.author_name
            note_type = candidate.note.note_type.value if use_note and candidate.note else candidate.card.note_type.value
            key = f"{author}:{note_type}:{title[:30]}"
            if key in seen_keys:
                continue
            seen_keys.add(key)
            picked.append(candidate)
            if len(picked) >= max_picks:
                break
        return picked

    def synthesize(
        self,
        topic: str,
        keywords: list[str],
        notes: list[NoteEntity],
        *,
        coverage: dict,
    ) -> str:
        summaries = [n.to_summary() for n in notes]
        for summary, note in zip(summaries, notes):
            summary["keyword"] = note.source_keyword
            summary["capabilities"] = note.applied_capabilities

        with self._timed("llm_synthesize"):
            return self.media.call_text(
                f"你刚完成了一次小红书话题研究。主题是“{topic}”。\n\n"
                f"## 覆盖范围\n{json.dumps(coverage, ensure_ascii=False, indent=2)}\n\n"
                f"## 搜索关键词\n{json.dumps(keywords, ensure_ascii=False)}\n\n"
                f"## 样本笔记摘要\n{json.dumps(summaries, ensure_ascii=False, indent=1)}\n\n"
                "请输出一份中文 Markdown 研究结论，要求简洁、证据化，不要写散文长文。\n"
                "结构必须包含这些部分：\n"
                "## Coverage\n"
                "## 内容切入点\n"
                "## 用户需求与评论主题\n"
                "## 代表样本\n"
                "## 可执行建议\n"
                "## Unknowns\n\n"
                "写法要求：\n"
                "- 每节以短 bullet 为主\n"
                "- 明确指出哪些结论来自 deep read，哪些只是 lite read\n"
                "- 引用样本时写出标题\n"
                "- 如果证据不足就直接说不确定\n",
                2048,
            )

    # ── Vision Fallback (DOM-first, Vision-backup) ───────────────

    def _vision_extract_note(self, screenshot_b64: str) -> dict:
        """UX-fallback: extract note content from screenshot when DOM fails."""
        with self._timed("vision_extract_note"):
            raw = self.media.call_vision(
                screenshot_b64,
                "Extract the note content from this Xiaohongshu screenshot. "
                "Return a JSON object with these fields:\n"
                '{"title": "...", "author": "...", "content": "...", '
                '"likes": "...", "favorites": "...", "comments_count": "...", '
                '"hashtags": ["...", "..."], "date": "...", "image_count": N}',
                media_type="image/png",
                max_tokens=1024,
            )
        result = self.media.extract_json(raw)
        return result if isinstance(result, dict) else {}

    # ── Screenshot Helper ────────────────────────────────────────

    async def _take_screenshot(self, label: str) -> str:
        try:
            path = await self.browser.bridge.save_screenshot(
                self.output_dir / self.config.screenshot_dir / f"{label}.png"
            )
            if path:
                self._log_step("screenshot", f"{label}: saved")
            return path
        except Exception as e:
            self._log_step("screenshot_error", f"{label}: {e}")
            return ""

    async def _collect_note_comments(self, max_comments: int, max_scrolls: int) -> list[Comment]:
        """Collect and merge hot comments across several scroll rounds."""
        merged: list[Comment] = []
        for round_idx in range(max_scrolls + 1):
            raw_comments = await self.browser.extract_comments(
                max_comments=max_comments,
                prefer_hot=True,
            )
            merged = NoteEntity.merge_comments(
                [*merged, *[Comment.from_dom_dict(c) for c in raw_comments]]
            )
            if round_idx >= max_scrolls:
                break
            await self.browser.scroll_note(400)
            await asyncio.sleep(1)
        return merged[:max_comments]

    # ── Research Flow ───────────────────────────────────────────

    async def research(
        self, topic: str, keywords: list[str] | None = None
    ) -> dict:
        """Run a full research session."""
        self._t0 = time.time()
        self._step = 0
        self._log = []
        self._screenshots = []

        self._log_step("start", f"Research topic: {topic}")

        if self.manage_bridge_lifecycle:
            await self.browser.bridge.start()
            self._log_step("bridge_ready", f"WebSocket server on port {self.browser.bridge.port}")
        else:
            self._log_step("bridge_ready", f"Using external bridge on port {self.browser.bridge.port}")

        print(
            "\n  >>> Waiting for Chrome Extension to connect. <<<\n"
            "  >>> Open extension popup and click 'Connect'. <<<\n"
        )
        await self.browser.bridge.wait_for_connection(timeout=120, require_watch=getattr(self, '_watch', False))

        # Watch mode: create foreground window with sidebar
        if getattr(self, '_watch', False):
            await self.browser.bridge.create_watch_window(url="https://www.xiaohongshu.com")
            await asyncio.sleep(4)
            self._log_step("watch_mode", "Foreground window with watch sidebar created")
        else:
            tab_info = await self.browser.get_tab_info()
            if "xiaohongshu.com" not in tab_info.get("url", ""):
                self._log_step("navigate", "Going to xiaohongshu.com")
                await self.browser.navigate("https://www.xiaohongshu.com")
                await asyncio.sleep(3)

        if keywords is None:
            keywords = self.generate_keywords(topic)
        keywords = [keyword for keyword in dict.fromkeys(keywords) if keyword][: self.config.max_keywords]
        self._log_step("keywords", f"{len(keywords)} keywords: {keywords}")

        candidate_pool: list[ResearchCandidate] = []
        coverage_by_keyword: dict[str, int] = {}
        for ki, keyword in enumerate(keywords):
            self._log_step("search", f"[{ki+1}/{len(keywords)}] {keyword}")
            collected = await self._collect_search_candidates(keyword, ki)
            coverage_by_keyword[keyword] = len(collected)
            candidate_pool.extend(collected)

        candidate_pool = self._dedupe_candidates(candidate_pool)
        self._log_step("candidate_pool", f"{len(candidate_pool)} unique cards across {len(keywords)} keywords")

        keyword_rank = {keyword: idx for idx, keyword in enumerate(keywords)}
        lite_candidates = self.pick_candidates_for_lite(candidate_pool, topic, self.config.max_lite_notes)
        lite_candidates = sorted(
            lite_candidates,
            key=lambda candidate: (keyword_rank.get(candidate.keyword, 999), candidate.card.position),
        )
        self._log_step("lite_pick", f"{len(lite_candidates)} candidates selected for lite read")

        lite_plan = lite_note_plan(
            max_comments=self.config.lite_comment_count,
            max_comment_scrolls=self.config.lite_comment_scrolls,
        )
        for index, candidate in enumerate(lite_candidates, start=1):
            self._log_step("lite_read", f"[{index}/{len(lite_candidates)}] {candidate.card.title[:50]}")
            candidate.note = await self._process_note(candidate, lite_plan)

        lite_notes = [candidate for candidate in lite_candidates if candidate.note]
        deep_targets = self.pick_candidates_for_deep(lite_notes, topic, self.config.max_deep_notes)
        deep_targets = sorted(
            deep_targets,
            key=lambda candidate: (keyword_rank.get(candidate.keyword, 999), candidate.card.position),
        )
        self._log_step("deep_pick", f"{len(deep_targets)} lite notes selected for deep read")

        deep_plan = deep_note_plan(
            max_comments=self.config.max_comments_per_note,
            max_comment_scrolls=self.config.max_comment_scrolls,
            max_images=self.config.max_images_per_note,
        )
        for index, candidate in enumerate(deep_targets, start=1):
            self._log_step("deep_read", f"[{index}/{len(deep_targets)}] {candidate.card.title[:50]}")
            candidate.note = await self._process_note(candidate, deep_plan)

        all_notes = [candidate.note for candidate in lite_candidates if candidate.note]

        # Synthesize
        elapsed_collect = time.time() - self._t0
        self._log_step("synthesize", f"Data collection done in {elapsed_collect:.1f}s")

        synthesis = ""
        if all_notes:
            synthesis = self.synthesize(
                topic,
                keywords,
                all_notes,
                coverage={
                    "cards_scanned": len(candidate_pool),
                    "coverage_by_keyword": coverage_by_keyword,
                    "lite_reads": len(lite_candidates),
                    "deep_reads": len(deep_targets),
                },
            )

        elapsed_total = time.time() - self._t0
        self._log_step("done", f"Total: {elapsed_total:.1f}s, {len(all_notes)} notes")

        timing_summary = self.timing.summary()
        self._log_step("timing_summary", json.dumps(timing_summary, ensure_ascii=False))

        report = {
            "topic": topic,
            "keywords": keywords,
            "coverage": {
                "cards_scanned": len(candidate_pool),
                "coverage_by_keyword": coverage_by_keyword,
                "lite_reads": len(lite_candidates),
                "deep_reads": len(deep_targets),
            },
            "notes": [n.to_report_dict() for n in all_notes],
            "synthesis": synthesis,
            "timing": {
                "data_collection_s": round(elapsed_collect, 1),
                "total_s": round(elapsed_total, 1),
                "breakdown": timing_summary,
            },
            "screenshots": [s for s in self._screenshots if s],
            "log": self._log,
        }

        self._save_report(report)
        if self.manage_bridge_lifecycle:
            await self.browser.bridge.stop()
        return report

    async def _collect_search_candidates(self, keyword: str, keyword_index: int) -> list[ResearchCandidate]:
        await self.browser.navigate_to_search(keyword)
        search_state = await self.browser.wait_for_search_results(timeout_s=20, poll_s=2)
        if search_state.get("loading") and search_state.get("card_count", 0) == 0:
            self._log_step("search_wait_extend", f"{keyword}: still loading after initial wait, extending")
            search_state = await self.browser.wait_for_search_results(timeout_s=15, poll_s=2)
        self._log_step(
            "search_state",
            f"filter={search_state.get('active_filter') or 'unknown'} "
            f"cards={search_state.get('card_count', 0)} "
            f"loading={search_state.get('loading')} "
            f"no_results={search_state.get('has_no_results')}",
        )

        search_screenshot = await self._take_screenshot(f"search_{keyword_index+1}_{keyword}")
        if search_screenshot:
            self._screenshots.append(search_screenshot)

        if search_state.get("has_no_results"):
            self._log_step("search_empty", f"{keyword}: no results")
            return []
        if search_state.get("card_count", 0) == 0 and search_state.get("loading"):
            self._log_step("search_skip", f"{keyword}: still loading without cards, skip keyword")
            return []

        search_url = (await self.browser.get_tab_info()).get("url", "")
        candidates: list[ResearchCandidate] = []
        seen_ids: set[str] = set()
        stagnant_rounds = 0

        for round_idx in range(self.config.max_search_scroll_rounds):
            try:
                raw_cards = await self.browser.extract_search_cards()
            except Exception as exc:
                self._log_step("cards_round_error", f"{keyword}: round {round_idx+1} failed: {exc}")
                try:
                    await self.browser.wait_for_search_results(timeout_s=6, poll_s=1.0)
                except Exception:
                    pass
                if round_idx == 0:
                    return candidates
                break
            new_count = 0
            for raw in raw_cards:
                card = NoteCard.from_dom_dict(raw)
                dedupe_key = card.note_id or card.link or f"{card.title}:{card.author_name}"
                if not card.title or not dedupe_key or dedupe_key in seen_ids:
                    continue
                seen_ids.add(dedupe_key)
                candidates.append(ResearchCandidate(keyword=keyword, search_url=search_url, card=card))
                new_count += 1

            self._log_step("cards_round", f"{keyword}: round {round_idx+1} +{new_count}, total={len(candidates)}")
            if len(candidates) >= self.config.max_cards_per_keyword:
                break
            if new_count == 0:
                stagnant_rounds += 1
                if stagnant_rounds >= 2:
                    break
            else:
                stagnant_rounds = 0

            await self.browser.scroll_page(1100)
            await asyncio.sleep(1.5)
            await self.browser.wait_for_search_results(timeout_s=8, poll_s=1.0)

        return candidates[: self.config.max_cards_per_keyword]

    @staticmethod
    def _dedupe_candidates(candidates: list[ResearchCandidate]) -> list[ResearchCandidate]:
        deduped: list[ResearchCandidate] = []
        seen: set[str] = set()
        for candidate in candidates:
            key = candidate.card.note_id or candidate.card.link or f"{candidate.card.title}:{candidate.card.author_name}"
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(candidate)
        return deduped

    async def _process_note(self, candidate: ResearchCandidate, plan: NoteExtractionPlan) -> NoteEntity | None:
        """Open, extract, optionally enrich media, and return to search."""
        note_t0 = time.time()
        card = candidate.card
        self._log_step("open_note", f"{plan.level.value}: {card.title[:60]}")

        async def ensure_search_context() -> dict:
            try:
                current_state = await self.browser.detect_state()
            except Exception:
                current_state = {}
            try:
                current_url = (await self.browser.get_tab_info()).get("url", "")
            except Exception:
                current_url = ""

            if current_state.get("state") == "search_results" and current_url == candidate.search_url:
                return current_state

            await self.browser.navigate(candidate.search_url, wait_ms=5000)
            await asyncio.sleep(2)
            return await self.browser.wait_for_search_results(timeout_s=20, poll_s=1.5)

        async def ensure_note_detail(label: str, wait_s: float = 1.5) -> bool:
            await asyncio.sleep(wait_s)
            last_state = {}
            for _ in range(3):
                try:
                    last_state = await self.browser.detect_state()
                    if last_state.get("state") == "note_detail":
                        return True
                except Exception as exc:
                    self._log_step("state_probe_retry", f"{label}: {exc}")
                await asyncio.sleep(1)
            self._log_step("state_probe", f"{label}: {last_state.get('state', 'unknown')}")
            return False

        async def handle_anti_bot(label: str, state: dict | None = None) -> bool:
            detected = (state or {}).get("state", "")
            if not self.browser.is_anti_bot_state(detected):
                return False
            self._log_step("anti_bot_detected", f"{label}: {detected}")
            await asyncio.sleep(self.config.anti_bot_backoff_s)
            await ensure_search_context()
            return True

        t0 = time.time()
        await ensure_search_context()
        opened = False
        if card.note_id:
            self._log_step("open_attempt", f"dom note_id={card.note_id}")
            dom_click = await self.browser.click_note_by_id(card.note_id)
            if dom_click.get("ok"):
                opened = await ensure_note_detail("note_id_click")
        if not opened and card.position is not None:
            self._log_step("open_attempt", f"position={card.position}")
            indexed_click = await self.browser.click_card(card.position)
            if indexed_click.get("ok"):
                opened = await ensure_note_detail("position_click")
        if not opened and card.note_id:
            self._log_step("open_attempt", f"cdp note_id={card.note_id}")
            opened = await self.browser.open_note_on_search(card.note_id)
            if opened:
                opened = await ensure_note_detail("cdp_click")
        await asyncio.sleep(2)
        self.timing.record("open_note_from_search", time.time() - t0)

        state = await self.browser.detect_state()
        if state.get("state") != "note_detail":
            if await handle_anti_bot("open_note", state):
                return None
            self._log_step("state_mismatch", f"Expected note_detail, got {state.get('state')}")
            return None

        # DOM extraction → NoteEntity
        t0 = time.time()
        raw_note = await self.browser.extract_note_content()
        note = NoteEntity.from_dom_dict(raw_note)
        note.source_keyword = candidate.keyword
        note.source_context = "search"
        note.source_position = card.position
        note.extraction_level = plan.level.value
        note.requested_sections = plan.requested_sections
        note.applied_capabilities = list(plan.capabilities)
        dom_dt = time.time() - t0
        self.timing.record("dom_extract", dom_dt)

        # Screenshot
        note_label = re.sub(r'[^\w]', '_', card.title[:20]).strip('_') or f"note_{card.position}"
        note_screenshot = await self._take_screenshot(f"note_{note_label}")
        if note_screenshot:
            note.screenshot_path = note_screenshot

        self._log_step(
            "dom_extract",
            f"title='{note.title[:30]}' author='{note.author_name}' "
            f"content_len={len(note.content)} type={note.note_type.value}",
            duration=dom_dt,
        )

        # DOM-first / Vision-fallback for text content
        if not note.has_content and note.screenshot_path and plan.use_vision_fallback and self.config.use_vision_fallback:
            self._log_step("vision_fallback", "DOM empty → Vision extraction from screenshot")
            try:
                with open(note.screenshot_path, "rb") as f:
                    img_b64 = base64.b64encode(f.read()).decode()
                vision_data = self._vision_extract_note(img_b64)
                if vision_data:
                    for field in ("title", "content", "author", "likes"):
                        if not getattr(note, field if field != "author" else "author_name", None):
                            val = vision_data.get(field)
                            if val:
                                setattr(note, field if field != "author" else "author_name", val)
                    if not note.hashtags and vision_data.get("hashtags"):
                        note.hashtags = vision_data["hashtags"]
                    self._log_step("vision_extract", f"Got: title='{note.title[:30]}'")
            except Exception as e:
                self._log_step("vision_error", str(e))

        if plan.use_media:
            await self.processor.process_note(note, plan)

        # Comments (with scroll for more)
        if plan.include_comments:
            t0 = time.time()
            note.comments = await self._collect_note_comments(plan.max_comments, plan.max_comment_scrolls)
            note.refresh_derived_fields()
            comments_dt = time.time() - t0
            self.timing.record("comments_extract", comments_dt)
            self._log_step(
                "comments",
                f"{len(note.comments)} comments, hottest={note.hottest_comments(1)[0].like_count if note.comments else 0}",
                duration=comments_dt,
            )

        # Completeness
        comp = note.completeness
        score = note.completeness_score
        missing = [k for k, v in comp.items() if not v]
        note_dt = time.time() - note_t0
        self.timing.record("process_note_total", note_dt)
        self._log_step(
            "completeness",
            f"{score:.0%} — missing: {missing}" if missing else f"{score:.0%} — complete",
            duration=note_dt,
        )

        close_result = await self.browser.close_note()
        self._log_step("close_note", close_result.get("method", "unknown"))
        close_state = await self.browser.wait_for_state(
            {"search_results", "profile_page", "homepage", *self.browser.ANTI_BOT_STATES},
            timeout=4.0,
        )
        if await handle_anti_bot("close_note", close_state):
            return note
        if close_state.get("state") != "search_results":
            await ensure_search_context()
        await asyncio.sleep(self.config.inter_note_pause_s)

        return note

    # ── Report ──────────────────────────────────────────────────

    def _save_report(self, report: dict):
        json_path = self.output_dir / "report.json"
        with open(json_path, "w") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        self._log_step("saved", f"JSON: {json_path}")

        html = self._generate_html(report)
        html_path = self.output_dir / "report.html"
        with open(html_path, "w") as f:
            f.write(html)
        self._log_step("saved", f"HTML: {html_path}")

    def _generate_html(self, data: dict) -> str:
        def _esc(s):
            return str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        parts = [
            "<!DOCTYPE html><html><head><meta charset='utf-8'>",
            f"<title>XHS Research: {_esc(data['topic'])}</title>",
            "<style>",
            "body{font-family:-apple-system,sans-serif;max-width:1100px;margin:0 auto;padding:20px;line-height:1.6;color:#333}",
            "h1{color:#ff2442}h2{color:#333;border-bottom:2px solid #ff2442;padding-bottom:5px}",
            ".note{background:#fff;border:1px solid #eee;border-radius:8px;padding:16px;margin:12px 0;box-shadow:0 1px 3px rgba(0,0,0,0.05)}",
            ".meta{color:#888;font-size:13px}.tag{background:#fff0f0;color:#ff2442;padding:2px 8px;border-radius:12px;font-size:12px;margin:2px;display:inline-block}",
            ".synthesis{background:#f8f8f8;padding:20px;border-radius:8px;margin:20px 0}",
            ".comment{background:#fafafa;padding:8px 10px;margin:4px 0;border-radius:4px;font-size:13px}",
            ".comment strong{color:#333}.comment .likes{color:#999;font-size:11px;margin-left:8px}",
            "img.screenshot{max-width:100%;max-height:500px;border:1px solid #ddd;border-radius:6px;margin:8px 0}",
            ".timing{background:#e8f5e9;padding:12px;border-radius:6px;font-size:13px;margin:10px 0}",
            ".log{font-family:monospace;font-size:11px;color:#666;max-height:400px;overflow-y:auto;background:#f5f5f5;padding:10px;border-radius:6px}",
            markdown_styles(),
            "</style></head><body>",
            f"<h1>XHS Research: {_esc(data['topic'])}</h1>",
            f"<p class='meta'>Keywords: {', '.join(data['keywords'])} | "
            f"Notes: {len(data['notes'])} | "
            f"Time: {data.get('timing', {}).get('total_s', '?')}s</p>",
        ]

        timing = data.get("timing", {})
        coverage = data.get("coverage", {})
        parts.append(
            f"<div class='timing'>Data collection: {timing.get('data_collection_s', '?')}s | "
            f"Total: {timing.get('total_s', '?')}s | "
            f"Cards scanned: {coverage.get('cards_scanned', '?')} | "
            f"Lite reads: {coverage.get('lite_reads', '?')} | "
            f"Deep reads: {coverage.get('deep_reads', '?')}</div>"
        )

        if coverage:
            parts.append("<h2>Coverage</h2><div class='note'>")
            parts.append(f"<p><strong>Cards scanned:</strong> {coverage.get('cards_scanned', '?')}</p>")
            if coverage.get("coverage_by_keyword"):
                parts.append("<p><strong>By keyword:</strong></p><ul>")
                for keyword, count in coverage["coverage_by_keyword"].items():
                    parts.append(f"<li>{_esc(keyword)}: {count}</li>")
                parts.append("</ul>")
            parts.append("</div>")

        screenshots = data.get("screenshots", [])
        if screenshots:
            parts.append("<h2>Search Results Screenshots</h2>")
            for sp in screenshots:
                rel_path = os.path.relpath(sp, str(self.output_dir))
                parts.append(f'<img class="screenshot" src="{rel_path}" alt="search screenshot">')

        parts.append(f"<h2>Research Summary</h2><div class='synthesis'>{render_markdown_block(data.get('synthesis', ''))}</div>")

        parts.append(f"<h2>Notes ({len(data['notes'])})</h2>")
        for i, note in enumerate(data["notes"]):
            parts.append(f"<div class='note'><h3>{i+1}. {_esc(note.get('title', 'Untitled'))}</h3>")
            parts.append(
                f"<p class='meta'>Author: {_esc(note.get('author', '?'))} | "
                f"Likes: {_esc(note.get('likes', '?'))} | "
                f"Favorites: {_esc(note.get('favorites', '?'))} | "
                f"Comments: {_esc(note.get('comments_count', '?'))} | "
                f"Images: {note.get('image_count', '?')} | "
                f"Type: {note.get('type', '?')} | "
                f"Level: {_esc(note.get('extraction_level', ''))}</p>"
            )
            parts.append(f"<p class='meta'>Keyword: {_esc(note.get('source_keyword', ''))}</p>")
            if note.get("applied_capabilities"):
                parts.append(f"<p class='meta'>Capabilities: {_esc(', '.join(note['applied_capabilities']))}</p>")
            if note.get("author_url"):
                parts.append(f"<p class='meta'>Author URL: <a href=\"{_esc(note['author_url'])}\" target=\"_blank\">{_esc(note['author_url'])}</a></p>")
            if note.get("location") or note.get("ip_location"):
                parts.append(
                    f"<p class='meta'>Location: {_esc(note.get('location', ''))} | "
                    f"IP: {_esc(note.get('ip_location', ''))}</p>"
                )

            if note.get("hashtags"):
                tags = " ".join(f"<span class='tag'>{_esc(t)}</span>" for t in note["hashtags"])
                parts.append(f"<p>{tags}</p>")

            content = note.get("content", "")
            if content:
                parts.append(f"<p>{_esc(content[:600])}</p>")

            if note.get("format_hints") or note.get("price_mentions") or note.get("cta_phrases") or note.get("key_points"):
                parts.append("<div class='meta'>")
                if note.get("format_hints"):
                    parts.append(f"<p>Format: {_esc(', '.join(note['format_hints']))}</p>")
                if note.get("price_mentions"):
                    parts.append(f"<p>Price mentions: {_esc(', '.join(note['price_mentions']))}</p>")
                if note.get("cta_phrases"):
                    parts.append(f"<p>CTA: {_esc(' | '.join(note['cta_phrases']))}</p>")
                if note.get("key_points"):
                    parts.append("<p>Key points:</p><ol>")
                    for point in note["key_points"][:5]:
                        parts.append(f"<li>{_esc(point)}</li>")
                    parts.append("</ol>")
                parts.append("</div>")

            if note.get("image_descriptions"):
                parts.append("<h4>Image Content (Vision)</h4>")
                for desc in note["image_descriptions"]:
                    parts.append(render_markdown_block(desc[:1200], "vision"))

            if note.get("video_resolved_url") or note.get("video_url"):
                parts.append(
                    f"<p class='meta'>Video source: {_esc(note.get('video_resolved_url') or note.get('video_url'))}</p>"
                )
            if note.get("transcript_summary"):
                parts.append("<h4>Video Summary</h4>")
                parts.append(render_markdown_block(note["transcript_summary"][:2000], "vision"))

            if note.get("screenshot"):
                rel_path = os.path.relpath(note["screenshot"], str(self.output_dir))
                parts.append(f'<img class="screenshot" src="{rel_path}">')

            comment_items = note.get("hot_comments") or note.get("comments") or []
            if comment_items:
                parts.append(f"<h4>Hot Comments ({len(comment_items)})</h4>")
                for c in comment_items[:8]:
                    likes_str = f"<span class='likes'>{c.get('likes', '')}</span>" if c.get("likes") else ""
                    parts.append(
                        f"<div class='comment'><strong>{_esc(c.get('username', ''))}</strong>: "
                        f"{_esc(c.get('text', '')[:200])}{likes_str}</div>"
                    )

            parts.append("</div>")

        parts.append("<h2>Execution Log</h2><div class='log'>")
        for entry in data.get("log", []):
            parts.append(
                f"<div>[{entry.get('step', '')} {entry.get('elapsed_s', '')}s] "
                f"{_esc(entry.get('action', ''))}: {_esc(entry.get('detail', ''))}</div>"
            )
        parts.append("</div></body></html>")

        return "\n".join(parts)


async def run_research(
    topic: str,
    keywords: list[str] | None = None,
    output_dir: str = "research_output",
    port: int = 8765,
    watch: bool = False,
):
    """Convenience function to run a research session."""
    agent = XHSResearchAgent(output_dir=output_dir, port=port)
    if watch:
        agent._watch = True
    report = await agent.research(topic=topic, keywords=keywords)

    print(f"\n{'='*60}")
    print(f"Research Complete — {report['timing']['total_s']}s")
    print(f"{'='*60}")
    print(f"Notes: {len(report['notes'])}")
    for i, n in enumerate(report["notes"]):
        print(f"  {i+1}. {n.get('title', '?')[:50]} — {n.get('author', '?')} ({n.get('likes', '?')} likes)")
        if n.get("content"):
            print(f"     Content: {n['content'][:80]}...")
        print(f"     Comments: {len(n.get('comments', []))}, Images: {n.get('image_count', '?')}")
    print(f"\nReport: {output_dir}/report.html")

    return report
