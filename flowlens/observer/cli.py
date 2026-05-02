"""CLI for FlowLens Observer."""

from __future__ import annotations

import argparse
import json

from ..core.process_metrics import observer_capture_loop_snapshot
from .analysis import (
    BACKEND_SONNET,
    ask_question,
    extract_summaries,
    format_project_memories,
    generate_work_journal,
    run_query_repl,
)
from .paths import ObserverPaths
from .service import (
    ObserverCaptureService,
    ObserverConfig,
    install_launch_agent,
    launch_agent_status,
    uninstall_launch_agent,
)
from .store import ObserverStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Observer subsystem for FlowLens.")
    parser.add_argument("--root", default=None, help="Override observer data root.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("status", help="Show observer data and service status.")

    capture_once = subparsers.add_parser("capture-once", help="Capture the current desktop context once.")
    capture_once.add_argument("--reason", default="manual", help="Capture reason label.")
    capture_once.add_argument("--no-keyframe", action="store_true", help="Do not treat this capture as a keyframe.")

    subparsers.add_parser("capture-loop", help="Run the long-lived observer capture loop.")

    extract = subparsers.add_parser("extract", help="Generate content summaries from OCR captures.")
    extract.add_argument("--dry-run", action="store_true")
    extract.add_argument("--limit", type=int, default=None)
    extract.add_argument("--llm-backend", choices=["sonnet", "anthropic", "openai", "qwen-local"], default="sonnet")
    extract.add_argument("--with-vision", action="store_true", help="Also generate visual summaries from screenshots.")

    journal = subparsers.add_parser("journal", help="Generate an observer work journal.")
    journal.add_argument("hours", nargs="?", type=int, default=None)
    journal.add_argument("--no-llm", action="store_true")
    journal.add_argument("--llm-backend", choices=["sonnet", "anthropic", "openai", "qwen-local"], default="sonnet")

    memory = subparsers.add_parser("memory", help="Show stored project memory.")
    memory.add_argument("project", nargs="?", default=None)

    ask = subparsers.add_parser("ask", help="Q&A over observer captures.")
    ask.add_argument("--llm-backend", choices=["sonnet", "anthropic", "openai", "qwen-local"], default=BACKEND_SONNET)
    ask.add_argument("question", nargs=argparse.REMAINDER)

    subparsers.add_parser("install-agent", help="Install the launchd observer capture agent.")
    subparsers.add_parser("uninstall-agent", help="Uninstall the launchd observer capture agent.")
    return parser


def _print_status(paths: ObserverPaths) -> None:
    store = ObserverStore(paths)
    stats = store.stats()
    latest = store.latest_capture() or {}
    launchd = launch_agent_status(paths)
    config = ObserverConfig.from_env()
    screenshot_count = sum(1 for _ in paths.screenshots_dir.rglob("screen_*.*"))
    payload = {
        "root": str(paths.root),
        "db_path": str(paths.db_path),
        "resource_monitor_log_path": str(paths.resource_monitor_log_path),
        "config": {
            "check_interval": config.check_interval,
            "force_capture_interval": config.force_capture_interval,
            "diff_threshold": config.diff_threshold,
            "capture_backend": config.capture_backend,
            "capture_all_displays": config.capture_all_displays,
            "vision_enabled": config.enable_visual_summary,
            "vision_model": config.vision_model,
        },
        "stats": stats,
        "latest_capture": (
            {
                "timestamp": latest.get("timestamp"),
                "app_name": latest.get("app_name"),
                "changed_area_ratio": latest.get("changed_area_ratio"),
                "ocr_scope": latest.get("ocr_scope"),
                "visual_scope": latest.get("visual_scope"),
                "visual_model": latest.get("visual_model"),
                "capture_image_ms": latest.get("capture_image_ms"),
                "diff_ms": latest.get("diff_ms"),
                "save_ms": latest.get("save_ms"),
                "ocr_ms": latest.get("ocr_ms"),
                "visual_ms": latest.get("visual_ms"),
                "total_ms": latest.get("total_ms"),
            }
            if latest
            else None
        ),
        "screenshot_file_count": screenshot_count,
        "launch_agent": launchd,
        "capture_loop_process": observer_capture_loop_snapshot() or None,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    paths = ObserverPaths.resolve(args.root)

    if args.command == "status":
        _print_status(paths)
        return 0

    if args.command == "capture-once":
        service = ObserverCaptureService(paths, config=ObserverConfig.from_env())
        record = service.capture_once(
            reason=args.reason,
            is_keyframe=not args.no_keyframe,
        )
        print(json.dumps(record, ensure_ascii=False, indent=2))
        return 0

    if args.command == "capture-loop":
        service = ObserverCaptureService(paths, config=ObserverConfig.from_env())
        service.run_loop()
        return 0

    if args.command == "extract":
        stats = extract_summaries(
            paths,
            dry_run=args.dry_run,
            limit=args.limit,
            llm_backend=args.llm_backend,
            with_vision=args.with_vision or None,
        )
        print(json.dumps(stats, ensure_ascii=False, indent=2))
        return 0

    if args.command == "journal":
        print(
            generate_work_journal(
                paths,
                hours=args.hours,
                use_llm=not args.no_llm,
                llm_backend=args.llm_backend,
            )
        )
        return 0

    if args.command == "memory":
        print(format_project_memories(paths, project=args.project))
        return 0

    if args.command == "ask":
        question = " ".join(args.question).strip()
        if question:
            print(ask_question(paths, question, llm_backend=args.llm_backend))
        else:
            run_query_repl(paths, llm_backend=args.llm_backend)
        return 0

    if args.command == "install-agent":
        path = install_launch_agent(paths)
        print(f"Installed launch agent: {path}")
        return 0

    if args.command == "uninstall-agent":
        removed = uninstall_launch_agent(paths)
        print("Uninstalled launch agent" if removed else "Launch agent not installed")
        return 0

    parser.error(f"Unknown command: {args.command}")
    return 2
