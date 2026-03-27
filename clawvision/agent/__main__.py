"""Run XHS agents from command line.

Usage:
    # Topic research
    python -m clawvision.agent "露营装备"
    python -m clawvision.agent "露营装备" --keywords "露营装备推荐,露营好物"

    # User analysis
    python -m clawvision.agent --user "https://www.xiaohongshu.com/user/profile/xxx"
    python -m clawvision.agent --user <user_id>
"""

import argparse
import asyncio

from .xhs import run_research, run_user_analysis


def main():
    parser = argparse.ArgumentParser(description="XHS Research Agent")
    parser.add_argument("topic", nargs="?", default=None, help="Research topic")
    parser.add_argument("--keywords", "-k", default=None, help="Comma-separated keywords")
    parser.add_argument("--user", "-u", default=None, help="User profile URL or ID for user analysis")
    parser.add_argument("--output", "-o", default=None, help="Output directory")
    parser.add_argument("--port", "-p", type=int, default=8765, help="WebSocket port")
    parser.add_argument("--watch", "-w", action="store_true", help="Watch mode: foreground window with real-time activity sidebar")
    args = parser.parse_args()

    if args.user:
        output = args.output or "user_analysis"
        asyncio.run(run_user_analysis(
            user_url=args.user,
            output_dir=output,
            port=args.port,
            watch=args.watch,
        ))
    else:
        topic = args.topic
        if not topic:
            topic = input("Research topic: ").strip()
            if not topic:
                print("No topic provided. Use --user for user analysis.")
                return

        keywords = None
        if args.keywords:
            keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]

        output = args.output or "research_output"
        asyncio.run(run_research(
            topic=topic,
            keywords=keywords,
            output_dir=output,
            port=args.port,
            watch=args.watch,
        ))


if __name__ == "__main__":
    main()
