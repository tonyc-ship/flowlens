import unittest

from clawvision.agent.xhs.entities import (
    Comment,
    NoteEntity,
    NoteType,
    VideoInfo,
    parse_count_text,
)


class XHSEntityTests(unittest.TestCase):
    def test_parse_count_text_supports_common_units(self):
        self.assertEqual(parse_count_text("1.2万"), 12_000)
        self.assertEqual(parse_count_text("3.4k"), 3_400)
        self.assertEqual(parse_count_text("2,345"), 2_345)
        self.assertEqual(parse_count_text(""), 0)

    def test_merge_comments_prefers_hotter_and_richer_comment(self):
        duplicate_a = Comment.from_dom_dict(
            {
                "username": "露营控",
                "text": "这个卡式炉真的更适合新手",
                "likes": "12",
                "reply_count": 0,
            }
        )
        duplicate_b = Comment.from_dom_dict(
            {
                "username": "露营控",
                "text": "这个卡式炉真的更适合新手",
                "likes": "35",
                "is_author_reply": True,
                "sub_comments": [
                    {"username": "作者", "text": "同意，这个最稳", "likes": "3"},
                ],
            }
        )
        another = Comment.from_dom_dict(
            {
                "username": "徒步党",
                "text": "平折推车真的很占地方",
                "likes": "28",
            }
        )

        merged = NoteEntity.merge_comments([duplicate_a, duplicate_b, another])

        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0].username, "露营控")
        self.assertEqual(merged[0].like_count, 35)
        self.assertTrue(merged[0].is_author_reply)
        self.assertEqual(len(merged[0].sub_comments), 1)
        self.assertGreater(merged[0].heat_score, merged[1].heat_score)

    def test_note_refresh_derived_fields_extracts_core_signals(self):
        note = NoteEntity(
            note_type=NoteType.IMAGE,
            title="新手露营装备清单，建议和不建议一次说清",
            content=(
                "建议先买卡式炉和桌子，预算¥299就够。\n"
                "不建议新手直接买大帐篷，真的很占地方。\n"
                "评论区见链接，记得收藏。\n"
                "1. 卡式炉更适合新手。"
            ),
            hashtags=["#露营装备", "#新手攻略"],
        )

        note.refresh_derived_fields()

        self.assertIn("checklist", note.format_hints)
        self.assertIn("comparison", note.format_hints)
        self.assertIn("¥299", note.price_mentions)
        self.assertTrue(any("评论区见链接" in phrase for phrase in note.cta_phrases))
        self.assertTrue(any("卡式炉更适合新手" in point for point in note.key_points))

    def test_video_info_prefers_downloadable_source_and_requires_transcript(self):
        video = VideoInfo(
            url="blob:https://www.xiaohongshu.com/123",
            source_urls=[
                "https://sns-video.xiaohongshu.com/example/index.m3u8",
                "https://sns-video.xiaohongshu.com/example/video.mp4",
            ],
            poster_description="露营博主在草地上演示装备",
        )

        self.assertTrue(video.best_source_url().endswith(".mp4"))
        self.assertTrue(video.best_download_url().endswith(".mp4"))
        self.assertFalse(video.is_complete)

        video.transcript = "这条视频主要在讲新手露营装备怎么选。"
        self.assertTrue(video.is_complete)


if __name__ == "__main__":
    unittest.main()
