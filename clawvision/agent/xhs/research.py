"""XHS Research Agent — topic research on Xiaohongshu.

Uses XHSBrowser for DOM extraction and browser actions.
Uses MediaProcessor for LLM decisions and Vision analysis.

Flow:
  1. Generate search keywords (Claude Text)
  2. Navigate to XHS search via XHSBrowser
  3. Extract cards from DOM
  4. Pick best notes (Claude Text)
  5. Open each note, extract DOM content
  6. If DOM extraction incomplete → fallback to Vision (screenshot → Claude Vision)
  7. Extract comments from DOM
  8. Download images → Vision API for descriptions
  9. Synthesize findings (Claude Text)
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

from ..bridge import ExtensionBridge
from ..media import MediaProcessor
from .browser import XHSBrowser


@dataclass
class ResearchConfig:
    """Configuration for a research session."""
    max_notes_per_keyword: int = 2
    max_comment_scrolls: int = 2
    max_keywords: int = 3
    use_vision_fallback: bool = True
    use_vision_for_images: bool = True
    screenshot_dir: str = "screenshots"


class XHSResearchAgent:
    """Autonomous XHS research agent using XHSBrowser + MediaProcessor."""

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

        # Accept injected browser/media or create defaults
        if browser:
            self.browser = browser
        else:
            bridge = ExtensionBridge(port=port)
            self.browser = XHSBrowser(bridge)
            bridge.on_log(self._log_step)

        self.media = media or MediaProcessor()

        self._step = 0
        self._log: list[dict] = []
        self._t0 = 0
        self._screenshots: list[str] = []

    def _log_step(self, action: str, detail: str = ""):
        self._step += 1
        elapsed = time.time() - self._t0 if self._t0 else 0
        entry = {
            "step": self._step,
            "time": time.strftime("%H:%M:%S"),
            "elapsed_s": round(elapsed, 1),
            "action": action,
            "detail": detail[:200],
        }
        self._log.append(entry)
        print(f"  [{self._step:03d} {elapsed:5.1f}s] {action}: {detail[:100]}")

    # ── LLM Decision Functions ──────────────────────────────────

    def generate_keywords(self, topic: str) -> list[str]:
        raw = self.media.call_text(
            f"I want to research '{topic}' on Xiaohongshu (小红书). "
            f"Generate {self.config.max_keywords} Chinese search keywords "
            f"that would find the most relevant and diverse results. "
            f"Return only a JSON array of strings.",
            256,
        )
        result = self.media.extract_json(raw)
        return result if isinstance(result, list) else [topic]

    def pick_notes(self, cards: list[dict], topic: str, max_picks: int) -> list[dict]:
        if len(cards) <= max_picks:
            return cards

        raw = self.media.call_text(
            f"I'm researching '{topic}' on Xiaohongshu.\n"
            f"Here are the note cards from search results:\n"
            f"{json.dumps(cards, ensure_ascii=False, indent=1)}\n\n"
            f"Pick the {max_picks} most relevant and interesting notes. "
            f"Prefer notes with: high engagement, diverse perspectives, "
            f"content-rich titles, and relevance to the research topic. "
            f"Return a JSON array of the selected card objects (copy exactly).",
            2048,
        )
        picks = self.media.extract_json(raw)
        return (picks if isinstance(picks, list) else cards)[:max_picks]

    def synthesize(self, topic: str, keywords: list[str], notes: list[dict]) -> str:
        summaries = []
        for n in notes:
            summaries.append({
                "title": n.get("title", ""),
                "author": n.get("author", ""),
                "likes": n.get("likes", ""),
                "content_preview": n.get("content", "")[:300],
                "hashtags": n.get("hashtags", []),
                "image_count": n.get("image_count", 0),
                "image_descriptions": n.get("image_descriptions", []),
                "comments_count": n.get("comments_count", ""),
                "top_comments": [c.get("text", "")[:100] for c in n.get("comments", [])[:3]],
                "keyword": n.get("source_keyword", ""),
            })

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

    def describe_images(self, screenshot_b64: str, note_title: str) -> list[str]:
        raw = self.media.call_vision(
            screenshot_b64,
            f"This is a screenshot of a Xiaohongshu note titled '{note_title}'. "
            f"Describe the visual content you can see in the main image area (left side). "
            f"Focus on: what the image shows, any text/labels visible, product details, "
            f"colors, and overall aesthetic. Be specific and concise. "
            f"If there are multiple images visible, describe each. "
            f"Return a JSON array of description strings, one per image.",
            media_type="image/png",
            max_tokens=1024,
        )
        result = self.media.extract_json(raw)
        if isinstance(result, list):
            return [str(d) for d in result]
        return [raw[:200]]

    def vision_extract_note(self, screenshot_b64: str) -> dict:
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

    async def _describe_image_urls(
        self, urls: list[str], note_title: str, max_images: int = 3
    ) -> list[str]:
        descriptions = []
        for i, url in enumerate(urls[:max_images]):
            try:
                img_bytes = self.media.download_image(url, referer=XHSBrowser.XHS_REFERER)
                if not img_bytes:
                    continue
                desc = self.media.describe_image(
                    img_bytes,
                    f"Describe this image from a Xiaohongshu note titled '{note_title}'. "
                    f"Be concise (1-2 sentences). Focus on key visual content, "
                    f"any text/labels, products, and overall aesthetic.",
                    max_tokens=512,
                )
                descriptions.append(desc)
                self._log_step("image_vision", f"[{i+1}] {desc[:80]}")
            except Exception as e:
                self._log_step("image_error", f"[{i+1}] Failed: {e}")
                descriptions.append(f"(failed to load: {str(e)[:50]})")
        return descriptions

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

        # Start bridge and wait for extension
        await self.browser.bridge.start()
        self._log_step("bridge_ready", f"WebSocket server on port {self.browser.bridge.port}")

        print(
            "\n  >>> Waiting for Chrome Extension to connect. <<<\n"
            "  >>> Open extension popup and click 'Connect'. <<<\n"
        )
        await self.browser.bridge.wait_for_connection(timeout=120)

        # Check we're on XHS
        tab_info = await self.browser.get_tab_info()
        if "xiaohongshu.com" not in tab_info.get("url", ""):
            self._log_step("navigate", "Going to xiaohongshu.com")
            await self.browser.navigate("https://www.xiaohongshu.com")
            await asyncio.sleep(3)

        # Generate keywords
        if keywords is None:
            keywords = self.generate_keywords(topic)
        self._log_step("keywords", f"{len(keywords)} keywords: {keywords}")

        all_notes = []
        seen_titles = set()

        for ki, keyword in enumerate(keywords):
            self._log_step("search", f"[{ki+1}/{len(keywords)}] {keyword}")

            await self.browser.navigate_to_search(keyword)

            # Screenshot search results
            search_screenshot = await self._take_screenshot(f"search_{ki+1}_{keyword}")
            if search_screenshot:
                self._screenshots.append(search_screenshot)

            # Extract cards (retry once if empty)
            cards = await self.browser.extract_search_cards()
            if not cards:
                await asyncio.sleep(3)
                cards = await self.browser.extract_search_cards()

            self._log_step("cards", f"{len(cards)} cards from DOM")
            for c in cards[:5]:
                print(f"      {c.get('title', '?')[:40]} | {c.get('author', '?')} | {c.get('likes', '?')}")

            if not cards:
                self._log_step("no_cards", f"No cards found for '{keyword}'")
                continue

            # Pick best notes
            picks = self.pick_notes(cards, topic, self.config.max_notes_per_keyword)
            self._log_step("picked", f"{len(picks)} notes to examine")

            search_url = (await self.browser.get_tab_info()).get("url", "")

            for card in picks:
                title = card.get("title", "")
                if not title or title in seen_titles:
                    if title:
                        self._log_step("skip_dup", f"Already: {title[:40]}")
                    continue
                seen_titles.add(title)

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

        report = {
            "topic": topic,
            "keywords": keywords,
            "notes": all_notes,
            "synthesis": synthesis,
            "timing": {
                "data_collection_s": round(elapsed_collect, 1),
                "total_s": round(elapsed_total, 1),
            },
            "screenshots": [s for s in self._screenshots if s],
            "log": self._log,
        }

        self._save_report(report)
        await self.browser.bridge.stop()
        return report

    async def _process_note(
        self, card: dict, keyword: str, search_url: str
    ) -> dict | None:
        """Open, extract, and close a single note."""
        title = card.get("title", f"card_{card.get('position', '?')}")
        self._log_step("open_note", title[:60])

        idx = card.get("position", 0)
        link = card.get("link", "")

        # Click card to open as overlay on search page
        await self.browser.click_card(idx)
        await asyncio.sleep(3)

        # Verify note detail opened
        state = await self.browser.detect_state()
        if state.get("state") != "note_detail":
            if link and "/explore/" in link:
                self._log_step("navigate_fallback", f"Click didn't open overlay, navigating to {link[:50]}")
                await self.browser.navigate(link, wait_ms=5000)
                await asyncio.sleep(3)
                state = await self.browser.detect_state()

        if state.get("state") not in ("note_detail",):
            self._log_step("state_mismatch", f"Expected note_detail, got {state.get('state')}")
            await self.browser.navigate(search_url, wait_ms=5000)
            await asyncio.sleep(2)
            return None

        # Extract note content from DOM
        note = await self.browser.extract_note_content()
        note["source_keyword"] = keyword

        # Screenshot
        note_label = re.sub(r'[^\w]', '_', title[:20]).strip('_') or f"note_{idx}"
        note_screenshot = await self._take_screenshot(f"note_{note_label}")
        if note_screenshot:
            note["screenshot"] = note_screenshot

        has_content = bool(note.get("title") or note.get("content"))
        self._log_step(
            "dom_extract",
            f"title='{note.get('title', '')[:30]}' author='{note.get('author', '')}' "
            f"content_len={len(note.get('content', ''))} type={note.get('type', '?')}"
        )

        # Vision fallback if DOM extraction failed
        if not has_content and note_screenshot and self.config.use_vision_fallback:
            self._log_step("vision_fallback", "DOM empty, trying Vision extraction from screenshot")
            try:
                with open(note_screenshot, "rb") as f:
                    img_b64 = base64.b64encode(f.read()).decode()
                vision_data = self.vision_extract_note(img_b64)
                if vision_data:
                    for k, v in vision_data.items():
                        if not note.get(k) and v:
                            note[k] = v
                    self._log_step("vision_extract", f"Got: title='{note.get('title', '')[:30]}'")
            except Exception as e:
                self._log_step("vision_error", str(e))

        # Image understanding via Vision API
        image_urls = note.get("image_urls", [])
        if image_urls and self.config.use_vision_for_images:
            descs = await self._describe_image_urls(image_urls, note.get("title", title))
            note["image_descriptions"] = descs
            self._log_step("image_desc", f"{len(descs)} image(s) described via URL")

        # Extract comments (with scroll for more)
        all_comments = await self.browser.extract_comments()
        for _ in range(self.config.max_comment_scrolls):
            await self.browser.scroll_note(400)
            more = await self.browser.extract_comments()
            existing_keys = {f"{c.get('username', '')}:{c.get('text', '')[:30]}" for c in all_comments}
            for c in more:
                key = f"{c.get('username', '')}:{c.get('text', '')[:30]}"
                if key not in existing_keys:
                    all_comments.append(c)
                    existing_keys.add(key)

        note["comments"] = all_comments
        self._log_step("comments", f"{len(all_comments)} comments (deduped)")

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
