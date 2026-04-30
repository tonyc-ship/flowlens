"""Socai MCP entry point.

Exposes the Socai tool registry (`socai.tools.build_tools`) over
the MCP stdio transport. The tool set is identical to what the internal
agent loop sees — host models (Claude Desktop / Cursor / Claude Code)
get the same fine + macro tools the internal agent would, so behavior
stays consistent across internal and external consumers.

Socai itself runs no LLM here — the host plans, Socai provides
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
_idle_shutdown_task: asyncio.Task | None = None
_active_tool_calls = 0


def _bridge_log_to_stderr(action: str, detail: str = "") -> None:
    try:
        print(f"[bridge] {action}: {detail}", file=sys.stderr, flush=True)
    except Exception:
        pass


def _default_port() -> int:
    raw = os.environ.get("SOCAI_MCP_PORT") or os.environ.get("SOCAI_BRIDGE_PORT") or "8765"
    try:
        return int(raw)
    except ValueError:
        return 8765


def _idle_timeout_seconds() -> float:
    raw = os.environ.get("SOCAI_MCP_IDLE_TIMEOUT_SECONDS", "30")
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 30.0


def _cancel_idle_shutdown_locked() -> None:
    global _idle_shutdown_task
    current = asyncio.current_task()
    if _idle_shutdown_task is not None and _idle_shutdown_task is not current:
        _idle_shutdown_task.cancel()
    if _idle_shutdown_task is not current:
        _idle_shutdown_task = None


async def _stop_runtime_locked(reason: str) -> None:
    """Stop the live browser bridge. Caller must hold _start_lock."""
    global _bridge, _ctx, _tools_by_name
    bridge = _bridge
    _bridge = None
    _ctx = None
    _tools_by_name = {}
    if bridge is None:
        return
    try:
        await bridge.stop()
        _bridge_log_to_stderr("bridge_stopped", reason)
    except Exception as exc:
        _bridge_log_to_stderr("bridge_stop_error", f"{reason}: {exc}")


async def _idle_shutdown_after(delay_s: float) -> None:
    try:
        await asyncio.sleep(delay_s)
    except asyncio.CancelledError:
        return
    async with _start_lock:
        if _active_tool_calls > 0 or _bridge is None:
            return
        await _stop_runtime_locked(f"idle for {delay_s:g}s")


def _schedule_idle_shutdown_locked() -> None:
    global _idle_shutdown_task
    _cancel_idle_shutdown_locked()
    delay_s = _idle_timeout_seconds()
    if delay_s <= 0 or _bridge is None or _active_tool_calls > 0:
        return
    _idle_shutdown_task = asyncio.create_task(_idle_shutdown_after(delay_s))
    _bridge_log_to_stderr("idle_shutdown_scheduled", f"{delay_s:g}s")


async def _ensure_runtime_for_call() -> tuple[ExtensionBridge, ToolContext, dict[str, Tool]]:
    """Lazily start the bridge and mark one MCP tool call as active."""
    global _bridge, _ctx, _tools_by_name, _active_tool_calls
    async with _start_lock:
        _cancel_idle_shutdown_locked()
        _active_tool_calls += 1
        if _bridge is not None and _ctx is not None and _tools_by_name:
            return _bridge, _ctx, _tools_by_name

        port = _default_port()
        bridge = ExtensionBridge(port=port)
        bridge.on_log(_bridge_log_to_stderr)
        try:
            await bridge.start()
            await bridge.wait_for_connection(timeout=120)

            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            run_dir = Path(tempfile.gettempdir()) / f"socai_mcp_{stamp}"
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
        except Exception:
            _active_tool_calls = max(0, _active_tool_calls - 1)
            try:
                await bridge.stop()
            except Exception:
                pass
            raise


async def _release_runtime_after_call() -> None:
    global _active_tool_calls
    async with _start_lock:
        _active_tool_calls = max(0, _active_tool_calls - 1)
        _schedule_idle_shutdown_locked()


async def _shutdown_runtime(reason: str) -> None:
    async with _start_lock:
        _cancel_idle_shutdown_locked()
        await _stop_runtime_locked(reason)


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
    server: Server = Server("socai")

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
        acquired_runtime = False
        try:
            _, ctx, tools = await _ensure_runtime_for_call()
            acquired_runtime = True
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
        finally:
            if acquired_runtime:
                await _release_runtime_after_call()

    return server


async def _run_stdio() -> None:
    server = build_server()
    try:
        async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                InitializationOptions(
                    server_name="socai",
                    server_version="0.1.0",
                    capabilities=server.get_capabilities(
                        notification_options=NotificationOptions(),
                        experimental_capabilities={},
                    ),
                ),
            )
    finally:
        await _shutdown_runtime("stdio exit")


def main() -> None:
    try:
        asyncio.run(_run_stdio())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
