import unittest
from unittest import IsolatedAsyncioTestCase

from flowlens.core.verification import (
    VerificationResult,
    assess_expected_text_state,
    compact_text,
    dom_assessment_to_result,
    verify_dom_first,
)


class VerificationHelpersTest(unittest.TestCase):
    def test_compact_text_removes_whitespace(self) -> None:
        self.assertEqual(compact_text(" Reply \n READY "), "replyready")

    def test_assess_expected_text_state_detects_prompt_still_present(self) -> None:
        result = assess_expected_text_state(
            {"found": True, "empty": False, "text": "Reply with the single word READY.", "textLength": 33},
            "Reply with the single word READY.",
        )
        self.assertEqual(result.status, "contains_expected")

    def test_assess_expected_text_state_detects_empty_composer(self) -> None:
        result = assess_expected_text_state(
            {"found": True, "empty": True, "text": "", "textLength": 0},
            "Reply with the single word READY.",
        )
        self.assertEqual(result.status, "empty")

    def test_assess_expected_text_state_keeps_ambiguous_text_separate(self) -> None:
        result = assess_expected_text_state(
            {"found": True, "empty": False, "text": "Ask Gemini 3", "textLength": 12},
            "Reply with the single word READY.",
        )
        self.assertEqual(result.status, "ambiguous")

    def test_dom_assessment_to_result_maps_empty_to_passed(self) -> None:
        assessment = assess_expected_text_state(
            {"found": True, "empty": True, "text": "", "textLength": 0},
            "hello world",
        )
        result = dom_assessment_to_result(assessment)
        self.assertEqual(result.status, "passed")
        self.assertEqual(result.source, "dom")


class DomFirstVerificationTest(IsolatedAsyncioTestCase):
    async def test_verify_dom_first_returns_dom_result_without_vision(self) -> None:
        decision = await verify_dom_first(
            lambda: _return_result(VerificationResult(status="passed", source="dom", detail="ok"))
        )
        self.assertEqual(decision.result.status, "passed")
        self.assertIsNone(decision.vision_result)

    async def test_verify_dom_first_uses_vision_on_ambiguous_dom(self) -> None:
        dom_result = VerificationResult(status="ambiguous", source="dom", detail="unclear")
        decision = await verify_dom_first(
            lambda: _return_result(dom_result),
            vision_verify=lambda result: _return_result(
                VerificationResult(status="passed", source="vision", detail=f"resolved from {result.status}")
            ),
        )
        self.assertEqual(decision.dom_result.status, "ambiguous")
        self.assertEqual(decision.result.status, "passed")
        self.assertEqual(decision.result.source, "vision")


async def _return_result(result: VerificationResult) -> VerificationResult:
    return result
