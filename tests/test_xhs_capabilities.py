from __future__ import annotations

import unittest

from flowlens.platforms.xhs.capabilities import (
    capability_catalog,
    deep_note_plan,
    lite_note_plan,
    plan_for_level,
)
from flowlens.platforms.xhs.spec import entity_schema_catalog, load_xhs_spec


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

    def test_plan_for_level_allows_overrides(self):
        plan = plan_for_level("deep", include_media=False, include_comments=False)
        self.assertFalse(plan.use_media)
        self.assertFalse(plan.include_comments)

    def test_entities_and_capabilities_are_declared_in_yaml(self):
        spec = load_xhs_spec()
        self.assertIn("entities", spec)
        self.assertIn("capabilities", spec)
        schemas = entity_schema_catalog()
        self.assertIn("note", schemas)
        self.assertTrue(any(field.name == "title" for field in schemas["note"].key_fields))


if __name__ == "__main__":
    unittest.main()
