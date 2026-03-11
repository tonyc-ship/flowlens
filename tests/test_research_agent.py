"""Test the XHS Research Agent — validates key behaviors step by step.

Run with Chrome open on xiaohongshu.com (logged in).

Usage:
    python tests/test_research_agent.py              # Full research test
    python tests/test_research_agent.py --step N     # Run only step N
    python tests/test_research_agent.py --quick      # Minimal test (1 keyword, 1 note)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

# Load API key
with open(os.path.expanduser("~/.zshrc.pre-oh-my-zsh")) as f:
    for line in f:
        if "ANTHROPIC_API_KEY" in line and "export" in line:
            val = line.strip().split("=", 1)[1].strip().strip('"').strip("'")
            os.environ["ANTHROPIC_API_KEY"] = val
            break


def test_step_1_state_detection():
    """Step 1: Can we detect the current page state?"""
    print("\n" + "=" * 60)
    print("STEP 1: State Detection (live)")
    print("=" * 60)

    from clawvision.screen import ScreenController
    from clawvision.skills.xiaohongshu_skill import XiaohongshuSkill
    from clawvision.vision.llm import VisionLLM

    screen = ScreenController()
    skill = XiaohongshuSkill()
    llm = VisionLLM()

    win = screen.find_chrome_window("小红书")
    if not win:
        print("  SKIP: No Chrome window with XHS found")
        return False

    screenshot = screen.capture_window(win)
    screenshot.save("tests/eval_report/live_current_page.png")

    prompt = skill.get_state_detection_prompt()
    t0 = time.time()
    response = llm.analyze_page(screenshot, prompt, max_tokens=64)
    dt = time.time() - t0

    state = "unknown"
    for s in skill.get_states():
        if s in response.strip().lower():
            state = s
            break

    print(f"  Detected: {state} ({dt:.1f}s)")
    print(f"  Raw: {response.strip()}")
    print(f"  Available transitions: {list(skill.get_transitions(state).keys())}")
    return True


def test_step_2_grounding():
    """Step 2: Can we ground elements on the live page?"""
    print("\n" + "=" * 60)
    print("STEP 2: Grounding (live)")
    print("=" * 60)

    from clawvision.screen import ScreenController
    from clawvision.vision.grounding import GroundingModel

    screen = ScreenController()
    gm = GroundingModel(backend="uitars_mlx")

    win = screen.find_chrome_window("小红书")
    if not win:
        print("  SKIP: No Chrome window with XHS found")
        return False

    screenshot = screen.capture_window(win)

    queries = [
        "the search input box at the top center of the page",
        "the first note card in the results or homepage grid",
        "the XHS red logo in the top-left corner",
    ]

    for q in queries:
        t0 = time.time()
        result = gm.ground(screenshot, q)
        dt = time.time() - t0
        if result:
            w, h = screenshot.size
            print(f"  FOUND '{q[:50]}' → ({result.x},{result.y}) = ({result.x/w*100:.0f}%,{result.y/h*100:.0f}%) [{dt:.1f}s]")
        else:
            print(f"  MISS  '{q[:50]}' [{dt:.1f}s]")
    return True


def test_step_3_search():
    """Step 3: Can we search a keyword?"""
    print("\n" + "=" * 60)
    print("STEP 3: Search (live)")
    print("=" * 60)

    from clawvision.workflows.research_agent import XHSResearchAgent

    agent = XHSResearchAgent(
        output_dir="tests/eval_report/research_test",
        max_notes_per_keyword=1,
    )
    try:
        win = agent._find_window()
    except RuntimeError as e:
        print(f"  SKIP: {e}")
        return False

    cards = agent.search_keyword("露营装备推荐", win)
    print(f"  Found {len(cards)} cards")
    for c in cards[:3]:
        print(f"    - {c.get('title', '?')[:40]} by {c.get('author', '?')} ({c.get('likes', '?')} likes)")
    return len(cards) > 0


def test_step_4_open_and_extract():
    """Step 4: Open a note and extract content."""
    print("\n" + "=" * 60)
    print("STEP 4: Open Note + Extract (live)")
    print("=" * 60)

    from clawvision.workflows.research_agent import XHSResearchAgent

    agent = XHSResearchAgent(
        output_dir="tests/eval_report/research_test",
        max_notes_per_keyword=1,
    )
    try:
        win = agent._find_window()
    except RuntimeError as e:
        print(f"  SKIP: {e}")
        return False

    # First search
    cards = agent.search_keyword("露营装备", win)
    if not cards:
        print("  SKIP: no cards found")
        return False

    # Open first card
    title = cards[0].get("title", "first note")
    if not agent.open_note(win, title):
        print("  FAIL: could not open note")
        return False

    # Extract content
    note = agent.extract_note_content(win)
    print(f"  Title: {note.title}")
    print(f"  Author: {note.author}")
    print(f"  Content: {note.content[:100]}...")
    print(f"  Hashtags: {note.hashtags}")
    print(f"  Engagement: {note.likes} likes, {note.favorites} favs, {note.comments_count} comments")
    print(f"  Images: {note.image_count}")

    # Browse images
    note = agent.browse_images(win, note)
    for i, desc in enumerate(note.image_descriptions):
        print(f"  Image {i+1}: {desc[:80]}")

    # Close
    agent.close_note(win)
    return True


def test_step_5_full_research():
    """Step 5: Run a small but complete research session."""
    print("\n" + "=" * 60)
    print("STEP 5: Full Research Session")
    print("=" * 60)

    from clawvision.workflows.research_agent import XHSResearchAgent

    agent = XHSResearchAgent(
        output_dir="tests/eval_report/research_test",
        max_notes_per_keyword=2,
        max_images_per_note=5,
        max_comment_scrolls=1,
        browse_author_profile=True,
    )

    report = agent.research(
        topic="2025春季露营装备趋势",
        keywords=["春季露营装备", "露营好物推荐"],
    )

    print(f"\n  Research complete!")
    print(f"  Notes collected: {len(report.notes)}")
    print(f"  Authors profiled: {len(report.authors)}")
    print(f"  Screenshots taken: {len(report.screenshots)}")
    print(f"  Report saved to: tests/eval_report/research_test/report.html")

    return len(report.notes) > 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--step", type=int, help="Run only this step (1-5)")
    parser.add_argument("--quick", action="store_true", help="Quick test (steps 1-3 only)")
    args = parser.parse_args()

    print("=" * 60)
    print("XHS Research Agent — Live Test")
    print("=" * 60)
    print("Requires: Chrome open with xiaohongshu.com (logged in)")

    steps = {
        1: ("State Detection", test_step_1_state_detection),
        2: ("Grounding", test_step_2_grounding),
        3: ("Search", test_step_3_search),
        4: ("Open + Extract", test_step_4_open_and_extract),
        5: ("Full Research", test_step_5_full_research),
    }

    if args.step:
        name, fn = steps[args.step]
        print(f"\nRunning step {args.step}: {name}")
        fn()
    else:
        max_step = 3 if args.quick else 5
        results = {}
        for i in range(1, max_step + 1):
            name, fn = steps[i]
            try:
                results[name] = fn()
            except Exception as e:
                print(f"  ERROR: {e}")
                import traceback
                traceback.print_exc()
                results[name] = False

        print("\n" + "=" * 60)
        print("SUMMARY")
        print("=" * 60)
        for name, passed in results.items():
            status = "PASS" if passed else "FAIL"
            print(f"  [{status}] {name}")


if __name__ == "__main__":
    main()
