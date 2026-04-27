"""WeChat desktop app adapter built on generic macOS window helpers."""

from __future__ import annotations

import re
import time
from pathlib import Path

from PIL import Image

from ...core.desktop import DesktopCapture, DesktopWindowSession
from ...core.ocr_layout import NormalizedRegion, OCRPage
from ...debug import MacOSController, WindowInfo
from ...perception.apple_ocr import AppleOCR
from ...perception.llm import VisionLLM
from .vision_profiles import WECHAT_UI_SIMPLE_CHECK

WECHAT_APP_NAME = "WeChat"

WECHAT_SIDEBAR_REGION = NormalizedRegion(0.02, 0.0, 0.30, 0.95)
WECHAT_SEARCH_REGION = NormalizedRegion(0.05, 0.93, 0.27, 0.99)
WECHAT_HEADER_REGION = NormalizedRegion(0.30, 0.93, 0.75, 0.99)
WECHAT_FULL_TITLE_REGION = NormalizedRegion(0.0, 0.88, 0.30, 0.99)
WECHAT_TIMELINE_REGION = NormalizedRegion(0.32, 0.10, 0.95, 0.90)
WECHAT_FULL_TIMELINE_REGION = NormalizedRegion(0.02, 0.10, 0.98, 0.88)
WECHAT_RIGHT_PANE_REGION = NormalizedRegion(0.30, 0.10, 0.98, 0.95)
WECHAT_SCROLL_X = 0.68
WECHAT_SCROLL_Y = 0.45

_TITLE_COUNTER_RE = re.compile(r"[\(（]\d+[\)）]")
_SIDEBAR_TIME_RE = re.compile(r"(?:\d{1,2}:\d{2}|yesterday|today)", re.IGNORECASE)
_TITLE_NORMALIZE_RE = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff]+")

_WECHAT_MAIN_TOKENS = (
    "Chats",
    "Contacts",
    "Favorites",
    "Moments",
    "搜索",
    "通讯录",
    "收藏",
    "朋友圈",
)
_WECHAT_ENTRY_TOKENS = (
    "Enter Weixin",
    "Switch Account",
    "Transfer files only",
    "进入微信",
    "切换账号",
    "仅传输文件",
)


def normalize_wechat_title(text: str) -> str:
    cleaned = _TITLE_COUNTER_RE.sub("", str(text or ""))
    cleaned = _TITLE_NORMALIZE_RE.sub("", cleaned.casefold())
    return cleaned.strip()


def _usable_title_text(text: str) -> bool:
    text = str(text or "").strip()
    if not text or text.casefold() == "search":
        return False
    return bool(normalize_wechat_title(text))


def _title_sort_key(item) -> tuple[int, int, float]:
    normalized = normalize_wechat_title(item.text)
    return (len(normalized), len(str(item.text or "").strip()), float(item.confidence or 0.0))


