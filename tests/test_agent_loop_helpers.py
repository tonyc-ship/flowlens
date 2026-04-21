from __future__ import annotations

import unittest

from flowlens.perception.media import (
    BACKEND_KIMI,
    BACKEND_QWEN_CLOUD,
    MediaConfig,
    MediaProcessor,
)
from flowlens.platforms.xhs.agent_profile import append_note_screenshot_index


class AgentLoopReportHelperTest(unittest.TestCase):
    def test_append_missing_note_screenshots_adds_xhs_evidence_section(self) -> None:
        report = "# Report\n\nBody."
        site_results = [
            {
                "action": "read_note",
                "entity": {
                    "title": "南港 AR1 体验",
                    "author": "车主A",
                    "url": "https://www.xiaohongshu.com/explore/abc",
                    "screenshot": "003_note_detail.png",
                },
            }
        ]

        updated = append_note_screenshot_index(report, site_results)

        self.assertIn("## 笔记截图索引", updated)
        self.assertIn("![南港 AR1 体验](003_note_detail.png)", updated)
        self.assertIn("[笔记链接](https://www.xiaohongshu.com/explore/abc)", updated)
        self.assertNotIn("小红书直链经常被风控或限流", updated)

    def test_append_missing_note_screenshots_does_not_duplicate_existing_images(self) -> None:
        report = "# Report\n\n![南港 AR1 体验](003_note_detail.png)"
        site_results = [{"entity": {"title": "南港 AR1 体验", "screenshot": "003_note_detail.png"}}]

        updated = append_note_screenshot_index(report, site_results)

        self.assertEqual(updated, report)

    def test_append_missing_note_screenshots_dedupes_same_note_url(self) -> None:
        report = "# Report\n\nBody."
        site_results = [
            {
                "notes": [
                    {
                        "entity": {
                            "title": "运动轮胎对比",
                            "author": "我是光荣纳税人",
                            "url": "https://www.xiaohongshu.com/explore/6621370900000000010068e0",
                            "screenshot": "007_topic_scan_7_lite.png",
                        }
                    }
                ]
            },
            {
                "entity": {
                    "title": "运动轮胎对比",
                    "author": "我是光荣纳税人",
                    "url": "https://www.xiaohongshu.com/explore/6621370900000000010068e0",
                    "screenshot": "011_note_detail.png",
                }
            },
        ]

        updated = append_note_screenshot_index(report, site_results)

        self.assertIn("007_topic_scan_7_lite.png", updated)
        self.assertNotIn("011_note_detail.png", updated)

    def test_append_missing_note_screenshots_skips_duplicate_when_report_already_has_same_note(self) -> None:
        report = "# Report\n\n![运动轮胎对比](007_topic_scan_7_lite.png)"
        site_results = [
            {
                "notes": [
                    {
                        "entity": {
                            "title": "运动轮胎对比",
                            "author": "我是光荣纳税人",
                            "url": "https://www.xiaohongshu.com/explore/6621370900000000010068e0",
                            "screenshot": "007_topic_scan_7_lite.png",
                        }
                    }
                ],
            },
            {
                "entity": {
                    "title": "运动轮胎对比",
                    "author": "我是光荣纳税人",
                    "url": "https://www.xiaohongshu.com/explore/6621370900000000010068e0",
                    "screenshot": "011_note_detail.png",
                }
            },
        ]

        updated = append_note_screenshot_index(report, site_results)

        self.assertEqual(updated, report)


class MediaProcessorRoutingTest(unittest.TestCase):
    def test_kimi_and_qwen_cloud_models_use_native_media_backends(self) -> None:
        kimi = MediaProcessor(MediaConfig(model="kimi-k2.5"))
        qwen = MediaProcessor(MediaConfig(model="qwen3.6-plus"))

        self.assertEqual(kimi.backend, BACKEND_KIMI)
        self.assertEqual(qwen.backend, BACKEND_QWEN_CLOUD)


if __name__ == "__main__":
    unittest.main()
