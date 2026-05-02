"""Tooling for capability-pack discovery and activation."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable

from ..tool import Tool, ToolContext
from ...tools.capability_packs import (
    CAPABILITY_PACKS,
    capability_pack_spec,
    dependency_closure,
    pack_available,
    pack_unavailable_reason,
)


def _tool_pack_id(tool: Tool) -> str:
    return str(getattr(tool, "capability_pack", "") or "").strip()


def _group_tools_by_pack(tools: list[Tool]) -> dict[str, list[Tool]]:
    grouped: dict[str, list[Tool]] = defaultdict(list)
    for tool in tools:
        pack_id = _tool_pack_id(tool)
        if not pack_id:
            continue
        grouped[pack_id].append(tool)
    return dict(grouped)


def _tool_param_keys(tool: Tool) -> list[str]:
    properties = tool.parameters.get("properties", {})
    if not isinstance(properties, dict):
        return []
    return [str(key) for key in list(properties)[:8]]


class ListCapabilityPacksTool(Tool):
    def __init__(
        self,
        *,
        tools_provider: Callable[[], list[Tool]],
        browser_available: bool,
        desktop_available: bool,
    ):
        self._tools_provider = tools_provider
        self._browser_available = browser_available
        self._desktop_available = desktop_available

    @property
    def name(self) -> str:
        return "list_capability_packs"

    @property
    def description(self) -> str:
        return (
            "List the high-level capability packs that this run can use. "
            "Use this first when deciding whether to work in the browser, on the desktop, "
            "or with a site/app-specific pack like Xiaohongshu or WeChat."
        )

    @property
    def always_available(self) -> bool:
        return True

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, params: dict, ctx: ToolContext) -> str:
        grouped = _group_tools_by_pack(self._tools_provider())
        payload = []
        active = set(ctx.active_capability_packs or set())
        for spec in CAPABILITY_PACKS.values():
            available = pack_available(
                spec,
                browser_available=self._browser_available,
                desktop_available=self._desktop_available,
            )
            payload.append(
                {
                    "pack_id": spec.pack_id,
                    "title": spec.title,
                    "summary": spec.summary,
                    "when_to_use": spec.when_to_use,
                    "dependencies": list(spec.dependencies),
                    "available": available,
                    "active": spec.pack_id in active,
                    "tool_count": len(grouped.get(spec.pack_id, [])),
                    "unavailable_reason": (
                        ""
                        if available
                        else pack_unavailable_reason(
                            spec,
                            browser_available=self._browser_available,
                            desktop_available=self._desktop_available,
                        )
                    ),
                }
            )
        return json.dumps(payload, ensure_ascii=False, indent=2)


class DescribeCapabilityPackTool(Tool):
    def __init__(
        self,
        *,
        tools_provider: Callable[[], list[Tool]],
        browser_available: bool,
        desktop_available: bool,
    ):
        self._tools_provider = tools_provider
        self._browser_available = browser_available
        self._desktop_available = desktop_available

    @property
    def name(self) -> str:
        return "describe_capability_pack"

    @property
    def description(self) -> str:
        return (
            "Show the detailed tools inside one capability pack, including each tool's purpose and input keys. "
            "Use this before activating a pack when you need to inspect the concrete operations."
        )

    @property
    def always_available(self) -> bool:
        return True

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "pack_id": {
                    "type": "string",
                    "enum": list(CAPABILITY_PACKS),
                }
            },
            "required": ["pack_id"],
        }

    async def execute(self, params: dict, ctx: ToolContext) -> str:
        pack_id = str(params.get("pack_id") or "").strip()
        spec = capability_pack_spec(pack_id)
        if spec is None:
            return json.dumps({"ok": False, "error": f"Unknown capability pack: {pack_id}"}, ensure_ascii=False, indent=2)
        available = pack_available(
            spec,
            browser_available=self._browser_available,
            desktop_available=self._desktop_available,
        )
        grouped = _group_tools_by_pack(self._tools_provider())
        tools = grouped.get(spec.pack_id, [])
        payload = {
            "pack_id": spec.pack_id,
            "title": spec.title,
            "summary": spec.summary,
            "when_to_use": spec.when_to_use,
            "details": spec.details,
            "dependencies": list(spec.dependencies),
            "available": available,
            "active": spec.pack_id in set(ctx.active_capability_packs or set()),
            "unavailable_reason": (
                ""
                if available
                else pack_unavailable_reason(
                    spec,
                    browser_available=self._browser_available,
                    desktop_available=self._desktop_available,
                )
            ),
            "tools": [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "parameter_keys": _tool_param_keys(tool),
                }
                for tool in tools
            ],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)


class ActivateCapabilityPackTool(Tool):
    def __init__(
        self,
        *,
        tools_provider: Callable[[], list[Tool]],
        browser_available: bool,
        desktop_available: bool,
    ):
        self._tools_provider = tools_provider
        self._browser_available = browser_available
        self._desktop_available = desktop_available

    @property
    def name(self) -> str:
        return "activate_capability_pack"

    @property
    def description(self) -> str:
        return (
            "Activate one capability pack so its concrete tools become callable on subsequent turns. "
            "Dependencies are activated automatically."
        )

    @property
    def always_available(self) -> bool:
        return True

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "pack_id": {
                    "type": "string",
                    "enum": list(CAPABILITY_PACKS),
                }
            },
            "required": ["pack_id"],
        }

    async def execute(self, params: dict, ctx: ToolContext) -> str:
        pack_id = str(params.get("pack_id") or "").strip()
        spec = capability_pack_spec(pack_id)
        if spec is None:
            return json.dumps({"ok": False, "error": f"Unknown capability pack: {pack_id}"}, ensure_ascii=False, indent=2)

        grouped = _group_tools_by_pack(self._tools_provider())
        to_activate = dependency_closure([pack_id])
        newly_active: list[str] = []
        active = set(ctx.active_capability_packs or set())

        for candidate_id in to_activate:
            candidate_spec = capability_pack_spec(candidate_id)
            if candidate_spec is None:
                continue
            if not pack_available(
                candidate_spec,
                browser_available=self._browser_available,
                desktop_available=self._desktop_available,
            ):
                return json.dumps(
                    {
                        "ok": False,
                        "error": f"Capability pack {candidate_id} is not available in this run.",
                        "unavailable_reason": pack_unavailable_reason(
                            candidate_spec,
                            browser_available=self._browser_available,
                            desktop_available=self._desktop_available,
                        ),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            active.add(candidate_id)
            newly_active.append(candidate_id)

        ctx.active_capability_packs = active
        payload = {
            "ok": True,
            "activated": newly_active,
            "active_capability_packs": sorted(active),
            "available_tools_after_activation": sorted(
                tool.name
                for tool in self._tools_provider()
                if tool.always_available or _tool_pack_id(tool) in active
            ),
            "activated_pack_details": [
                {
                    "pack_id": candidate_id,
                    "tool_names": sorted(tool.name for tool in grouped.get(candidate_id, [])),
                }
                for candidate_id in newly_active
            ],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)


def make_capability_pack_tools(
    *,
    tools_provider: Callable[[], list[Tool]],
    browser_available: bool,
    desktop_available: bool,
) -> list[Tool]:
    return [
        ListCapabilityPacksTool(
            tools_provider=tools_provider,
            browser_available=browser_available,
            desktop_available=desktop_available,
        ),
        DescribeCapabilityPackTool(
            tools_provider=tools_provider,
            browser_available=browser_available,
            desktop_available=desktop_available,
        ),
        ActivateCapabilityPackTool(
            tools_provider=tools_provider,
            browser_available=browser_available,
            desktop_available=desktop_available,
        ),
    ]
