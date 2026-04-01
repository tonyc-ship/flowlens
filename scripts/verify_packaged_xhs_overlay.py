#!/usr/bin/env python3
"""Smoke-test the installed ClawVision Desktop app with the XHS watch overlay.

This drives the installed macOS app through Accessibility, launches the
pre-filled `研究露营` preset in the XHS view, waits for a new desktop task dir,
captures screenshots, and records the key log markers needed for regression
checks. It intentionally avoids free-form typing because non-interactive
keystroke permission on macOS can be flaky in CI/agent sessions.
"""

from __future__ import annotations

import json
import subprocess
import time
from datetime import datetime
from pathlib import Path

from clawvision.debug.macos import MacOSController

APP_PATH = Path("/Applications/ClawVision Desktop.app")
TASK_ROOT = Path("/Users/tonychong/Library/Application Support/com.clawvision.desktop/task_runs/desktop_app")
OUTPUT_ROOT = Path("task_runs")
APP_BINARY = APP_PATH / "Contents/MacOS/clawvision_desktop"
TASK_TIMEOUT_S = 480.0


def run_applescript(script: str) -> str:
    result = subprocess.run(
        ["osascript", "-e", script],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "AppleScript failed")
    return result.stdout.strip()


def latest_task_dir() -> Path | None:
    if not TASK_ROOT.exists():
        return None
    items = sorted((p for p in TASK_ROOT.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime)
    return items[-1] if items else None


def click_button(title: str) -> None:
    script = f'''
tell application "System Events"
  tell process "ClawVision Desktop"
    set uiElems to entire contents of window 1
    repeat with e in uiElems
      try
        if role of e is "AXButton" and title of e is "{title}" then
          perform action "AXPress" of e
          return "clicked"
        end if
      end try
    end repeat
  end tell
end tell
return "notfound"
'''
    result = run_applescript(script)
    if result != "clicked":
        raise RuntimeError(f"Unable to click button {title!r}")


def capture_screen(path: Path) -> None:
    subprocess.run(["screencapture", "-x", str(path)], check=True)


def wait_for_new_task(previous: Path | None, timeout_s: float = 20.0) -> Path:
    deadline = time.time() + timeout_s
    previous_name = previous.name if previous else None
    while time.time() < deadline:
        current = latest_task_dir()
        if current and current.name != previous_name:
            return current
        time.sleep(0.5)
    raise RuntimeError("Timed out waiting for a new desktop task dir")


def wait_for_log_line(task_dir: Path, needle: str, timeout_s: float = 60.0) -> str | None:
    log_path = task_dir / "desktop.log"
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if log_path.exists():
            for line in log_path.read_text(errors="ignore").splitlines():
                if needle in line:
                    return line
        time.sleep(0.5)
    return None


def main() -> int:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = OUTPUT_ROOT / f"packaged_xhs_overlay_verify_{timestamp}"
    output_dir.mkdir(parents=True, exist_ok=True)

    previous = latest_task_dir()

    subprocess.run(["pkill", "-f", str(APP_BINARY)], check=False)
    subprocess.run(["pkill", "-f", "python -m clawvision desktop run"], check=False)
    subprocess.run(["pkill", "-f", "chatbots-companion"], check=False)
    subprocess.run(["open", "-a", str(APP_PATH)], check=True)
    time.sleep(3)

    controller = MacOSController()
    controller.activate_app("ClawVision Desktop")
    capture_screen(output_dir / "01_app_open.png")

    # Switch to XHS view and launch the preset task. The preset avoids
    # non-interactive typing/keystroke permission issues.
    try:
        click_button("XHS Research")
    except RuntimeError:
        controller.click(1033, 176)
    time.sleep(1)
    click_button("研究露营")
    time.sleep(0.2)
    click_button("Start")

    task_dir = wait_for_new_task(previous)
    watch_line = wait_for_log_line(task_dir, "watch_window", timeout_s=30)
    search_line = wait_for_log_line(task_dir, "search_submit", timeout_s=60)

    time.sleep(12)
    capture_screen(output_dir / "02_browser_overlay.png")

    complete_line = wait_for_log_line(task_dir, "TASK COMPLETE", timeout_s=TASK_TIMEOUT_S)
    controller.activate_app("ClawVision Desktop")
    time.sleep(1)
    capture_screen(output_dir / "03_final_state.png")

    report = {
      "task_dir": str(task_dir),
      "watch_window_line": watch_line,
      "search_submit_line": search_line,
      "task_complete_line": complete_line,
      "screenshots": {
        "app_open": str(output_dir / "01_app_open.png"),
        "browser_overlay": str(output_dir / "02_browser_overlay.png"),
        "final_state": str(output_dir / "03_final_state.png"),
      },
      "notes": [
        "This verification path uses the built-in XHS preset button instead of free-form typing.",
        "Review 02_browser_overlay.png to visually confirm the in-page watch overlay is visible.",
        "Review 03_final_state.png to confirm the installed app leaves the RUNNING state after completion.",
      ],
    }

    (output_dir / "result.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
