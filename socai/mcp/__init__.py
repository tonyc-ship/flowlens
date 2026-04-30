"""SocAI MCP server package.

Exposes a single `socai-mcp` entry point (see `socai.mcp.server`).
Site-specific tool sets live under `socai.mcp.sites` — each module
registers its own tools on the shared FastMCP app so new sites can be
added without touching the transport layer.
"""
