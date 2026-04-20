"""Backend + provider registry tests.

Covers the contract shared by all backends:
  - Provider routing by model-name prefix (Anthropic, OpenAI, DeepSeek, Kimi,
    Qwen cloud, and local MLX).
  - create_backend dispatches to the right class with the right base_url and
    emits a helpful error when the API key is missing.
  - LocalBackend's text-based <tool_call> parser and history compaction, which
    the local agent loop relies on.
"""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest import mock

from flowlens.core.auth import (
    PROVIDER_ANTHROPIC,
    PROVIDER_DEEPSEEK,
    PROVIDER_KIMI,
    PROVIDER_OPENAI,
    PROVIDER_QWEN,
    resolve_model_provider,
)
from flowlens.agent.backends import (
    DeepSeekBackend,
    KimiBackend,
    LocalBackend,
    QwenBackend,
    _summarize_result_blocks_for_history,
    create_backend,
)


STUB_ENV = {
    "DEEPSEEK_API_KEY": "sk-fake-ds",
    "MOONSHOT_API_KEY": "sk-fake-mk",
    "DASHSCOPE_API_KEY": "sk-fake-ds2",
}


class ModelRoutingTest(unittest.TestCase):
    def test_prefixes_route_to_correct_provider(self) -> None:
        cases = {
            "claude-sonnet-4-6": PROVIDER_ANTHROPIC,
            "gpt-5": PROVIDER_OPENAI,
            "o3-mini": PROVIDER_OPENAI,
            "deepseek-chat": PROVIDER_DEEPSEEK,
            "deepseek-reasoner": PROVIDER_DEEPSEEK,
            "kimi-k2-0905-preview": PROVIDER_KIMI,
            "moonshot-v1-128k": PROVIDER_KIMI,
            "qwen-plus": PROVIDER_QWEN,
            "qwen3.6-plus": PROVIDER_QWEN,
            # Local MLX aliases must win over the lowercase qwen- cloud prefix.
            "qwen-local": "local",
            "Qwen3.5-9B-MLX-4bit": "local",
            "ui-tars-local": "local",
        }
        for model, expected in cases.items():
            self.assertEqual(resolve_model_provider(model), expected, msg=model)


class BackendFactoryTest(unittest.TestCase):
    def test_create_backend_dispatches_cn_providers_with_base_url(self) -> None:
        with mock.patch.dict(os.environ, STUB_ENV, clear=False):
            ds = create_backend("deepseek-chat")
            kimi = create_backend("kimi-k2-0905-preview")
            qwen = create_backend("qwen-plus")

        self.assertIsInstance(ds, DeepSeekBackend)
        self.assertIsInstance(kimi, KimiBackend)
        self.assertIsInstance(qwen, QwenBackend)
        self.assertIn("api.deepseek.com", str(ds.client.base_url))
        self.assertIn("api.moonshot.cn", str(kimi.client.base_url))
        self.assertIn("dashscope.aliyuncs.com", str(qwen.client.base_url))

    def test_missing_api_key_raises_with_env_var_hint(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch("flowlens.agent.backends.resolve_provider_auth", return_value=None):
            with self.assertRaises(RuntimeError) as ctx:
                create_backend("deepseek-chat")
        self.assertIn("DeepSeek", str(ctx.exception))
        self.assertIn("DEEPSEEK_API_KEY", str(ctx.exception))


class QwenBackendThinkingModeTest(unittest.TestCase):
    def test_tool_requests_disable_thinking_and_preserve_reasoning_history(self) -> None:
        captured_request = {}

        def fake_create(**request):
            captured_request.update(request)
            message = SimpleNamespace(
                content="I will inspect the page.",
                tool_calls=[
                    SimpleNamespace(
                        id="call_1",
                        function=SimpleNamespace(
                            name="screenshot",
                            arguments='{"label": "initial_state"}',
                        ),
                    )
                ],
                model_extra={"reasoning_content": "need page state"},
            )
            return SimpleNamespace(
                choices=[SimpleNamespace(message=message, finish_reason="tool_calls")],
                usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
            )

        backend = QwenBackend.__new__(QwenBackend)
        backend.model = "qwen3.6-plus"
        backend.client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create))
        )

        response = backend.create_message(
            system="system",
            messages=[{"role": "user", "content": "search xhs"}],
            tools=[
                {
                    "name": "screenshot",
                    "description": "Take a screenshot",
                    "input_schema": {
                        "type": "object",
                        "properties": {"label": {"type": "string"}},
                    },
                }
            ],
            max_tokens=256,
        )

        self.assertEqual(captured_request["extra_body"], {"enable_thinking": False})

        stored = backend.format_assistant_content(response)
        self.assertEqual(stored[0], {"type": "reasoning_content", "text": "need page state"})

        assistant_message = backend._message_to_chat({"role": "assistant", "content": stored})[0]
        self.assertEqual(assistant_message["reasoning_content"], "need page state")
        self.assertEqual(assistant_message["tool_calls"][0]["id"], "call_1")

    def test_qwen_tool_history_adds_empty_reasoning_content_when_missing(self) -> None:
        backend = QwenBackend.__new__(QwenBackend)
        assistant_message = backend._message_to_chat(
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "call_1",
                        "name": "screenshot",
                        "input": {"label": "initial_state"},
                    }
                ],
            }
        )[0]

        self.assertEqual(assistant_message["reasoning_content"], "")


