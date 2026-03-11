"""XHS Research Agent — agentic workflow for deep Xiaohongshu research.

Behaves like a human researcher:
1. Searches multiple keywords related to a topic
2. Browses search results, picks the most relevant/popular posts
3. Opens each post, reads content, browses ALL images (via keyboard arrows)
4. Scrolls through comments for interesting interactions
5. Visits promising authors' profiles to understand their identity
6. Crops key screenshots for reporting
7. Synthesizes findings into a structured research report

Uses the state machine Skill + grounding + LLM architecture.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from ..screen import ScreenController, WindowInfo
from ..skills.xiaohongshu_skill import XiaohongshuSkill
from ..vision.grounding import GroundingModel
from ..vision.llm import VisionLLM


@dataclass
class NoteData:
    """Collected data from a single XHS note."""

    title: str = ""
    author: str = ""
    content: str = ""
    hashtags: list[str] = field(default_factory=list)
    date: str = ""
    likes: str = ""
    favorites: str = ""
    comments_count: str = ""
    image_count: int = 1
    image_descriptions: list[str] = field(default_factory=list)
    comments: list[dict] = field(default_factory=list)
    screenshots: list[str] = field(default_factory=list)  # paths to saved screenshots
    source_keyword: str = ""


@dataclass
class AuthorData:
    """Collected data from an author's profile."""

    name: str = ""
    bio: str = ""
    followers: str = ""
    notes_count: str = ""
    recent_topics: list[str] = field(default_factory=list)
    screenshot: str = ""


@dataclass
class ResearchReport:
    """Final research output."""

    topic: str
    keywords_searched: list[str] = field(default_factory=list)
    notes: list[NoteData] = field(default_factory=list)
    authors: list[AuthorData] = field(default_factory=list)
    synthesis: str = ""
    screenshots: list[str] = field(default_factory=list)


