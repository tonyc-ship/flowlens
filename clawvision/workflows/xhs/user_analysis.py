"""XHS User Analyzer — deep analysis of a single Xiaohongshu creator.

Task-level orchestration only. Delegates note understanding to NoteProcessor.

Navigates to user profile, collects all posts as NoteCards, opens top notes
via CDP click (avoids anti-bot), NoteProcessor handles all media processing,
and generates an HTML report.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from ...core.bridge import ExtensionBridge, ensure_extension_connection
from ...core.reporting import markdown_styles, render_markdown_block
from ...perception.media import MediaProcessor
from ...platforms.xhs.browser import XHSBrowser
from ...platforms.xhs.capabilities import NoteExtractionPlan, deep_note_plan, lite_note_plan
from ...platforms.xhs.entities import (
    AuthorEntity, Comment, NoteCard, NoteEntity, NoteType, parse_count_text,
)
from ...platforms.xhs.processor import NoteProcessor, ProcessorConfig, TimingRecord


@dataclass
class UserAnalysisConfig:
    max_scroll_rounds: int = 30
    max_timeline_samples: int = 12
    max_deep_notes: int = 4
    lite_comment_count: int = 4
    lite_comment_scrolls: int = 0
    max_comments_per_note: int = 20
    max_comment_scrolls: int = 2
    inter_note_pause_s: float = 1.2
    anti_bot_backoff_s: float = 8.0
    screenshot_dir: str = "screenshots"
    # NoteProcessor config
    max_images_per_note: int = 10
    vision_concurrency: int = 3


@dataclass
class CreatorSample:
    card: NoteCard
    note: NoteEntity | None = None


class XHSUserAnalyzer:
    """Deep analysis of a single XHS user/creator. Delegates media to NoteProcessor."""

    def __init__(
        self,
        output_dir: str = "user_analysis",
        port: int = 8765,
        config: UserAnalysisConfig | None = None,
        browser: XHSBrowser | None = None,
        media: MediaProcessor | None = None,
        manage_bridge_lifecycle: bool | None = None,
    ):
        self.config = config or UserAnalysisConfig()
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

    # ── Screenshot ──────────────────────────────────────────────

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

    # ── Main Flow ───────────────────────────────────────────────

    async def analyze(self, user_url: str) -> dict:
        """Run full user analysis. Returns report dict with AuthorEntity data."""
        self._t0 = time.time()
        self._step = 0
        self._log = []

        self._log_step("start", f"Analyzing user: {user_url}")

        if self.manage_bridge_lifecycle:
            await self.browser.bridge.start()
            self._log_step("bridge_ready", f"WebSocket on port {self.browser.bridge.port}")
        else:
            self._log_step("bridge_ready", f"Using external bridge on port {self.browser.bridge.port}")
        print("\n  >>> Waiting for Chrome Extension to connect. <<<\n")
        await ensure_extension_connection(
            self.browser.bridge,
            require_watch=getattr(self, "_watch", False),
            timeout=120,
            warmup_active_tab=False,
        )

        # Watch mode: create foreground window with in-page overlay
        if getattr(self, '_watch', False):
            await self.browser.bridge.create_watch_window(url="https://www.xiaohongshu.com")
            await asyncio.sleep(2)
            self._log_step("watch_mode", "Foreground window with in-page watch overlay created")

        # Navigate to profile
        profile_url = await self.browser.navigate_to_profile(user_url)
        self._log_step("navigate", profile_url)

        # Screenshot profile
        profile_screenshot = await self._take_screenshot("profile_header")

        # Extract profile info → AuthorEntity
        raw_profile = await self.browser.extract_profile_info()
        author = AuthorEntity.from_dom_dict(raw_profile)
        author.profile_url = profile_url
        if profile_screenshot:
            author.screenshot_path = profile_screenshot
        self._log_step("profile", f"{author.name} | followers={author.followers}")

        # Collect all post cards by scrolling → NoteCards
        author.note_cards = await self._collect_all_notes()
        self._log_step("cards_total", f"{len(author.note_cards)} posts collected")

        lite_samples = self._select_timeline_samples(author.note_cards, self.config.max_timeline_samples)
        self._log_step("timeline_pick", f"{len(lite_samples)} cards selected for lite timeline read")

        anti_bot_strikes = 0
        lite_plan = lite_note_plan(
            max_comments=self.config.lite_comment_count,
            max_comment_scrolls=self.config.lite_comment_scrolls,
        )
        for index, sample in enumerate(lite_samples, start=1):
            self._log_step("lite_read", f"[{index}/{len(lite_samples)}] {sample.card.title[:40]}")
            result = await self._process_note(sample.card, profile_url, lite_plan)
            if result is None:
                continue
            if result == "anti_bot":
                anti_bot_strikes += 1
                self._log_step("anti_bot", f"Strike {anti_bot_strikes} — backing off 15s")
                await asyncio.sleep(15)
                if anti_bot_strikes >= 3:
                    self._log_step("anti_bot_stop", "3 strikes — stopping note collection")
                    break
            else:
                anti_bot_strikes = 0
                sample.note = result
                author.detailed_notes.append(result)
            if index < len(lite_samples):
                await asyncio.sleep(2)

        deep_targets = self._pick_deep_samples(lite_samples, self.config.max_deep_notes)
        self._log_step("deep_pick", f"{len(deep_targets)} lite notes selected for deep read")

        deep_plan = deep_note_plan(
            max_comments=self.config.max_comments_per_note,
            max_comment_scrolls=self.config.max_comment_scrolls,
            max_images=self.config.max_images_per_note,
        )
        for index, sample in enumerate(deep_targets, start=1):
            self._log_step("deep_read", f"[{index}/{len(deep_targets)}] {sample.card.title[:40]}")
            result = await self._process_note(sample.card, profile_url, deep_plan)
            if isinstance(result, NoteEntity):
                sample.note = result
                for note_index, existing in enumerate(author.detailed_notes):
                    if existing.note_id and existing.note_id == result.note_id:
                        author.detailed_notes[note_index] = result
                        break
                else:
                    author.detailed_notes.append(result)
            if index < len(deep_targets):
                await asyncio.sleep(2)

        # LLM synthesis
        elapsed_collect = time.time() - self._t0
        self._log_step("synthesize", f"Collection done in {elapsed_collect:.1f}s")

        t0 = time.time()
        author.content_analysis = self._analyze_content_strategy(author)
        self.timing.record("llm_content_analysis", time.time() - t0)

        elapsed_total = time.time() - self._t0
        self._log_step("done", f"Total: {elapsed_total:.1f}s")

        # Log author completeness
        comp = author.completeness
        score = author.completeness_score
        missing = [k for k, v in comp.items() if not v]
        self._log_step("author_completeness", f"{score:.0%} — missing: {missing}" if missing else f"{score:.0%} — complete")

        # Log timing summary
        timing_summary = self.timing.summary()
        self._log_step("timing_summary", json.dumps(timing_summary, ensure_ascii=False))

        # Build report
        report = {
            "profile": author.to_report_dict(),
            "profile_url": profile_url,
            "sampling": {
                "timeline_samples": len(lite_samples),
                "deep_reads": len(deep_targets),
                "total_cards": len(author.note_cards),
            },
            "all_cards": [
                {"note_id": c.note_id, "title": c.title, "author": c.author_name,
                 "likes": c.likes, "type": c.note_type.value, "link": c.link}
                for c in author.note_cards
            ],
            "detailed_notes": [n.to_report_dict() for n in author.detailed_notes],
            "analysis": author.content_analysis,
            "timing": {
                "data_collection_s": round(elapsed_collect, 1),
                "total_s": round(elapsed_total, 1),
                "breakdown": timing_summary,
            },
            "log": self._log,
        }

        self._save_report(report)
        if self.manage_bridge_lifecycle:
            await self.browser.bridge.stop()
        return report

    def _select_timeline_samples(self, cards: list[NoteCard], sample_count: int) -> list[CreatorSample]:
        if len(cards) <= sample_count:
            return [CreatorSample(card=card) for card in cards]

        selected_indexes: set[int] = set()
        if sample_count > 1:
            for index in range(sample_count // 2):
                selected_indexes.add(round(index * (len(cards) - 1) / max(1, (sample_count // 2) - 1)))
        else:
            selected_indexes.add(0)

        by_signal = sorted(
            enumerate(cards),
            key=lambda item: parse_count_text(item[1].likes),
            reverse=True,
        )
        for index, _card in by_signal:
            if len(selected_indexes) >= sample_count:
                break
            selected_indexes.add(index)

        ordered = sorted(selected_indexes)[:sample_count]
        return [CreatorSample(card=cards[index]) for index in ordered]

    def _pick_deep_samples(self, samples: list[CreatorSample], max_picks: int) -> list[CreatorSample]:
        available = [sample for sample in samples if sample.note]
        if len(available) <= max_picks:
            return available

        sample_summaries = [
            {
                "index": idx,
                "position": sample.note.source_position,
                "title": sample.note.title,
                "date": sample.note.date,
                "likes": sample.note.likes,
                "favorites": sample.note.favorites,
                "comments": sample.note.comments_count,
                "type": sample.note.note_type.value,
                "content_preview": sample.note.content[:220],
                "format_hints": sample.note.format_hints,
            }
            for idx, sample in enumerate(available)
        ]
        with self._timed("llm_pick_deep_notes"):
            raw = self.media.call_text(
                "你正在做一个小红书作者起号拆解任务。下面这些帖子已经完成了轻量阅读：\n"
                f"{json.dumps(sample_summaries, ensure_ascii=False, indent=1)}\n\n"
                f"请选择 {max_picks} 篇最值得深读的帖子。优先考虑：阶段代表性、爆款信号、内容差异、可能的增长节点。"
                "返回 JSON 数组，内容为 index 整数。",
                768,
            )
        picks = self.media.extract_json(raw)
        if isinstance(picks, list):
            selected = [sample for idx, sample in enumerate(available) if idx in {int(v) for v in picks if isinstance(v, int)}]
            if selected:
                return selected[:max_picks]

        return sorted(
            available,
            key=lambda sample: parse_count_text(sample.note.likes) + parse_count_text(sample.note.favorites),
            reverse=True,
        )[:max_picks]

    # ── Collect All Post Cards ──────────────────────────────────

    async def _collect_all_notes(self) -> list[NoteCard]:
        all_cards: list[NoteCard] = []
        seen_ids: set[str] = set()

        for round_idx in range(self.config.max_scroll_rounds):
            raw_cards = await self.browser.extract_profile_notes()
            new_count = 0
            for raw in raw_cards:
                card = NoteCard.from_dom_dict(raw)
                nid = card.note_id or card.link
                if nid and nid not in seen_ids:
                    seen_ids.add(nid)
                    all_cards.append(card)
                    new_count += 1

            if new_count == 0 and round_idx > 0:
                self._log_step("scroll_done", f"No new cards after round {round_idx+1}")
                break

            self._log_step("scroll", f"Round {round_idx+1}: {new_count} new, {len(all_cards)} total")
            await self.browser.scroll_page(800)
            await asyncio.sleep(1.5)

        return all_cards

    # ── Process Single Note ─────────────────────────────────────

    async def _process_note(
        self, card: NoteCard, profile_url: str, plan: NoteExtractionPlan
    ) -> NoteEntity | str | None:
        """Open a note from profile, extract and process, return to profile."""
        note_t0 = time.time()
        note_id = card.note_id
        opened_as_overlay = False

        if not note_id and not card.link:
            return None

        async def ensure_profile_context() -> dict:
            return await self.browser.restore_profile_context(profile_url)

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
            await ensure_profile_context()
            return True

        await ensure_profile_context()

        if note_id:
            self._log_step("open_attempt", f"dom note_id={note_id}")
            click_result = await self.browser.click_note_by_id(note_id)
            if click_result.get("ok"):
                opened_as_overlay = await ensure_note_detail("profile_note_id_click")

        if not opened_as_overlay and card.position is not None:
            self._log_step("open_attempt", f"position={card.position}")
            click_result = await self.browser.click_card(card.position)
            if click_result.get("ok"):
                opened_as_overlay = await ensure_note_detail("profile_position_click")

        if not opened_as_overlay and note_id:
            self._log_step("open_attempt", f"cdp note_id={note_id}")
            opened_as_overlay = await self.browser.open_note_on_profile(note_id)
            if opened_as_overlay:
                opened_as_overlay = await ensure_note_detail("cdp_profile_click")

        state = await self.browser.detect_state()
        if state.get("state") != "note_detail":
            if await handle_anti_bot("open_note", state):
                return "anti_bot"
            self._log_step("state_mismatch", f"Expected note_detail, got {state.get('state')}")
            return None

        # Screenshot
        safe_label = re.sub(r'[^\w]', '_', card.title[:20]).strip('_') or note_id[:8]
        note_screenshot = await self._take_screenshot(f"note_{safe_label}")

        # DOM extraction → NoteEntity
        t0 = time.time()
        raw_note = await self.browser.extract_note_content()
        note = NoteEntity.from_dom_dict(raw_note)
        note.note_id = note_id
        note.card_likes = card.likes
        note.source_context = "profile"
        note.source_position = card.position
        note.extraction_level = plan.level.value
        note.requested_sections = plan.requested_sections
        note.applied_capabilities = list(plan.capabilities)
        if note_screenshot:
            note.screenshot_path = note_screenshot
        dom_dt = time.time() - t0
        self.timing.record("dom_extract", dom_dt)

        # Detect empty content (may indicate anti-bot)
        if not note.has_content:
            try:
                state = await self.browser.detect_state()
                if self.browser.is_anti_bot_state(state.get("state")) or state.get("state") in ("error", "unknown"):
                    self._log_step("anti_bot_detected", f"Empty content, state={state.get('state')}")
                    if opened_as_overlay:
                        await self.browser.close_note()
                        await asyncio.sleep(1)
                    else:
                        await ensure_profile_context()
                    return "anti_bot"
            except Exception:
                pass

        self._log_step(
            "extract",
            f"type={note.note_type.value} title='{note.title[:30]}' "
            f"content_len={len(note.content)}",
            duration=dom_dt,
        )

        if plan.use_media:
            await self.processor.process_note(note, plan)

        # Comments → Comment entities
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

        # Return to profile
        if opened_as_overlay:
            close_result = await self.browser.close_note()
            self._log_step("close_note", close_result.get("method", "unknown"))
            close_state = await self.browser.wait_for_state(
                {"profile_page", "homepage", *self.browser.ANTI_BOT_STATES},
                timeout=4.0,
            )
            if await handle_anti_bot("close_note", close_state):
                return note
            if close_state.get("state") != "profile_page":
                await ensure_profile_context()
        else:
            await ensure_profile_context()
        await asyncio.sleep(self.config.inter_note_pause_s)

        return note

    # ── LLM Analysis ────────────────────────────────────────────

    def _analyze_content_strategy(self, author: AuthorEntity) -> str:
        post_summaries = [
            {
                "position": card.position,
                "title": card.title[:60],
                "likes": card.likes,
                "type": card.note_type.value,
            }
            for card in author.note_cards[:80]
        ]
        note_summaries = [note.to_summary() for note in author.detailed_notes]

        prompt = (
            "请基于下面的小红书账号资料，输出一份“作者起号拆解”Markdown 报告。"
            "不要写成散文，要用短 bullet 和证据表达。\n\n"
            f"## 用户信息\n"
            f"昵称: {author.name}\n"
            f"简介: {author.bio}\n"
            f"粉丝: {author.followers}\n"
            f"关注: {author.following}\n"
            f"获赞与收藏: {author.total_likes}\n"
            f"认证: {author.verify_text or '无'}\n"
            f"标签: {', '.join(author.tags)}\n\n"
            f"## 帖子卡片概览\n{json.dumps(post_summaries, ensure_ascii=False, indent=1)}\n\n"
            f"## 轻量/深度样本\n{json.dumps(note_summaries, ensure_ascii=False, indent=1)}\n\n"
            "必须包含这些部分：\n"
            "## Coverage\n"
            "## 定位与内容支柱\n"
            "## 时间线与阶段\n"
            "## 增长信号\n"
            "## 可能的商业化/买量信号\n"
            "## 可复制动作\n"
            "## Unknowns\n\n"
            "要求：\n"
            "- 时间线部分必须引用帖子标题和日期/位置线索\n"
            "- 对“买量/投放”只能给出代理信号，不能无证据下结论\n"
            "- 明确哪些判断来自 deep read，哪些来自 lite read\n"
            "- 总长度控制在 30 行左右，不要冗长\n"
        )
        return self.media.call_text(prompt, 3072)

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

        profile = data.get("profile", {})
        all_cards = data.get("all_cards", [])
        detailed = data.get("detailed_notes", [])

        parts = [
            "<!DOCTYPE html><html><head><meta charset='utf-8'>",
            f"<title>XHS User Analysis: {_esc(profile.get('name', ''))}</title>",
            "<style>",
            "body{font-family:-apple-system,sans-serif;max-width:1100px;margin:0 auto;padding:20px;line-height:1.6;color:#333}",
            "h1{color:#ff2442}h2{color:#333;border-bottom:2px solid #ff2442;padding-bottom:5px}h3{color:#555}",
            ".profile-header{background:#fff;border:1px solid #eee;border-radius:12px;padding:24px;margin:16px 0;display:flex;gap:20px;align-items:flex-start;box-shadow:0 2px 8px rgba(0,0,0,0.05)}",
            ".profile-stats{display:flex;gap:24px;margin:12px 0}.profile-stats .stat{text-align:center}.profile-stats .stat .num{font-size:20px;font-weight:bold;color:#333}.profile-stats .stat .label{font-size:12px;color:#888}",
            ".note{background:#fff;border:1px solid #eee;border-radius:8px;padding:16px;margin:12px 0;box-shadow:0 1px 3px rgba(0,0,0,0.05)}",
            ".meta{color:#888;font-size:13px}",
            ".tag{background:#fff0f0;color:#ff2442;padding:2px 8px;border-radius:12px;font-size:12px;margin:2px;display:inline-block}",
            ".analysis{background:#f8f8f8;padding:20px;border-radius:8px;margin:20px 0;white-space:pre-wrap;line-height:1.8}",
            ".comment{background:#fafafa;padding:8px 10px;margin:4px 0;border-radius:4px;font-size:13px}",
            ".comment strong{color:#333}.comment .likes{color:#999;font-size:11px;margin-left:8px}",
            "img.screenshot{max-width:100%;max-height:500px;border:1px solid #ddd;border-radius:6px;margin:8px 0}",
            ".ocr{background:#fffde7;border:1px solid #ffd54f;border-radius:6px;padding:10px;margin:8px 0;font-size:13px;white-space:pre-wrap}",
            ".transcript{background:#e8f5e9;border:1px solid #81c784;border-radius:6px;padding:10px;margin:8px 0;font-size:13px;white-space:pre-wrap}",
            ".cards-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:8px;margin:12px 0}",
            ".card-mini{background:#fafafa;padding:8px;border-radius:4px;font-size:12px;border:1px solid #eee}",
            ".card-mini .title{font-weight:bold;color:#333}.card-mini .stats{color:#888;font-size:11px}",
            ".timing{background:#e8f5e9;padding:12px;border-radius:6px;font-size:13px;margin:10px 0}",
            ".log{font-family:monospace;font-size:11px;color:#666;max-height:400px;overflow-y:auto;background:#f5f5f5;padding:10px;border-radius:6px}",
            markdown_styles(),
            "</style></head><body>",
        ]

        parts.append(f"<h1>XHS User Analysis: {_esc(profile.get('name', ''))}</h1>")

        timing = data.get("timing", {})
        sampling = data.get("sampling", {})
        parts.append(
            f"<div class='timing'>Total posts: {len(all_cards)} | "
            f"Detailed: {len(detailed)} | "
            f"Timeline samples: {sampling.get('timeline_samples', '?')} | "
            f"Deep reads: {sampling.get('deep_reads', '?')} | "
            f"Data collection: {timing.get('data_collection_s', '?')}s | "
            f"Total: {timing.get('total_s', '?')}s</div>"
        )

        # Profile header
        parts.append("<h2>Profile</h2><div class='profile-header'>")
        if profile.get("avatar_url"):
            parts.append(f"<img src='{profile['avatar_url']}' style='width:80px;height:80px;border-radius:50%'>")
        parts.append("<div>")
        parts.append(f"<h3 style='margin:0'>{_esc(profile.get('name', ''))}")
        if profile.get("verified"):
            parts.append(f" <span style='color:#ff2442'>✓ {_esc(profile.get('verify_text', ''))}</span>")
        parts.append("</h3>")
        if profile.get("xhs_id"):
            parts.append(f"<p class='meta'>小红书号: {_esc(profile['xhs_id'])}</p>")
        if profile.get("bio"):
            parts.append(f"<p>{_esc(profile['bio'])}</p>")
        parts.append("<div class='profile-stats'>")
        for key, label in [("following", "关注"), ("followers", "粉丝"), ("total_likes", "获赞与收藏")]:
            parts.append(f"<div class='stat'><div class='num'>{_esc(profile.get(key, ''))}</div><div class='label'>{label}</div></div>")
        parts.append("</div>")
        if profile.get("tags"):
            parts.append("<p>" + " ".join(f"<span class='tag'>{_esc(t)}</span>" for t in profile["tags"]) + "</p>")
        parts.append("</div></div>")

        if profile.get("screenshot"):
            rel = os.path.relpath(profile["screenshot"], str(self.output_dir))
            parts.append(f'<img class="screenshot" src="{rel}" alt="profile">')

        # Analysis
        parts.append(f"<h2>Strategy Analysis</h2><div class='analysis'>{render_markdown_block(data.get('analysis', ''))}</div>")

        # All posts overview
        parts.append(f"<h2>All Posts ({len(all_cards)})</h2>")
        parts.append("<div class='cards-grid'>")
        for c in all_cards:
            icon = "🎬" if c.get("type") == "video" else "📷"
            parts.append(
                f"<div class='card-mini'>"
                f"<div class='title'>{icon} {_esc(c.get('title', 'Untitled')[:40])}</div>"
                f"<div class='stats'>❤️ {_esc(c.get('likes', '?'))}</div>"
                f"</div>"
            )
        parts.append("</div>")

        # Detailed notes
        parts.append(f"<h2>Detailed Notes ({len(detailed)})</h2>")
        for i, note in enumerate(detailed):
            parts.append(f"<div class='note'><h3>{i+1}. {_esc(note.get('title', 'Untitled'))}</h3>")
            parts.append(
                f"<p class='meta'>Type: {note.get('type', '?')} | "
                f"Likes: {_esc(note.get('likes', '?'))} | "
                f"Favorites: {_esc(note.get('favorites', '?'))} | "
                f"Comments: {_esc(note.get('comments_count', '?'))} | "
                f"Images: {note.get('image_count', '?')} | "
                f"Level: {_esc(note.get('extraction_level', ''))}</p>"
            )
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
                parts.append(f"<p>{_esc(content[:800])}</p>")

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

            if note.get("cover_description"):
                parts.append("<h4>Cover Image (Vision)</h4>")
                parts.append(render_markdown_block(note["cover_description"][:1600], "vision"))

            if note.get("image_descriptions"):
                parts.append("<h4>Image Content (Vision)</h4>")
                for desc in note["image_descriptions"]:
                    parts.append(render_markdown_block(desc[:1200], "vision"))

            if note.get("ocr_results"):
                parts.append("<h4>Text in Images (OCR)</h4>")
                for r in note["ocr_results"]:
                    parts.append(f"<div class='ocr'>Image {r['image_index']+1}:\n{_esc(r['text'][:500])}</div>")

            if note.get("transcript_summary"):
                parts.append("<h4>Video Transcript Summary</h4>")
                parts.append(render_markdown_block(note["transcript_summary"][:2000], "transcript"))
            if note.get("transcript"):
                parts.append(f"<details><summary>Full transcript ({len(note['transcript'])} chars)</summary><div class='transcript'>{_esc(note['transcript'][:2000])}</div></details>")
            if note.get("video_resolved_url") or note.get("video_url"):
                parts.append(
                    f"<p class='meta'>Video source: {_esc(note.get('video_resolved_url') or note.get('video_url'))}</p>"
                )

            if note.get("screenshot"):
                rel = os.path.relpath(note["screenshot"], str(self.output_dir))
                parts.append(f'<img class="screenshot" src="{rel}">')

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

        # Log
        parts.append("<h2>Execution Log</h2><div class='log'>")
        for entry in data.get("log", []):
            parts.append(
                f"<div>[{entry.get('step', '')} {entry.get('elapsed_s', '')}s] "
                f"{_esc(entry.get('action', ''))}: {_esc(entry.get('detail', ''))}</div>"
            )
        parts.append("</div></body></html>")

        return "\n".join(parts)


async def run_user_analysis(
    user_url: str,
    output_dir: str = "user_analysis",
    port: int = 8765,
    config: UserAnalysisConfig | None = None,
    watch: bool = False,
) -> dict:
    """Convenience function to run user analysis."""
    analyzer = XHSUserAnalyzer(config=config, output_dir=output_dir, port=port)
    if watch:
        analyzer._watch = True
    report = await analyzer.analyze(user_url)

    print(f"\n{'='*60}")
    print(f"User Analysis Complete — {report['timing']['total_s']}s")
    print(f"{'='*60}")
    profile = report["profile"]
    print(f"User: {profile.get('name', '?')} | Followers: {profile.get('followers', '?')}")
    print(f"Posts: {len(report['all_cards'])} total, {len(report['detailed_notes'])} detailed")
    print(f"\nReport: {output_dir}/report.html")

    return report
