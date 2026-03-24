"""XHS-specific browser interaction layer.

Wraps the generic ExtensionBridge with Xiaohongshu DOM extraction commands,
note opening patterns (CDP click for anti-bot avoidance), and page state
detection. This captures all XHS-specific browser "experience" — how to
open notes, extract content, handle overlays, detect anti-bot, etc.

To support a new platform, create a similar browser module (e.g. douyin/browser.py)
that wraps ExtensionBridge with that platform's DOM commands.

XHS Page States
---------------
1. **homepage** — Grid of recommended note cards, left sidebar
   (首页/发现/发布/通知/我), search bar at top center, XHS red logo top-left.
2. **search_results** — Search query in search bar, filter tabs
   (全部/图文/视频/用户), waterfall grid of matching note cards.
3. **note_detail** — Two forms:
   - Modal overlay: dark semi-transparent bg, white modal. Left panel = image
     carousel, right panel = author info + text content + hashtags + comments.
     Engagement bar at bottom (heart, star, comment, share). Carousel has
     left/right arrows (visible on hover, use keyboard arrows).
   - Full-page: note takes whole page, image left, content + recommendations
     right.
4. **profile_page** — User avatar, display name, XHS ID, bio,
   follower/following/likes counts. Below is grid of published notes.
   SPA: scrolling loads more cards.

State Transitions
-----------------
- homepage -> search_results  (click search box + type query)
- homepage -> note_detail     (click card)
- search_results -> note_detail    (click card -> opens as modal overlay)
- search_results -> search_results (scroll for more, change filter tab, refine query)
- note_detail -> search_results    (press Escape or click X to close modal)
- note_detail -> profile_page      (click author name/avatar)
- note_detail -> note_detail       (arrow keys for carousel, scroll for comments)
- profile_page -> note_detail      (click note card -> opens as modal overlay)
- profile_page -> search_results   (browser back)

Anti-Bot Prior
--------------
- Prefer opening notes from visible search/profile cards over direct `/explore/{note_id}`
  navigation. XHS commonly throttles direct detail-page loads with 404 / scan-on-phone /
  security verification while still allowing human-like in-page modal opens.
- Prefer closing the modal via UI (`X` button or Escape) instead of reloading the search page.
  Reloading adds request pressure and can reorder results, which hurts reproducibility.

DOM Extraction Patterns
-----------------------
- Cards: each card has title text, author name, like count, cover image,
  link with note_id.
- Note content: title (bold/large), author name + avatar, full text body,
  hashtags (#), date, image carousel indicator (e.g. "2/5"), engagement counts.
- Comments: username, text, like count, author replies, timestamps. XHS renders
  comments twice in DOM — dedup by username + text[:30].
- Profile: display name, XHS ID, bio text, follower/following/likes counts,
  note grid.
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
    ANTI_BOT_STATES = {"error_page", "mobile_only_gate", "security_verification"}

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
            state = await self.detect_state()
            if state.get("state") in self.ANTI_BOT_STATES:
                return True
            tab = await self.bridge.get_tab_info()
            title = tab.get("title", "")
            url = tab.get("url", "")
            return "不见了" in title or "Page Isn't Available" in title or "404" in url
        except Exception:
            return False

    @classmethod
    def is_anti_bot_state(cls, state: str | None) -> bool:
        return (state or "") in cls.ANTI_BOT_STATES

    async def wait_for_state(
        self,
        expected: str | set[str] | tuple[str, ...] | list[str],
        *,
        timeout: float = 5.0,
        poll: float = 0.5,
    ) -> dict:
        """Wait until detect_state() reports one of the expected values."""
        targets = {expected} if isinstance(expected, str) else set(expected)
        last_state: dict = {}
        for _ in range(max(1, int(timeout / poll))):
            try:
                last_state = await self.detect_state()
                if last_state.get("state") in targets:
                    return last_state
            except Exception:
                pass
            await asyncio.sleep(poll)
        return last_state

    # ── DOM Extraction Commands ─────────────────────────────────
    # These forward to the XHS content script running on xiaohongshu.com

    async def extract_search_cards(self) -> list[dict]:
        """Extract search result cards from the current page."""
        result = await self.bridge.send_command("extract_search_cards")
        return result.get("cards", [])

    async def extract_search_tabs(self) -> list[dict]:
        """Extract top-level search tabs like 全部 / 图文 / 视频 / 用户."""
        result = await self.bridge.send_command("extract_search_tabs")
        return result.get("tabs", [])

    async def get_search_page_state(self) -> dict:
        """Inspect whether search results are ready, still loading, or empty."""
        return await self.bridge.send_command("get_search_page_state")

    async def click_search_tab(self, label: str) -> dict:
        """Switch the active XHS search tab."""
        return await self.bridge.send_command("click_search_tab", {"label": label})

    async def wait_for_search_results(
        self,
        *,
        preferred_filter: str | None = None,
        timeout_s: float = 20.0,
        poll_s: float = 1.5,
    ) -> dict:
        """Wait until search cards are ready or the page clearly reports no results."""
        if preferred_filter:
            await self.click_search_tab(preferred_filter)
            await asyncio.sleep(1)

        deadline = asyncio.get_running_loop().time() + timeout_s
        last_state: dict = {}
        while asyncio.get_running_loop().time() < deadline:
            try:
                last_state = await self.get_search_page_state()
            except Exception:
                await asyncio.sleep(poll_s)
                continue
            if last_state.get("card_count", 0) > 0 or last_state.get("has_no_results"):
                return last_state
            await asyncio.sleep(poll_s)
        return last_state

    async def extract_note_content(self) -> dict:
        """Extract note title, content, images, etc. from DOM."""
        result = await self.bridge.send_command("extract_note_content")
        return result.get("note", {})

    async def collect_carousel_images(self, max_images: int = 20) -> tuple[list[str], dict]:
        """Flip through all carousel images and collect every unique URL.

        Uses arrow key navigation to trigger XHS lazy-loading of carousel
        slides, collecting image URLs as they appear in the DOM.
        Returns (list of image URLs, debug info dict).
        """
        result = await self.bridge.send_command(
            "collect_carousel_images", {"max_images": max_images}
        )
        return result.get("image_urls", []), result.get("debug", {})

    async def extract_comments(
        self,
        max_comments: int | None = None,
        prefer_hot: bool = True,
    ) -> list[dict]:
        """Extract comments from note detail, optionally ranked by heat."""
        params = {"prefer_hot": prefer_hot}
        if max_comments is not None:
            params["max_comments"] = max_comments
        result = await self.bridge.send_command("extract_comments", params)
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

    async def click_note_link(self, url: str) -> dict:
        """Click a specific search result note by its link."""
        return await self.bridge.send_command("click_note_link", {"url": url})

    async def click_note_by_id(self, note_id: str) -> dict:
        """Click a specific search/profile note by its note_id."""
        return await self.bridge.send_command("click_note_by_id", {"note_id": note_id})

    async def close_note(self) -> dict:
        """Close the note detail overlay."""
        try:
            await self.bridge.press_key("Escape", code="Escape", windows_virtual_key_code=27)
            state = await self.wait_for_state({"search_results", "profile_page", "homepage"}, timeout=2.5)
            if state.get("state") in {"search_results", "profile_page", "homepage"}:
                return {"ok": True, "method": "cdp_escape"}
        except Exception:
            pass
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
        try:
        # Step 1: Scroll card into view (instant, not smooth — avoid coord mismatch)
            scroll_js = f"""
            const cards = document.querySelectorAll('section.note-item, [data-note-id]');
            for (const card of cards) {{
                const link = card.querySelector('a[href]');
                const cardNoteId = card.dataset?.noteId || card.getAttribute('data-note-id') || '';
                if (cardNoteId === '{note_id}' || (link && link.href.includes('{note_id}'))) {{
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
                const cardNoteId = card.dataset?.noteId || card.getAttribute('data-note-id') || '';
                if (cardNoteId === '{note_id}' || (link && link.href.includes('{note_id}'))) {{
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
            if await self.wait_for_overlay(timeout=5.0):
                return True
            state = await self.wait_for_state("note_detail", timeout=3.0)
            return state.get("state") == "note_detail"
        except Exception:
            return False

    async def open_note_on_search(self, note_id: str) -> bool:
        """Open a search result note via CDP real mouse click."""
        if not note_id:
            return False

        try:
            scroll_js = f"""
            const cards = document.querySelectorAll('section.note-item, [data-note-id]');
            for (const card of cards) {{
                const link = card.querySelector('a[href]');
                const cardNoteId = card.dataset?.noteId || card.getAttribute('data-note-id') || '';
                if (cardNoteId === '{note_id}' || (link && link.href.includes('{note_id}'))) {{
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

            rect_js = f"""
            const cards = document.querySelectorAll('section.note-item, [data-note-id]');
            for (const card of cards) {{
                const link = card.querySelector('a[href]');
                const cardNoteId = card.dataset?.noteId || card.getAttribute('data-note-id') || '';
                if (cardNoteId === '{note_id}' || (link && link.href.includes('{note_id}'))) {{
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

            cx = value["x"] + random.randint(-4, 4)
            cy = value["y"] + random.randint(-4, 4)
            await self.bridge.mouse_move(cx, cy)
            await asyncio.sleep(0.2 + 0.15 * random.random())
            await self.bridge.click_at(cx, cy)
            if await self.wait_for_overlay(timeout=5.0):
                return True
            state = await self.wait_for_state("note_detail", timeout=3.0)
            return state.get("state") == "note_detail"
        except Exception:
            return False

    async def wait_for_overlay(self, timeout: float = 5.0) -> bool:
        """Poll for XHS note overlay to open with content loaded."""
        for _ in range(int(timeout / 0.5)):
            await asyncio.sleep(0.5)
            try:
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
            except Exception:
                state = await self.wait_for_state(self.ANTI_BOT_STATES, timeout=0.5, poll=0.25)
                if self.is_anti_bot_state(state.get("state")):
                    return False
                continue
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
