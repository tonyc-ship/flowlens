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

    def read_open_conversation_title(self, page: OCRPage | None = None) -> str:
        if page is None:
            _, _, page = self.capture_state()
        candidates = sorted(
            page.within(WECHAT_HEADER_REGION),
            key=lambda item: (-item.confidence, -(len(item.text))),
        )
        for item in candidates:
            text = item.text.strip()
            if text and text.casefold() not in {"search"}:
                return text

        # Full-window title fallback is only safe when the right pane already
        # looks like a chat body; otherwise sidebar rows can be mistaken as titles.
        full_candidates = sorted(
            page.within(WECHAT_FULL_TITLE_REGION),
            key=lambda item: (-item.confidence, -(len(item.text))),
        )
        for item in full_candidates:
            text = item.text.strip()
            if text and text.casefold() not in {"search"} and self._full_window_chat_like(page):
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

        direct = self.session.click_text(target, region=WECHAT_SIDEBAR_REGION)
        if direct is not None:
            time.sleep(1.0)
            _, image, page = self.capture_state()
            if self.conversation_visible(image, page):
                return {"opened": True, "method": "sidebar_ocr", "match": direct.text}

        search = (
            self.session.click_text("Search", region=WECHAT_SEARCH_REGION)
            or self.session.click_text("搜索", region=WECHAT_SEARCH_REGION)
        )
        if search is None:
            self.session.click_relative(0.16, 0.955)
        time.sleep(0.2)
        self.controller.hotkey("command", "a")
        self.controller.paste_text(target)
        self.controller.press_key("enter")
        time.sleep(1.5)

        # Search can either open the chat directly or surface a result row.
        followup = self.session.click_text(target)
        if followup is not None:
            time.sleep(0.8)
        _, image, page = self.capture_state()
        return {
            "opened": self.conversation_visible(image, page) and self.ensure_conversation_title(target),
            "method": "search_then_ocr",
            "match": followup.text if followup is not None else target,
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

    def scroll_history_up(self, *, repeats: int = 6, line_delta: int = 8) -> tuple[int, int]:
        return self.session.scroll_lines(
            line_delta,
            x=WECHAT_SCROLL_X,
            y=WECHAT_SCROLL_Y,
            repeats=repeats,
            visible_only=True,
        )
