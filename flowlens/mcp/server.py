"""FlowLens MCP entry point.

Exposes the FlowLens tool registry (`flowlens.tools.build_tools`) over
the MCP stdio transport. The tool set is identical to what the internal
agent loop sees — host models (Claude Desktop / Cursor / Claude Code)
get the same fine + macro tools the internal agent would, so behavior
stays consistent across internal and external consumers.

FlowLens itself runs no LLM here — the host plans, FlowLens provides
browser-level capabilities backed by the user's real logged-in Chrome.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import mcp.server.stdio
import mcp.types as mcp_types
from mcp.server import NotificationOptions, Server
from mcp.server.models import InitializationOptions

from ..agent.tool import Tool, ToolContext
from ..core.bridge import ExtensionBridge
from ..perception.media import MediaProcessor
from ..tools import build_tools


_bridge: ExtensionBridge | None = None
_ctx: ToolContext | None = None
_tools_by_name: dict[str, Tool] = {}
_start_lock = asyncio.Lock()


def _bridge_log_to_stderr(action: str, detail: str = "") -> None:
    try:
        print(f"[bridge] {action}: {detail}", file=sys.stderr, flush=True)
    except Exception:
        pass


def _default_port() -> int:
    raw = os.environ.get("FLOWLENS_MCP_PORT") or os.environ.get("FLOWLENS_BRIDGE_PORT") or "8765"
    try:
        return int(raw)
    except ValueError:
        return 8765


async def _ensure_runtime() -> tuple[ExtensionBridge, ToolContext, dict[str, Tool]]:
    """Lazily start the bridge and build the tool registry on first use."""
    global _bridge, _ctx, _tools_by_name
    async with _start_lock:
        if _bridge is not None and _ctx is not None and _tools_by_name:
            return _bridge, _ctx, _tools_by_name

        port = _default_port()
        bridge = ExtensionBridge(port=port)
        bridge.on_log(_bridge_log_to_stderr)
        await bridge.start()
        await bridge.wait_for_connection(timeout=120)

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_dir = Path(tempfile.gettempdir()) / f"flowlens_mcp_{stamp}"
        run_dir.mkdir(parents=True, exist_ok=True)
        ctx = ToolContext(run_dir=run_dir)
        media = MediaProcessor()

        tools = build_tools(
            bridge,
            ext_bridge=bridge,
            media=media,
            site_media=media,
        )
        tools_by_name = {tool.name: tool for tool in tools}

        _bridge = bridge
        _ctx = ctx
        _tools_by_name = tools_by_name
        return bridge, ctx, tools_by_name


def _tool_catalog_for_listing() -> list[Tool]:
    """Return a Tool list for list_tools without needing a live bridge.

    Builds throwaway instances with dummy args so the schemas are available
    before the extension has connected. Tools never get executed from this
    catalog — they're only read for name / description / parameters.
    """
    # Lightweight media stub — tools only read `media` during execute().
    class _StubMedia:
        pass
    stub_media = _StubMedia()

    class _StubBridge:
        async def get_tab_info(self):
            return {}
    stub_bridge = _StubBridge()

    return build_tools(
        stub_bridge,  # type: ignore[arg-type]
        ext_bridge=stub_bridge,  # type: ignore[arg-type]
        media=stub_media,  # type: ignore[arg-type]
        site_media=stub_media,  # type: ignore[arg-type]
    )


def _to_mcp_tool(tool: Tool) -> mcp_types.Tool:
    return mcp_types.Tool(
        name=tool.name,
        description=tool.description,
        inputSchema=tool.parameters,
    )


def _coerce_result(value: Any) -> list[mcp_types.TextContent]:
    """Normalize a Tool.execute result to MCP content blocks."""
    if value is None:
        return [mcp_types.TextContent(type="text", text="")]
    if isinstance(value, str):
        return [mcp_types.TextContent(type="text", text=value)]
    if isinstance(value, list):
        blocks: list[mcp_types.TextContent] = []
        for item in value:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    blocks.append(mcp_types.TextContent(type="text", text=str(item.get("text", ""))))
                else:
                    blocks.append(mcp_types.TextContent(type="text", text=json.dumps(item, ensure_ascii=False)))
            else:
                blocks.append(mcp_types.TextContent(type="text", text=str(item)))
        return blocks or [mcp_types.TextContent(type="text", text="")]
    return [mcp_types.TextContent(type="text", text=json.dumps(value, ensure_ascii=False, default=str))]


def build_server() -> Server:
    server: Server = Server("flowlens")

    @server.list_tools()
    async def _list_tools() -> list[mcp_types.Tool]:
        # Prefer live catalog after the bridge has started (so the schemas are
        # exactly what execute() will validate against). Fall back to a dry
        # catalog for the very first list_tools before any execute call.
        if _tools_by_name:
            return [_to_mcp_tool(tool) for tool in _tools_by_name.values()]
        return [_to_mcp_tool(tool) for tool in _tool_catalog_for_listing()]

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict | None) -> list[mcp_types.TextContent]:
        _, ctx, tools = await _ensure_runtime()
        tool = tools.get(name)
        if tool is None:
            return [mcp_types.TextContent(type="text", text=f"Unknown tool: {name}")]
        params = arguments or {}
        ctx.active_tool_name = name
        ctx.turn += 1
        try:
            value = await tool.execute(params, ctx)
        except Exception as exc:
            return [mcp_types.TextContent(type="text", text=f"Tool {name} failed: {exc}")]
        return _coerce_result(value)

    return server


async def _run_stdio() -> None:
    server = build_server()
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="flowlens",
                server_version="0.1.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


def main() -> None:
    try:
        asyncio.run(_run_stdio())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
