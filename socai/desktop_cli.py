"""Backward-compatible wrapper around the unified SocAI run CLI."""

from __future__ import annotations

from .run_cli import UnifiedRunPlan, infer_run_plan, main as _run_main

# Legacy alias kept for compatibility with older imports/tests.
infer_desktop_task = infer_run_plan
DesktopTaskRequest = UnifiedRunPlan


def main(argv: list[str] | None = None) -> int:
    args = list(argv or [])
    if args[:1] == ["run"]:
        args = args[1:]
    return _run_main(args)


__all__ = ["DesktopTaskRequest", "infer_desktop_task", "infer_run_plan", "main"]
