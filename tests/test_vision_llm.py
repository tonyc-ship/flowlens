"""Vision backend normalization tests."""

from __future__ import annotations

import unittest

from flowlens.perception.llm import VisionLLM


class VisionLlmBackendTest(unittest.TestCase):
    def test_kimi_backend_is_preserved(self) -> None:
        llm = VisionLLM(backend="kimi")
        self.assertEqual(llm.backend, "kimi")

    def test_qwen_backend_is_preserved(self) -> None:
        llm = VisionLLM(backend="qwen")
        self.assertEqual(llm.backend, "qwen")


if __name__ == "__main__":
    unittest.main()
