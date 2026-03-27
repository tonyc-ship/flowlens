"""Manual smoke test for the local visual debugging stack.

Examples:
    ./.venv/bin/python tests/manual_visual_debug.py --list-displays
    ./.venv/bin/python tests/manual_visual_debug.py --list-windows --app "Google Chrome"
    ./.venv/bin/python tests/manual_visual_debug.py --inspect --app "Google Chrome" --mode chrome-watch
    ./.venv/bin/python tests/manual_visual_debug.py --watch --app "Google Chrome" --mode chrome-watch
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clawvision.debug import VisualDebugger


def main() -> int:
    parser = argparse.ArgumentParser(description="Manual visual-debug smoke test")
    parser.add_argument("--list-displays", action="store_true")
    parser.add_argument("--list-windows", action="store_true")
    parser.add_argument("--inspect", action="store_true")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--app", default=None, help="Target app, defaults to Google Chrome for inspect/watch")
    parser.add_argument("--title", default=None, help="Window title filter")
    parser.add_argument("--mode", default="chrome-watch", choices=["general", "chrome-watch"])
    parser.add_argument("--question", default="Describe whether Chrome, the extension UI, and a side panel are visible.")
    parser.add_argument("--interval", type=float, default=1.5)
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--max-dim", type=int, default=896)
    parser.add_argument("--visible-only", action="store_true", help="Capture the currently visible front window for the app")
    parser.add_argument("--verify-capture", action="store_true", help="Verify screenshot fidelity with the visual model")
    parser.add_argument("--locate", default=None, help="Ground a UI element query")
    parser.add_argument("--ground-backend", default="auto", choices=["auto", "uitars_mlx", "uground_mlx", "claude", "ollama"])
    parser.add_argument("--save-dir", default=None, help="Directory for capture artifacts")
    args = parser.parse_args()

    debugger = VisualDebugger(grounding_backend=args.ground_backend)

    if args.list_displays:
        print(json.dumps([item.to_dict() for item in debugger.list_displays()], ensure_ascii=False, indent=2))
        return 0

    if args.list_windows:
        print(json.dumps([item.to_dict() for item in debugger.list_windows(args.app, title_contains=args.title)], ensure_ascii=False, indent=2))
        return 0

    target_app = args.app or "Google Chrome"
    targets = debugger.resolve_targets(app_name=target_app, title_contains=args.title, visible_only=args.visible_only)
    save_dir = Path(args.save_dir) if args.save_dir else Path("task_runs") / "visual_debug" / "manual_smoke"

    if args.inspect:
        result = debugger.inspect_targets(
            targets,
            mode=args.mode,
            question=args.question,
            max_dim=args.max_dim,
            locate=args.locate,
            save_dir=save_dir,
            verify_capture=args.verify_capture,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.watch:
        for item in debugger.watch(
            targets,
            mode=args.mode,
            question=args.question,
            interval=args.interval,
            iterations=None if args.iterations == 0 else args.iterations,
            max_dim=args.max_dim,
            locate=args.locate,
            save_dir=save_dir,
            verify_capture=args.verify_capture,
        ):
            print(json.dumps(item, ensure_ascii=False))
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
