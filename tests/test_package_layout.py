import unittest


class PackageLayoutTest(unittest.TestCase):
    def test_canonical_packages_expose_expected_entry_points(self) -> None:
        from flowlens.core.bridge import ExtensionBridge
        from flowlens.xhs_cli import main as xhs_main

        self.assertTrue(ExtensionBridge)
        self.assertTrue(xhs_main)

    def test_agent_cli_is_default_entry(self) -> None:
        from flowlens.agent.cli import main
        from flowlens.agent.loop import run_agent

        self.assertTrue(main)
        self.assertTrue(run_agent)

    def test_new_layered_packages_are_importable(self) -> None:
        from flowlens import core, observer, perception, platforms, reasoning, workflows
        from flowlens.platforms.xhs import XHSSiteAdapter
        from flowlens.reasoning import TaskAgent

        self.assertTrue(core)
        self.assertTrue(observer)
        self.assertTrue(perception)
        self.assertTrue(platforms)
        self.assertTrue(XHSSiteAdapter)
        self.assertTrue(reasoning)
        self.assertTrue(workflows)
        self.assertTrue(TaskAgent)
