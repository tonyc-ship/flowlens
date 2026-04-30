"""Smoke test: CLI entry points + every layered package is importable."""

import unittest


class PackageLayoutTest(unittest.TestCase):
    def test_all_documented_packages_and_entry_points_import(self) -> None:
        from socai import core, observer, perception, platforms, reasoning, workflows
        from socai.agent.cli import main as agent_main
        from socai.agent.loop import run_agent
        from socai.core.bridge import ExtensionBridge
        from socai.platforms.xhs import XHSSiteAdapter
        from socai.reasoning import TaskAgent
        from socai.xhs_cli import main as xhs_main

        for obj in (core, observer, perception, platforms, reasoning, workflows,
                    agent_main, run_agent, ExtensionBridge, XHSSiteAdapter,
                    TaskAgent, xhs_main):
            self.assertTrue(obj)
