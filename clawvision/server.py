"""Compatibility stub for the archived screen-level MCP server."""

from __future__ import annotations

from pathlib import Path


def main() -> None:
    archive_path = Path(__file__).resolve().parent.parent / "archive" / "legacy_mcp"
    raise SystemExit(
        "clawvision.server has been archived.\n"
        "Use `clawvision` or `python -m clawvision` for the active XHS agent CLI.\n"
        f"Archived reference files: {archive_path}"
    )


if __name__ == "__main__":
    main()
