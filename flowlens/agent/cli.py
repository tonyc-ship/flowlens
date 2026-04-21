"""CLI entry point for the agent loop."""

from __future__ import annotations

import argparse
import asyncio

from ..core.auth import default_cloud_model
from ..core.bridge import BridgeAlreadyRunningError


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
        choices=[
            "anthropic", "openai",
            "kimi", "qwen",
            "qwen-local", "ui-tars-local",
        ],
        default=None,
        help=(
            "LLM backend override. Hosted: anthropic, openai, kimi, qwen. "
            "Local MLX: qwen-local, ui-tars-local."
        ),
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
    elif args.backend in {"anthropic", "openai", "kimi", "qwen"}:
        model = default_cloud_model(provider=args.backend)
    else:
        model = default_cloud_model()

    from .loop import run_agent

    try:
        result = asyncio.run(
            run_agent(
                task=args.task,
                max_turns=args.max_turns,
                model=model,
                run_dir=args.run_dir,
            )
        )
    except KeyboardInterrupt:
        print("\nCancelled.")
        return 130
    except BridgeAlreadyRunningError as exc:
        print(f"\nError: {exc}\n")
        return 1

    print(f"\n{'='*60}")
    print(f"Agent completed in {result['turns']} turns")
    print(f"Run directory: {result['run_dir']}")
    print(f"Screenshots: {len(result['screenshots'])}")
    print(f"{'='*60}")
    print(result["result"])

    return 0
