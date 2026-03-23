"""Primary CLI for the XHS + Chrome Extension workflow."""

from __future__ import annotations

from .agent.__main__ import main as agent_main


def main() -> None:
    agent_main()
