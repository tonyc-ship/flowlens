import unittest

from flowlens.perception.policy import (
    BACKEND_CLOUD,
    BACKEND_LOCAL,
    BACKEND_OPENAI,
    TaskModelPolicy,
    normalize_backend_choice,
)


class TaskModelPolicyTest(unittest.TestCase):
    def test_normalize_backend_choice_maps_local_aliases(self) -> None:
        self.assertEqual(normalize_backend_choice("local"), BACKEND_LOCAL)
        self.assertEqual(normalize_backend_choice("qwen"), BACKEND_LOCAL)
        self.assertEqual(normalize_backend_choice("qwen-local"), BACKEND_LOCAL)

    def test_normalize_backend_choice_maps_openai_aliases(self) -> None:
        self.assertEqual(normalize_backend_choice("openai"), BACKEND_OPENAI)
        self.assertEqual(normalize_backend_choice("gpt-5"), BACKEND_OPENAI)

    def test_normalize_backend_choice_defaults_to_cloud(self) -> None:
        self.assertEqual(normalize_backend_choice(None), BACKEND_CLOUD)
        self.assertEqual(normalize_backend_choice("sonnet"), BACKEND_CLOUD)
        self.assertEqual(normalize_backend_choice("anything-else"), BACKEND_CLOUD)

    def test_task_model_policy_builds_local_policy(self) -> None:
        policy = TaskModelPolicy.from_choice("local")
        self.assertEqual(policy.mode, "local")
        self.assertEqual(policy.reasoning_backend, BACKEND_LOCAL)
        self.assertEqual(policy.vision_backend, BACKEND_LOCAL)

    def test_task_model_policy_builds_cloud_policy(self) -> None:
        policy = TaskModelPolicy.from_choice("sonnet")
        self.assertEqual(policy.mode, "cloud")
        self.assertEqual(policy.reasoning_backend, BACKEND_CLOUD)
        self.assertEqual(policy.vision_backend, BACKEND_CLOUD)

    def test_task_model_policy_builds_openai_policy(self) -> None:
        policy = TaskModelPolicy.from_choice("openai")
        self.assertEqual(policy.mode, "cloud")
        self.assertEqual(policy.reasoning_backend, BACKEND_OPENAI)
        self.assertEqual(policy.vision_backend, BACKEND_OPENAI)
