"""Backend + provider registry tests.

Covers the shared backend contract:
  - create_backend dispatches CN providers with the correct base_url and emits
    a helpful error when the API key is missing.
  - QwenBackend's tool-history quirk (always emits a ``reasoning_content`` key
    so DashScope does not reject the turn).
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

from flowlens.agent.backends import KimiBackend, QwenBackend, create_backend


STUB_ENV = {
    "MOONSHOT_API_KEY": "sk-fake-mk",
    "DASHSCOPE_API_KEY": "sk-fake-ds2",
}


class BackendFactoryTest(unittest.TestCase):
    def test_create_backend_dispatches_cn_providers_with_base_url(self) -> None:
        with mock.patch.dict(os.environ, STUB_ENV, clear=False):
            kimi = create_backend("kimi-k2-0905-preview")
            qwen = create_backend("qwen-plus")

        self.assertIsInstance(kimi, KimiBackend)
        self.assertIsInstance(qwen, QwenBackend)
        self.assertIn("api.moonshot.cn", str(kimi.client.base_url))
        self.assertIn("dashscope.aliyuncs.com", str(qwen.client.base_url))

    def test_missing_api_key_raises_with_env_var_hint(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch("flowlens.agent.backends.resolve_provider_auth", return_value=None):
            with self.assertRaises(RuntimeError) as ctx:
                create_backend("kimi-k2-0905-preview")
        self.assertIn("Kimi", str(ctx.exception))
        self.assertIn("MOONSHOT_API_KEY", str(ctx.exception))


class QwenBackendHistoryTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
