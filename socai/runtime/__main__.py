"""Entry point for the Socai Python runtime sidecar."""
from __future__ import annotations

import argparse
import sys

from .server import run_stdio


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--transport",
        choices=("stdio",),
        default="stdio",
        help="Runtime transport. The desktop app currently uses newline-delimited JSON-RPC over stdio.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    parse_args(argv or sys.argv[1:])
    return run_stdio()


if __name__ == "__main__":
    raise SystemExit(main())
