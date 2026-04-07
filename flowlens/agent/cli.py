"""CLI entry point for the agent loop."""

from __future__ import annotations

import argparse
import asyncio
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="flowlens agent",
        description="Run an LLM-driven browser automation agent.",
    )
    parser.add_argument("task", help="Natural language task description")
    parser.add_argument(
        "--max-turns",
        type=int,
        default=40,
        help="Maximum LLM turns (default: 40)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model ID (default: claude-sonnet-4-6, or qwen-local for local)",
    )
    parser.add_argument(
        "--backend",
        choices=["anthropic", "qwen-local", "ui-tars-local"],
        default="anthropic",
        help="LLM backend: anthropic (default), qwen-local, or ui-tars-local",
    )
    parser.add_argument(
        "--run-dir",
        default=None,
        help="Directory for screenshots and artifacts",
    )

    args = parser.parse_args(argv)

    # Resolve model based on backend
    if args.model:
        model = args.model
    elif args.backend == "qwen-local":
        model = "qwen-local"
    elif args.backend == "ui-tars-local":
        model = "ui-tars-local"
    else:
        model = "claude-sonnet-4-6"

    from .loop import run_agent

    result = asyncio.run(
        run_agent(
            task=args.task,
            max_turns=args.max_turns,
            model=model,
            run_dir=args.run_dir,
        )
    )

    print(f"\n{'='*60}")
    print(f"Agent completed in {result['turns']} turns")
    print(f"Run directory: {result['run_dir']}")
    print(f"Screenshots: {len(result['screenshots'])}")
    print(f"{'='*60}")
    print(result["result"])

    return 0
