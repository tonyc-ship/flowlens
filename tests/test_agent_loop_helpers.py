"""Agent loop reporting helpers.

`append_note_screenshot_index` adds an XHS "笔记截图索引" section and
de-duplicates entries by note URL + existing inline images. Consolidated
test drives the happy path and both dedup paths at once.
"""

from __future__ import annotations

import unittest

from flowlens.platforms.xhs.agent_profile import append_note_screenshot_index


class AppendNoteScreenshotIndexTest(unittest.TestCase):
    def test_appends_and_dedupes_correctly(self) -> None:
        base_note = {
            "title": "运动轮胎对比",
            "author": "我是光荣纳税人",
            "url": "https://www.xiaohongshu.com/explore/6621370900000000010068e0",
        }
        results = [
            # From topic_scan: first screenshot for this note
            {"notes": [{"entity": {**base_note, "screenshot": "007_topic_scan.png"}}]},
            # From a later read_note: second screenshot for the SAME note — should be dropped
            {"entity": {**base_note, "screenshot": "011_note_detail.png"}},
            # A different note: should be appended
            {"entity": {
                "title": "南港 AR1 体验",
                "author": "车主A",
                "url": "https://www.xiaohongshu.com/explore/abc",
                "screenshot": "003_note_detail.png",
            }},
        ]

        updated = append_note_screenshot_index("# Report\n\nBody.", results)

        # Section header + both unique notes present
        self.assertIn("## 笔记截图索引", updated)
        self.assertIn("![运动轮胎对比](007_topic_scan.png)", updated)
        self.assertIn("![南港 AR1 体验](003_note_detail.png)", updated)
        # Duplicate for the same note URL is suppressed
        self.assertNotIn("011_note_detail.png", updated)

        # And when the report already inlines one of the screenshots, nothing is re-added
        pre_filled = "# Report\n\n![运动轮胎对比](007_topic_scan.png)"
        self.assertEqual(
            append_note_screenshot_index(pre_filled, results[:2]),
            pre_filled,
        )


if __name__ == "__main__":
    unittest.main()
