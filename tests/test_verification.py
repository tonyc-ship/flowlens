"""Verification tests — DOM-first with vision fallback."""

import unittest
from unittest import IsolatedAsyncioTestCase

from flowlens.core.verification import VerificationResult, verify_dom_first


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
