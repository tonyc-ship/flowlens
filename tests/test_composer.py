import unittest
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, mock

from clawvision.core.composer import ComposerSpec, enter_text, submit_attempt_order, submit_with_dom_verification


class ComposerHelpersTest(unittest.TestCase):
    def test_submit_attempt_order_prefers_enter_for_enter_sites(self) -> None:
        self.assertEqual(submit_attempt_order("enter"), ("enter", "button"))

    def test_submit_attempt_order_prefers_button_for_auto_sites(self) -> None:
        self.assertEqual(submit_attempt_order("auto"), ("button", "enter"))


class ComposerAutomationTest(IsolatedAsyncioTestCase):
    async def test_enter_text_falls_back_to_keyboard_when_dom_set_fails(self) -> None:
        tab = SimpleNamespace(
            set_chat_input_text=mock.AsyncMock(return_value={"ok": False}),
            click_at=mock.AsyncMock(return_value={}),
            type_text=mock.AsyncMock(return_value={}),
        )
        spec = ComposerSpec(
            input_selectors=("textarea",),
            submit_selectors=("button",),
            submit_mode="enter",
        )

        result = await enter_text(
            tab,
            spec,
            "hello world",
            input_result={"x": 12, "y": 34},
            focus_settle_s=0,
        )

        self.assertEqual(result.method, "keyboard")
        tab.click_at.assert_awaited_once_with(12, 34)
        tab.type_text.assert_awaited_once_with("hello world")

    async def test_submit_with_dom_verification_retries_with_secondary_strategy(self) -> None:
        tab = SimpleNamespace(
            click_at=mock.AsyncMock(return_value={}),
            press_key=mock.AsyncMock(return_value={}),
            click_chat_submit=mock.AsyncMock(return_value={"clicked": True, "hint": "send-button"}),
            get_chat_input_state=mock.AsyncMock(
                side_effect=[
                    {"found": True, "empty": False, "text": "hello world", "textLength": 11},
                    {"found": True, "empty": True, "text": "", "textLength": 0},
                ]
            ),
        )
        spec = ComposerSpec(
            input_selectors=("textarea",),
            submit_selectors=("button",),
            submit_mode="enter",
        )

        result = await submit_with_dom_verification(
            tab,
            spec,
            "hello world",
            input_result={"x": 12, "y": 34},
            focus_settle_s=0,
            post_submit_settle_s=0,
        )

        self.assertEqual(result.status, "sent")
        self.assertEqual([attempt.outcome for attempt in result.attempts], ["retry", "sent"])
        tab.press_key.assert_awaited_once_with("Enter", code="Enter")
        tab.click_chat_submit.assert_awaited_once_with(["button"], anchor={"x": 12, "y": 34})

    async def test_submit_with_dom_verification_reports_ambiguous_state(self) -> None:
        tab = SimpleNamespace(
            click_at=mock.AsyncMock(return_value={}),
            press_key=mock.AsyncMock(return_value={}),
            click_chat_submit=mock.AsyncMock(return_value={"clicked": True}),
            get_chat_input_state=mock.AsyncMock(
                return_value={"found": True, "empty": False, "text": "partial draft", "textLength": 13}
            ),
        )
        spec = ComposerSpec(
            input_selectors=("textarea",),
            submit_selectors=("button",),
            submit_mode="button",
        )

        result = await submit_with_dom_verification(
            tab,
            spec,
            "hello world",
            input_result={"x": 12, "y": 34},
            focus_settle_s=0,
            post_submit_settle_s=0,
        )

        self.assertEqual(result.status, "ambiguous")
        self.assertEqual(result.attempts[-1].outcome, "ambiguous")

    async def test_submit_with_dom_verification_can_use_vision_to_resolve_ambiguous_dom(self) -> None:
        tab = SimpleNamespace(
            click_at=mock.AsyncMock(return_value={}),
            press_key=mock.AsyncMock(return_value={}),
            click_chat_submit=mock.AsyncMock(return_value={"clicked": True}),
            get_chat_input_state=mock.AsyncMock(
                return_value={"found": True, "empty": False, "text": "partial draft", "textLength": 13}
            ),
        )
        spec = ComposerSpec(
            input_selectors=("textarea",),
            submit_selectors=("button",),
            submit_mode="button",
        )

        async def _vision_verifier(attempt_result, dom_result):
            self.assertEqual(attempt_result.assessment.status, "ambiguous")
            self.assertEqual(dom_result.status, "ambiguous")
            from clawvision.core.verification import VerificationResult

            return VerificationResult(status="passed", source="vision", detail="generation visible")

        result = await submit_with_dom_verification(
            tab,
            spec,
            "hello world",
            input_result={"x": 12, "y": 34},
            focus_settle_s=0,
            post_submit_settle_s=0,
            vision_verifier=_vision_verifier,
        )

        self.assertEqual(result.status, "sent")
        self.assertEqual(result.attempts[-1].verification_source, "vision")
        self.assertEqual(result.attempts[-1].verification_status, "passed")
