"""Xiaohongshu (Little Red Book) specific workflows.

Automates research tasks on xiaohongshu.com web version
using screen-level control and visual understanding.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

from PIL import Image

from ..screen import ScreenController, WindowInfo
from ..vision.llm import VisionLLM
from ..vision.ocr import OCREngine


@dataclass
class NoteCard:
    """A single Xiaohongshu note card extracted from search results."""

    title: str
    author: str
    likes: str | None = None
    cover_image: Image.Image | None = None
    position: tuple[int, int, int, int] | None = None  # x, y, w, h on screen


@dataclass
class SearchResult:
    """Structured result from a Xiaohongshu search."""

    query: str
    notes: list[NoteCard] = field(default_factory=list)
    screenshot: Image.Image | None = None
    page_description: str = ""


class XiaohongshuWorkflow:
    """Automate research tasks on Xiaohongshu web version.

    Assumes:
    - Chrome is open with user already logged in to xiaohongshu.com
    - Screen Recording and Accessibility permissions are granted
    """

    XHS_URL = "https://www.xiaohongshu.com"

    def __init__(self):
        self.screen = ScreenController()
        self.llm = VisionLLM()
        self.ocr = OCREngine(self.llm)
        self.action_history: list[str] = []
        self._seen_titles: list[str] = []  # Tracks titles for scroll dedup

    # ------------------------------------------------------------------
    # Deduplication helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_title_duplicate(title: str, seen: list[str]) -> bool:
        """Check if *title* is a fuzzy duplicate of any entry in *seen*.

        Uses simple substring matching: if either string contains the other
        (after stripping whitespace and lowering), it is considered a duplicate.
        """
        normed = title.strip().lower()
        if not normed:
            return True  # Blank titles are always "duplicates"
        for s in seen:
            s_normed = s.strip().lower()
            if normed in s_normed or s_normed in normed:
                return True
        return False

    def _dedup_notes(self, notes: list[NoteCard]) -> list[NoteCard]:
        """Return only notes whose titles have not been seen before.

        Updates ``self._seen_titles`` with newly accepted titles.
        """
        unique: list[NoteCard] = []
        for note in notes:
            if not self._is_title_duplicate(note.title, self._seen_titles):
                self._seen_titles.append(note.title)
                unique.append(note)
        return unique

    # ------------------------------------------------------------------
    # State verification
    # ------------------------------------------------------------------

    def verify_state(
        self, window: WindowInfo, expected_state: str,
    ) -> tuple[bool, str]:
        """Capture a screenshot and ask the LLM whether the page matches *expected_state*.

        Returns:
            (success, description) where *success* is True when the LLM
            confirms the page is in the expected state.
        """
        screenshot = self.screen.capture_window(window)
        prompt = (
            f"I just performed an action and expect the page to be in this state:\n"
            f'  "{expected_state}"\n\n'
            "Look at the screenshot and answer:\n"
            "1. Does the current page match the expected state? (yes/no)\n"
            "2. Briefly describe what you actually see.\n\n"
            "Reply in this exact format (no markdown):\n"
            "MATCH: yes  OR  MATCH: no\n"
            "DESCRIPTION: <your description>"
        )
        response = self.llm.analyze_page(screenshot, prompt)
        success = "match: yes" in response.lower()
        return success, response

    # ------------------------------------------------------------------
    # Window lookup
    # ------------------------------------------------------------------

    def _find_xhs_window(self) -> WindowInfo | None:
        """Find the Chrome window with Xiaohongshu open."""
        return self.screen.find_chrome_window("小红书")

    def _capture_and_analyze(self, window: WindowInfo) -> tuple[Image.Image, str]:
        """Capture window screenshot and get page analysis."""
        screenshot = self.screen.capture_window(window)
        analysis = self.llm.analyze_page(screenshot)
        return screenshot, analysis

    def search(self, query: str, max_notes: int = 10) -> SearchResult:
        """Search Xiaohongshu for a query and extract note cards.

        Steps:
        1. Find Chrome window with XHS
        2. Locate and click search box
        3. Type query and press Enter
        4. Wait for results to load
        5. Extract visible note cards
        """
        result = SearchResult(query=query)

        # Find the XHS window
        window = self._find_xhs_window()
        if not window:
            raise RuntimeError(
                "Cannot find Chrome window with Xiaohongshu. "
                "Please open https://www.xiaohongshu.com in Chrome first."
            )

        # Capture current state
        screenshot = self.screen.capture_window(window)

        # Find and click search box
        element = self.llm.locate_element(screenshot, "search input box / search bar")
        if not element or not element.get("found"):
            raise RuntimeError("Cannot find search box on Xiaohongshu page")

        # Convert percentage coords to absolute screen coords
        click_x = window.x + int(element["x"] / 100 * window.width)
        click_y = window.y + int(element["y"] / 100 * window.height)

        self.screen.click(click_x, click_y)
        self.action_history.append(f"Clicked search box at ({click_x}, {click_y})")
        time.sleep(0.3)

        # Clear existing text and type query
        self.screen.hotkey("command", "a")
        self.screen.type_text(query)
        self.action_history.append(f"Typed query: {query}")
        time.sleep(0.2)

        self.screen.press_key("enter")
        self.action_history.append("Pressed Enter to search")
        time.sleep(2)  # Wait for results to load

        # Verify that search results loaded
        ok, desc = self.verify_state(window, f"Xiaohongshu search results page for '{query}'")
        if not ok:
            self.action_history.append(f"State verification warning: {desc}")

        # Capture search results
        screenshot = self.screen.capture_window(window)
        result.screenshot = screenshot

        # Analyze results page
        result.page_description = self.llm.analyze_page(
            screenshot,
            "This is a Xiaohongshu search results page. "
            "List each visible note card with: title, author name, and like count. "
            "Format as a numbered list.",
        )

        # Extract structured data for each note
        notes_data = self.ocr.extract_structured(
            screenshot,
            ["note_titles", "note_authors", "note_likes"],
        )

        # Parse into NoteCard objects
        titles = notes_data.get("note_titles", [])
        if isinstance(titles, list):
            for i, title in enumerate(titles[:max_notes]):
                authors = notes_data.get("note_authors", [])
                likes = notes_data.get("note_likes", [])
                note = NoteCard(
                    title=str(title),
                    author=str(authors[i]) if isinstance(authors, list) and i < len(authors) else "",
                    likes=str(likes[i]) if isinstance(likes, list) and i < len(likes) else None,
                )
                result.notes.append(note)

        return result

    def capture_note_detail(self, note_index: int) -> dict:
        """Click on a specific note and capture its detail page.

        Returns dict with screenshot, title, content, likes, comments summary.
        """
        window = self._find_xhs_window()
        if not window:
            raise RuntimeError("Cannot find Xiaohongshu Chrome window")

        screenshot = self.screen.capture_window(window)

        # Ask Claude to find the nth note card
        element = self.llm.locate_element(
            screenshot,
            f"the note card number {note_index + 1} (counting from top-left, row by row)",
        )

        if not element or not element.get("found"):
            raise RuntimeError(f"Cannot find note #{note_index + 1} on page")

        click_x = window.x + int(element["x"] / 100 * window.width)
        click_y = window.y + int(element["y"] / 100 * window.height)

        self.screen.click(click_x, click_y)
        self.action_history.append(f"Clicked note #{note_index + 1}")
        time.sleep(2)  # Wait for detail page to load

        # Verify that the detail page loaded
        ok, desc = self.verify_state(
            window, f"Xiaohongshu note detail page for note #{note_index + 1}"
        )
        if not ok:
            self.action_history.append(f"State verification warning: {desc}")

        # Capture detail page
        screenshot = self.screen.capture_window(window)

        detail = self.ocr.extract_structured(
            screenshot,
            ["title", "author", "content_text", "likes", "favorites", "comments_count"],
        )
        detail["screenshot"] = screenshot

        return detail

    def browse_note_images(self, max_images: int = 5) -> list[Image.Image]:
        """Navigate through all images in a note detail view.

        Clicks the right arrow to advance through the image carousel,
        capturing a screenshot at each position.  Stops when the last
        image is reached (no right arrow found) or *max_images* have
        been collected.

        Must be called while a note detail page is visible.
        """
        window = self._find_xhs_window()
        if not window:
            raise RuntimeError("Cannot find Xiaohongshu Chrome window")

        images: list[Image.Image] = []

        for i in range(max_images):
            screenshot = self.screen.capture_window(window)
            images.append(screenshot)
            self.action_history.append(f"Captured note image {i + 1}")

            if i >= max_images - 1:
                break

            # Ask the LLM to find the "next image" arrow
            element = self.llm.locate_element(
                screenshot,
                "the right arrow button to go to the next image in the carousel. "
                "If there is no right arrow or we are on the last image, reply with found=false.",
            )

            if not element or not element.get("found"):
                self.action_history.append("No more images (right arrow not found)")
                break

            self.screen.click_relative(window, element["x"], element["y"])
            self.action_history.append(
                f"Clicked next-image arrow at ({element['x']:.1f}%, {element['y']:.1f}%)"
            )
            time.sleep(1)  # Wait for the next image to load

        return images

    def scroll_comments(self, max_scrolls: int = 3) -> list[dict]:
        """Scroll through the comments section of a note detail page.

        At each scroll position the LLM extracts visible comments.
        Returns a deduplicated list of comment dicts with keys:
        ``username``, ``text``, ``likes``.

        Must be called while a note detail page is visible.
        """
        window = self._find_xhs_window()
        if not window:
            raise RuntimeError("Cannot find Xiaohongshu Chrome window")

        seen_keys: set[str] = set()
        all_comments: list[dict] = []

        center_x = window.x + window.width // 2
        center_y = window.y + window.height // 2

        for scroll_round in range(max_scrolls + 1):
            screenshot = self.screen.capture_window(window)

            prompt = (
                "You are looking at a Xiaohongshu note detail page. "
                "List every visible comment in the comments section. "
                "For each comment provide: username, text, likes (number). "
                'Return valid JSON: a list of objects with keys "username", "text", "likes". '
                "If no comments are visible, return an empty list []."
            )
            raw = self.llm.analyze_page(screenshot, prompt)

            json_match = re.search(r"\[.*\]", raw, re.DOTALL)
            if json_match:
                try:
                    parsed = json.loads(json_match.group())
                except json.JSONDecodeError:
                    parsed = []
            else:
                parsed = []

            for comment in parsed:
                if not isinstance(comment, dict):
                    continue
                key = f"{comment.get('username', '')}:{comment.get('text', '')}"
                if key not in seen_keys:
                    seen_keys.add(key)
                    all_comments.append({
                        "username": comment.get("username", ""),
                        "text": comment.get("text", ""),
                        "likes": comment.get("likes", 0),
                    })

            if scroll_round < max_scrolls:
                self.screen.scroll(-5, x=center_x, y=center_y)
                self.action_history.append(
                    f"Scrolled comments (round {scroll_round + 1}/{max_scrolls})"
                )
                time.sleep(1.5)

        return all_comments

    def extract_note_engagement(self, screenshot: Image.Image) -> dict:
        """Extract engagement metrics from a note detail screenshot.

        Uses the LLM to read likes, favorites, comments count, and
        shares from the visible UI.  Returns a dict with integer values
        (0 when a metric is not visible).
        """
        prompt = (
            "You are looking at a Xiaohongshu note detail page. "
            "Extract the engagement metrics visible on screen. "
            "Return ONLY valid JSON with these keys (integer values): "
            '"likes", "favorites", "comments", "shares". '
            "If a metric is not visible, use 0."
        )
        raw = self.llm.analyze_page(screenshot, prompt)

        json_match = re.search(r"\{.*\}", raw, re.DOTALL)
        result: dict[str, int] = {"likes": 0, "favorites": 0, "comments": 0, "shares": 0}
        if json_match:
            try:
                parsed = json.loads(json_match.group())
                for key in result:
                    val = parsed.get(key, 0)
                    try:
                        result[key] = int(val)
                    except (ValueError, TypeError):
                        result[key] = 0
            except json.JSONDecodeError:
                pass

        return result

    def scroll_and_collect(self, rounds: int = 3) -> list[Image.Image]:
        """Scroll down and capture screenshots at each position.

        After each scroll, extracts note titles from the screenshot and
        skips screenshots that contain only already-seen notes (dedup).
        Useful for collecting more notes beyond the initial viewport.
        """
        window = self._find_xhs_window()
        if not window:
            raise RuntimeError("Cannot find Xiaohongshu Chrome window")

        screenshots: list[Image.Image] = []
        center_x = window.x + window.width // 2
        center_y = window.y + window.height // 2

        for i in range(rounds):
            screenshot = self.screen.capture_window(window)

            # Extract titles visible in this viewport for dedup
            notes_data = self.ocr.extract_structured(screenshot, ["note_titles"])
            titles = notes_data.get("note_titles", [])
            if isinstance(titles, list):
                new_notes = [
                    NoteCard(title=str(t), author="")
                    for t in titles
                ]
                unique = self._dedup_notes(new_notes)
            else:
                unique = [None]  # Force inclusion when OCR returns non-list

            # Only keep the screenshot if it has new content
            if unique:
                screenshots.append(screenshot)
            else:
                self.action_history.append(
                    f"Scroll round {i + 1}/{rounds}: skipped (all duplicates)"
                )

            self.screen.scroll(-5, x=center_x, y=center_y)
            self.action_history.append(f"Scrolled down (round {i + 1}/{rounds})")
            time.sleep(1.5)  # Wait for lazy-loaded content

        return screenshots
