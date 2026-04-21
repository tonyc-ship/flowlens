"""Generic run-state tools for the browser agent."""

from __future__ import annotations

import json

from ..tool import Tool, ToolContext


class UpdateTaskPlanTool(Tool):
    @property
    def name(self) -> str:
        return "update_task_plan"

    @property
    def description(self) -> str:
        return (
            "Store or update the task checklist for the current run. "
            "Use this for multi-step work so you can track what is pending, in progress, and completed."
        )

    @property
    def always_available(self) -> bool:
        return True

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "steps": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "title": {"type": "string"},
                            "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]},
                            "details": {"type": "string"},
                        },
                        "required": ["title", "status"],
                    },
                },
                "note": {"type": "string"},
            },
            "required": ["steps"],
        }

    async def execute(self, params: dict, ctx: ToolContext) -> str:
        if ctx.run_state is None:
            return "Run state is not available."
        plan = ctx.run_state.update_plan(
            list(params.get("steps") or []),
            note=str(params.get("note") or ""),
            turn=ctx.turn or None,
        )
        return json.dumps(
            {
                "ok": True,
                "steps": plan.get("steps", []),
                "notes_count": len(plan.get("notes", [])),
                "plan_path": str(ctx.run_state.plan_path.relative_to(ctx.run_dir)),
            },
            ensure_ascii=False,
            indent=2,
        )


class ReadRunStateTool(Tool):
    @property
    def name(self) -> str:
        return "read_run_state"

    @property
    def description(self) -> str:
        return (
            "Read the structured run state for this task. "
            "Use it to revisit the current plan, working memory, saved artifacts, evidence summaries, or recent events."
        )

    @property
    def always_available(self) -> bool:
        return True

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "section": {
                    "type": "string",
                    "enum": ["working_memory", "plan", "artifacts", "evidence", "events"],
                },
                "item_key": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            "required": ["section"],
        }

    async def execute(self, params: dict, ctx: ToolContext) -> str:
        if ctx.run_state is None:
            return "Run state is not available."
        payload = ctx.run_state.read_section(
            str(params.get("section") or ""),
            item_key=str(params.get("item_key") or ""),
            limit=int(params.get("limit") or 10),
        )
        return json.dumps(payload, ensure_ascii=False, indent=2)


class ReadSavedArtifactTool(Tool):
    @property
    def name(self) -> str:
        return "read_saved_artifact"

    @property
    def description(self) -> str:
        return (
            "Read the full saved content of an artifact created earlier in the run, "
            "such as a JSON extraction result or another persisted file."
        )

    @property
    def always_available(self) -> bool:
        return True

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "max_chars": {"type": "integer", "minimum": 200, "maximum": 100000},
            },
            "required": ["path"],
        }

    async def execute(self, params: dict, ctx: ToolContext) -> str:
        if ctx.run_state is None:
            return "Run state is not available."
        payload = ctx.run_state.read_artifact(
            str(params.get("path") or ""),
            max_chars=int(params.get("max_chars") or 20000),
        )
        return json.dumps(payload, ensure_ascii=False, indent=2)


def make_state_tools() -> list[Tool]:
    return [UpdateTaskPlanTool(), ReadRunStateTool(), ReadSavedArtifactTool()]
