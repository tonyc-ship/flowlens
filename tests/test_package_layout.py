import unittest


class PackageLayoutTest(unittest.TestCase):
    def test_canonical_packages_expose_expected_entry_points(self) -> None:
        from clawvision.core.bridge import ExtensionBridge
        from clawvision.workflows.chat.runner import MultiChatRunner

        self.assertTrue(ExtensionBridge)
        self.assertTrue(MultiChatRunner)

    def test_python_module_entry_stays_canonical(self) -> None:
        from clawvision.workflows.xhs.cli import main

        self.assertTrue(main)

    def test_new_layered_packages_are_importable(self) -> None:
        from clawvision import core, observer, perception, platforms, reasoning, workflows
        from clawvision.platforms.xhs import XHSBrowser
        from clawvision.reasoning import TaskAgent
        from clawvision.workflows.xhs import XHSTaskRunner

        self.assertTrue(core)
        self.assertTrue(observer)
        self.assertTrue(perception)
        self.assertTrue(platforms)
        self.assertTrue(reasoning)
        self.assertTrue(workflows)
        self.assertTrue(XHSBrowser)
        self.assertTrue(TaskAgent)
        self.assertTrue(XHSTaskRunner)
