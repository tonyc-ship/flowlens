"""Smoke test: CLI entry points + every layered package is importable."""

import unittest


class PackageLayoutTest(unittest.TestCase):
    def test_all_documented_packages_and_entry_points_import(self) -> None:
        from flowlens import core, observer, perception, platforms, reasoning, workflows
        from flowlens.agent.cli import main as agent_main
        from flowlens.agent.loop import run_agent
        from flowlens.core.bridge import ExtensionBridge
        from flowlens.platforms.xhs import XHSSiteAdapter
        from flowlens.reasoning import TaskAgent
        from flowlens.xhs_cli import main as xhs_main

        for obj in (core, observer, perception, platforms, reasoning, workflows,
                    agent_main, run_agent, ExtensionBridge, XHSSiteAdapter,
                    TaskAgent, xhs_main):
            self.assertTrue(obj)
