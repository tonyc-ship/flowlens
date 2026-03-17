"""Test the User Analyzer — analyze XHS creator accounts.

Prerequisites:
1. Reload chrome_extension/ in Chrome (content.js was updated)
2. Navigate to xiaohongshu.com and log in
3. Run this test script

Usage:
    python tests/test_user_analyzer.py                     # default test users
    python tests/test_user_analyzer.py --user <url_or_id>  # specific user
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from clawvision.agent.xhs import XHSUserAnalyzer as UserAnalyzer, UserAnalysisConfig, run_user_analysis


async def test_find_users():
    """Step 1: Find XHS operation expert accounts to analyze."""
    print("\n=== Finding XHS Operation Expert Accounts ===\n")
    from clawvision.agent.bridge import ExtensionBridge

    bridge = ExtensionBridge(port=8765)
    await bridge.start()
    print("  Waiting for extension...")
    await bridge.wait_for_connection(timeout=120)

    # Search for XHS operation experts
    keywords = ["小红书运营干货", "小红书涨粉教程"]
    users = {}

    for kw in keywords:
        url = f"https://www.xiaohongshu.com/search_result?keyword={kw}&source=web_search_result_notes"
        await bridge.navigate(url, wait_ms=5000)
        await asyncio.sleep(4)

        cards = await bridge.extract_search_cards()
        print(f"  '{kw}': {len(cards)} cards")

        for c in cards:
            author = c.get("author", "")
            if author and author not in users:
                users[author] = {
                    "author": author,
                    "title": c.get("title", ""),
                    "likes": c.get("likes", ""),
                    "link": c.get("link", ""),
                }

    print(f"\n  Unique authors: {len(users)}")
    for u in list(users.values())[:15]:
        print(f"    {u['author']:20s} | likes={u['likes']:8s} | {u['title'][:40]}")

    await bridge.stop()
    return list(users.values())


async def test_user_analysis(user_url: str, output_dir: str):
    """Run full user analysis on a specific user."""
    print(f"\n=== User Analysis: {user_url} ===\n")

    config = UserAnalysisConfig(
        max_scroll_rounds=15,
        max_notes_to_detail=8,
        max_images_per_note=3,
        max_comment_scrolls=1,
        use_apple_ocr=True,
        use_whisper=True,
        use_vision_for_covers=True,
    )

    report = await run_user_analysis(
        user_url=user_url,
        output_dir=output_dir,
        port=8765,
        config=config,
    )

    profile = report["profile"]
    print(f"\n  User: {profile.get('name', '?')}")
    print(f"  Followers: {profile.get('followers', '?')}")
    print(f"  Total posts: {len(report['all_cards'])}")
    print(f"  Detailed: {len(report['detailed_notes'])}")
    print(f"  Time: {report['timing']['total_s']}s")
    print(f"  Report: {output_dir}/report.html")

    return report


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", "-u", default=None, help="Specific user URL or ID")
    parser.add_argument("--find", action="store_true", help="Just find users, don't analyze")
    args = parser.parse_args()

    if args.find:
        await test_find_users()
        return

    if args.user:
        await test_user_analysis(args.user, "tests/eval_report/user_analysis_custom")
        return

    # Default: find users then analyze top ones
    # Step 1: Find operation experts
    users = await test_find_users()

    if not users:
        print("No users found!")
        return

    # Step 2: Pick first 2 users with the most engaging content
    # and run analysis on each
    print("\n" + "=" * 60)
    print("Now analyzing top users...")
    print("=" * 60)

    for i, u in enumerate(users[:2]):
        link = u.get("link", "")
        if not link:
            continue

        # Extract user profile URL from note link
        # We'll navigate to the note first, then extract author URL
        output = f"tests/eval_report/user_analysis_{i+1}"
        print(f"\n>>> Analyzing user {i+1}: {u['author']} <<<")
        await test_user_analysis(link, output)


if __name__ == "__main__":
    asyncio.run(main())
