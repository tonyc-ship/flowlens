from __future__ import annotations

import unittest

from flowlens.platforms.xhs.processor import XHSSiteAdapter


class XHSSearchTransitionTest(unittest.TestCase):
    def test_visible_keyword_beats_stale_url_keyword(self) -> None:
        state = {
            "page_state": "search_results",
            "input_keyword": "南港AR1轮胎",
            "url_keyword": "小红书网页版",
            "card_count": 16,
            "tabs": ["全部", "图文", "视频"],
            "loading": False,
        }

        self.assertTrue(XHSSiteAdapter._search_transition_ok(state, "南港AR1轮胎"))

    def test_mismatched_visible_keyword_is_rejected(self) -> None:
        state = {
            "page_state": "search_results",
            "input_keyword": "小红书网页版",
            "url_keyword": "南港AR1轮胎",
            "card_count": 16,
            "tabs": ["全部", "图文", "视频"],
            "loading": False,
        }

        self.assertFalse(XHSSiteAdapter._search_transition_ok(state, "南港AR1轮胎"))


if __name__ == "__main__":
    unittest.main()
