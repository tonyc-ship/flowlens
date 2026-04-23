"""FlowLens MCP entry point.

Single stdio server that hosts tools from one or more site modules.
Sites are selected via the `FLOWLENS_MCP_SITES` env var (comma list);
default is "xhs". Each site module exposes a `register(mcp)` callable
that attaches its tools to the shared FastMCP app.
"""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

from .sites import xhs as xhs_site


SITE_REGISTRY = {
    "xhs": xhs_site.register,
    "xiaohongshu": xhs_site.register,
}


def _selected_sites() -> list[str]:
    raw = os.environ.get("FLOWLENS_MCP_SITES", "xhs")
    names = [name.strip().lower() for name in raw.split(",") if name.strip()]
    seen: set = set()
    ordered: list[str] = []
    for name in names:
        if name in SITE_REGISTRY and name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered or ["xhs"]


def build_app() -> FastMCP:
    sites = _selected_sites()
    mcp = FastMCP(
        name="flowlens",
        instructions=(
            "FlowLens MCP — high-level site-aware tools backed by the user's "
            "real logged-in Chrome (via the FlowLens Chrome extension). "
            "Because actions run inside the authenticated session, they are "
            "far more resilient to anti-bot than headless scrapers.\n\n"
            "Host priors:\n"
            "- Always start with xhs_session_check to confirm connection and login.\n"
            "- Prefer xhs_topic_scan for a topic; prefer xhs_search_notes + xhs_read_note\n"
            "  only when the user wants per-note control.\n"
            "- If a tool reports anti-bot signals (security_verification, error_page,\n"
            "  'scan on phone'), STOP and tell the user — do not retry in a tight loop.\n"
            f"Sites enabled this session: {', '.join(sites)}"
        ),
    )
    for name in sites:
        SITE_REGISTRY[name](mcp)
    return mcp


def main() -> None:
    build_app().run("stdio")


if __name__ == "__main__":
    main()
