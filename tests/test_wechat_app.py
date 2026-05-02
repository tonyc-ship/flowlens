"""WeChat desktop adapter tests."""

from __future__ import annotations

import unittest

from flowlens.core.ocr_layout import OCRLine, OCRPage
from flowlens.platforms.wechat.app import WeChatDesktopApp
from flowlens.platforms.wechat.parser import WeChatConversationParser
from flowlens.platforms.wechat.models import WeChatParsedCapture
from flowlens.workflows.wechat.task_runner import _best_conversation_title


class WeChatTitleParsingTest(unittest.TestCase):
    def test_title_counter_does_not_hide_group_name(self) -> None:
        app = object.__new__(WeChatDesktopApp)
        page = OCRPage(
            lines=(
                OCRLine(text="X mcp（三群）", confidence=0.30, x=0.33, y=0.95, w=0.07, h=0.03),
                OCRLine(text="（158）", confidence=0.50, x=0.42, y=0.95, w=0.04, h=0.03),
                OCRLine(text="Search", confidence=1.00, x=0.10, y=0.95, w=0.08, h=0.03),
            ),
            width_px=1000,
            height_px=800,
        )

        self.assertEqual(app.read_open_conversation_title(page), "X mcp（三群）")

    def test_sidebar_with_search_counts_as_main_window(self) -> None:
        app = object.__new__(WeChatDesktopApp)
        app.resolve_window = lambda: type("Window", (), {"width": 1000, "height": 800})()
        page = OCRPage(
            lines=(
                OCRLine(text="Search", confidence=1.00, x=0.08, y=0.95, w=0.08, h=0.03),
                OCRLine(text="x-mcp（三..", confidence=0.30, x=0.12, y=0.82, w=0.08, h=0.03),
                OCRLine(text="Official Accounts", confidence=0.50, x=0.12, y=0.75, w=0.10, h=0.03),
                OCRLine(text="Vibe Friends", confidence=0.50, x=0.12, y=0.68, w=0.10, h=0.03),
            ),
            width_px=1000,
            height_px=800,
        )

        self.assertTrue(app._looks_like_main_window(None, page))

    def test_parser_title_ignores_counter_fragment(self) -> None:
        parser = object.__new__(WeChatConversationParser)
        page = OCRPage(
            lines=(
                OCRLine(text="（158）", confidence=0.50, x=0.42, y=0.95, w=0.04, h=0.03),
                OCRLine(text="x-mcp（三群）", confidence=0.30, x=0.33, y=0.95, w=0.08, h=0.03),
            ),
            width_px=1000,
            height_px=800,
        )

        self.assertEqual(parser._read_title(page), "x-mcp（三群）")

    def test_best_conversation_title_does_not_use_last_counter(self) -> None:
        captures = [
            WeChatParsedCapture(
                capture_index=0,
                screenshot_path="",
                conversation_title="x-mcp（三群）",
                parser_mode="ocr_layout",
                ocr_line_count=0,
                page_signature="",
            ),
            WeChatParsedCapture(
                capture_index=1,
                screenshot_path="",
                conversation_title="（158）",
                parser_mode="ocr_layout",
                ocr_line_count=0,
                page_signature="",
            ),
        ]

        self.assertEqual(_best_conversation_title(captures), "x-mcp（三群）")


if __name__ == "__main__":
    unittest.main()
