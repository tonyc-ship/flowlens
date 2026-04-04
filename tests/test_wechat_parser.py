from __future__ import annotations

import json
import unittest

from PIL import Image

from flowlens.core.ocr_layout import OCRPage
from flowlens.platforms.wechat.parser import WeChatConversationParser


class _FakeVision:
    def __init__(self, *, two_b_payload: dict, nine_b_payload: dict | None = None):
        self.two_b_payload = two_b_payload
        self.nine_b_payload = nine_b_payload

    def analyze_page(self, _image, _prompt, *, config):
        if getattr(config, "local_model_name", "") == "Qwen3.5-2B-6bit":
            return json.dumps(self.two_b_payload, ensure_ascii=False)
        payload = self.nine_b_payload if self.nine_b_payload is not None else self.two_b_payload
        return json.dumps(payload, ensure_ascii=False)


class WeChatParserTests(unittest.TestCase):
    def test_ocr_layout_parsing_filters_overlay_and_normalizes_dates(self) -> None:
        page = OCRPage.from_results(
            [
                {"text": "冬虫夏草（4）", "confidence": 0.9, "bbox": {"x": 0.02, "y": 0.90, "w": 0.12, "h": 0.03}},
                {"text": "3/916:00", "confidence": 0.9, "bbox": {"x": 0.45, "y": 0.64, "w": 0.10, "h": 0.03}},
                {"text": "wf", "confidence": 0.9, "bbox": {"x": 0.115, "y": 0.57, "w": 0.03, "h": 0.02}},
                {"text": "这种菜真不如家里吃的好", "confidence": 0.9, "bbox": {"x": 0.137, "y": 0.52, "w": 0.24, "h": 0.03}},
                {"text": "我们上次去参观月子中心，给我们吃的试吃餐都很", "confidence": 0.9, "bbox": {"x": 0.365, "y": 0.30, "w": 0.49, "h": 0.03}},
                {"text": "好", "confidence": 0.9, "bbox": {"x": 0.363, "y": 0.27, "w": 0.03, "h": 0.02}},
                {"text": "K Go to the latest message", "confidence": 0.9, "bbox": {"x": 0.677, "y": 0.23, "w": 0.26, "h": 0.03}},
            ],
            size_px=(2004, 1518),
        )
        parser = WeChatConversationParser()

        parsed = parser.parse_capture(
            capture_index=0,
            screenshot_path="capture.jpg",
            image=Image.new("RGB", (2004, 1518), "white"),
            ocr_page=page,
        )

        self.assertEqual(parsed.parser_mode, "ocr_layout")
        self.assertEqual(parsed.date_markers, ["3/9 16:00"])
        self.assertEqual(len(parsed.messages), 2)
        self.assertEqual(parsed.messages[0].speaker, "wf")
        self.assertEqual(parsed.messages[0].side, "left")
        self.assertEqual(parsed.messages[1].speaker, "self")
        self.assertEqual(parsed.messages[1].side, "right")
        self.assertIn("都很\n好", parsed.messages[1].text)
        self.assertFalse(any("latest message" in item.text.lower() for item in parsed.messages))

    def test_hybrid_parser_uses_9b_when_2b_side_assignment_is_suspicious(self) -> None:
        page = OCRPage.from_results(
            [
                {"text": "冬虫夏草（4）", "confidence": 0.9, "bbox": {"x": 0.02, "y": 0.90, "w": 0.12, "h": 0.03}},
                {"text": "3/9 15:53", "confidence": 0.9, "bbox": {"x": 0.45, "y": 0.82, "w": 0.10, "h": 0.03}},
                {"text": "wf", "confidence": 0.9, "bbox": {"x": 0.115, "y": 0.75, "w": 0.03, "h": 0.02}},
                {"text": "这种菜真不如家里吃的好", "confidence": 0.9, "bbox": {"x": 0.137, "y": 0.70, "w": 0.24, "h": 0.03}},
                {"text": "3/916:00", "confidence": 0.9, "bbox": {"x": 0.45, "y": 0.64, "w": 0.10, "h": 0.03}},
                {"text": "温柔的萌萌", "confidence": 0.9, "bbox": {"x": 0.115, "y": 0.57, "w": 0.09, "h": 0.02}},
                {"text": "这个是一个朋友去的七万的月子中她吃的饭", "confidence": 0.9, "bbox": {"x": 0.137, "y": 0.52, "w": 0.42, "h": 0.03}},
            ],
            size_px=(2004, 1518),
        )
        vision = _FakeVision(
            two_b_payload={
                "messages": [
                    {"speaker": "wf", "side": "left", "kind": "text", "text": "这种菜真不如家里吃的好"},
                    {"speaker": "self", "side": "right", "kind": "text", "text": "这个是一个朋友去的七万的月子中她吃的饭"},
                ],
                "date_markers": ["3/9 15:53", "3/9 16:00"],
                "quality": "good",
            },
            nine_b_payload={
                "messages": [
                    {"speaker": "wf", "side": "left", "kind": "text", "text": "这种菜真不如家里吃的好"},
                    {"speaker": "温柔的萌萌", "side": "left", "kind": "text", "text": "这个是一个朋友去的七万的月子中她吃的饭"},
                ],
                "date_markers": ["3/9 15:53", "3/9 16:00"],
                "quality": "good",
            },
        )
        parser = WeChatConversationParser(vision=vision)

        parsed = parser.parse_capture(
            capture_index=4,
            screenshot_path="capture_04.jpg",
            image=Image.new("RGB", (2004, 1518), "white"),
            ocr_page=page,
        )

        self.assertEqual(parsed.parser_mode, "vision_fallback_9b")
        self.assertEqual([item.side for item in parsed.messages], ["left", "left"])
        self.assertEqual(parsed.messages[1].speaker, "温柔的萌萌")

    def test_hybrid_parser_maps_generic_2b_speaker_to_ocr_labels(self) -> None:
        page = OCRPage.from_results(
            [
                {"text": "冬虫夏草（4）", "confidence": 0.9, "bbox": {"x": 0.02, "y": 0.90, "w": 0.12, "h": 0.03}},
                {"text": "温柔的萌萌", "confidence": 0.9, "bbox": {"x": 0.115, "y": 0.72, "w": 0.09, "h": 0.02}},
                {"text": "变美喷雾", "confidence": 0.9, "bbox": {"x": 0.164, "y": 0.53, "w": 0.12, "h": 0.03}},
                {"text": "温柔的萌萌", "confidence": 0.9, "bbox": {"x": 0.115, "y": 0.45, "w": 0.09, "h": 0.02}},
                {"text": "给小孩喷一点", "confidence": 0.9, "bbox": {"x": 0.137, "y": 0.40, "w": 0.13, "h": 0.03}},
                {"text": "温柔的萌萌", "confidence": 0.9, "bbox": {"x": 0.115, "y": 0.33, "w": 0.09, "h": 0.02}},
                {"text": "嘻嘻", "confidence": 0.9, "bbox": {"x": 0.135, "y": 0.28, "w": 0.05, "h": 0.03}},
                {"text": "我看着都挺像的", "confidence": 0.9, "bbox": {"x": 0.671, "y": 0.79, "w": 0.17, "h": 0.03}},
            ],
            size_px=(2004, 1518),
        )
        vision = _FakeVision(
            two_b_payload={
                "messages": [
                    {"speaker": "self", "side": "right", "kind": "text", "text": "我看着都挺像的😂"},
                    {"speaker": "左侧", "side": "left", "kind": "text", "text": "变美喷雾"},
                    {"speaker": "左侧", "side": "left", "kind": "text", "text": "给小孩喷一点"},
                    {"speaker": "左侧", "side": "left", "kind": "text", "text": "嘻嘻"},
                ],
                "date_markers": [],
                "quality": "good",
            }
        )
        parser = WeChatConversationParser(vision=vision)

        parsed = parser.parse_capture(
            capture_index=11,
            screenshot_path="capture_11.jpg",
            image=Image.new("RGB", (2004, 1518), "white"),
            ocr_page=page,
        )

        self.assertEqual(parsed.parser_mode, "vision_layout_2b")
        self.assertEqual(parsed.messages[0].speaker, "self")
        self.assertTrue(all(item.speaker == "温柔的萌萌" for item in parsed.messages[1:]))


if __name__ == "__main__":
    unittest.main()
