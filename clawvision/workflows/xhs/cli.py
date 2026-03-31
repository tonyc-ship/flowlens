"""CLI for Xiaohongshu workflows."""

from __future__ import annotations

import argparse
import asyncio

from .research import run_research
from .user_analysis import run_user_analysis


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Xiaohongshu workflows")
    parser.add_argument("topic", nargs="?", default=None, help="Research topic")
    parser.add_argument("--keywords", "-k", default=None, help="Comma-separated keywords")
    parser.add_argument("--user", "-u", default=None, help="User profile URL or ID for creator analysis")
    parser.add_argument("--output", "-o", default=None, help="Output directory")
    parser.add_argument("--port", "-p", type=int, default=8765, help="WebSocket port")
    parser.add_argument(
        "--watch",
        "-w",
        action="store_true",
        help="Watch mode: foreground window with real-time activity sidebar",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.user:
        output = args.output or "user_analysis"
        asyncio.run(
            run_user_analysis(
                user_url=args.user,
                output_dir=output,
                port=args.port,
                watch=args.watch,
            )
        )
        return 0

    topic = args.topic
    if not topic:
        topic = input("Research topic: ").strip()
        if not topic:
            print("No topic provided. Use --user for creator analysis.")
            return 1

    keywords = None
    if args.keywords:
        keywords = [keyword.strip() for keyword in args.keywords.split(",") if keyword.strip()]

    output = args.output or "research_output"
    asyncio.run(
        run_research(
            topic=topic,
            keywords=keywords,
            output_dir=output,
            port=args.port,
            watch=args.watch,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
