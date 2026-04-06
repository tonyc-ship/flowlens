from __future__ import annotations

import unittest

from PIL import Image

from flowlens.core.ocr_layout import OCRPage
from flowlens.debug.macos import WindowInfo
from flowlens.platforms.wechat.app import WeChatDesktopApp, normalize_wechat_title


class _FakeController:
    def __init__(self, *, width: int = 1002, height: int = 1041) -> None:
        self.window = WindowInfo(
            window_id=1,
            owner="WeChat",
            title="Weixin",
            x=0,
            y=0,
            width=width,
            height=height,
            layer=0,
            on_screen=True,
            capture_backend="region",
        )

    def best_window_for_app(self, _app_name: str, *, title_contains=None, visible_only: bool = False):
        del title_contains, visible_only
        return self.window

    def activate_app(self, _app_name: str) -> None:
        return None

    def open_app(self, _app_name: str) -> None:
        return None


class WeChatAppTests(unittest.TestCase):
    def test_normalize_wechat_title_ignores_separators_and_case(self) -> None:
        variants = ["x-mcp", "x_mcp", "x mcp", "X-MCP", "x（4）-mcp"]
        normalized = {normalize_wechat_title(item) for item in variants}
        self.assertEqual(normalized, {"xmcp"})

    def test_entry_panel_detection_recognizes_enter_weixin_prompt(self) -> None:
        app = WeChatDesktopApp(controller=_FakeController(), vision=None)
        page = OCRPage.from_results(
            [
                {"text": "Enter Weixin", "confidence": 0.9, "bbox": {"x": 0.38, "y": 0.54, "w": 0.18, "h": 0.04}},
                {"text": "Transfer files only", "confidence": 0.9, "bbox": {"x": 0.34, "y": 0.42, "w": 0.26, "h": 0.04}},
            ],
            size_px=(560, 760),
        )

        self.assertTrue(app._entry_panel_visible(page))

    def test_main_window_detection_uses_sidebar_tokens(self) -> None:
        app = WeChatDesktopApp(controller=_FakeController(), vision=None)
        page = OCRPage.from_results(
            [
                {"text": "Chats", "confidence": 0.9, "bbox": {"x": 0.04, "y": 0.86, "w": 0.05, "h": 0.03}},
                {"text": "Contacts", "confidence": 0.9, "bbox": {"x": 0.04, "y": 0.74, "w": 0.08, "h": 0.03}},
                {"text": "Search", "confidence": 0.9, "bbox": {"x": 0.06, "y": 0.93, "w": 0.08, "h": 0.03}},
            ],
            size_px=(2004, 1518),
        )

        self.assertTrue(app._looks_like_main_window(Image.new("RGB", (2004, 1518), "white"), page))

    def test_main_window_detection_rejects_small_entry_panel(self) -> None:
        app = WeChatDesktopApp(controller=_FakeController(width=280, height=380), vision=None)
        page = OCRPage.from_results(
            [
                {"text": "Enter Weixin", "confidence": 0.9, "bbox": {"x": 0.38, "y": 0.54, "w": 0.18, "h": 0.04}},
            ],
            size_px=(560, 760),
        )

        self.assertFalse(app._looks_like_main_window(Image.new("RGB", (560, 760), "white"), page))


if __name__ == "__main__":
    unittest.main()
