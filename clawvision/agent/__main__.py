"""Run the XHS Research Agent from command line.

Usage:
    python -m clawvision.agent                              # interactive
    python -m clawvision.agent "露营装备" --keywords "露营装备推荐,露营好物"
"""

import argparse
import asyncio

from .xhs_agent import run_research


def main():
    parser = argparse.ArgumentParser(description="XHS Research Agent")
    parser.add_argument("topic", nargs="?", default=None, help="Research topic")
    parser.add_argument("--keywords", "-k", default=None, help="Comma-separated keywords")
    parser.add_argument("--output", "-o", default="research_output", help="Output directory")
    parser.add_argument("--port", "-p", type=int, default=8765, help="WebSocket port")
    args = parser.parse_args()

    topic = args.topic
    if not topic:
        topic = input("Research topic: ").strip()
        if not topic:
            print("No topic provided.")
            return

    keywords = None
    if args.keywords:
        keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]

    asyncio.run(run_research(
        topic=topic,
        keywords=keywords,
        output_dir=args.output,
        port=args.port,
    ))


if __name__ == "__main__":
    main()
