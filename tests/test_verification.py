import unittest

from clawvision.agent.verification import assess_expected_text_state, compact_text


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