class XHSResearchAgent:
    """Autonomous research agent for Xiaohongshu."""

    def __init__(
        self,
        output_dir: str = "research_output",
        grounding_backend: str = "auto",
        max_notes_per_keyword: int = 3,
        max_images_per_note: int = 10,
        max_comment_scrolls: int = 2,
        browse_author_profile: bool = True,
    ):
        self.screen = ScreenController()
        self.llm = VisionLLM()
        self.grounding = GroundingModel(backend=grounding_backend)
        self.skill = XiaohongshuSkill()

        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        (self.output_dir / "screenshots").mkdir(exist_ok=True)

        self.max_notes_per_keyword = max_notes_per_keyword
        self.max_images_per_note = max_images_per_note
        self.max_comment_scrolls = max_comment_scrolls
        self.browse_author_profile = browse_author_profile

        self._step = 0
        self._log: list[dict] = []

    # ── Logging & Screenshots ───────────────────────────────────────

    def _log_step(self, action: str, detail: str = "", **kwargs):
        self._step += 1
        entry = {
            "step": self._step,
            "time": time.strftime("%H:%M:%S"),
            "action": action,
            "detail": detail,
            **kwargs,
        }
        self._log.append(entry)
        print(f"  [{self._step:03d}] {action}: {detail[:80]}")

    def _save_screenshot(self, image: Image.Image, name: str) -> str:
        """Save a screenshot and return its relative path."""
        path = self.output_dir / "screenshots" / f"{self._step:03d}_{name}.png"
        # Resize for reasonable file sizes
        w, h = image.size
        if max(w, h) > 2000:
            scale = 2000 / max(w, h)
            image = image.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        image.save(str(path))
        return str(path)

    def _crop_and_save(
        self, image: Image.Image, name: str, region: str = "full"
    ) -> str:
        """Crop a region of interest and save it."""
        w, h = image.size
        crops = {
            "full": (0, 0, w, h),
            "content_left": (int(w * 0.05), int(h * 0.05), int(w * 0.55), int(h * 0.90)),
            "content_right": (int(w * 0.50), int(h * 0.03), int(w * 0.85), int(h * 0.92)),
            "engagement_bar": (int(w * 0.45), int(h * 0.88), int(w * 0.85), h),
            "top_section": (0, 0, w, int(h * 0.15)),
        }
        box = crops.get(region, crops["full"])
        cropped = image.crop(box)
        return self._save_screenshot(cropped, f"{name}_{region}")

    # ── Core Browser Interactions ───────────────────────────────────

    def _find_window(self) -> WindowInfo:
        win = self.screen.find_chrome_window("小红书")
        if not win:
            raise RuntimeError("No Chrome window with XHS found")
        return win

    def _capture(self, window: WindowInfo) -> Image.Image:
        return self.screen.capture_window(window)

    def _detect_state(self, screenshot: Image.Image) -> str:
        prompt = self.skill.get_state_detection_prompt()
        response = self.llm.analyze_page(screenshot, prompt, max_tokens=64)
        states = self.skill.get_states()
        for state_name in states:
            if state_name in response.strip().lower():
                return state_name
        return "unknown"

    def _click_element(self, window: WindowInfo, query: str, screenshot: Image.Image | None = None) -> bool:
        """Find and click a UI element. Returns True if successful."""
        if screenshot is None:
            screenshot = self._capture(window)
        result = self.grounding.ground(screenshot, query)
        if result is None:
            self._log_step("click_failed", f"Could not find: {query}")
            return False

        img_w, img_h = screenshot.size
        scale_x = window.width / img_w
        scale_y = window.height / img_h
        screen_x = window.x + int(result.x * scale_x)
        screen_y = window.y + int(result.y * scale_y)

        self.screen.activate_app(window.owner)
        self.screen.click(screen_x, screen_y)
        self._log_step("click", f"{query} → ({screen_x},{screen_y})")
        return True

    def _extract_json(self, screenshot: Image.Image, prompt: str) -> dict | list | None:
        """Extract structured JSON from a screenshot."""
        raw = self.llm.analyze_page(screenshot, prompt, max_tokens=2048)
        m = re.search(r"[\[{].*[\]}]", raw, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass
        return None

    # ── Research Actions ────────────────────────────────────────────

    def search_keyword(self, keyword: str, window: WindowInfo) -> list[dict]:
        """Search for a keyword and extract visible card summaries."""
        self._log_step("search", keyword)

        # Make sure we're on a searchable page (not in note detail overlay)
        for attempt in range(3):
            screenshot = self._capture(window)
            state = self._detect_state(screenshot)
            if state != "note_detail":
                break
            self._log_step("close_overlay", f"Closing note detail (attempt {attempt+1})")
            self.screen.press_key("escape")
            time.sleep(1.5)
        else:
            # Still stuck — try clicking outside the modal
            self._log_step("force_close", "Clicking top-left to escape modal")
            self.screen.click(window.x + 50, window.y + 50)
            time.sleep(1)

        # Click search box
        if not self._click_element(window, "the search input box at the top center of the page", screenshot):
            return []
        time.sleep(0.5)

        # Clear existing text thoroughly and type new keyword
        self.screen.hotkey("command", "a")
        time.sleep(0.1)
        self.screen.press_key("backspace")
        time.sleep(0.2)
        # Click search box again to make sure it's focused
        self._click_element(window, "the search input box at the top center of the page")
        time.sleep(0.3)
        self.screen.hotkey("command", "a")
        time.sleep(0.1)
        self.screen.type_text(keyword)
        time.sleep(0.5)
        self.screen.press_key("enter")
        self._log_step("search_submitted", f"Typed '{keyword}' and pressed Enter")
        time.sleep(3)  # Wait for results to load

        # Capture and extract cards
        screenshot = self._capture(window)
        self._save_screenshot(screenshot, f"search_{keyword}")

        # Verify we're on search results
        state = self._detect_state(screenshot)
        if state != "search_results":
            self._log_step("search_state_issue", f"Expected search_results, got {state}")
            # Try waiting more
            time.sleep(2)
            screenshot = self._capture(window)

        rules = self.skill.get_extraction_rules("search_results")
        cards = self._extract_json(screenshot, rules["cards"].prompt)
        if isinstance(cards, list):
            self._log_step("search_results", f"Found {len(cards)} cards for '{keyword}'")
            return cards

        self._log_step("search_no_cards", "LLM extraction returned no cards")
        return []

    def open_note(self, window: WindowInfo, card_desc: str) -> bool:
        """Open a note by clicking on it. Returns True if note detail opened."""
        self._log_step("open_note", card_desc[:60])
        screenshot = self._capture(window)

        if not self._click_element(window, f"the note card with title '{card_desc}'", screenshot):
            # Fallback: try positional description
            if not self._click_element(window, card_desc, screenshot):
                return False

        time.sleep(2)

        # Verify we're on note detail
        screenshot = self._capture(window)
        state = self._detect_state(screenshot)
        if state == "note_detail":
            self._log_step("state_confirmed", "note_detail")
            return True

        self._log_step("state_mismatch", f"Expected note_detail, got {state}")
        return False

    def extract_note_content(self, window: WindowInfo) -> NoteData:
        """Extract all content from the current note detail."""
        screenshot = self._capture(window)
        rules = self.skill.get_extraction_rules("note_detail")

        note = NoteData()

        # Extract main content
        content_data = self._extract_json(screenshot, rules["note_content"].prompt)
        if isinstance(content_data, dict):
            note.title = content_data.get("title", "")
            note.author = content_data.get("author", "")
            note.content = content_data.get("content", "")
            note.hashtags = content_data.get("hashtags", [])
            note.date = content_data.get("date", "")
            note.likes = content_data.get("likes", "")
            note.favorites = content_data.get("favorites", "")
            note.comments_count = content_data.get("comments_count", "")

            indicator = content_data.get("image_indicator")
            if indicator and "/" in str(indicator):
                try:
                    note.image_count = int(str(indicator).split("/")[1])
                except (ValueError, IndexError):
                    note.image_count = 1

        self._log_step("extract_content", f"'{note.title}' by {note.author}, {note.likes} likes")

        # Save content screenshot
        path = self._crop_and_save(screenshot, f"note_{note.title[:20]}", "content_right")
        note.screenshots.append(path)

        return note

    def browse_images(self, window: WindowInfo, note: NoteData) -> NoteData:
        """Browse through all images in a note using keyboard arrows."""
        if note.image_count <= 1:
            # Still describe the single image
            screenshot = self._capture(window)
            rules = self.skill.get_extraction_rules("note_detail")
            img_data = self._extract_json(screenshot, rules["image_description"].prompt)
            if isinstance(img_data, dict):
                note.image_descriptions.append(img_data.get("description", ""))
            path = self._crop_and_save(screenshot, "image_1", "content_left")
            note.screenshots.append(path)
            self._log_step("single_image", note.image_descriptions[0][:60] if note.image_descriptions else "")
            return note

        self._log_step("browse_images", f"{note.image_count} images to browse")

        for i in range(min(note.image_count, self.max_images_per_note)):
            screenshot = self._capture(window)

            # Describe current image
            rules = self.skill.get_extraction_rules("note_detail")
            img_data = self._extract_json(screenshot, rules["image_description"].prompt)
            if isinstance(img_data, dict):
                desc = img_data.get("description", "")
                note.image_descriptions.append(desc)
                self._log_step("image", f"[{i+1}/{note.image_count}] {desc[:60]}")

            # Save cropped image area
            path = self._crop_and_save(screenshot, f"image_{i+1}", "content_left")
            note.screenshots.append(path)

            # Press right arrow for next image (except on last)
            if i < note.image_count - 1:
                self.screen.press_key("right")
                time.sleep(1)

        return note

    def scroll_comments(self, window: WindowInfo, note: NoteData) -> NoteData:
        """Scroll through comments and extract interesting ones."""
        self._log_step("scroll_comments", f"max {self.max_comment_scrolls} scrolls")

        cx = window.x + window.width // 2
        cy = window.y + window.height // 2

        all_comments = []
        seen_keys = set()

        for scroll_round in range(self.max_comment_scrolls + 1):
            screenshot = self._capture(window)
            rules = self.skill.get_extraction_rules("note_detail")
            comments = self._extract_json(screenshot, rules["comments"].prompt)

            if isinstance(comments, list):
                for c in comments:
                    if not isinstance(c, dict):
                        continue
                    key = f"{c.get('username', '')}:{c.get('text', '')[:30]}"
                    if key not in seen_keys:
                        seen_keys.add(key)
                        all_comments.append(c)

            if scroll_round < self.max_comment_scrolls:
                # Scroll down in the comment area (right panel)
                self.screen.scroll(-3, x=cx + window.width // 4, y=cy)
                time.sleep(1.5)

        note.comments = all_comments
        self._log_step("comments_found", f"{len(all_comments)} unique comments")

        # Save a comment section screenshot if we have comments
        if all_comments:
            screenshot = self._capture(window)
            path = self._crop_and_save(screenshot, "comments", "content_right")
            note.screenshots.append(path)

        return note

    def close_note(self, window: WindowInfo):
        """Close the current note detail."""
        self.screen.press_key("escape")
        time.sleep(1)
        self._log_step("close_note", "pressed Escape")

    def visit_author_profile(self, window: WindowInfo, author_name: str) -> AuthorData | None:
        """Visit an author's profile page."""
        self._log_step("visit_author", author_name)

        screenshot = self._capture(window)
        if not self._click_element(
            window,
            f"the author's username or avatar ('{author_name}') at the top of the note",
            screenshot,
        ):
            return None

        time.sleep(3)  # Author profile loads as a new page

        screenshot = self._capture(window)
        state = self._detect_state(screenshot)

        if state != "profile_page":
            # Might still be transitioning — wait and retry
            time.sleep(2)
            screenshot = self._capture(window)
            state = self._detect_state(screenshot)

        if state != "profile_page":
            self._log_step("profile_skip", f"Got state {state}, not profile_page")
            # Try browser back
            self.screen.hotkey("command", "[")
            time.sleep(1)
            return None

        rules = self.skill.get_extraction_rules("profile_page")
        profile = self._extract_json(screenshot, rules["profile_info"].prompt)

        author = AuthorData()
        if isinstance(profile, dict):
            author.name = profile.get("display_name", author_name)
            author.bio = profile.get("bio", "")
            author.followers = profile.get("followers", "")
            author.notes_count = profile.get("notes_count", "")

        author.screenshot = self._save_screenshot(screenshot, f"author_{author_name[:10]}")
        self._log_step("author_data", f"{author.name}: {author.followers} followers, {author.notes_count} notes")

        # Go back to previous page
        self.screen.hotkey("command", "[")
        time.sleep(2)

        return author

    # ── High-Level Research Flow ────────────────────────────────────

    def research(self, topic: str, keywords: list[str] | None = None) -> ResearchReport:
        """Run a full research session on a topic.

        Args:
            topic: The research topic (e.g., "2025春季穿搭趋势")
            keywords: Specific keywords to search. If None, asks the LLM to generate them.
        """
        report = ResearchReport(topic=topic)

        window = self._find_window()
        self._log_step("start", f"Research topic: {topic}")

        # Generate keywords if not provided
        if keywords is None:
            keywords = self._generate_keywords(topic)
        report.keywords_searched = keywords
        self._log_step("keywords", f"{len(keywords)} keywords: {keywords}")

        seen_authors = set()

        for keyword in keywords:
            # Search
            cards = self.search_keyword(keyword, window)
            if not cards:
                continue

            # LLM picks the most relevant/interesting notes
            picks = self._pick_notes(cards, topic, self.max_notes_per_keyword)
            self._log_step("picked", f"{len(picks)} notes to examine from '{keyword}'")

            for card in picks:
                title = card.get("title", card.get("position", "unknown"))

                # Open note
                if not self.open_note(window, title):
                    continue

                # Extract content
                note = self.extract_note_content(window)
                note.source_keyword = keyword

                # Browse all images
                note = self.browse_images(window, note)

                # Check comments
                note = self.scroll_comments(window, note)

                report.notes.append(note)

                # Visit author profile (if new and interesting)
                if self.browse_author_profile and note.author and note.author not in seen_authors:
                    seen_authors.add(note.author)
                    author = self.visit_author_profile(window, note.author)
                    if author:
                        report.authors.append(author)

                # Close note
                self.close_note(window)
                time.sleep(0.5)

        # Generate synthesis
        report.synthesis = self._synthesize(report)
        report.screenshots = [s for n in report.notes for s in n.screenshots]

        # Save report
        self._save_report(report)

        self._log_step("done", f"Research complete: {len(report.notes)} notes, {len(report.authors)} authors")
        return report

    def _generate_keywords(self, topic: str) -> list[str]:
        """Ask LLM to generate search keywords for a topic."""
        prompt = (
            f"I want to research '{topic}' on Xiaohongshu (小红书). "
            f"Generate 3-5 Chinese search keywords that would find the most "
            f"relevant and diverse results. Return only a JSON array of strings.\n"
            f"Example: [\"关键词1\", \"关键词2\", \"关键词3\"]"
        )
        import anthropic
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text
        m = re.search(r"\[.*\]", raw, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass
        return [topic]

    def _pick_notes(self, cards: list[dict], topic: str, max_picks: int) -> list[dict]:
        """LLM picks the most relevant/interesting notes from search results."""
        if len(cards) <= max_picks:
            return cards

        prompt = (
            f"I'm researching '{topic}' on Xiaohongshu.\n"
            f"Here are the visible note cards:\n"
            f"{json.dumps(cards, ensure_ascii=False, indent=1)}\n\n"
            f"Pick the {max_picks} most relevant and interesting notes for this research. "
            f"Prefer notes with high engagement, diverse perspectives, and content-rich titles. "
            f"Return a JSON array of the selected card objects (copy them exactly)."
        )
        import anthropic
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text
        m = re.search(r"\[.*\]", raw, re.DOTALL)
        if m:
            try:
                picks = json.loads(m.group())
                if isinstance(picks, list):
                    return picks[:max_picks]
            except json.JSONDecodeError:
                pass
        return cards[:max_picks]

    def _synthesize(self, report: ResearchReport) -> str:
        """Generate a synthesis of all collected data."""
        notes_summary = []
        for n in report.notes:
            notes_summary.append({
                "title": n.title,
                "author": n.author,
                "likes": n.likes,
                "content_preview": n.content[:200],
                "hashtags": n.hashtags,
                "image_count": n.image_count,
                "comments_count": n.comments_count,
                "keyword": n.source_keyword,
            })

        prompt = (
            f"I researched '{report.topic}' on Xiaohongshu. Here's what I found:\n\n"
            f"Keywords searched: {report.keywords_searched}\n\n"
            f"Notes collected:\n{json.dumps(notes_summary, ensure_ascii=False, indent=1)}\n\n"
            f"Authors investigated:\n"
            + "\n".join(f"  - {a.name}: {a.bio[:100]}" for a in report.authors)
            + "\n\n"
            f"Synthesize the key findings into a research report (2-3 paragraphs in Chinese). "
            f"Focus on: main trends, popular content themes, notable creators, "
            f"audience engagement patterns, and actionable insights."
        )
        import anthropic
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text

    # ── Report Generation ───────────────────────────────────────────

    def _save_report(self, report: ResearchReport):
        """Save the research report as JSON and HTML."""
        # JSON
        report_data = {
            "topic": report.topic,
            "keywords": report.keywords_searched,
            "notes": [
                {
                    "title": n.title,
                    "author": n.author,
                    "content": n.content,
                    "hashtags": n.hashtags,
                    "date": n.date,
                    "likes": n.likes,
                    "favorites": n.favorites,
                    "comments_count": n.comments_count,
                    "image_count": n.image_count,
                    "image_descriptions": n.image_descriptions,
                    "comments": n.comments,
                    "screenshots": n.screenshots,
                    "keyword": n.source_keyword,
                }
                for n in report.notes
            ],
            "authors": [
                {
                    "name": a.name,
                    "bio": a.bio,
                    "followers": a.followers,
                    "notes_count": a.notes_count,
                    "screenshot": a.screenshot,
                }
                for a in report.authors
            ],
            "synthesis": report.synthesis,
            "log": self._log,
        }
        with open(self.output_dir / "report.json", "w") as f:
            json.dump(report_data, f, indent=2, ensure_ascii=False)

        # HTML
        html = self._generate_html(report_data)
        with open(self.output_dir / "report.html", "w") as f:
            f.write(html)

    def _generate_html(self, data: dict) -> str:
        def _esc(s):
            return str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        def _img(path):
            if not path:
                return ""
            # Make path relative to output dir
            try:
                rel = os.path.relpath(path, str(self.output_dir))
            except ValueError:
                rel = path
            return f'<img src="{rel}" style="max-width:500px;max-height:400px;border:1px solid #ddd;margin:5px;">'

        parts = [
            "<!DOCTYPE html><html><head><meta charset='utf-8'>",
            f"<title>XHS Research: {_esc(data['topic'])}</title>",
            "<style>",
            "body{font-family:-apple-system,sans-serif;max-width:1200px;margin:0 auto;padding:20px;line-height:1.6}",
            "h1{color:#ff2442}h2{color:#333;border-bottom:2px solid #ff2442;padding-bottom:5px}",
            "h3{color:#555}.note{background:#fff;border:1px solid #eee;border-radius:8px;padding:15px;margin:15px 0}",
            ".meta{color:#888;font-size:13px}.tag{background:#fff0f0;color:#ff2442;padding:2px 8px;border-radius:12px;font-size:12px;margin:2px}",
            ".synthesis{background:#f8f8f8;padding:20px;border-radius:8px;margin:20px 0;white-space:pre-wrap}",
            ".comment{background:#fafafa;padding:8px;margin:4px 0;border-radius:4px;font-size:13px}",
            ".screenshots{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0}",
            ".author{background:#fff8f0;padding:12px;border-radius:8px;margin:10px 0}",
            ".log{font-family:monospace;font-size:11px;color:#666}",
            "</style></head><body>",
            f"<h1>小红书研究报告: {_esc(data['topic'])}</h1>",
            f"<p class='meta'>关键词: {', '.join(data['keywords'])}</p>",
            f"<p class='meta'>共收集 {len(data['notes'])} 篇笔记, {len(data['authors'])} 位作者</p>",
        ]

        # Synthesis
        parts.append(f"<h2>研究综述</h2><div class='synthesis'>{_esc(data['synthesis'])}</div>")

        # Notes
        parts.append(f"<h2>笔记详情 ({len(data['notes'])}篇)</h2>")
        for i, note in enumerate(data["notes"]):
            parts.append(f"<div class='note'><h3>{i+1}. {_esc(note['title'])}</h3>")
            parts.append(f"<p class='meta'>作者: {_esc(note['author'])} | ❤️ {_esc(note['likes'])} | ⭐ {_esc(note['favorites'])} | 💬 {_esc(note['comments_count'])} | 📷 {note['image_count']}张图</p>")
            parts.append(f"<p class='meta'>来源关键词: {_esc(note['keyword'])} | 日期: {_esc(note['date'])}</p>")
            if note["hashtags"]:
                tags = " ".join(f"<span class='tag'>{_esc(t)}</span>" for t in note["hashtags"])
                parts.append(f"<p>{tags}</p>")
            parts.append(f"<p>{_esc(note['content'][:500])}</p>")
            if note["image_descriptions"]:
                parts.append("<h4>图片内容</h4><ol>")
                for desc in note["image_descriptions"]:
                    parts.append(f"<li>{_esc(desc[:200])}</li>")
                parts.append("</ol>")
            if note["comments"]:
                parts.append(f"<h4>热门评论 ({len(note['comments'])}条)</h4>")
                for c in note["comments"][:5]:
                    parts.append(f"<div class='comment'><strong>{_esc(c.get('username',''))}</strong>: {_esc(c.get('text','')[:150])}</div>")
            if note["screenshots"]:
                parts.append("<div class='screenshots'>")
                for s in note["screenshots"][:4]:
                    parts.append(_img(s))
                parts.append("</div>")
            parts.append("</div>")

        # Authors
        if data["authors"]:
            parts.append(f"<h2>作者分析 ({len(data['authors'])}位)</h2>")
            for a in data["authors"]:
                parts.append(f"<div class='author'><h3>{_esc(a['name'])}</h3>")
                parts.append(f"<p>{_esc(a['bio'])}</p>")
                parts.append(f"<p class='meta'>粉丝: {_esc(a['followers'])} | 笔记数: {_esc(a['notes_count'])}</p>")
                if a["screenshot"]:
                    parts.append(_img(a["screenshot"]))
                parts.append("</div>")

        # Action log
        parts.append("<h2>执行日志</h2><div class='log'>")
        for entry in data.get("log", []):
            parts.append(f"<div>[{entry.get('time','')}] {_esc(entry.get('action',''))}: {_esc(entry.get('detail',''))}</div>")
        parts.append("</div>")

        parts.append("</body></html>")
        return "\n".join(parts)
