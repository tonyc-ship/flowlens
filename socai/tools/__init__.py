"""Unified SocAI tool registry.

Single source of truth for the set of Tool instances available to any
LLM consumer — the internal agent loop (`socai.agent.loop`) and the
external MCP server (`socai.mcp.server`) both import `build_tools`
from here so the two surfaces never drift.
"""

from .registry import build_tools

__all__ = ["build_tools"]
