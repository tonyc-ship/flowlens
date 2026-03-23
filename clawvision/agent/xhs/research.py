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
from .browser import XHSBrowser
from .entities import Comment, NoteCard, NoteEntity, NoteType
from .processor import NoteProcessor, ProcessorConfig, TimingRecord


@dataclass
class ResearchConfig:
    """Configuration for a research session."""
    max_notes_per_keyword: int = 2
    max_comment_scrolls: int = 2
    max_keywords: int = 3
    use_vision_fallback: bool = True
    screenshot_dir: str = "screenshots"
    # NoteProcessor config
    max_images_per_note: int = 10
    vision_concurrency: int = 3


class XHSResearchAgent:
    """Autonomous XHS research agent. Delegates media processing to NoteProcessor."""

    def __init__(
        self,
        output_dir: str = "research_output",
        port: int = 8765,
        config: ResearchConfig | None = None,
        browser: XHSBrowser | None = None,
        media: MediaProcessor | None = None,
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

    def pick_notes(self, cards: list[NoteCard], topic: str, max_picks: int) -> list[NoteCard]:
        if len(cards) <= max_picks:
            return cards

        card_dicts = [
            {"title": c.title, "author": c.author_name, "likes": c.likes,
             "type": c.note_type.value, "position": c.position}
            for c in cards
        ]
        with self._timed("llm_pick_notes"):
            raw = self.media.call_text(
                f"I'm researching '{topic}' on Xiaohongshu.\n"
                f"Here are the note cards from search results:\n"
                f"{json.dumps(card_dicts, ensure_ascii=False, indent=1)}\n\n"
                f"Pick the {max_picks} most relevant and interesting notes. "
                f"Prefer notes with: high engagement, diverse perspectives, "
                f"content-rich titles, and relevance to the research topic. "
                f"Return a JSON array of position numbers (integers) for the selected notes.",
                512,
            )
        picks = self.media.extract_json(raw)
        if isinstance(picks, list):
            picked_positions = set(picks)
            selected = [c for c in cards if c.position in picked_positions]
            if selected:
                return selected[:max_picks]
        return cards[:max_picks]

    def synthesize(self, topic: str, keywords: list[str], notes: list[NoteEntity]) -> str:
        summaries = [n.to_summary() for n in notes]
        for s, n in zip(summaries, notes):
            s["keyword"] = n.source_keyword

        with self._timed("llm_synthesize"):
            return self.media.call_text(
                f"I researched '{topic}' on Xiaohongshu (小红书).\n\n"
                f"Keywords searched: {json.dumps(keywords, ensure_ascii=False)}\n\n"
                f"Notes collected:\n{json.dumps(summaries, ensure_ascii=False, indent=1)}\n\n"
                f"Write a research report in Chinese (3-4 paragraphs). Cover:\n"
                f"1. Main trends and themes found\n"
                f"2. Popular content patterns and engagement insights\n"
                f"3. Key opinions and recommendations from creators\n"
                f"4. Actionable takeaways for someone interested in this topic",
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

        await self.browser.bridge.start()
        self._log_step("bridge_ready", f"WebSocket server on port {self.browser.bridge.port}")

        print(
            "\n  >>> Waiting for Chrome Extension to connect. <<<\n"
            "  >>> Open extension popup and click 'Connect'. <<<\n"
        )
        await self.browser.bridge.wait_for_connection(timeout=120)

        tab_info = await self.browser.get_tab_info()
        if "xiaohongshu.com" not in tab_info.get("url", ""):
            self._log_step("navigate", "Going to xiaohongshu.com")
            await self.browser.navigate("https://www.xiaohongshu.com")
            await asyncio.sleep(3)

        if keywords is None:
            keywords = self.generate_keywords(topic)
        self._log_step("keywords", f"{len(keywords)} keywords: {keywords}")

        all_notes: list[NoteEntity] = []
        seen_titles: set[str] = set()

        for ki, keyword in enumerate(keywords):
            self._log_step("search", f"[{ki+1}/{len(keywords)}] {keyword}")

            await self.browser.navigate_to_search(keyword)

            search_screenshot = await self._take_screenshot(f"search_{ki+1}_{keyword}")
            if search_screenshot:
                self._screenshots.append(search_screenshot)

            raw_cards = await self.browser.extract_search_cards()
            if not raw_cards:
                await asyncio.sleep(3)
                raw_cards = await self.browser.extract_search_cards()

            cards = [NoteCard.from_dom_dict(c) for c in raw_cards]
            self._log_step("cards", f"{len(cards)} cards from DOM")
            for c in cards[:5]:
                print(f"      {c.title[:40]} | {c.author_name} | {c.likes}")

            if not cards:
                self._log_step("no_cards", f"No cards found for '{keyword}'")
                continue

            picks = self.pick_notes(cards, topic, self.config.max_notes_per_keyword)
            self._log_step("picked", f"{len(picks)} notes to examine")

            search_url = (await self.browser.get_tab_info()).get("url", "")

            for card in picks:
                if not card.title or card.title in seen_titles:
                    if card.title:
                        self._log_step("skip_dup", f"Already: {card.title[:40]}")
                    continue
                seen_titles.add(card.title)

                note = await self._process_note(card, keyword, search_url)
                if note:
                    all_notes.append(note)

        # Synthesize
        elapsed_collect = time.time() - self._t0
        self._log_step("synthesize", f"Data collection done in {elapsed_collect:.1f}s")

        synthesis = ""
        if all_notes:
            synthesis = self.synthesize(topic, keywords, all_notes)

        elapsed_total = time.time() - self._t0
        self._log_step("done", f"Total: {elapsed_total:.1f}s, {len(all_notes)} notes")

        timing_summary = self.timing.summary()
        self._log_step("timing_summary", json.dumps(timing_summary, ensure_ascii=False))

        report = {
            "topic": topic,
            "keywords": keywords,
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
        await self.browser.bridge.stop()
        return report

    async def _process_note(
        self, card: NoteCard, keyword: str, search_url: str
    ) -> NoteEntity | None:
        """Open, extract, process media, and close a single note."""
        note_t0 = time.time()
        self._log_step("open_note", card.title[:60])

        # Click card to open as overlay
        t0 = time.time()
        await self.browser.click_card(card.position)
        await asyncio.sleep(3)
        self.timing.record("click_card", time.time() - t0)

        state = await self.browser.detect_state()
        if state.get("state") != "note_detail":
            if card.link and "/explore/" in card.link:
                self._log_step("navigate_fallback", "Click didn't open overlay, navigating")
                await self.browser.navigate(card.link, wait_ms=5000)
                await asyncio.sleep(3)
                state = await self.browser.detect_state()

        if state.get("state") != "note_detail":
            self._log_step("state_mismatch", f"Expected note_detail, got {state.get('state')}")
            await self.browser.navigate(search_url, wait_ms=5000)
            await asyncio.sleep(2)
            return None

        # DOM extraction → NoteEntity
        t0 = time.time()
        raw_note = await self.browser.extract_note_content()
        note = NoteEntity.from_dom_dict(raw_note)
        note.source_keyword = keyword
        note.source_context = "search"
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
        if not note.has_content and note.screenshot_path and self.config.use_vision_fallback:
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

        # NoteProcessor handles all media (images/video) — DOM-first, carousel-fallback
        await self.processor.process_note(note)

        # Comments (with scroll for more)
        t0 = time.time()
        raw_comments = await self.browser.extract_comments()
        for _ in range(self.config.max_comment_scrolls):
            await self.browser.scroll_note(400)
            more = await self.browser.extract_comments()
            existing_keys = {f"{c.get('username', '')}:{c.get('text', '')[:30]}" for c in raw_comments}
            for c in more:
                key = f"{c.get('username', '')}:{c.get('text', '')[:30]}"
                if key not in existing_keys:
                    raw_comments.append(c)
                    existing_keys.add(key)

        note.comments = [Comment.from_dom_dict(c) for c in raw_comments]
        comments_dt = time.time() - t0
        self.timing.record("comments_extract", comments_dt)
        self._log_step("comments", f"{len(note.comments)} comments (deduped)", duration=comments_dt)

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

        # Close note and return to search
        await self.browser.close_note()
        await asyncio.sleep(1)

        state = await self.browser.detect_state()
        if state.get("state") != "search_results":
            self._log_step("recovery", f"State after close: {state.get('state')}, navigating back")
            await self.browser.navigate(search_url, wait_ms=5000)
            await asyncio.sleep(2)

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
            ".synthesis{background:#f8f8f8;padding:20px;border-radius:8px;margin:20px 0;white-space:pre-wrap}",
            ".comment{background:#fafafa;padding:8px 10px;margin:4px 0;border-radius:4px;font-size:13px}",
            ".comment strong{color:#333}.comment .likes{color:#999;font-size:11px;margin-left:8px}",
            "img.screenshot{max-width:100%;max-height:500px;border:1px solid #ddd;border-radius:6px;margin:8px 0}",
            ".timing{background:#e8f5e9;padding:12px;border-radius:6px;font-size:13px;margin:10px 0}",
            ".log{font-family:monospace;font-size:11px;color:#666;max-height:400px;overflow-y:auto;background:#f5f5f5;padding:10px;border-radius:6px}",
            "</style></head><body>",
            f"<h1>XHS Research: {_esc(data['topic'])}</h1>",
            f"<p class='meta'>Keywords: {', '.join(data['keywords'])} | "
            f"Notes: {len(data['notes'])} | "
            f"Time: {data.get('timing', {}).get('total_s', '?')}s</p>",
        ]

        timing = data.get("timing", {})
        parts.append(
            f"<div class='timing'>Data collection: {timing.get('data_collection_s', '?')}s | "
            f"Total: {timing.get('total_s', '?')}s</div>"
        )

        screenshots = data.get("screenshots", [])
        if screenshots:
            parts.append("<h2>Search Results Screenshots</h2>")
            for sp in screenshots:
                rel_path = os.path.relpath(sp, str(self.output_dir))
                parts.append(f'<img class="screenshot" src="{rel_path}" alt="search screenshot">')

        parts.append(f"<h2>Research Summary</h2><div class='synthesis'>{_esc(data.get('synthesis', ''))}</div>")

        parts.append(f"<h2>Notes ({len(data['notes'])})</h2>")
        for i, note in enumerate(data["notes"]):
            parts.append(f"<div class='note'><h3>{i+1}. {_esc(note.get('title', 'Untitled'))}</h3>")
            parts.append(
                f"<p class='meta'>Author: {_esc(note.get('author', '?'))} | "
                f"Likes: {_esc(note.get('likes', '?'))} | "
                f"Favorites: {_esc(note.get('favorites', '?'))} | "
                f"Comments: {_esc(note.get('comments_count', '?'))} | "
                f"Images: {note.get('image_count', '?')} | "
                f"Type: {note.get('type', '?')}</p>"
            )
            parts.append(f"<p class='meta'>Keyword: {_esc(note.get('source_keyword', ''))}</p>")

            if note.get("hashtags"):
                tags = " ".join(f"<span class='tag'>{_esc(t)}</span>" for t in note["hashtags"])
                parts.append(f"<p>{tags}</p>")

            content = note.get("content", "")
            if content:
                parts.append(f"<p>{_esc(content[:600])}</p>")

            if note.get("image_descriptions"):
                parts.append("<h4>Image Content (Vision)</h4><ol>")
                for desc in note["image_descriptions"]:
                    parts.append(f"<li>{_esc(desc[:300])}</li>")
                parts.append("</ol>")

            if note.get("screenshot"):
                rel_path = os.path.relpath(note["screenshot"], str(self.output_dir))
                parts.append(f'<img class="screenshot" src="{rel_path}">')

            if note.get("comments"):
                parts.append(f"<h4>Comments ({len(note['comments'])})</h4>")
                for c in note["comments"][:8]:
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
):
    """Convenience function to run a research session."""
    agent = XHSResearchAgent(output_dir=output_dir, port=port)
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
