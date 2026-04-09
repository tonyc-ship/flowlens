import unittest

from flowlens.agent.backends import (
    LLMResponse,
    LocalBackend,
    _summarize_result_blocks_for_history,
)


class LocalBackendPromptingTest(unittest.TestCase):
    def test_parse_tool_calls_rejects_empty_name(self) -> None:
        backend = LocalBackend.__new__(LocalBackend)

        text_segments, tool_calls = backend._parse_tool_calls(
            '<tool_call>{"name": "", "arguments": {}}</tool_call>'
        )

        self.assertFalse(tool_calls)
        self.assertTrue(any("Failed to parse" in segment for segment in text_segments))

    def test_format_assistant_content_drops_thinking_blocks(self) -> None:
        backend = LocalBackend.__new__(LocalBackend)
        response = LLMResponse(
            text_blocks=["[Thinking] internal reasoning", "Visible answer"],
            tool_calls=[],
            stop_reason="end_turn",
            input_tokens=0,
            output_tokens=0,
        )

        content = backend.format_assistant_content(response)

        self.assertEqual(content, [{"type": "text", "text": "Visible answer"}])

    def test_compress_tool_result_text_truncates_large_json(self) -> None:
        payload = '{"entity":{"title":"t","content":"' + ("x" * 5000) + '"}}'

        compact = LocalBackend._compress_tool_result_text(payload, max_chars=800)

        self.assertLessEqual(len(compact), 815)
        self.assertIn("truncated", compact)

    def test_summarize_result_blocks_omits_image_payloads(self) -> None:
        blocks = [
            {"type": "text", "text": "Screenshot saved to 001_homepage.jpg"},
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "x" * 5000}},
        ]

        summary = _summarize_result_blocks_for_history(blocks)

        self.assertEqual(summary[0]["type"], "text")
        self.assertIn("001_homepage.jpg", summary[0]["text"])
        self.assertIn("Image omitted from history", summary[0]["text"])


if __name__ == "__main__":
    unittest.main()
