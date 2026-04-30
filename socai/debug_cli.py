"""CLI for local visual debugging and macOS automation."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from .core.runtime import task_runs_root
from .debug import MacOSController, VisualDebugger


def _default_save_dir() -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return task_runs_root() / "visual_debug" / stamp


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Socai visual debugging tools")
    subparsers = parser.add_subparsers(dest="command", required=True)

    displays = subparsers.add_parser("displays", help="List detected displays")
    displays.add_argument("--json", action="store_true", help="Emit JSON")

    windows = subparsers.add_parser("windows", help="List visible windows")
    windows.add_argument("--app", default=None, help="Filter by app name")
    windows.add_argument("--title", default=None, help="Filter by title substring")
    windows.add_argument("--on-screen-only", action="store_true", help="Only include windows on the current Space")
    windows.add_argument("--json", action="store_true", help="Emit JSON")

    activate = subparsers.add_parser("activate", help="Activate an app")
    activate.add_argument("--app", required=True, help="App name, for example Google Chrome")

    open_url = subparsers.add_parser("open-url", help="Open a URL in an app")
    open_url.add_argument("url", help="URL to open")
    open_url.add_argument("--app", default="Google Chrome", help="Browser app name")

    hotkey = subparsers.add_parser("hotkey", help="Send a key combination")
    hotkey.add_argument("keys", nargs="+", help="Modifiers followed by the final key, for example command shift y")

    click = subparsers.add_parser("click", help="Click global screen coordinates")
    click.add_argument("--x", type=int, required=True)
    click.add_argument("--y", type=int, required=True)
    click.add_argument("--button", default="left", choices=["left", "right"])
    click.add_argument("--clicks", type=int, default=1)

    inspect = subparsers.add_parser("inspect", help="Capture and analyze one frame")
    _add_capture_target_args(inspect)
    _add_inspect_args(inspect)

    watch = subparsers.add_parser("watch", help="Continuously capture and analyze frames")
    _add_capture_target_args(watch)
    _add_inspect_args(watch)
    watch.add_argument("--interval", type=float, default=1.5, help="Polling interval in seconds")
    watch.add_argument("--iterations", type=int, default=10, help="Number of loops; 0 means infinite")
    watch.add_argument("--change-threshold", type=float, default=4.0, help="Skip analysis when frames barely change")

    return parser


def _add_capture_target_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--app", default=None, help="Capture the largest window for this app")
    parser.add_argument("--window-id", type=int, default=None, help="Capture a specific window")
    parser.add_argument("--display-index", type=int, default=None, help="Capture a display by index")
    parser.add_argument("--display-id", type=int, default=None, help="Capture a display by CG display id")
    parser.add_argument("--all-displays", action="store_true", help="Capture all displays")
    parser.add_argument("--all-windows", action="store_true", help="Capture all matching app windows")
    parser.add_argument("--frontmost-app", action="store_true", help="Capture the frontmost application window")
    parser.add_argument("--visible-only", action="store_true", help="Capture the current visible front window for the app instead of arbitrary windows across spaces")
    parser.add_argument("--title", default=None, help="Match windows by title substring")


def _add_inspect_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--mode", default="general", choices=["general", "chrome-watch"], help="Prompt shape")
    parser.add_argument("--question", default=None, help="Extra question to ask the visual model")
    parser.add_argument("--max-dim", type=int, default=896, help="Resize captures to this long edge for inference")
    parser.add_argument("--max-tokens", type=int, default=192, help="Generation length for the visual model")
    parser.add_argument("--locate", default=None, help="Locate an element with the grounding model")
    parser.add_argument("--ground-backend", default="auto", choices=["auto", "uitars_mlx", "uground_mlx", "claude", "ollama"], help="Grounding backend")
    parser.add_argument("--click-located", action="store_true", help="Click the grounded element after locating it")
    parser.add_argument("--save-dir", default=None, help="Directory for saved screenshots")
    parser.add_argument("--verify-capture", action="store_true", help="Ask the vision model to verify the screenshot really matches the intended target")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")


def _emit(payload, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False))
        return
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _resolve_save_dir(raw: str | None) -> Path | None:
    if raw == "":
        return None
    return Path(raw) if raw else _default_save_dir()


def _resolve_targets(debugger: VisualDebugger, args) -> list:
    return debugger.resolve_targets(
        app_name=args.app,
        window_id=args.window_id,
        display_index=args.display_index,
        display_id=args.display_id,
        all_displays=args.all_displays,
        all_windows=args.all_windows,
        frontmost_app=args.frontmost_app,
        visible_only=args.visible_only,
        title_contains=args.title,
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    controller = MacOSController()

    if args.command == "displays":
        payload = [item.to_dict() for item in controller.list_displays()]
        _emit(payload, args.json)
        return 0

    if args.command == "windows":
        payload = [
            item.to_dict()
            for item in controller.list_windows(
                args.app,
                on_screen_only=args.on_screen_only,
                title_contains=args.title,
            )
        ]
        _emit(payload, args.json)
        return 0

    if args.command == "activate":
        controller.activate_app(args.app)
        print(f"activated={args.app}")
        return 0

    if args.command == "open-url":
        controller.open_url(args.url, browser=args.app)
        print(f"opened={args.url}")
        return 0

    if args.command == "hotkey":
        controller.hotkey(*args.keys)
        print(f"hotkey={' '.join(args.keys)}")
        return 0

    if args.command == "click":
        controller.click(args.x, args.y, button=args.button, clicks=args.clicks)
        print(f"clicked=({args.x},{args.y})")
        return 0

    debugger = VisualDebugger(
        controller=controller,
        grounding_backend=args.ground_backend,
    )
    targets = _resolve_targets(debugger, args)
    save_dir = _resolve_save_dir(args.save_dir)

    if args.command == "inspect":
        payload = debugger.inspect_targets(
            targets,
            mode=args.mode,
            question=args.question,
            max_dim=args.max_dim,
            max_tokens=args.max_tokens,
            locate=args.locate,
            click_located=args.click_located,
            save_dir=save_dir,
            verify_capture=args.verify_capture,
        )
        _emit(payload, args.json)
        return 0

    if args.command == "watch":
        iterations = None if args.iterations == 0 else args.iterations
        for payload in debugger.watch(
            targets,
            mode=args.mode,
            question=args.question,
            interval=args.interval,
            iterations=iterations,
            max_dim=args.max_dim,
            max_tokens=args.max_tokens,
            change_threshold=args.change_threshold,
            locate=args.locate,
            click_located=args.click_located,
            save_dir=save_dir,
            verify_capture=args.verify_capture,
        ):
            _emit(payload, args.json)
        return 0

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
