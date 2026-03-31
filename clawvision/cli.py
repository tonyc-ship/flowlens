"""Primary CLI for the XHS + Chrome Extension workflow."""

from __future__ import annotations

import sys

from .debug_cli import main as debug_main
from .desktop_cli import main as desktop_main
from .extension_cli import main as extension_main
from .workflows.chat.cli import main as chatbots_main
from .workflows.chat.companion import main as chatbots_companion_main
from .workflows.xhs.cli import main as xhs_main


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "extension":
        raise SystemExit(extension_main(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "chatbots":
        raise SystemExit(chatbots_main(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "chatbots-companion":
        raise SystemExit(chatbots_companion_main(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "debug":
        raise SystemExit(debug_main(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "desktop":
        raise SystemExit(desktop_main(sys.argv[2:]))
    raise SystemExit(xhs_main(sys.argv[1:]))
