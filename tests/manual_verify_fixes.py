"""Manual verification of fixes 2, 3, and 4.

Fix 2: Note exit uses Escape/X — no navigation fallback
Fix 3: Side panel opens automatically in watch mode
Fix 4: Close tab only, not entire window

Usage:
    python tests/manual_verify_fixes.py
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from flowlens.core.bridge import ExtensionBridge
from flowlens.platforms.xhs.browser import XHSBrowser

OUTPUT_DIR = Path("task_runs/verify_fixes")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


async def screenshot(bridge: ExtensionBridge, name: str) -> str:
    """Take a CDP screenshot and save it."""
    try:
        path = OUTPUT_DIR / f"{name}.png"
        saved = await bridge.save_screenshot(path)
        print(f"  [screenshot] {saved}")
        return saved
    except Exception as e:
        print(f"  [screenshot] FAILED for {name}: {e}")
        return ""


def report_step(step: str, status: str, detail: str = ""):
    icon = "PASS" if status == "pass" else "FAIL" if status == "fail" else "INFO"
    print(f"\n  [{icon}] {step}: {detail}")


async def main():
    results = []
    bridge = ExtensionBridge(port=8765)
    bridge.on_log(lambda a, d="": print(f"  [bridge] {a}: {d}"))

    await bridge.start()
    print("\n  >>> Waiting for Chrome Extension to connect. <<<\n")
    await bridge.wait_for_connection(timeout=120, require_watch=True)

    # ─── FIX 3: Side panel opens in watch mode ──────────────────
    print("\n" + "=" * 60)
    print("FIX 3: Testing side panel auto-open in watch mode")
    print("=" * 60)

    watch_result = await bridge.create_watch_window(url="https://www.xiaohongshu.com/explore")
    print(f"  Watch result: {json.dumps(watch_result, indent=2)}")

    side_panel_opened = watch_result.get("sidePanel", False)
    watch_mode_active = watch_result.get("watchMode", False)
    automation_tab_id = watch_result.get("tabId")
    automation_window_id = watch_result.get("windowId")

    report_step("watch_mode", "pass" if watch_mode_active else "fail",
                f"watchMode={watch_mode_active}")
    report_step("side_panel", "pass" if side_panel_opened else "fail",
                f"sidePanel={side_panel_opened}")
    results.append({"fix": 3, "test": "side_panel_open", "pass": side_panel_opened})
    results.append({"fix": 3, "test": "watch_mode_active", "pass": watch_mode_active})

    # Send a test log entry to verify side panel receives it
    await bridge.watch_log("session", "Verification test started — checking side panel feed")
    await asyncio.sleep(3)

    # Wait for XHS to load
    await asyncio.sleep(5)
    await screenshot(bridge, "01_watch_mode_xhs_loaded")

    # ─── FIX 2: Note exit without navigation ────────────────────
    print("\n" + "=" * 60)
    print("FIX 2: Testing note exit (Escape/X, no navigation fallback)")
    print("=" * 60)

    # Navigate to search
    print("\n  Navigating to search...")
    await bridge.send_command("navigate", {
        "url": "https://www.xiaohongshu.com/search_result?keyword=露营装备&source=web_search_result_notes",
        "waitMs": 5000,
    })
    await asyncio.sleep(5)
    await screenshot(bridge, "02_search_results")

    # Get the current URL before opening a note
    pre_state = await bridge.send_command("detect_state")
    pre_url = pre_state.get("url", "")
    print(f"  Pre-open URL: {pre_url}")

    # Extract search cards and click the first one
    cards = await bridge.send_command("extract_search_cards", {"maxCards": 5})
    card_list = cards.get("cards", [])
    print(f"  Found {len(card_list)} cards")

    if card_list:
        first_card = card_list[0]
        print(f"  Opening note: {first_card.get('title', 'untitled')[:40]}")

        # Click the card to open note modal
        click_result = await bridge.send_command("click_card", {
            "cardIndex": 0,
            "selector": first_card.get("coverSelector", ""),
        })
        print(f"  Click result: {click_result}")
        await asyncio.sleep(4)
        await screenshot(bridge, "03_note_opened")

        # Now close the note using the real XHSBrowser.close_note() path
        # which tries CDP Escape first (isTrusted: true), then falls back to content script
        print("\n  Closing note via XHSBrowser.close_note() (CDP Escape → content script fallback)...")
        browser = XHSBrowser(bridge)
        close_result = await browser.close_note()
        close_method = close_result.get("method", "unknown")
        print(f"  Close method: {close_method}")

        # Wait and check state
        await asyncio.sleep(2)
        post_state = await bridge.send_command("detect_state")
        post_url = post_state.get("url", "")
        post_page_state = post_state.get("state", "")
        print(f"  Post-close URL: {post_url}")
        print(f"  Post-close state: {post_page_state}")
        await screenshot(bridge, "04_after_close_note")

        # Verify: URL should NOT have changed (no navigation/reload)
        url_unchanged = pre_url == post_url
        used_human_close = close_method in ("escape", "button", "cdp_escape", "cdp_button")
        back_on_search = post_page_state in ("search_results", "")

        report_step("close_method", "pass" if used_human_close else "fail",
                    f"method={close_method}")
        report_step("url_preserved", "pass" if url_unchanged else "fail",
                    f"pre={pre_url[:60]} post={post_url[:60]}")
        report_step("state_restored", "pass" if back_on_search else "fail",
                    f"state={post_page_state}")

        results.append({"fix": 2, "test": "human_close_method", "pass": used_human_close})
        results.append({"fix": 2, "test": "url_preserved", "pass": url_unchanged})
        results.append({"fix": 2, "test": "state_restored", "pass": back_on_search})
    else:
        report_step("cards", "fail", "No search cards found — cannot test note exit")
        results.append({"fix": 2, "test": "cards_found", "pass": False})

    # ─── FIX 4: Close tab only, not entire window ───────────────
    print("\n" + "=" * 60)
    print("FIX 4: Testing close_tab (should keep window open)")
    print("=" * 60)

    # First, create a second tab in the same window so closing one doesn't close the window
    print(f"  Creating a second tab in window {automation_window_id} to verify window survives...")
    extra_tab = await bridge.send_command("create_tab", {
        "url": "https://www.google.com",
        "windowId": automation_window_id,
    })
    extra_tab_id = extra_tab.get("tabId")
    print(f"  Extra tab created: {extra_tab_id}")
    await asyncio.sleep(2)

    # Release the pinned tab first
    await bridge.release_active_tab()

    # Close just the automation tab (not the window)
    print(f"  Closing automation tab {automation_tab_id} via close_tab...")
    close_tab_ok = False
    try:
        await bridge.close_tab(automation_tab_id)
        close_tab_ok = True
        print("  close_tab command succeeded")
    except Exception as e:
        print(f"  close_tab command FAILED: {e}")
    await asyncio.sleep(1)

    # Verify the window still exists by taking a screenshot of the extra tab
    window_alive = False
    try:
        await bridge.lock_active_tab(extra_tab_id)
        await screenshot(bridge, "05_window_after_close_tab")
        window_alive = True
        print("  Window still alive — screenshot taken of remaining tab")
    except Exception as e:
        print(f"  Window check FAILED: {e}")

    report_step("close_tab_succeeded", "pass" if close_tab_ok else "fail",
                f"close_tab({automation_tab_id})")
    report_step("window_survived", "pass" if window_alive else "fail",
                f"window {automation_window_id} still has tabs")
    results.append({"fix": 4, "test": "close_tab_succeeded", "pass": close_tab_ok})
    results.append({"fix": 4, "test": "window_survived", "pass": window_alive})

    # Cleanup: close the extra tab too
    try:
        await bridge.release_active_tab()
        await bridge.close_tab(extra_tab_id)
    except Exception:
        pass

    # ─── Summary ────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("VERIFICATION SUMMARY")
    print("=" * 60)
    for r in results:
        icon = "PASS" if r["pass"] else "FAIL"
        note = f" ({r['note']})" if r.get("note") else ""
        print(f"  [{icon}] Fix {r['fix']}: {r['test']}{note}")

    all_pass = all(r["pass"] for r in results)
    print(f"\n  Overall: {'ALL PASSED' if all_pass else 'SOME FAILED'}")

    # Save results
    with open(OUTPUT_DIR / "results.json", "w") as f:
        json.dump(results, f, indent=2)
    print(f"  Results saved to {OUTPUT_DIR / 'results.json'}")

    await bridge.stop()
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
