"""CLI for Chrome extension operational commands."""

from __future__ import annotations

import argparse
from pathlib import Path

from .extension_ops import run_extension_reload_sync


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Socai extension operations")
    subparsers = parser.add_subparsers(dest="command", required=True)

    reload_parser = subparsers.add_parser("reload", help="Reload the unpacked Chrome extension through the live bridge")
    reload_parser.add_argument("--port", "-p", type=int, default=8765, help="WebSocket port")
    reload_parser.add_argument("--timeout", "-t", type=float, default=30, help="Wait time for bridge connection/reconnect")
    reload_parser.add_argument("--output", "-o", default=None, help="Output directory for report artifacts")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "reload":
        output_dir = Path(args.output) if args.output else None
        result = run_extension_reload_sync(
            port=args.port,
            timeout=args.timeout,
            output_dir=output_dir,
        )
        if result.success:
            print("Extension reloaded.")
        else:
            print(f"Extension reload failed: {result.error}")
        return 0 if result.success else 1

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
