"""Unified run planner and MCP idle-shutdown tests."""

from __future__ import annotations

import asyncio
import contextlib
import os
import unittest
from unittest import mock

from socai.run_cli import infer_run_plan, resolve_llm_backend
from socai.mcp import server as mcp_server


class UnifiedPlannerTest(unittest.TestCase):
    def test_wechat_group_need_analysis_recommends_wechat_pack(self) -> None:
        plan = infer_run_plan(
            "总结微信 x-mcp群里最近几周的用户需求和讨论话题",
            planner=lambda _prompt: {
                "recommended_packs": ["wechat"],
                "use_browser": False,
                "reasoning": "The request refers to a WeChat group discussion.",
            },
        )

        self.assertEqual(plan.recommended_packs, ["desktop_generic", "wechat"])
        self.assertFalse(plan.use_browser)

    def test_xhs_profile_url_recommends_xhs_pack(self) -> None:
        plan = infer_run_plan(
            "帮我拆解这个作者 https://www.xiaohongshu.com/user/profile/abc123",
            planner=lambda _prompt: {
                "recommended_packs": ["xiaohongshu"],
                "use_browser": True,
                "reasoning": "The request contains a Xiaohongshu profile URL.",
            },
        )

        self.assertEqual(plan.recommended_packs, ["browser_generic", "xiaohongshu"])
        self.assertTrue(plan.use_browser)


class RunCliModelDefaultsTest(unittest.TestCase):
    @mock.patch("socai.run_cli.preferred_provider", return_value="openai")
    def test_auto_backend_follows_auth_default_provider(self, _preferred_provider) -> None:
        self.assertEqual(resolve_llm_backend("auto"), "openai")

    def test_model_override_decides_backend_provider(self) -> None:
        self.assertEqual(resolve_llm_backend("auto", model="gpt-5.4"), "openai")
        self.assertEqual(resolve_llm_backend("auto", model="claude-sonnet-4-6"), "sonnet")
        self.assertEqual(resolve_llm_backend("auto", model="qwen-local"), "qwen-local")


class McpIdleShutdownTest(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self) -> None:
        task = mcp_server._idle_shutdown_task
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        mcp_server._bridge = None
        mcp_server._ctx = None
        mcp_server._tools_by_name = {}
        mcp_server._active_tool_calls = 0
        mcp_server._idle_shutdown_task = None

    async def test_idle_shutdown_releases_live_bridge(self) -> None:
        class FakeBridge:
            def __init__(self) -> None:
                self.stopped = False

            async def stop(self) -> None:
                self.stopped = True

        fake_bridge = FakeBridge()
        mcp_server._bridge = fake_bridge  # type: ignore[assignment]
        mcp_server._ctx = object()  # type: ignore[assignment]
        mcp_server._tools_by_name = {"dummy": object()}  # type: ignore[dict-item]
        mcp_server._active_tool_calls = 1

        with mock.patch.dict(os.environ, {"SOCAI_MCP_IDLE_TIMEOUT_SECONDS": "0.01"}):
            await mcp_server._release_runtime_after_call()
            await asyncio.sleep(0.05)

        self.assertTrue(fake_bridge.stopped)
        self.assertIsNone(mcp_server._bridge)
        self.assertEqual(mcp_server._tools_by_name, {})


if __name__ == "__main__":
    unittest.main()
