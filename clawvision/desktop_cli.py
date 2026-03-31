"""Desktop-oriented entrypoint for the Tauri shell."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from .reasoning.tasks import make_creator_growth_breakdown_task, make_topic_research_task
from .workflows.xhs import XHSTaskRunner

PROFILE_URL_RE = re.compile(r"https?://www\.xiaohongshu\.com/user/profile/[^\s]+")
CREATOR_HINT_RE = re.compile(r"(作者|博主|起号|账号|profile|creator)", re.IGNORECASE)
TOPIC_PREFIX_RE = re.compile(r"^(帮我)?(研究|分析|调研|搜索|看看|看一下|看|做一个)\s*")


@dataclass
class DesktopTaskRequest:
    kind: str
    prompt: str
    topic: str = ""
    profile_url: str = ""


def _clean_topic(prompt: str) -> str:
    topic = TOPIC_PREFIX_RE.sub("", prompt.strip())
    return topic.strip("：:，,。.！？!? ") or prompt.strip()


def infer_desktop_task(prompt: str) -> DesktopTaskRequest:
    trimmed = prompt.strip()
    if not trimmed:
        raise ValueError("Task prompt is empty")

    match = PROFILE_URL_RE.search(trimmed)
    if match:
        return DesktopTaskRequest(
            kind="creator_growth_breakdown",
            prompt=trimmed,
            profile_url=match.group(0),
        )

    if CREATOR_HINT_RE.search(trimmed):
        raise ValueError("Creator tasks currently require a Xiaohongshu profile URL in the prompt.")

    return DesktopTaskRequest(
        kind="topic_research",
        prompt=trimmed,
        topic=_clean_topic(trimmed),
    )


async def _run_request(request: DesktopTaskRequest, *, output_root: str, port: int) -> dict:
    task = (
        make_creator_growth_breakdown_task(request.profile_url)
        if request.kind == "creator_growth_breakdown"
        else make_topic_research_task(request.topic)
    )

    runner = XHSTaskRunner(output_root=output_root, port=port, record_interval=1.5, watch=True)
    result = await runner.run(task)
    return {
        "request": asdict(request),
        "workflow_report_dir": result.get("workflow_report_dir", ""),
        "session_gif": result.get("session_gif", ""),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Desktop bridge for ClawVision.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run a prompt through the XHS task layer.")
    run_parser.add_argument("--prompt", required=True, help="Free-form task prompt from the desktop app.")
    run_parser.add_argument("--output-root", default="task_runs/desktop_app", help="Task output root.")
    run_parser.add_argument("--port", type=int, default=8765, help="Extension websocket port.")
    run_parser.add_argument("--dry-run", action="store_true", help="Only infer the request; do not run.")

    args = parser.parse_args(argv)

    if args.command == "run":
        request = infer_desktop_task(args.prompt)
        if args.dry_run:
            print(json.dumps(asdict(request), ensure_ascii=False, indent=2))
            return 0

        result = asyncio.run(
            _run_request(request, output_root=str(Path(args.output_root)), port=args.port)
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    parser.error(f"Unknown command: {args.command}")
    return 2
