"""XHS entity parsing — load-bearing for note extraction pipelines."""

import unittest

from flowlens.platforms.xhs.entities import (
    Comment,
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
