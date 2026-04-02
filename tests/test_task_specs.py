from __future__ import annotations

import unittest

from clawvision.reasoning.tasks import (
    TaskKind,
    make_creator_growth_breakdown_task,
    make_topic_research_task,
    make_wechat_chat_summary_task,
)


class TaskSpecsTest(unittest.TestCase):
    def test_topic_research_task_contains_topic_and_kind(self):
        task = make_topic_research_task("护肤干货", preset_keywords=["护肤干货"])
        self.assertEqual(task.kind, TaskKind.TOPIC_RESEARCH)
        self.assertEqual(task.payload["topic"], "护肤干货")
        self.assertIn("护肤干货", task.to_prompt())
        self.assertTrue(task.slug())

    def test_creator_growth_task_contains_profile_url(self):
        url = "https://www.xiaohongshu.com/user/profile/123"
        task = make_creator_growth_breakdown_task(url, creator_name="测试作者")
        self.assertEqual(task.kind, TaskKind.CREATOR_GROWTH_BREAKDOWN)
        self.assertEqual(task.payload["profile_url"], url)
        self.assertIn("测试作者", task.to_prompt())

    def test_wechat_summary_task_contains_conversation(self):
        task = make_wechat_chat_summary_task("冬虫夏草")
        self.assertEqual(task.kind, TaskKind.WECHAT_CHAT_SUMMARY)
        self.assertEqual(task.payload["conversation"], "冬虫夏草")
        self.assertIn("微信会话总结", task.title)


if __name__ == "__main__":
    unittest.main()
