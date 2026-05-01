#!/usr/bin/env python3
"""Run the full Socai prototype demo and save a reproducible artifact bundle.

Session 6 scope:
- Run each prototype step in sequence: discovery → targets → controlled tab → XHS probe
- Save all screenshots, JSON results, timing, and diagnostics to a timestamped run directory
- Generate a summary report

This script is the CLI equivalent of clicking every button in the Tauri app.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from chrome_discovery import discover_chrome_cdp


@dataclass
class StepResult:
    name: str
    status: str
    elapsed: float
    data: dict[str, Any]
    error: str | None = None


async def run_discovery() -> dict[str, Any]:
    return discover_chrome_cdp()


async def run_targets(browser_ws_url: str, timeout: float) -> dict[str, Any]:
    from cdp_connect import connect_cdp_with_retry

    client = await connect_cdp_with_retry(browser_ws_url, per_attempt_timeout=timeout)
    try:
        result = await asyncio.wait_for(client.send_raw("Target.getTargets"), timeout=timeout)
        targets = result.get("targetInfos", [])
        pages = [
            t for t in targets
            if t.get("type") == "page"
            and not (t.get("url") or "").startswith(("chrome://", "devtools://", "chrome-extension://", "about:"))
        ]
        return {
            "status": "connected",
            "total_targets": len(targets),
            "page_targets": len(pages),
        }
    finally:
        try:
            await asyncio.wait_for(client.stop(), timeout=2)
        except Exception:
            pass


async def run_controlled_tab(browser_ws_url: str, timeout: float, run_dir: Path) -> dict[str, Any]:
    from cdp_connect import connect_cdp_with_retry
    from cdp_controlled_tab import SocaiCDPPage, SOCAI_TITLE_PREFIX, create_controlled_tab

    screenshot_path = run_dir / "controlled_tab.png"
    client = await connect_cdp_with_retry(browser_ws_url, per_attempt_timeout=timeout)
    try:
        page = await create_controlled_tab(client, "about:blank")
        await page.mark_title(f"{SOCAI_TITLE_PREFIX} — Demo")
        await asyncio.sleep(0.5)

        title_result = await page.evaluate_js("document.title")
        await page.capture_screenshot(screenshot_path)

        return {
            "status": "controlled_tab_ready",
            "title": title_result.value,
            "target_id": page.target_id,
            "screenshot": str(screenshot_path),
        }
    finally:
        try:
            await asyncio.wait_for(client.stop(), timeout=2)
        except Exception:
            pass


async def run_xhs_probe(browser_ws_url: str, timeout: float, run_dir: Path) -> dict[str, Any]:
    from cdp_connect import connect_cdp_with_retry
    from cdp_controlled_tab import SocaiCDPPage, SOCAI_TITLE_PREFIX, create_controlled_tab
    from cdp_xhs_probe import read_page_probe

    before_path = run_dir / "xhs_before_scroll.png"
    after_path = run_dir / "xhs_after_scroll.png"

    client = await connect_cdp_with_retry(browser_ws_url, per_attempt_timeout=timeout)
    try:
        page = await create_controlled_tab(client, "about:blank")
        await page.mark_title(f"{SOCAI_TITLE_PREFIX} — XHS")
        await page.navigate("https://www.xiaohongshu.com/explore", wait_seconds=6.0)
        await page.mark_title(f"{SOCAI_TITLE_PREFIX} — XHS")

        before_state = await read_page_probe(page)
        await page.capture_screenshot(before_path)

        scroll_y = await page.scroll(650)
        await asyncio.sleep(1.5)
        after_state = await read_page_probe(page)
        await page.capture_screenshot(after_path)

        landed_url = after_state.get("url") or before_state.get("url") or ""
        operated = "xiaohongshu.com" in landed_url and after_path.exists()

        return {
            "status": "xhs_probe_ready" if operated else "xhs_probe_inconclusive",
            "landed_url": landed_url,
            "title": after_state.get("title"),
            "scrollY": scroll_y,
            "before_screenshot": str(before_path),
            "after_screenshot": str(after_path),
            "before_state": before_state,
            "after_state": after_state,
        }
    finally:
        try:
            await asyncio.wait_for(client.stop(), timeout=2)
        except Exception:
            pass


async def run_demo(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = Path(args.output_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    steps: list[StepResult] = []
    browser_ws_url: str | None = None

    # Step 1: Discovery
    t0 = time.monotonic()
    try:
        discovery = await run_discovery()
        status = discovery.get("status", "unknown")
        if status == "cdp_available":
            browser_ws_url = discovery.get("endpoint", {}).get("browser_ws_url")
        steps.append(StepResult("discovery", status, time.monotonic() - t0, discovery))
    except Exception as exc:
        steps.append(StepResult("discovery", "error", time.monotonic() - t0, {}, str(exc)))

    if not browser_ws_url:
        return finish(steps, run_dir)

    # Step 2: Targets
    t0 = time.monotonic()
    try:
        targets = await run_targets(browser_ws_url, args.timeout)
        steps.append(StepResult("targets", targets.get("status", "unknown"), time.monotonic() - t0, targets))
    except Exception as exc:
        steps.append(StepResult("targets", "error", time.monotonic() - t0, {}, str(exc)))

    # Step 3: Controlled tab
    t0 = time.monotonic()
    try:
        tab = await run_controlled_tab(browser_ws_url, args.timeout, run_dir)
        steps.append(StepResult("controlled_tab", tab.get("status", "unknown"), time.monotonic() - t0, tab))
    except Exception as exc:
        steps.append(StepResult("controlled_tab", "error", time.monotonic() - t0, {}, str(exc)))

    # Step 4: XHS probe
    t0 = time.monotonic()
    try:
        xhs = await run_xhs_probe(browser_ws_url, args.timeout, run_dir)
        steps.append(StepResult("xhs_probe", xhs.get("status", "unknown"), time.monotonic() - t0, xhs))
    except Exception as exc:
        steps.append(StepResult("xhs_probe", "error", time.monotonic() - t0, {}, str(exc)))

    return finish(steps, run_dir)


def finish(steps: list[StepResult], run_dir: Path) -> dict[str, Any]:
    all_ok = all(s.status not in ("error", "setup_required") for s in steps)
    summary = {
        "overall": "pass" if all_ok else "fail",
        "run_dir": str(run_dir),
        "steps": [
            {
                "name": s.name,
                "status": s.status,
                "elapsed_seconds": round(s.elapsed, 2),
                "error": s.error,
                **{k: v for k, v in s.data.items() if k in (
                    "total_targets", "page_targets", "title", "target_id",
                    "screenshot", "landed_url", "scrollY",
                    "before_screenshot", "after_screenshot",
                )},
            }
            for s in steps
        ],
    }

    (run_dir / "demo_result.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    # Write per-step full data
    for step in steps:
        (run_dir / f"{step.name}.json").write_text(
            json.dumps(step.data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    return summary


def default_run_dir() -> str:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return str(Path("app/demo_runs") / f"demo_{stamp}")


def print_human(result: dict[str, Any]) -> None:
    print(f"Socai demo run: {result['overall']}")
    print(f"Run dir: {result['run_dir']}")
    print()
    for step in result.get("steps", []):
        marker = "✅" if step["status"] not in ("error", "setup_required") else "❌"
        print(f"  {marker} {step['name']}: {step['status']} ({step['elapsed_seconds']}s)")
        if step.get("error"):
            print(f"     Error: {step['error'][:120]}")
        if step.get("title"):
            print(f"     Title: {step['title']}")
        if step.get("landed_url"):
            print(f"     URL: {step['landed_url'][:100]}")


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
