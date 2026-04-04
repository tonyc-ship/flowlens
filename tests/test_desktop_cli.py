import unittest

from flowlens.desktop_cli import infer_desktop_task


class DesktopCliTests(unittest.TestCase):
    def test_infer_topic_task(self):
        req = infer_desktop_task("研究护肤干货")
        self.assertEqual(req.kind, "topic_research")
        self.assertEqual(req.topic, "护肤干货")

    def test_infer_creator_task_from_profile_url(self):
        req = infer_desktop_task(
            "拆解这个作者 https://www.xiaohongshu.com/user/profile/665e81660000000003033638"
        )
        self.assertEqual(req.kind, "creator_growth_breakdown")
        self.assertTrue(req.profile_url.endswith("665e81660000000003033638"))

    def test_infer_wechat_summary_task(self):
        req = infer_desktop_task('请总结微信会话 "冬虫夏草" 的聊天记录')
        self.assertEqual(req.kind, "wechat_chat_summary")
        self.assertEqual(req.conversation, "冬虫夏草")


if __name__ == "__main__":
    unittest.main()
