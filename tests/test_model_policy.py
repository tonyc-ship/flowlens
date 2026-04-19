"""TaskModelPolicy / backend normalization tests.

Verifies that CLI backend aliases map to the correct reasoning + vision
backends across all supported providers (cloud, Chinese vendors, local).
"""

import unittest

from flowlens.perception.policy import (
    BACKEND_CLOUD,
    BACKEND_DEEPSEEK,
    BACKEND_KIMI,
    BACKEND_LOCAL,
    BACKEND_OPENAI,
    BACKEND_QWEN_CLOUD,
    TaskModelPolicy,
    normalize_backend_choice,
)


class TaskModelPolicyTest(unittest.TestCase):
    def test_normalize_backend_choice_all_aliases(self) -> None:
        cases = {
            # Cloud defaults
            None: BACKEND_CLOUD,
            "sonnet": BACKEND_CLOUD,
            "anything-else": BACKEND_CLOUD,
            # OpenAI
            "openai": BACKEND_OPENAI,
            "gpt-5": BACKEND_OPENAI,
            # Chinese vendors — bare vendor name routes to the cloud backend.
            "deepseek": BACKEND_DEEPSEEK,
            "kimi": BACKEND_KIMI,
            "moonshot": BACKEND_KIMI,
            "qwen": BACKEND_QWEN_CLOUD,
            # Local MLX — "qwen-local" is the explicit local alias.
            "local": BACKEND_LOCAL,
            "qwen-local": BACKEND_LOCAL,
        }
        for alias, expected in cases.items():
            self.assertEqual(normalize_backend_choice(alias), expected, msg=repr(alias))

    def test_from_choice_sets_reasoning_and_vision_backends(self) -> None:
        # Cloud providers with native vision → reasoning == vision.
        for alias, expected in [
            ("sonnet", BACKEND_CLOUD),
            ("openai", BACKEND_OPENAI),
            ("local", BACKEND_LOCAL),
        ]:
            policy = TaskModelPolicy.from_choice(alias)
            self.assertEqual(policy.reasoning_backend, expected)
            self.assertEqual(policy.vision_backend, expected)

        # DeepSeek is text-only and falls back to Sonnet for vision.
        policy = TaskModelPolicy.from_choice("deepseek")
        self.assertEqual(policy.reasoning_backend, BACKEND_DEEPSEEK)
        self.assertEqual(policy.vision_backend, BACKEND_CLOUD)

        # Kimi and cloud Qwen are multimodal, so their vision backend stays aligned.
        for alias, reasoning in [
            ("kimi", BACKEND_KIMI),
            ("qwen", BACKEND_QWEN_CLOUD),
        ]:
            policy = TaskModelPolicy.from_choice(alias)
            self.assertEqual(policy.reasoning_backend, reasoning)
            self.assertEqual(policy.vision_backend, reasoning)


if __name__ == "__main__":
    unittest.main()
