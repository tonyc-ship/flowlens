"""Primary CLI for the XHS + Chrome Extension workflow."""

from __future__ import annotations

import sys

from .agent.__main__ import main as agent_main
from .debug_cli import main as debug_main
from .extension_cli import main as extension_main


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "extension":
        raise SystemExit(extension_main(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "debug":
        raise SystemExit(debug_main(sys.argv[2:]))
    agent_main()
