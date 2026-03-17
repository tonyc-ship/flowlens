"""XHS Research Agent — Chrome Extension + External Agent architecture.

Uses Chrome Extension for DOM extraction and browser actions.
Uses Claude API for LLM decisions and Vision analysis.

Flow:
  1. Generate search keywords (Claude Text)
  2. Navigate to XHS search via Extension
  3. Extract cards from DOM via Extension
  4. Pick best notes (Claude Text)
  5. Open each note, extract DOM content via Extension
  6. If DOM extraction incomplete → fallback to Vision (screenshot → Claude Vision)
  7. Extract comments from DOM via Extension
  8. Capture screenshot for image understanding (Claude Vision)
  9. Synthesize findings (Claude Text)
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import anthropic

from .bridge import ExtensionBridge

# Load API key from zshrc if not in env
if not os.environ.get("ANTHROPIC_API_KEY"):
    for p in [
        os.path.expanduser("~/.zshrc.pre-oh-my-zsh"),
        os.path.expanduser("~/.zshrc"),
    ]:
        if os.path.exists(p):
            with open(p) as f:
                for line in f:
                    if "ANTHROPIC_API_KEY" in line and "export" in line:
                        val = line.strip().split("=", 1)[1].strip().strip('"').strip("'")
                        os.environ["ANTHROPIC_API_KEY"] = val
                        break

MODEL = "claude-sonnet-4-6"


@dataclass
class ResearchConfig:
    """Configuration for a research session."""
    max_notes_per_keyword: int = 2
    max_comment_scrolls: int = 2
    max_keywords: int = 3
    use_vision_fallback: bool = True
    use_vision_for_images: bool = True
    screenshot_dir: str = "screenshots"


class XHSAgent:
    """Autonomous XHS research agent using Chrome Extension bridge."""

    def __init__(
        self,
        output_dir: str = "research_output",
        port: int = 8765,
        config: ResearchConfig | None = None,
    ):
        self.bridge = ExtensionBridge(port=port)
        self.client = anthropic.Anthropic()
        self.config = config or ResearchConfig()
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        (self.output_dir / self.config.screenshot_dir).mkdir(exist_ok=True)

        self._step = 0
        self._log: list[dict] = []
        self._t0 = 0

        # Wire bridge logging
        self.bridge.on_log(self._log_step)

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

    # ── LLM Calls ───────────────────────────────────────────────

    def _call_text(self, prompt: str, max_tokens: int = 1024) -> str:
        """Call Claude with text-only prompt."""
        resp = self.client.messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text

    def _call_vision(self, image_b64: str, prompt: str, max_tokens: int = 2048) -> str:
        """Call Claude Vision with screenshot + text prompt."""
        # Strip data URL prefix if present
        if "," in image_b64:
            image_b64 = image_b64.split(",", 1)[1]

        resp = self.client.messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": image_b64,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }],
        )
        return resp.content[0].text

    def _extract_json(self, text: str):
        """Extract JSON from LLM response text."""
        m = re.search(r"[\[{][\s\S]*[\]}]", text)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass
        return None

    # ── LLM Decision Functions ──────────────────────────────────

    def generate_keywords(self, topic: str) -> list[str]:
        raw = self._call_text(
            f"I want to research '{topic}' on Xiaohongshu (小红书). "
            f"Generate {self.config.max_keywords} Chinese search keywords "
            f"that would find the most relevant and diverse results. "
            f"Return only a JSON array of strings.",
            256,
        )
        result = self._extract_json(raw)
        return result if isinstance(result, list) else [topic]

    def pick_notes(self, cards: list[dict], topic: str, max_picks: int) -> list[dict]:
        if len(cards) <= max_picks:
            return cards

        raw = self._call_text(
            f"I'm researching '{topic}' on Xiaohongshu.\n"
            f"Here are the note cards from search results:\n"
            f"{json.dumps(cards, ensure_ascii=False, indent=1)}\n\n"
            f"Pick the {max_picks} most relevant and interesting notes. "
            f"Prefer notes with: high engagement, diverse perspectives, "
            f"content-rich titles, and relevance to the research topic. "
            f"Return a JSON array of the selected card objects (copy exactly).",
            2048,
        )
        picks = self._extract_json(raw)
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

        return self._call_text(
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
        """Use Vision to describe images visible in the note screenshot."""
        raw = self._call_vision(
            screenshot_b64,
            f"This is a screenshot of a Xiaohongshu note titled '{note_title}'. "
            f"Describe the visual content you can see in the main image area (left side). "
            f"Focus on: what the image shows, any text/labels visible, product details, "
            f"colors, and overall aesthetic. Be specific and concise. "
            f"If there are multiple images visible, describe each. "
            f"Return a JSON array of description strings, one per image.",
            1024,
        )
        result = self._extract_json(raw)
        if isinstance(result, list):
            return [str(d) for d in result]
        return [raw[:200]]

    def vision_extract_note(self, screenshot_b64: str) -> dict:
        """Fallback: extract note content from screenshot using Vision."""
        raw = self._call_vision(
            screenshot_b64,
            "Extract the note content from this Xiaohongshu screenshot. "
            "Return a JSON object with these fields:\n"
            '{"title": "...", "author": "...", "content": "...", '
            '"likes": "...", "favorites": "...", "comments_count": "...", '
            '"hashtags": ["...", "..."], "date": "...", "image_count": N}',
            1024,
        )
        result = self._extract_json(raw)
        return result if isinstance(result, dict) else {}

    async def _describe_image_urls(
        self, urls: list[str], note_title: str, max_images: int = 3
    ) -> list[str]:
        """Download image URLs and describe them with Vision API."""
        import urllib.request

        descriptions = []
        for i, url in enumerate(urls[:max_images]):
            try:
                # Download image
                req = urllib.request.Request(url, headers={
                    "User-Agent": "Mozilla/5.0",
                    "Referer": "https://www.xiaohongshu.com/",
                })
                with urllib.request.urlopen(req, timeout=10) as resp:
                    img_data = resp.read()
                img_b64 = base64.b64encode(img_data).decode()

                # Determine media type from response or URL
                media_type = resp.headers.get("Content-Type", "image/jpeg")
                if not media_type.startswith("image/"):
                    media_type = "image/jpeg"
                # Claude Vision supports jpeg, png, gif, webp
                if "webp" in media_type:
                    media_type = "image/webp"

                # Send to Vision API
                resp = self.client.messages.create(
                    model=MODEL,
                    max_tokens=512,
                    messages=[{
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": media_type,
                                    "data": img_b64,
                                },
                            },
                            {
                                "type": "text",
                                "text": (
                                    f"Describe this image from a Xiaohongshu note titled '{note_title}'. "
                                    f"Be concise (1-2 sentences). Focus on key visual content, "
                                    f"any text/labels, products, and overall aesthetic."
                                ),
                            },
                        ],
                    }],
                )
                desc = resp.content[0].text
                descriptions.append(desc)
                self._log_step("image_vision", f"[{i+1}] {desc[:80]}")
            except Exception as e:
                self._log_step("image_error", f"[{i+1}] Failed: {e}")
                descriptions.append(f"(failed to load: {str(e)[:50]})")

        return descriptions

    # ── Screenshot Helper ────────────────────────────────────────

    async def _take_screenshot(self, label: str) -> str:
        """Take a screenshot and save it. Returns relative path or empty string."""
        try:
            data_url = await self.bridge.capture_screenshot()
            if not data_url or not data_url.startswith("data:"):
                self._log_step("screenshot_fail", f"{label}: no data returned")
                return ""

            # Decode and save
            b64_data = data_url.split(",", 1)[1] if "," in data_url else data_url
            img_bytes = base64.b64decode(b64_data)

            # Determine extension from data URL
            ext = "jpg" if "jpeg" in data_url else "png"
            filename = f"{label}.{ext}"
            filepath = self.output_dir / self.config.screenshot_dir / filename
            filepath.parent.mkdir(parents=True, exist_ok=True)
            filepath.write_bytes(img_bytes)

            self._log_step("screenshot", f"{label}: {len(img_bytes)//1024}KB → {filepath.name}")
            return str(filepath)
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

        self._log_step("start", f"Research topic: {topic}")

        # Start bridge and wait for extension
        await self.bridge.start()
        self._log_step("bridge_ready", f"WebSocket server on port {self.bridge.port}")

        print(
            "\n  >>> Waiting for Chrome Extension to connect. <<<\n"
            "  >>> Open extension popup and click 'Connect'. <<<\n"
        )
        await self.bridge.wait_for_connection(timeout=120)

        # Check we're on XHS
        tab_info = await self.bridge.get_tab_info()
        if "xiaohongshu.com" not in tab_info.get("url", ""):
            self._log_step("navigate", "Going to xiaohongshu.com")
            await self.bridge.navigate("https://www.xiaohongshu.com")
            await asyncio.sleep(3)

        # Generate keywords
        if keywords is None:
            keywords = self.generate_keywords(topic)
        self._log_step("keywords", f"{len(keywords)} keywords: {keywords}")

        all_notes = []
        seen_titles = set()
        self._screenshots = []  # Collect screenshots for report

        for ki, keyword in enumerate(keywords):
            self._log_step("search", f"[{ki+1}/{len(keywords)}] {keyword}")

            # Navigate to search URL
            search_url = (
                f"https://www.xiaohongshu.com/search_result"
                f"?keyword={keyword}&source=web_search_result_notes"
            )
            await self.bridge.navigate(search_url, wait_ms=5000)
            await asyncio.sleep(3)

            # Screenshot search results
            search_screenshot = await self._take_screenshot(f"search_{ki+1}_{keyword}")
            if search_screenshot:
                self._screenshots.append(search_screenshot)

            # Extract cards (retry once if empty)
            cards = await self.bridge.extract_search_cards()
            if not cards:
                await asyncio.sleep(3)
                cards = await self.bridge.extract_search_cards()

            self._log_step("cards", f"{len(cards)} cards from DOM")
            for c in cards[:5]:
                print(f"      {c.get('title', '?')[:40]} | {c.get('author', '?')} | {c.get('likes', '?')}")

            if not cards:
                self._log_step("no_cards", f"No cards found for '{keyword}'")
                continue

            # Pick best notes
            picks = self.pick_notes(cards, topic, self.config.max_notes_per_keyword)
            self._log_step("picked", f"{len(picks)} notes to examine")

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

        # Collect all screenshot paths for report
        screenshot_paths = [s for s in getattr(self, '_screenshots', []) if s]

        # Build report
        report = {
            "topic": topic,
            "keywords": keywords,
            "notes": all_notes,
            "synthesis": synthesis,
            "timing": {
                "data_collection_s": round(elapsed_collect, 1),
                "total_s": round(elapsed_total, 1),
            },
            "screenshots": screenshot_paths,
            "log": self._log,
        }

        # Save
        self._save_report(report)
        await self.bridge.stop()

        return report

    async def _process_note(
        self, card: dict, keyword: str, search_url: str
    ) -> dict | None:
        """Open, extract, and close a single note."""
        title = card.get("title", f"card_{card.get('position', '?')}")
        self._log_step("open_note", title[:60])

        # Open note — click card (stays on same page as overlay, better for DOM)
        idx = card.get("position", 0)
        link = card.get("link", "")

        # Try clicking the card first (opens as overlay on search page)
        await self.bridge.click_card(idx)
        await asyncio.sleep(3)

        # Verify we're on note detail
        state = await self.bridge.detect_state()
        if state.get("state") != "note_detail":
            # Fallback: navigate directly
            if link and "/explore/" in link:
                self._log_step("navigate_fallback", f"Click didn't open overlay, navigating to {link[:50]}")
                await self.bridge.navigate(link, wait_ms=5000)
                await asyncio.sleep(3)
                state = await self.bridge.detect_state()

        if state.get("state") not in ("note_detail",):
            self._log_step("state_mismatch", f"Expected note_detail, got {state.get('state')}")
            await self.bridge.navigate(search_url, wait_ms=5000)
            await asyncio.sleep(2)
            return None

        # Extract note content from DOM (content.js waits for async render)
        note = await self.bridge.extract_note_content()
        note["source_keyword"] = keyword

        # Screenshot the note detail
        note_label = re.sub(r'[^\w]', '_', title[:20]).strip('_') or f"note_{idx}"
        note_screenshot = await self._take_screenshot(f"note_{note_label}")
        if note_screenshot:
            note["screenshot"] = note_screenshot

        # Check if DOM extraction got content
        has_content = bool(note.get("title") or note.get("content"))
        self._log_step(
            "dom_extract",
            f"title='{note.get('title', '')[:30]}' author='{note.get('author', '')}' "
            f"content_len={len(note.get('content', ''))} type={note.get('type', '?')}"
        )

        # Vision fallback: if DOM extraction failed, use screenshot + Vision API
        if not has_content and note_screenshot and self.config.use_vision_fallback:
            self._log_step("vision_fallback", "DOM empty, trying Vision extraction from screenshot")
            try:
                with open(note_screenshot, "rb") as f:
                    img_b64 = base64.b64encode(f.read()).decode()
                vision_data = self.vision_extract_note(img_b64)
                if vision_data:
                    # Merge vision data (don't overwrite existing DOM data)
                    for k, v in vision_data.items():
                        if not note.get(k) and v:
                            note[k] = v
                    has_content = bool(note.get("title") or note.get("content"))
                    self._log_step("vision_extract", f"Got: title='{note.get('title', '')[:30]}'")
            except Exception as e:
                self._log_step("vision_error", str(e))
        elif not has_content:
            self._log_step("dom_extract_empty", "DOM extraction got no content (video note?)")

        # Image understanding via Vision API (download image URLs)
        image_urls = note.get("image_urls", [])
        if image_urls and self.config.use_vision_for_images:
            descs = await self._describe_image_urls(image_urls, note.get("title", title))
            note["image_descriptions"] = descs
            self._log_step("image_desc", f"{len(descs)} image(s) described via URL")

        # Extract comments (with scroll for more)
        all_comments = await self.bridge.extract_comments()
        for _ in range(self.config.max_comment_scrolls):
            await self.bridge.scroll_note(400)
            more = await self.bridge.extract_comments()
            # Merge new comments
            existing_keys = {f"{c.get('username', '')}:{c.get('text', '')[:30]}" for c in all_comments}
            for c in more:
                key = f"{c.get('username', '')}:{c.get('text', '')[:30]}"
                if key not in existing_keys:
                    all_comments.append(c)
                    existing_keys.add(key)

        note["comments"] = all_comments
        self._log_step("comments", f"{len(all_comments)} comments (deduped)")

        # Close note and return to search
        await self.bridge.close_note()
        await asyncio.sleep(1)

        # Verify we're back on search results
        state = await self.bridge.detect_state()
        if state.get("state") != "search_results":
            self._log_step("recovery", f"State after close: {state.get('state')}, navigating back")
            await self.bridge.navigate(search_url, wait_ms=5000)
            await asyncio.sleep(2)

        return note

    # ── Report ──────────────────────────────────────────────────

    def _save_report(self, report: dict):
        """Save report as JSON and HTML."""
        # JSON
        json_path = self.output_dir / "report.json"
        with open(json_path, "w") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        self._log_step("saved", f"JSON: {json_path}")

        # HTML
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

        # Timing
        timing = data.get("timing", {})
        parts.append(
            f"<div class='timing'>Data collection: {timing.get('data_collection_s', '?')}s | "
            f"Total: {timing.get('total_s', '?')}s</div>"
        )

        # Search screenshots
        screenshots = data.get("screenshots", [])
        if screenshots:
            parts.append("<h2>Search Results Screenshots</h2>")
            for sp in screenshots:
                rel_path = os.path.relpath(sp, str(self.output_dir))
                parts.append(f'<img class="screenshot" src="{rel_path}" alt="search screenshot">')

        # Synthesis
        parts.append(f"<h2>Research Summary</h2><div class='synthesis'>{_esc(data.get('synthesis', ''))}</div>")

        # Notes
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

            # Image descriptions
            if note.get("image_descriptions"):
                parts.append("<h4>Image Content (Vision)</h4><ol>")
                for desc in note["image_descriptions"]:
                    parts.append(f"<li>{_esc(desc[:300])}</li>")
                parts.append("</ol>")

            # Screenshot
            if note.get("screenshot"):
                rel_path = os.path.relpath(note["screenshot"], str(self.output_dir))
                parts.append(f'<img class="screenshot" src="{rel_path}">')

            # Comments
            if note.get("comments"):
                parts.append(f"<h4>Comments ({len(note['comments'])})</h4>")
                for c in note["comments"][:8]:
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


async def run_research(
    topic: str,
    keywords: list[str] | None = None,
    output_dir: str = "research_output",
    port: int = 8765,
):
    """Convenience function to run a research session."""
    agent = XHSAgent(output_dir=output_dir, port=port)
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
