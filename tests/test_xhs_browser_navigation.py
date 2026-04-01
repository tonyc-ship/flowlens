from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, mock

from clawvision.platforms.xhs.browser import XHSBrowser


class XHSBrowserNavigationTest(IsolatedAsyncioTestCase):
    async def test_navigate_to_search_prefers_visible_search_ui(self) -> None:
        bridge = SimpleNamespace()
        browser = XHSBrowser(bridge)
        browser.get_tab_info = mock.AsyncMock(side_effect=[{"url": "https://www.xiaohongshu.com/explore"}, {"url": "https://www.xiaohongshu.com/search_result?keyword=%E6%8A%A4%E8%82%A4"}])
        browser.detect_state = mock.AsyncMock(return_value={"state": "homepage"})
        browser.navigate = mock.AsyncMock()
        browser.submit_search_query = mock.AsyncMock(return_value={"ok": True})
        browser.wait_for_search_results = mock.AsyncMock(return_value={"page_state": "search_results", "card_count": 12})

        url = await browser.navigate_to_search("护肤")

        self.assertIn("search_result", url)
        browser.submit_search_query.assert_awaited_once_with("护肤")
        browser.navigate.assert_not_awaited()

    async def test_navigate_to_search_falls_back_when_submit_does_not_reach_search_results(self) -> None:
        bridge = SimpleNamespace()
        browser = XHSBrowser(bridge)
        browser.get_tab_info = mock.AsyncMock(return_value={"url": "https://www.xiaohongshu.com/explore"})
        browser.detect_state = mock.AsyncMock(return_value={"state": "homepage"})
        browser.navigate = mock.AsyncMock()
        browser.submit_search_query = mock.AsyncMock(return_value={"ok": True, "strategy": "submit_failed"})
        browser.wait_for_search_results = mock.AsyncMock(return_value={"page_state": "homepage", "card_count": 12})

        url = await browser.navigate_to_search("护肤")

        self.assertIn("search_result", url)
        browser.navigate.assert_awaited_once()

    async def test_navigate_to_search_falls_back_when_dom_submit_keeps_stale_keyword_context(self) -> None:
        bridge = SimpleNamespace()
        browser = XHSBrowser(bridge)
        browser.get_tab_info = mock.AsyncMock(side_effect=[
            {"url": "https://www.xiaohongshu.com/search_result?keyword=qwen%203.5%20MacBook"},
            {"url": "https://www.xiaohongshu.com/search_result?keyword=qwen%203.5%20MacBook"},
        ])
        browser.detect_state = mock.AsyncMock(return_value={"state": "homepage"})
        browser.navigate = mock.AsyncMock()
        browser.submit_search_query = mock.AsyncMock(return_value={"ok": True, "strategy": "click_search_target"})
        browser.wait_for_search_results = mock.AsyncMock(return_value={"page_state": "search_results", "card_count": 12})
        browser._visible_search_keyword = mock.AsyncMock(return_value="Qwen 3.5 本地部署")

        url = await browser.navigate_to_search("Qwen 3.5 本地部署")

        self.assertIn("search_result", url)
        self.assertEqual(browser.last_search_route, "url_fallback")
        browser.navigate.assert_awaited_once()

    async def test_navigate_to_search_does_not_reuse_search_results_for_different_keyword(self) -> None:
        bridge = SimpleNamespace()
        browser = XHSBrowser(bridge)
        browser.get_tab_info = mock.AsyncMock(return_value={
            "url": "https://www.xiaohongshu.com/search_result?keyword=qwen%203.5%20MacBook"
        })
        browser.detect_state = mock.AsyncMock(return_value={"state": "search_results"})
        browser._visible_search_keyword = mock.AsyncMock(return_value="qwen 3.5 MacBook")
        browser.navigate = mock.AsyncMock()
        browser.submit_search_query = mock.AsyncMock(return_value={"ok": True})
        browser.wait_for_search_results = mock.AsyncMock(return_value={"page_state": "search_results", "card_count": 12})

        await browser.navigate_to_search("Qwen 3.5 本地部署")

        browser.submit_search_query.assert_awaited_once_with("Qwen 3.5 本地部署")

    async def test_wait_for_search_results_ignores_homepage_cards(self) -> None:
        bridge = SimpleNamespace()
        browser = XHSBrowser(bridge)
        browser.get_search_page_state = mock.AsyncMock(side_effect=[
            {"page_state": "homepage", "card_count": 12, "has_no_results": False},
            {"page_state": "search_results", "card_count": 8, "has_no_results": False},
        ])

        state = await browser.wait_for_search_results(timeout_s=1.0, poll_s=0)

        self.assertEqual(state.get("page_state"), "search_results")
        self.assertEqual(browser.get_search_page_state.await_count, 2)

    async def test_restore_search_context_uses_close_before_direct_navigation(self) -> None:
        bridge = SimpleNamespace()
        browser = XHSBrowser(bridge)
        browser.detect_state = mock.AsyncMock(return_value={"state": "note_detail"})
        browser.get_tab_info = mock.AsyncMock(side_effect=[{"url": "https://www.xiaohongshu.com/explore/abc"}, {"url": "https://www.xiaohongshu.com/search_result?keyword=%E6%8A%A4%E8%82%A4"}])
        browser._matches_search_context = mock.AsyncMock(side_effect=[True, True])
        browser.close_note = mock.AsyncMock(return_value={"ok": True})
        browser.wait_for_state = mock.AsyncMock(return_value={"state": "search_results"})
        browser.go_back = mock.AsyncMock()
        browser.submit_search_query = mock.AsyncMock()
        browser.navigate = mock.AsyncMock()

        state = await browser.restore_search_context(
            "护肤",
            "https://www.xiaohongshu.com/search_result?keyword=%E6%8A%A4%E8%82%A4",
        )

        self.assertEqual(state.get("state"), "search_results")
        browser.close_note.assert_awaited_once()
        browser.go_back.assert_not_awaited()
        browser.navigate.assert_not_awaited()

    async def test_restore_profile_context_uses_back_before_direct_navigation(self) -> None:
        bridge = SimpleNamespace()
        browser = XHSBrowser(bridge)
        browser.detect_state = mock.AsyncMock(return_value={"state": "search_results"})
        browser.get_tab_info = mock.AsyncMock(side_effect=[{"url": "https://www.xiaohongshu.com/search_result?keyword=a"}, {"url": "https://www.xiaohongshu.com/user/profile/123"}])
        browser.close_note = mock.AsyncMock()
        browser.go_back = mock.AsyncMock(return_value={"ok": True})
        browser.wait_for_state = mock.AsyncMock(return_value={"state": "profile_page"})
        browser.navigate = mock.AsyncMock()

        state = await browser.restore_profile_context("https://www.xiaohongshu.com/user/profile/123")

        self.assertEqual(state.get("state"), "profile_page")
        browser.go_back.assert_awaited_once()
        browser.navigate.assert_not_awaited()

    async def test_restore_profile_context_ignores_query_params(self) -> None:
        bridge = SimpleNamespace()
        browser = XHSBrowser(bridge)
        browser.detect_state = mock.AsyncMock(return_value={"state": "profile_page"})
        browser.get_tab_info = mock.AsyncMock(return_value={
            "url": "https://www.xiaohongshu.com/user/profile/123?channel_type=web_profile_page&xsec_token=abc",
        })
        browser.close_note = mock.AsyncMock()
        browser.go_back = mock.AsyncMock()
        browser.navigate = mock.AsyncMock()

        state = await browser.restore_profile_context("https://www.xiaohongshu.com/user/profile/123")

        self.assertEqual(state.get("state"), "profile_page")
        browser.go_back.assert_not_awaited()
        browser.navigate.assert_not_awaited()

    async def test_matches_search_context_can_fall_back_to_visible_input_value(self) -> None:
        bridge = SimpleNamespace()
        browser = XHSBrowser(bridge)
        browser._visible_search_keyword = mock.AsyncMock(return_value="护肤")

        matched = await browser._matches_search_context(
            "https://www.xiaohongshu.com/search_result?source=web_search_result_notes",
            "https://www.xiaohongshu.com/search_result?keyword=%E6%8A%A4%E8%82%A4",
            "护肤",
        )

        self.assertTrue(matched)

    async def test_matches_search_context_rejects_different_current_keyword(self) -> None:
        bridge = SimpleNamespace()
        browser = XHSBrowser(bridge)
        browser._visible_search_keyword = mock.AsyncMock(return_value="qwen 3.5 MacBook")

        matched = await browser._matches_search_context(
            "https://www.xiaohongshu.com/search_result?keyword=qwen%203.5%20MacBook",
            "https://www.xiaohongshu.com/search_result?keyword=Qwen%203.5%20%E6%9C%AC%E5%9C%B0%E9%83%A8%E7%BD%B2",
            "Qwen 3.5 本地部署",
        )

        self.assertFalse(matched)


if __name__ == "__main__":
    unittest.main()
