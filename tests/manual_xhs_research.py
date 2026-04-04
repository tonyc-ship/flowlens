"""Manual XHS research integration script.

Prerequisites:
1. Load `chrome_extension/` as an unpacked extension in Chrome.
2. Log in to Xiaohongshu in that Chrome profile.
3. Run this script and click `Connect` in the extension popup.

Usage:
    python tests/manual_xhs_research.py
    python tests/manual_xhs_research.py --topic "咖啡拉花" --keywords "咖啡拉花教程,拉花技巧"
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from flowlens.core.bridge import ExtensionBridge
from flowlens.platforms.xhs import XHSBrowser
from flowlens.workflows.xhs import run_research


async def test_basic_connection():
    """Test 1: Basic connection and state detection."""
    print("\n=== Test 1: Basic Connection ===")

    bridge = ExtensionBridge(port=8765)
    browser = XHSBrowser(bridge)
    await bridge.start()
    print("  WebSocket server started. Click 'Connect' in extension popup...")
    await bridge.wait_for_connection(timeout=60)

    tab = await bridge.get_tab_info()
    print(f"  Tab: {tab.get('url', '?')}")

    state = await browser.detect_state()
    print(f"  State: {state}")

    screenshot = await bridge.save_screenshot("/tmp/test_screenshot.png")
    print(f"  Screenshot saved: {screenshot}")

    await bridge.stop()
    print("  PASSED")


async def test_search_extraction():
    """Test 2: Search and card extraction."""
    print("\n=== Test 2: Search + Card Extraction ===")

    bridge = ExtensionBridge(port=8765)
    browser = XHSBrowser(bridge)
    await bridge.start()
    print("  Waiting for extension...")
    await bridge.wait_for_connection(timeout=60)

    keyword = "露营装备推荐"
    print(f"  Navigating to search: {keyword}")
    await browser.navigate_to_search(keyword)

    cards = await browser.extract_search_cards()
    print(f"  Found {len(cards)} cards")
    for c in cards[:5]:
        print(f"    - {c.get('title', '?')[:40]} | {c.get('author', '?')} | likes={c.get('likes', '?')}")

    assert len(cards) > 0, "Expected at least 1 card"

    await bridge.save_screenshot("/tmp/test_search.png")
    await bridge.stop()
    print(f"  PASSED ({len(cards)} cards)")


async def test_note_extraction():
    """Test 3: Open note and extract content."""
    print("\n=== Test 3: Note Content Extraction ===")

    bridge = ExtensionBridge(port=8765)
    browser = XHSBrowser(bridge)
    await bridge.start()
    print("  Waiting for extension...")
    await bridge.wait_for_connection(timeout=60)

    keyword = "露营装备推荐"
    await browser.navigate_to_search(keyword)

    cards = await browser.extract_search_cards()
    if not cards:
        print("  SKIP: No cards found")
        await bridge.stop()
        return

    first_card = cards[0]
    print(f"  Opening: {first_card.get('title', '?')[:40]}")

    await browser.click_card(0)
    await asyncio.sleep(2)

    state = await browser.detect_state()
    print(f"  State: {state}")

    note = await browser.extract_note_content()
    print(f"  Title: {note.get('title', '?')[:50]}")
    print(f"  Author: {note.get('author', '?')}")
    print(f"  Content: {note.get('content', '?')[:80]}...")
    print(f"  Likes: {note.get('likes', '?')}")
    print(f"  Type: {note.get('type', '?')}")
    print(f"  Images: {note.get('image_count', '?')}")
    print(f"  Hashtags: {note.get('hashtags', [])}")

    comments = await browser.extract_comments()
    print(f"  Comments: {len(comments)}")
    for c in comments[:3]:
        print(f"    - {c.get('username', '?')}: {c.get('text', '?')[:50]}...")

    await bridge.save_screenshot("/tmp/test_note.png")
    await browser.close_note()
    await asyncio.sleep(1)

    await bridge.stop()
    print(
        "  PASSED "
        f"(title={bool(note.get('title'))}, content={bool(note.get('content'))}, comments={len(comments)})"
    )


async def test_full_research():
    """Test 4: Full research flow."""
    print("\n=== Test 4: Full Research Flow ===")
    report = await run_research(
        topic="2025春季露营装备趋势",
        keywords=["露营装备推荐", "露营好物清单"],
        output_dir="tests/eval_report/extension_agent",
        port=8765,
    )
    print(f"\n  Notes: {len(report['notes'])}")
    print(f"  Time: {report['timing']['total_s']}s")
    print("  PASSED" if len(report["notes"]) > 0 else "  FAILED: 0 notes")


async def test_new_topic():
    """Test 5: New topic research."""
    print("\n=== Test 5: New Topic Research ===")
    report = await run_research(
        topic="咖啡拉花入门教程",
        keywords=["咖啡拉花教程", "拉花技巧入门"],
        output_dir="tests/eval_report/extension_agent_coffee",
        port=8765,
    )
    print(f"\n  Notes: {len(report['notes'])}")
    print(f"  Time: {report['timing']['total_s']}s")
    print("  PASSED" if len(report["notes"]) > 0 else "  FAILED: 0 notes")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", "-t", type=int, default=4, help="Test number (1-5)")
    parser.add_argument("--topic", default=None)
    parser.add_argument("--keywords", default=None)
    args = parser.parse_args()

    if args.topic:
        keywords = [k.strip() for k in args.keywords.split(",")] if args.keywords else None
        await run_research(
            topic=args.topic,
            keywords=keywords,
            output_dir="tests/eval_report/extension_agent_custom",
            port=8765,
        )
        return

    tests = {
        1: test_basic_connection,
        2: test_search_extraction,
        3: test_note_extraction,
        4: test_full_research,
        5: test_new_topic,
    }

    test_fn = tests.get(args.test)
    if test_fn:
        await test_fn()
    else:
        print(f"Unknown test: {args.test}. Available: {list(tests.keys())}")


if __name__ == "__main__":
    asyncio.run(main())
