"""Chrome process cleanup helpers for chatbot fanout runs."""

from __future__ import annotations

import os
import re
import signal
import subprocess
import time

TEMP_CHROME_MARKER = "browser-use-user-data-dir-"


def parse_orphaned_chrome_processes(output: str) -> list[dict]:
    """Parse `pgrep -fal` output for stale temp-profile Chrome processes."""
    processes: list[dict] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or TEMP_CHROME_MARKER not in line:
            continue

        match = re.match(r"^(\d+)\s+(.*)$", line)
        if not match:
            continue

        pid = int(match.group(1))
        command = match.group(2)
        if "pgrep -fal" in command:
            continue

        processes.append({"pid": pid, "command": command})
    return processes


def list_orphaned_chrome_processes() -> list[dict]:
    """List stale Chrome processes created with temporary browser-use profiles."""
    result = subprocess.run(
        ["pgrep", "-fal", TEMP_CHROME_MARKER],
        check=False,
        capture_output=True,
        text=True,
    )
    return parse_orphaned_chrome_processes(result.stdout)


def cleanup_orphaned_chrome_processes(*, grace_period_s: float = 1.0) -> dict:
    """Kill stale headless Chrome processes launched with temp browser-use profiles."""
    matched = list_orphaned_chrome_processes()
    terminated: list[int] = []
    force_killed: list[int] = []

    for item in matched:
        try:
            os.kill(item["pid"], signal.SIGTERM)
            terminated.append(item["pid"])
        except ProcessLookupError:
            continue

    if terminated:
        time.sleep(grace_period_s)

    remaining_after_term = {item["pid"]: item for item in list_orphaned_chrome_processes()}
    for pid in terminated:
        if pid not in remaining_after_term:
            continue
        try:
            os.kill(pid, signal.SIGKILL)
            force_killed.append(pid)
        except ProcessLookupError:
            continue

    if force_killed:
        time.sleep(0.5)

    final_remaining = list_orphaned_chrome_processes()
    return {
        "matched": len(matched),
        "terminated": terminated,
        "force_killed": force_killed,
        "remaining": [item["pid"] for item in final_remaining],
    }
