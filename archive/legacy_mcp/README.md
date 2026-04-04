# Legacy MCP Route

This folder keeps the old screen-level MCP approach out of the active code path.

- `server_snapshot.py` is the last in-tree snapshot of the old MCP server.
- `screen.py` is the old macOS screen capture / input helper used by that route.
- `manual_screen_smoke.py` is the old manual smoke script for `screen.py`.

These files are not part of the supported runtime anymore. The maintained path is:

- Python orchestration in `flowlens.agent.xhs`
- Browser execution via `chrome_extension/`
- Shared media / vision utilities in `flowlens.agent.media` and `flowlens.vision`
