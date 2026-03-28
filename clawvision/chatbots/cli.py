"""CLI for the workflow-level multi-chatbot fanout task."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from .cleanup import cleanup_orphaned_chrome_processes
from .runner import run_multi_chat_sync


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Ask a question to ChatGPT, Gemini, and Claude simultaneously",
    )
    parser.add_argument("question", nargs="?", help="The question to ask all chatbots")
    parser.add_argument("--port", "-p", type=int, default=8765, help="WebSocket port for extension bridge")
    parser.add_argument("--output", "-o", default=None, help="Output directory for screenshots and report")
    parser.add_argument("--vision", "-v", default=None, choices=["qwen-local", "sonnet"],
                        help="Vision backend for verification (default: auto-detect)")
    parser.add_argument("--cleanup-only", action="store_true",
                        help="Kill orphaned temp-profile Chrome processes and exit")
    parser.add_argument("--skip-preflight-cleanup", action="store_true",
                        help="Skip automatic cleanup of stale temp-profile Chrome processes")
    parser.add_argument("--skip-visible-verify", action="store_true",
                        help="Skip local visual verification of real visible Chrome windows")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    # Suppress noisy websockets frame-level logging
    logging.getLogger("websockets").setLevel(logging.WARNING)

    if args.cleanup_only:
        report = cleanup_orphaned_chrome_processes()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if not report["remaining"] else 1

    if not args.question:
        parser.error("question is required unless --cleanup-only is used")

    output_dir = Path(args.output) if args.output else None

    result = run_multi_chat_sync(
        args.question,
        port=args.port,
        output_dir=output_dir,
        vision_backend=args.vision,
        cleanup_orphaned=not args.skip_preflight_cleanup,
        verify_visible_windows=not args.skip_visible_verify,
    )

    # Print summary
    print(f"\nQuestion: {result['question']}")
    print(f"Elapsed: {result['elapsed_s']}s | Vision: {result['vision_backend']}")
    cleanup = result.get("preflight_cleanup") or {}
    if cleanup:
        print(
            "Cleanup:"
            f" matched={cleanup.get('matched', 0)}"
            f" force_killed={len(cleanup.get('force_killed', []))}"
            f" remaining={len(cleanup.get('remaining', []))}"
        )
    for cb in result["chatbots"]:
        status_icon = {"generating": "+", "error": "x"}.get(cb["status"], "~")
        print(f"  [{status_icon}] {cb['name']}: {cb['status']}", end="")
        if cb["error"]:
            print(f" ({cb['error']})", end="")
        print()

    any_error = any(cb["status"] == "error" for cb in result["chatbots"])
    return 1 if any_error else 0


if __name__ == "__main__":
    raise SystemExit(main())
