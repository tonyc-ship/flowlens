"""Manual test for watch mode.

Starts the bridge, enables watch mode on the current tab, sends test log
entries, and exercises the Chrome side panel activity feed.

Usage:
    python tests/manual_watch_mode.py
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from clawvision.core.bridge import ExtensionBridge


def trigger_side_panel_shortcut() -> None:
    """Best-effort macOS shortcut to open the extension side panel in Chrome."""
    try:
        subprocess.run(
            [
                "osascript",
                "-e",
                'tell application "Google Chrome" to activate',
                "-e",
                "delay 0.3",
                "-e",
                'tell application "System Events" to keystroke "y" using {command down, shift down}',
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception:
        pass


async def main():
    bridge = ExtensionBridge(port=8765)
    bridge.on_log(lambda a, d="": print(f"  [bridge] {a}: {d}"))

    await bridge.start()
    print("\n  >>> Waiting for Chrome Extension to connect. <<<")
    print("  >>> The script will try to open the Chrome side panel automatically. <<<\n")
    await bridge.wait_for_connection(timeout=120, require_watch=True)

    # Step 1: Enable watch mode on the current window
    print("\n--- Opening watch side panel on current window ---")
    result = await bridge.create_watch_window(url="https://www.xiaohongshu.com/explore")
    print(f"  Watch mode: {result}")
    trigger_side_panel_shortcut()
    await asyncio.sleep(3)

    # Step 2: Send a session log
    print("\n--- Sending watch log entries ---")
    await bridge.watch_log("session", "Watch mode test started")
    await asyncio.sleep(0.5)

    # Step 3: Send thinking entries
    await bridge.watch_log(
        "think",
        "Generate 4 diverse keywords covering camping gear",
        phase="keyword_generation",
        observation="User wants to research camping equipment on XHS",
        reasoning="Need diverse keywords to cover tents, sleeping bags, cooking gear, and general recommendations",
        decision="Generate 4 keywords: 露营装备推荐, 露营帐篷, 露营睡袋, 户外炊具",
        evidence="Topic is '露营装备' — camping equipment. Chinese XHS users search with specific product categories.",
    )
    await asyncio.sleep(1)

    # Step 4: Send action entries
    await bridge.watch_log(
        "action",
        "Navigating to XHS search page",
        action_name="navigate",
        detail="https://www.xiaohongshu.com/search_result?keyword=露营装备推荐",
    )
    await asyncio.sleep(0.5)

    await bridge.watch_log(
        "result",
        "Navigation complete — page loaded",
        action_name="navigate",
        duration=3.2,
    )
    await asyncio.sleep(0.5)

    # Step 5: Send extract entries
    await bridge.watch_log(
        "extract",
        "Found 18 search result cards",
        action_name="extract_search_cards",
    )
    await asyncio.sleep(0.5)

    # Step 6: Send a thinking entry about picking notes
    await bridge.watch_log(
        "think",
        "Pick top 5 notes for lite extraction based on relevance and engagement",
        phase="note_selection",
        observation="18 cards available with varying engagement (50-5000 likes)",
        reasoning="Prioritize notes with high engagement and relevant titles. Mix image and video notes for diversity.",
        decision="Selected notes #2, #5, #7, #11, #15 for lite read",
    )
    await asyncio.sleep(1)

    # Step 7: Test click highlight
    print("\n--- Testing click highlights ---")
    await bridge.watch_log(
        "click",
        "Clicking card #2 cover image",
        x=450,
        y=320,
        target="section.note-item .cover",
    )
    await bridge.watch_highlight(x=450, y=320)
    await asyncio.sleep(2)

    # Step 8: Test another click
    await bridge.watch_log(
        "click",
        "Clicking card #5 cover image",
        x=780,
        y=520,
        target="section.note-item .cover",
    )
    await bridge.watch_highlight(x=780, y=520)
    await asyncio.sleep(2)

    # Step 9: Warning entry
    await bridge.watch_log(
        "warning",
        "Anti-bot state detected — backing off 8 seconds",
    )
    await asyncio.sleep(1)

    # Step 10: More thinking
    await bridge.watch_log(
        "think",
        "Retry with gentler navigation pattern",
        phase="anti_bot_recovery",
        observation="XHS returned security_verification page after 3 rapid note opens",
        reasoning="Need to slow down and use more human-like timing between actions",
        decision="Wait 8s, then resume with 3s pause between note opens",
    )
    await asyncio.sleep(1)

    # Step 11: Result entry
    await bridge.watch_log(
        "result",
        "Recovery successful — page loaded normally",
        action_name="anti_bot_recovery",
        duration=8.5,
    )
    await asyncio.sleep(1)

    # Step 12: Error entry
    await bridge.watch_log(
        "error",
        "Failed to extract comments: DOM selector not found",
    )
    await asyncio.sleep(1)

    # Step 13: Info entry
    await bridge.watch_log(
        "info",
        "Processing complete — 5 notes extracted, 3 with full content",
    )
    await asyncio.sleep(1)

    print("\n" + "=" * 60)
    print("Watch mode test complete!")
    print("Check the Chrome side panel for all watch-mode entries.")
    print("=" * 60)
    print("\nPress Ctrl+C to stop.")

    # Keep running so the panel stays visible
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        await bridge.stop()


if __name__ == "__main__":
    asyncio.run(main())
