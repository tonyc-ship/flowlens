"""Composer tests — DOM-first submit flow with vision fallback."""

import unittest
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, mock

from flowlens.core.composer import (
    ComposerSpec,
    submit_attempt_order,
    submit_with_dom_verification,
)


class ComposerSubmitTest(IsolatedAsyncioTestCase):
    def test_submit_attempt_order_depends_on_mode(self) -> None:
        self.assertEqual(submit_attempt_order("enter"), ("enter", "button"))
        self.assertEqual(submit_attempt_order("auto"), ("button", "enter"))

    async def test_submit_retries_when_first_strategy_leaves_text_behind(self) -> None:
        """Enter-first fails (text still there) → fall back to button click →
        DOM goes empty → status 'sent'. Exercises retry + verification."""
        tab = SimpleNamespace(
            click_at=mock.AsyncMock(return_value={}),
            press_key=mock.AsyncMock(return_value={}),
            click_chat_submit=mock.AsyncMock(return_value={"clicked": True}),
            get_chat_input_state=mock.AsyncMock(side_effect=[
                {"found": True, "empty": False, "text": "hello world", "textLength": 11},
                {"found": True, "empty": True, "text": "", "textLength": 0},
            ]),
        )
        spec = ComposerSpec(
            input_selectors=("textarea",),
            submit_selectors=("button",),
            submit_mode="enter",
        )
        result = await submit_with_dom_verification(
            tab, spec, "hello world",
            input_result={"x": 12, "y": 34},
            focus_settle_s=0, post_submit_settle_s=0,
        )
        self.assertEqual(result.status, "sent")
        self.assertEqual([a.outcome for a in result.attempts], ["retry", "sent"])
        tab.press_key.assert_awaited_once_with("Enter", code="Enter")
        tab.click_chat_submit.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
