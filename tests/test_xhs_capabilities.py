from __future__ import annotations

import unittest

from clawvision.agent.xhs.capabilities import (
    capability_catalog,
    deep_note_plan,
    lite_note_plan,
)


class XHSCapabilitiesTest(unittest.TestCase):
    def test_capability_catalog_contains_expected_core_tools(self):
        names = {cap.name for cap in capability_catalog()}
        self.assertIn("xhs.note.open_basic", names)
        self.assertIn("xhs.note.sample_comments", names)
        self.assertIn("xhs.note.video_audio", names)

    def test_lite_and_deep_plans_have_expected_cost_profiles(self):
        lite = lite_note_plan()
        deep = deep_note_plan()
        self.assertFalse(lite.use_media)
        self.assertTrue(deep.use_media)
        self.assertLess(lite.estimated_latency_s[0], deep.estimated_latency_s[0])
        self.assertIn("media", deep.requested_sections)
        self.assertNotIn("media", lite.requested_sections)


if __name__ == "__main__":
    unittest.main()
