#!/usr/bin/env python3
"""Run the desktop CDP diagnostic sequence and save an artifact bundle."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from socai.cdp.diagnostics import ControlledTabConfig, run_controlled_tab_diagnostic
from socai.cdp.discovery import discover_chrome_cdp
from socai.cdp.targets import TargetListConfig, list_chrome_targets
from socai.platforms.xhs.cdp_diagnostics import XHSCdpProbeConfig, run_xhs_cdp_probe


@dataclass(frozen=True)
class StepResult:
    name: str
    status: str
    elapsed: float
    data: dict[str, Any]
    error: str | None = None


async def timed_step(name: str, func: Callable[[], Awaitable[dict[str, Any]]]) -> StepResult:
    """Run a diagnostic step and capture timing/error metadata."""

    started = time.monotonic()
    try:
        data = await func()
        return StepResult(name, data.get("status", "unknown"), time.monotonic() - started, data)
    except Exception as exc:  # noqa: BLE001 - diagnostic boundary
        return StepResult(name, "error", time.monotonic() - started, {}, str(exc))


async def run_demo(args: argparse.Namespace) -> dict[str, Any]:
    """Run discovery, targets, controlled-tab, and XHS diagnostics."""

    run_dir = Path(args.output_dir).expanduser().resolve()
    run_dir.mkdir(parents=True, exist_ok=True)

    steps = [
        await timed_step("discovery", run_discovery),
        await timed_step("targets", lambda: list_chrome_targets(TargetListConfig(timeout=args.timeout))),
        await timed_step(
            "controlled_tab",
            lambda: run_controlled_tab_diagnostic(
                ControlledTabConfig(timeout=args.timeout, screenshot_path=run_dir / "controlled_tab.png")
            ),
        ),
        await timed_step(
            "xhs_probe",
            lambda: run_xhs_cdp_probe(XHSCdpProbeConfig(timeout=args.timeout, output_dir=run_dir)),
        ),
    ]
    return finish(steps, run_dir)


async def run_discovery() -> dict[str, Any]:
    """Async wrapper for synchronous Chrome discovery."""

    return discover_chrome_cdp()


def finish(steps: list[StepResult], run_dir: Path) -> dict[str, Any]:
    """Write per-step data and a compact demo summary."""

    all_ok = all(step.status not in {"error", "failed", "connection_failed"} for step in steps)
    summary = {
        "overall": "pass" if all_ok else "fail",
        "run_dir": str(run_dir),
        "steps": [summarize_step(step) for step in steps],
    }

    (run_dir / "demo_result.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    for step in steps:
        (run_dir / f"{step.name}.json").write_text(
            json.dumps(step.data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    return summary


def summarize_step(step: StepResult) -> dict[str, Any]:
    """Return a compact, human-scannable step summary."""

    data = step.data
    summary = {
        "name": step.name,
        "status": step.status,
        "elapsed_seconds": round(step.elapsed, 2),
        "error": step.error,
    }
    for key in (
        "target_count",
        "shown_target_count",
        "marked_title",
        "target_id",
        "screenshot_path",
        "run_dir",
    ):
        if key in data:
            summary[key] = data[key]

    diagnostics = data.get("diagnostics") or {}
    for key in ("landed_url", "readyState", "scrollY", "possibleSecurityVerification", "possibleLoginPrompt"):
        if key in diagnostics:
            summary[key] = diagnostics[key]

    screenshots = data.get("screenshots") or {}
    if screenshots:
        summary["screenshots"] = screenshots

    return summary


def default_run_dir() -> str:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return str(Path("task_runs") / f"desktop_cdp_demo_{stamp}")


def print_human(result: dict[str, Any]) -> None:
    print(f"Socai desktop CDP demo: {result['overall']}")
    print(f"Run dir: {result['run_dir']}")
    print()
    for step in result.get("steps", []):
        marker = "✅" if step["status"] not in {"error", "failed", "connection_failed"} else "❌"
        print(f"  {marker} {step['name']}: {step['status']} ({step['elapsed_seconds']}s)")
        if step.get("error"):
            print(f"     Error: {step['error'][:140]}")
        if step.get("marked_title"):
            print(f"     Title: {step['marked_title']}")
        if step.get("landed_url"):
            print(f"     URL: {step['landed_url'][:120]}")
        if step.get("screenshot_path"):
            print(f"     Screenshot: {step['screenshot_path']}")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON only")
    parser.add_argument("--output-dir", default=default_run_dir(), help="Directory for demo artifacts")
    parser.add_argument("--timeout", type=float, default=30.0, help="CDP operation timeout in seconds")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    result = asyncio.run(run_demo(args))

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_human(result)

    return 0 if result["overall"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
