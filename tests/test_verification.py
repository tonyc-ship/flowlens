"""Verification tests — text-state assessment + DOM-first with vision fallback."""

import unittest
from unittest import IsolatedAsyncioTestCase

from flowlens.core.verification import (
    VerificationResult,
    assess_expected_text_state,
    verify_dom_first,
)


class AssessExpectedTextStateTest(unittest.TestCase):
    def test_maps_dom_states_to_expected_outcomes(self) -> None:
        expected = "Reply with the single word READY."
        cases = [
            ({"found": True, "empty": True, "text": "", "textLength": 0}, "empty"),
            ({"found": True, "empty": False, "text": expected, "textLength": len(expected)},
             "contains_expected"),
            ({"found": True, "empty": False, "text": "Ask Gemini 3", "textLength": 12},
             "ambiguous"),
        ]
        for dom_state, expected_status in cases:
            self.assertEqual(
                assess_expected_text_state(dom_state, expected).status,
                expected_status,
                msg=dom_state,
            )


class DomFirstVerificationTest(IsolatedAsyncioTestCase):
    async def test_falls_back_to_vision_when_dom_is_ambiguous(self) -> None:
        async def dom():
            return VerificationResult(status="ambiguous", source="dom", detail="unclear")

        async def vision(result):
            return VerificationResult(status="passed", source="vision",
                                       detail=f"resolved from {result.status}")

        decision = await verify_dom_first(dom, vision_verify=vision)
        self.assertEqual(decision.dom_result.status, "ambiguous")
        self.assertEqual(decision.result.status, "passed")
        self.assertEqual(decision.result.source, "vision")


if __name__ == "__main__":
    unittest.main()