class KimiBackendThinkingModeTest(unittest.TestCase):
    def test_kimi_k25_tool_requests_disable_thinking(self) -> None:
        captured_request = {}

        def fake_create(**request):
            captured_request.update(request)
            message = SimpleNamespace(
                content="I will inspect the page.",
                tool_calls=[
                    SimpleNamespace(
                        id="call_1",
                        function=SimpleNamespace(
                            name="screenshot",
                            arguments='{"label": "initial_state"}',
                        ),
                    )
                ],
                model_extra={},
            )
            return SimpleNamespace(
                choices=[SimpleNamespace(message=message, finish_reason="tool_calls")],
                usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
            )

        backend = KimiBackend.__new__(KimiBackend)
        backend.model = "kimi-k2.5"
        backend.client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create))
        )

        backend.create_message(
            system="system",
            messages=[{"role": "user", "content": "search xhs"}],
            tools=[
                {
                    "name": "screenshot",
                    "description": "Take a screenshot",
                    "input_schema": {
                        "type": "object",
                        "properties": {"label": {"type": "string"}},
                    },
                }
            ],
            max_tokens=256,
        )

        self.assertEqual(captured_request["extra_body"], {"thinking": {"type": "disabled"}})

    def test_kimi_tool_history_preserves_reasoning_content(self) -> None:
        backend = KimiBackend.__new__(KimiBackend)
        assistant_message = backend._message_to_chat(
            {
                "role": "assistant",
                "content": [
                    {"type": "reasoning_content", "text": "need page state"},
                    {
                        "type": "tool_use",
                        "id": "call_1",
                        "name": "screenshot",
                        "input": {"label": "initial_state"},
                    },
                ],
            }
        )[0]

        self.assertEqual(assistant_message["reasoning_content"], "need page state")


class LocalBackendPromptingTest(unittest.TestCase):
    """LocalBackend has no SDK — these text-protocol helpers are load-bearing."""

    def test_parse_tool_calls_rejects_empty_name(self) -> None:
        backend = LocalBackend.__new__(LocalBackend)
        text_segments, tool_calls = backend._parse_tool_calls(
            '<tool_call>{"name": "", "arguments": {}}</tool_call>'
        )
        self.assertFalse(tool_calls)
        self.assertTrue(any("Failed to parse" in s for s in text_segments))

    def test_summarize_result_blocks_omits_image_payloads_but_keeps_path(self) -> None:
        blocks = [
            {"type": "text", "text": "Screenshot saved to 001_homepage.jpg"},
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                         "data": "x" * 5000}},
        ]
        summary = _summarize_result_blocks_for_history(blocks)
        self.assertEqual(summary[0]["type"], "text")
        self.assertIn("001_homepage.jpg", summary[0]["text"])
        self.assertIn("Image omitted from history", summary[0]["text"])


if __name__ == "__main__":
    unittest.main()
