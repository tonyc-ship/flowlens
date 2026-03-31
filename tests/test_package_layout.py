import unittest


class PackageLayoutTest(unittest.TestCase):
    def test_canonical_and_legacy_imports_point_to_same_core_objects(self) -> None:
        from clawvision.agent.bridge import ExtensionBridge as LegacyBridge
        from clawvision.chatbots.runner import MultiChatRunner as LegacyMultiChatRunner
        from clawvision.core.bridge import ExtensionBridge
        from clawvision.workflows.chat.runner import MultiChatRunner

        self.assertIs(LegacyBridge, ExtensionBridge)
        self.assertIs(LegacyMultiChatRunner, MultiChatRunner)

    def test_new_layered_packages_are_importable(self) -> None:
        from clawvision import core, perception, platforms, reasoning, workflows
        from clawvision.platforms.xhs import XHSBrowser
        from clawvision.reasoning import TaskAgent
        from clawvision.workflows.xhs import XHSTaskRunner

        self.assertTrue(core)
        self.assertTrue(perception)
        self.assertTrue(platforms)
        self.assertTrue(reasoning)
        self.assertTrue(workflows)
        self.assertTrue(XHSBrowser)
        self.assertTrue(TaskAgent)
        self.assertTrue(XHSTaskRunner)
