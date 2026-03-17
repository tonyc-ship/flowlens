"""XHS-specific browser interaction layer.

Wraps the generic ExtensionBridge with Xiaohongshu DOM extraction commands,
note opening patterns (CDP click for anti-bot avoidance), and page state
detection. This captures all XHS-specific browser "experience" — how to
open notes, extract content, handle overlays, detect anti-bot, etc.

To support a new platform, create a similar browser module (e.g. douyin/browser.py)
that wraps ExtensionBridge with that platform's DOM commands.
"""

from __future__ import annotations

import asyncio
import random

from ..bridge import ExtensionBridge


class XHSBrowser:
    """Xiaohongshu browser interface wrapping generic ExtensionBridge.

    Provides:
    - DOM extraction: search cards, note content, comments, profile info
    - Note opening via CDP mouse click (avoids anti-bot detection)
    - Anti-bot detection and page state awareness
    - Overlay management (open/close note detail modals)
    """

    XHS_REFERER = "https://www.xiaohongshu.com/"

    def __init__(self, bridge: ExtensionBridge):
        self.bridge = bridge

    # ── Delegated generic operations ────────────────────────────
    # Exposed for convenience so callers don't need bridge.bridge.navigate()

    async def navigate(self, url: str, wait_ms: int = 5000) -> dict:
        return await self.bridge.navigate(url, wait_ms)

    async def capture_screenshot(self) -> str:
        return await self.bridge.capture_screenshot()

    async def get_tab_info(self) -> dict:
        return await self.bridge.get_tab_info()

    async def scroll_page(self, pixels: int = 600) -> dict:
        return await self.bridge.scroll_page(pixels)

    # ── XHS Page State Detection ────────────────────────────────

    async def detect_state(self) -> dict:
        """Detect current XHS page state (search, note_detail, profile, etc.)."""
        return await self.bridge.send_command("detect_state")

    async def is_anti_bot_page(self) -> bool:
        """Check if current page shows XHS anti-bot block (404/error)."""
        try:
            tab = await self.bridge.get_tab_info()
            title = tab.get("title", "")
            url = tab.get("url", "")
            return (
                "不见了" in title
                or "Page Isn't Available" in title
                or "404" in url
            )
        except Exception:
            return False

    # ── DOM Extraction Commands ─────────────────────────────────
    # These forward to the XHS content script running on xiaohongshu.com

    async def extract_search_cards(self) -> list[dict]:
        """Extract search result cards from the current page."""
        result = await self.bridge.send_command("extract_search_cards")
        return result.get("cards", [])

    async def extract_note_content(self) -> dict:
        """Extract note title, content, images, etc. from DOM."""
        result = await self.bridge.send_command("extract_note_content")
        return result.get("note", {})

    async def extract_comments(self) -> list[dict]:
        """Extract comments from note detail (deduplicated)."""
        result = await self.bridge.send_command("extract_comments")
        return result.get("comments", [])

    async def extract_profile_info(self) -> dict:
        """Extract user profile data from profile page."""
        result = await self.bridge.send_command("extract_profile_info")
        return result.get("profile", {})

    async def extract_profile_notes(self) -> list[dict]:
        """Extract note cards from user profile page grid."""
        result = await self.bridge.send_command("extract_profile_notes")
        return result.get("notes", [])

    # ── XHS Navigation Actions ──────────────────────────────────

    async def click_card(self, index: int) -> dict:
        """Click a search/profile card by DOM index."""
        return await self.bridge.send_command("click_card", {"index": index})

    async def close_note(self) -> dict:
        """Close the note detail overlay."""
        return await self.bridge.send_command("close_note")

    async def scroll_note(self, pixels: int = 400) -> dict:
        """Scroll within the note detail panel."""
        return await self.bridge.send_command("scroll_note", {"pixels": pixels})

    # ── CDP Note Opening (anti-bot avoidant) ────────────────────
    # XHS blocks direct URL navigation to /explore/{note_id} as bot behavior.
    # Instead, we simulate a real human click on the card cover image using
    # Chrome DevTools Protocol mouse events. This opens the XHS React modal
    # overlay, which is the normal user experience.

    async def open_note_on_profile(self, note_id: str) -> bool:
        """Open a note from profile page via CDP real mouse click.

        Scrolls the card into view, moves mouse to its center, then clicks.
        This triggers XHS's React handler to open the modal overlay,
        indistinguishable from a human click.

        Returns True if overlay opened successfully.
        """
        # Step 1: Scroll card into view (instant, not smooth — avoid coord mismatch)
        scroll_js = f"""
            const cards = document.querySelectorAll('section.note-item, [data-note-id]');
            for (const card of cards) {{
                const link = card.querySelector('a[href]');
                if (link && link.href.includes('{note_id}')) {{
                    card.scrollIntoView({{ behavior: 'instant', block: 'center' }});
                    return {{ ok: true }};
                }}
            }}
            return {{ ok: false, error: 'Card not found' }};
        """
        scroll_result = await self.bridge.run_js(scroll_js)
        sv = scroll_result.get("value", scroll_result)
        if not (isinstance(sv, dict) and sv.get("ok")):
            return False

        await asyncio.sleep(0.5)

        # Step 2: Get accurate bounding rect after scroll settled
        rect_js = f"""
            const cards = document.querySelectorAll('section.note-item, [data-note-id]');
            for (const card of cards) {{
                const link = card.querySelector('a[href]');
                if (link && link.href.includes('{note_id}')) {{
                    const target = card.querySelector('.cover, .cover-ld, img, .note-cover') || card;
                    const rect = target.getBoundingClientRect();
                    return {{
                        ok: true,
                        x: Math.round(rect.left + rect.width / 2),
                        y: Math.round(rect.top + rect.height / 2),
                    }};
                }}
            }}
            return {{ ok: false, error: 'Card not found' }};
        """
        locate_result = await self.bridge.run_js(rect_js)
        value = locate_result.get("value", locate_result)
        if not (isinstance(value, dict) and value.get("ok")):
            return False

        # Step 3: CDP real mouse move + click (human-like timing)
        cx, cy = value["x"], value["y"]
        await self.bridge.mouse_move(cx, cy)
        await asyncio.sleep(0.3 + 0.2 * random.random())
        await self.bridge.click_at(cx, cy)

        # Step 4: Wait for overlay to open and content to load
        return await self.wait_for_overlay(timeout=5.0)

    async def wait_for_overlay(self, timeout: float = 5.0) -> bool:
        """Poll for XHS note overlay to open with content loaded."""
        for _ in range(int(timeout / 0.5)):
            await asyncio.sleep(0.5)
            check = await self.bridge.run_js("""
                const overlay = document.querySelector(
                    '.note-detail-mask, .note-overlay, .note-detail-modal, .note-detail'
                );
                const title = document.querySelector('.note-content .title, #detail-title, .title');
                return {
                    overlay: !!(overlay && overlay.offsetHeight > 0),
                    hasTitle: !!(title && title.textContent.trim()),
                };
            """)
            ov = check.get("value", check)
            if isinstance(ov, dict) and ov.get("overlay") and ov.get("hasTitle"):
                await asyncio.sleep(1)
                return True
        return False

    async def navigate_to_profile(self, user_url_or_id: str) -> str:
        """Navigate to a user's profile page. Accepts URL or user ID.
        Returns the canonical profile URL.
        """
        if user_url_or_id.startswith("http"):
            profile_url = user_url_or_id
        else:
            profile_url = f"https://www.xiaohongshu.com/user/profile/{user_url_or_id}"
        await self.navigate(profile_url, wait_ms=5000)
        await asyncio.sleep(2)
        return profile_url

    async def navigate_to_search(self, keyword: str) -> str:
        """Navigate to XHS search results for a keyword. Returns the URL."""
        import urllib.parse
        encoded = urllib.parse.quote(keyword)
        url = f"https://www.xiaohongshu.com/search_result?keyword={encoded}&source=web_search_result_notes"
        await self.navigate(url, wait_ms=5000)
        await asyncio.sleep(3)
        return url
