from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import IsolatedAsyncioTestCase

from flowlens.agent.loop import _select_active_tools
from flowlens.agent.run_state import RunState
from flowlens.agent.tool import Tool, ToolContext
from flowlens.agent.tools.state import (
    ReadRunStateTool,
    ReadSavedArtifactTool,
    UpdateTaskPlanTool,
)


class _DummyTool(Tool):
    def __init__(self, name: str, *, always_available: bool = False) -> None:
        self._name = name
        self._always_available = always_available

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._name

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    @property
    def always_available(self) -> bool:
        return self._always_available

    async def execute(self, params: dict, ctx: ToolContext) -> str:
        return "ok"


class RunStateTest(unittest.TestCase):
    def test_run_state_dedupes_evidence_and_updates_working_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            state = RunState(run_dir, "Compare two track-day notes", model="kimi-k2.5")

            (run_dir / "site_results").mkdir(parents=True, exist_ok=True)
            (run_dir / "site_results" / "001_note.json").write_text("{}", encoding="utf-8")
            (run_dir / "site_results" / "002_note.json").write_text("{}", encoding="utf-8")

            payload_one = {
                "action": "read_note",
                "entity": {
                    "note_id": "abc123",
                    "title": "金港赛道体验",
                    "author": "车手A",
                    "url": "https://www.xiaohongshu.com/explore/abc123",
                    "content_summary": "第一次去金港，弯道节奏比较碎。",
                    "screenshot": "003_note_detail.png",
                },
            }
            payload_two = {
                "action": "read_note",
                "entity": {
                    "note_id": "abc123",
                    "title": "金港赛道体验",
                    "author": "车手A",
                    "url": "https://www.xiaohongshu.com/explore/abc123",
                    "content_summary": "第二次去金港，主直道尾速一般，但出弯衔接顺畅，评论区也在讨论轮胎温度。",
                    "top_comments": ["评论1", "评论2"],
                    "screenshot": "004_note_detail.png",
                },
            }

            state.record_artifact(
                "site_results/001_note.json",
                label="read_note",
                artifact_kind="site_result",
                source_tool="run_site_action",
                turn=2,
                payload=payload_one,
            )
            state.record_artifact(
                "site_results/002_note.json",
                label="read_note",
                artifact_kind="site_result",
                source_tool="run_site_action",
                turn=3,
                payload=payload_two,
            )
            state.update_plan(
                [
                    {"id": "scan", "title": "Scan candidate posts", "status": "completed"},
                    {"id": "compare", "title": "Compare both tracks", "status": "in_progress"},
                ],
                note="Need one more post about lap-time differences.",
                turn=3,
            )

            evidence = state.read_section("evidence")
            self.assertEqual(evidence["count"], 1)
            item = evidence["items"][0]
            self.assertEqual(item["key"], "id:abc123")
            self.assertEqual(len(item["artifact_paths"]), 2)
            self.assertIn("轮胎温度", item["summary"])

            working_memory = state.read_section("working_memory")["content"]
            self.assertIn("Compare both tracks", working_memory)
            self.assertIn("金港赛道体验", working_memory)
            self.assertTrue((run_dir / "run_state" / "working_memory.md").is_file())
            self.assertTrue(state.has_structured_state())

class RunStateToolTest(IsolatedAsyncioTestCase):
    async def test_state_tools_read_and_write_run_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            state = RunState(run_dir, "Summarize race-track posts", model="qwen3.6-plus")
            artifact_path = run_dir / "artifacts" / "001_saved.json"
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            artifact_path.write_text('{"title":"Saved payload"}', encoding="utf-8")
            state.record_artifact(
                "artifacts/001_saved.json",
                label="saved_payload",
                artifact_kind="json",
                source_tool="extract_page_data",
                turn=1,
            )

            ctx = ToolContext(run_dir=run_dir, run_state=state, turn=2)

            update_tool = UpdateTaskPlanTool()
            read_state_tool = ReadRunStateTool()
            read_artifact_tool = ReadSavedArtifactTool()

            update_result = json.loads(
                await update_tool.execute(
                    {
                        "steps": [
                            {"id": "s1", "title": "Read saved evidence", "status": "in_progress"},
                        ],
                        "note": "Waiting for one more source.",
                    },
                    ctx,
                )
            )
            self.assertTrue(update_result["ok"])

            plan_payload = json.loads(await read_state_tool.execute({"section": "plan"}, ctx))
            self.assertEqual(plan_payload["content"]["steps"][0]["title"], "Read saved evidence")

            artifact_payload = json.loads(
                await read_artifact_tool.execute({"path": "artifacts/001_saved.json"}, ctx)
            )
            self.assertIn("Saved payload", artifact_payload["content"])


class ActiveToolSelectionTest(unittest.TestCase):
    def test_site_tool_filter_keeps_always_available_state_tools(self) -> None:
        tools = [
            _DummyTool("navigate"),
            _DummyTool("xhs_search_notes"),
            _DummyTool("update_task_plan", always_available=True),
        ]

        selected = _select_active_tools(
            tools,
            site_name="xiaohongshu",
            page_state="search_results",
            task="测试",
            messages=[],
        )

        selected_names = {tool.name for tool in selected}
        self.assertIn("xhs_search_notes", selected_names)
        self.assertIn("update_task_plan", selected_names)


if __name__ == "__main__":
    unittest.main()
