"""XHS entity parsing — load-bearing for note extraction pipelines."""

import unittest

from socai.platforms.xhs.entities import (
    Comment,
    ImageInfo,
    NoteEntity,
    NoteType,
    parse_count_text,
)


class XHSEntityTests(unittest.TestCase):
    def test_parse_counts_and_note_signals_and_comment_merge(self) -> None:
        # Numeric parsing of Chinese unit suffixes.
        self.assertEqual(parse_count_text("1.2万"), 12_000)
        self.assertEqual(parse_count_text("3.4k"), 3_400)
        self.assertEqual(parse_count_text("2,345"), 2_345)
        self.assertEqual(parse_count_text(""), 0)

        # Derived signals (key points, CTA, price, format hints).
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
        self.assertIn("¥299", note.price_mentions)
        self.assertTrue(any("评论区见链接" in p for p in note.cta_phrases))
        self.assertTrue(any("卡式炉更适合新手" in p for p in note.key_points))

        title_only = NoteEntity(title="只有标题")
        title_only.requested_sections = ("content",)
        self.assertFalse(title_only.completeness["content"])

        loading_only = NoteEntity.from_dom_dict({
            "title": "还在加载的笔记",
            "content": "刚刚\n加载中",
        })
        loading_only.requested_sections = ("content",)
        self.assertEqual(loading_only.content, "")
        self.assertFalse(loading_only.completeness["content"])

        media_note = NoteEntity(
            title="图片海报",
            content="建议参加赛道日活动。",
            images=[ImageInfo(ocr_text="1. 全长1400米 左弯6个 右弯4个")],
        )
        media_note.refresh_derived_fields()
        self.assertTrue(any("建议参加赛道日活动" in p for p in media_note.key_points))
        self.assertTrue(any("全长1400米" in p for p in media_note.media_key_points))

        debug_note = NoteEntity.from_dom_dict({
            "title": "带调试信息",
            "content": "正文",
            "extraction_debug": {"content_source": "root_text_after_title"},
        })
        self.assertEqual(debug_note.to_tool_dict()["extraction_debug"]["content_source"], "root_text_after_title")

        stale_note = NoteEntity.from_dom_dict({
            "note_id": "abc123",
            "title": "重复打开的笔记",
            "content": "这是正文",
            "_stale_warning": "same note as previous extract",
        })
        self.assertEqual(stale_note.stale_warning, "same note as previous extract")
        self.assertEqual(stale_note.to_tool_dict()["stale_warning"], "same note as previous extract")

        tokenized_url_note = NoteEntity.from_dom_dict({
            "note_id": "66f4fdb4000000001a022c8f",
            "url": (
                "https://www.xiaohongshu.com/explore/66f4fdb4000000001a022c8f"
                "?xsec_token=ABzs0MvXNkEdW7z5tdSvtxFlzTY-WPq0lctBGIQTlzWYo%3D"
                "&xsec_source=pc_search&source=web_explore_feed"
            ),
            "title": "保留真实帖子链接",
            "content": "正文",
        })
        self.assertIn("xsec_token=", tokenized_url_note.url)

        search_overlay_note = NoteEntity.from_dom_dict({
            "note_id": "66f4fdb4000000001a022c8f",
            "url": "https://www.xiaohongshu.com/search_result?keyword=%E5%B0%9A%E9%85%B7",
            "title": "搜索页覆盖层",
            "content": "正文",
        })
        self.assertEqual(
            search_overlay_note.url,
            "https://www.xiaohongshu.com/explore/66f4fdb4000000001a022c8f",
        )

        # Dedup keeps the richer of two near-duplicate comments.
        a = Comment.from_dom_dict({"username": "露营控", "text": "这个卡式炉真的更适合新手",
                                    "likes": "12", "reply_count": 0})
        b = Comment.from_dom_dict({"username": "露营控", "text": "这个卡式炉真的更适合新手",
                                    "likes": "35", "is_author_reply": True,
                                    "sub_comments": [{"username": "作者", "text": "同意", "likes": "3"}]})
        other = Comment.from_dom_dict({"username": "徒步党", "text": "平折推车真的很占地方",
                                        "likes": "28"})
        merged = NoteEntity.merge_comments([a, b, other])
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0].like_count, 35)
        self.assertTrue(merged[0].is_author_reply)


if __name__ == "__main__":
    unittest.main()
