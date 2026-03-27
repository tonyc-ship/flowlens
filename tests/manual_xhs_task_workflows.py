"""Manual task runner for high-value Xiaohongshu workflows.

Runs structured tasks through:
  StructuredTask -> TaskAgent -> XHSTaskRunner -> XHS workflow

Produces:
  - task-level HTML/JSON report
  - workflow-level HTML/JSON report
  - session recording GIF
  - reasoning log + screenshot verification
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from clawvision.agent import (  # noqa: E402
    XHSTaskRunner,
    make_creator_growth_breakdown_task,
    make_topic_research_task,
)


DEFAULT_CREATOR_URL = "https://www.xiaohongshu.com/user/profile/665e81660000000003033638"


def build_preset_task(preset: str):
    if preset == "topic_research":
        return make_topic_research_task(
            "护肤干货",
            preset_keywords=["护肤干货", "洗脸方法", "洗面奶选择"],
        )
    if preset == "creator_growth":
        return make_creator_growth_breakdown_task(
            DEFAULT_CREATOR_URL,
            creator_name="晞玥的运营笔记",
        )
    raise ValueError(f"Unknown preset: {preset}")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--preset",
        choices=["topic_research", "creator_growth"],
        help="Run one of the predefined high-value task presets.",
    )
    parser.add_argument("--topic", help="Override and run a topic research task.")
    parser.add_argument("--user-url", help="Override and run a creator growth breakdown task.")
    parser.add_argument("--creator-name", default="", help="Optional creator display name.")
    parser.add_argument("--output-root", default="task_runs", help="Root directory for task reports.")
    parser.add_argument("--watch", action="store_true", help="Watch mode: foreground window with real-time activity sidebar.")
    args = parser.parse_args()

    if args.topic:
        task = make_topic_research_task(args.topic)
    elif args.user_url:
        task = make_creator_growth_breakdown_task(args.user_url, creator_name=args.creator_name)
    else:
        task = build_preset_task(args.preset or "topic_research")

    runner = XHSTaskRunner(output_root=args.output_root, port=8765, record_interval=1.5, watch=args.watch)
    result = await runner.run(task)

    print(f"\nTask report: {Path(result['workflow_report_dir']).parent / 'report.html'}")
    print(f"Workflow report: {Path(result['workflow_report_dir']) / 'report.html'}")
    print(f"Recording: {result['session_gif']}")


if __name__ == "__main__":
    asyncio.run(main())