class WeChatDesktopApp:
    """High-level WeChat interactions with OCR-first targeting."""

    def __init__(
        self,
        *,
        controller: MacOSController | None = None,
        ocr: AppleOCR | None = None,
        vision: VisionLLM | None = None,
    ):
        self.controller = controller or MacOSController()
        self.ocr = ocr or AppleOCR()
        self.vision = vision
        self.session = DesktopWindowSession(
            WECHAT_APP_NAME,
            controller=self.controller,
            ocr=self.ocr,
        )

    def activate(self) -> None:
        self.session.activate()

    def resolve_window(self) -> WindowInfo:
        return self.session.resolve_window(visible_only=True)

    def capture_main_window(self, path: str | Path) -> DesktopCapture:
        return self.session.capture_to_path(path, visible_only=True)

    def capture_state(self) -> tuple[WindowInfo, Image.Image, OCRPage]:
        return self.session.capture_ocr_page(visible_only=True)

    def _open_state(self, expected_name: str = "") -> dict:
        _, image, page = self.capture_state()
        title = self.read_open_conversation_title(page)
        normalized_expected = normalize_wechat_title(expected_name)
        normalized_title = normalize_wechat_title(title)
        title_matches = not normalized_expected or normalized_expected in normalized_title
        visible = self.conversation_visible(image, page)
        return {
            "opened": bool(visible and title_matches),
            "visible": bool(visible),
            "title": title,
            "title_matches": bool(title_matches),
        }

    def _wait_for_open_state(self, expected_name: str = "", *, timeout_s: float = 3.0) -> dict:
        deadline = time.monotonic() + timeout_s
        last: dict = {"opened": False, "visible": False, "title": "", "title_matches": False}
        while True:
            last = self._open_state(expected_name)
            if last.get("opened"):
                return last
            if time.monotonic() >= deadline:
                return last
            time.sleep(0.25)

    def read_open_conversation_title(self, page: OCRPage | None = None) -> str:
        if page is None:
            _, _, page = self.capture_state()
        candidates = sorted(page.within(WECHAT_HEADER_REGION), key=_title_sort_key, reverse=True)
        for item in candidates:
            text = item.text.strip()
            if _usable_title_text(text):
                return text

        # Full-window title fallback is only safe when the right pane already
        # looks like a chat body; otherwise sidebar rows can be mistaken as titles.
        full_candidates = sorted(page.within(WECHAT_FULL_TITLE_REGION), key=_title_sort_key, reverse=True)
        for item in full_candidates:
            text = item.text.strip()
            if _usable_title_text(text) and self._full_window_chat_like(page):
                return text
        return ""

    def conversation_region(self, page: OCRPage | None = None) -> NormalizedRegion:
        if page is None:
            _, _, page = self.capture_state()
        split_title = page.within(WECHAT_HEADER_REGION)
        return WECHAT_TIMELINE_REGION if split_title else WECHAT_FULL_TIMELINE_REGION

    def conversation_visible(self, image: Image.Image | None = None, page: OCRPage | None = None) -> bool:
        if page is None or image is None:
            _, image, page = self.capture_state()

        title = self.read_open_conversation_title(page)
        if title and page.within(WECHAT_HEADER_REGION):
            region = self.conversation_region(page)
            meaningful = [
                item for item in page.within(region)
                if len(item.text.strip()) >= 4 and item.text.casefold() not in {"search"}
            ]
            if meaningful:
                return True

        if self.vision is None:
            return False
        right_crop = self.session.crop_image_region(image, WECHAT_RIGHT_PANE_REGION)
        response = self.vision.analyze_page(
            right_crop,
            "Does this cropped WeChat right pane show an opened conversation with visible chat messages or bubbles, rather than the empty home view or only the conversation list? Answer YES or NO only.",
            config=WECHAT_UI_SIMPLE_CHECK,
        )
        return response.strip().upper().startswith("Y")

    def open_conversation(self, conversation_name: str) -> dict:
        """Open a specific conversation by OCR-click or search fallback."""

        self.activate()
        self.ensure_main_window_ready()
        target = conversation_name.strip()
        if not target:
            return {"opened": False, "method": "current_conversation", "match": ""}

        current = self._open_state(target)
        if current.get("opened"):
            return {
                "opened": True,
                "method": "already_open",
                "match": current.get("title", ""),
                "title": current.get("title", ""),
            }

        direct = self.session.click_text(target, region=WECHAT_SIDEBAR_REGION)
        if direct is not None:
            state = self._wait_for_open_state(target, timeout_s=2.0)
            if state.get("opened"):
                return {
                    "opened": True,
                    "method": "sidebar_ocr",
                    "match": direct.text,
                    "title": state.get("title", ""),
                }

            # Some WeChat rows OCR as a short text fragment; clicking the text
            # center can miss the row activation target. Retry the whole row.
            self.session.click_relative(0.17, direct.center_y, clicks=2)
            state = self._wait_for_open_state(target, timeout_s=2.5)
            if state.get("opened"):
                return {
                    "opened": True,
                    "method": "sidebar_row_retry",
                    "match": direct.text,
                    "title": state.get("title", ""),
                }

        search = (
            self.session.click_text("Search", region=WECHAT_SEARCH_REGION)
            or self.session.click_text("搜索", region=WECHAT_SEARCH_REGION)
        )
        if search is None:
            self.session.click_relative(0.16, 0.955)
        time.sleep(0.2)
        self.controller.hotkey("command", "a")
        self.controller.paste_text(target)
        time.sleep(0.8)

        # Search can either surface a result row or open the chat on Enter.
        followup = self.session.click_text(target, region=WECHAT_SIDEBAR_REGION) or self.session.click_text(target)
        if followup is not None:
            state = self._wait_for_open_state(target, timeout_s=2.5)
            if not state.get("opened"):
                self.session.click_relative(0.17, followup.center_y, clicks=2)
                state = self._wait_for_open_state(target, timeout_s=2.5)
            if state.get("opened"):
                return {
                    "opened": True,
                    "method": "search_result_ocr",
                    "match": followup.text,
                    "title": state.get("title", ""),
                }

        self.controller.press_key("enter")
        state = self._wait_for_open_state(target, timeout_s=2.5)
        return {
            "opened": bool(state.get("opened")),
            "method": "search_then_ocr",
            "match": followup.text if followup is not None else target,
            "title": state.get("title", ""),
            "visible": state.get("visible", False),
            "title_matches": state.get("title_matches", False),
        }

    def open_first_visible_conversation(self) -> dict:
        self.activate()
        self.ensure_main_window_ready()
        _, _, page = self.capture_state()
        candidates = sorted(
            page.within(WECHAT_SIDEBAR_REGION),
            key=lambda item: (-item.center_y, item.center_x),
        )
        for item in candidates:
            text = item.text.strip()
            if not text:
                continue
            if item.center_y > 0.93:
                continue
            if text.casefold() == "search":
                continue
            if _SIDEBAR_TIME_RE.search(text):
                continue
            if any(token in text for token in ("[", "［", "：", ":")):
                continue
            if len(text) > 24:
                continue
            self.session.click_ocr_line(item)
            time.sleep(0.8)
            return {"opened": True, "method": "first_visible_sidebar", "match": text}
        raise RuntimeError("Could not find any visible WeChat conversation to open.")

    def ensure_conversation_title(self, expected_name: str) -> bool:
        if not expected_name.strip():
            return True
        _, image, page = self.capture_state()
        title = self.read_open_conversation_title(page)
        if normalize_wechat_title(expected_name) in normalize_wechat_title(title):
            return True
        if not self.conversation_visible(image, page):
            return False
        return normalize_wechat_title(expected_name) in normalize_wechat_title(title)

    def _full_window_chat_like(self, page: OCRPage) -> bool:
        right_pane_lines = [
            item for item in page.within(WECHAT_RIGHT_PANE_REGION)
            if len(item.text.strip()) >= 3 and item.text.casefold() not in {"search"}
        ]
        return bool(right_pane_lines)

    def ensure_main_window_ready(self, *, timeout_s: float = 12.0) -> None:
        deadline = time.monotonic() + timeout_s
        attempted_entry = False

        while time.monotonic() < deadline:
            _, image, page = self.capture_state()
            if self._looks_like_main_window(image, page):
                return

            if self._entry_panel_visible(page) and not attempted_entry:
                attempted_entry = True
                enter = (
                    self.session.click_text("Enter Weixin")
                    or self.session.click_text("进入微信")
                )
                if enter is not None:
                    time.sleep(2.0)
                    continue

            time.sleep(0.6)

        raise RuntimeError(
            "WeChat is running, but its main chat window is not visible on the current desktop. "
            "Bring the WeChat chat window to the current Space, then rerun the task."
        )

    def _looks_like_main_window(self, image: Image.Image, page: OCRPage) -> bool:
        window = self.resolve_window()
        if window.width < 500 or window.height < 600:
            return False

        unique_hits = {
            token for token in _WECHAT_MAIN_TOKENS
            if page.best_text_match(token, exact=False) is not None
        }
        if len(unique_hits) >= 2:
            return True

        sidebar_lines = [
            item for item in page.within(WECHAT_SIDEBAR_REGION)
            if item.text.strip() and item.center_y < 0.93
        ]
        search_hit = (
            page.best_text_match("Search", region=WECHAT_SEARCH_REGION, exact=False)
            or page.best_text_match("搜索", region=WECHAT_SEARCH_REGION, exact=False)
        )
        if search_hit is not None and len(sidebar_lines) >= 3:
            return True

        if self.vision is None:
            return False
        response = self.vision.analyze_page(
            image,
            (
                "Does this screenshot show the main WeChat desktop chat window with the "
                "conversation list or an opened chat, rather than Finder, a browser, or the "
                "small entry/login panel? Answer YES or NO only."
            ),
            config=WECHAT_UI_SIMPLE_CHECK,
        )
        return response.strip().upper().startswith("Y")

    def _entry_panel_visible(self, page: OCRPage) -> bool:
        return any(page.best_text_match(token, exact=False) is not None for token in _WECHAT_ENTRY_TOKENS)

    def scroll_history_up(self, *, repeats: int = 10, line_delta: int = 12) -> tuple[int, int]:
        return self.session.scroll_lines(
            line_delta,
            x=WECHAT_SCROLL_X,
            y=WECHAT_SCROLL_Y,
            repeats=repeats,
            visible_only=True,
        )
