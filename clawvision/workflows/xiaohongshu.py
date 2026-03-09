"""Xiaohongshu (Little Red Book) specific workflows.

Automates research tasks on xiaohongshu.com web version
using screen-level control and visual understanding.
"""

from __future__ import annotations

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

        # Capture detail page
        screenshot = self.screen.capture_window(window)

        detail = self.ocr.extract_structured(
            screenshot,
            ["title", "author", "content_text", "likes", "favorites", "comments_count"],
        )
        detail["screenshot"] = screenshot

        return detail

    def scroll_and_collect(self, rounds: int = 3) -> list[Image.Image]:
        """Scroll down and capture screenshots at each position.

        Useful for collecting more notes beyond the initial viewport.
        """
        window = self._find_xhs_window()
        if not window:
            raise RuntimeError("Cannot find Xiaohongshu Chrome window")

        screenshots = []
        center_x = window.x + window.width // 2
        center_y = window.y + window.height // 2

        for i in range(rounds):
            screenshot = self.screen.capture_window(window)
            screenshots.append(screenshot)
            self.screen.scroll(-5, x=center_x, y=center_y)
            self.action_history.append(f"Scrolled down (round {i + 1}/{rounds})")
            time.sleep(1.5)  # Wait for lazy-loaded content

        return screenshots
