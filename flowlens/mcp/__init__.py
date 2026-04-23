"""FlowLens MCP server package.

Exposes a single `flowlens-mcp` entry point (see `flowlens.mcp.server`).
Site-specific tool sets live under `flowlens.mcp.sites` — each module
registers its own tools on the shared FastMCP app so new sites can be
added without touching the transport layer.
"""
