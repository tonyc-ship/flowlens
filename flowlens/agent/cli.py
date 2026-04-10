"""CLI entry point for the agent loop."""

from __future__ import annotations

import argparse
import asyncio
import sys

from ..core.auth import default_cloud_model


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
        help="Model ID override. Defaults to the best available cloud model or the selected local alias.",
    )
    parser.add_argument(
        "--backend",
        choices=["anthropic", "openai", "qwen-local", "ui-tars-local"],
        default=None,
        help="LLM backend override: anthropic, openai, qwen-local, or ui-tars-local",
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
    elif args.backend == "openai":
        model = default_cloud_model(provider="openai")
    elif args.backend == "anthropic":
        model = default_cloud_model(provider="anthropic")
    else:
        model = default_cloud_model()

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
