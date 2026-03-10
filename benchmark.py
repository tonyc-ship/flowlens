"""ClawVision Benchmark: Xiaohongshu research tasks of varying difficulty.

Tests screen capture, page analysis, element location, text extraction,
navigation, and report generation on real Xiaohongshu pages.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

# Load API key
with open(os.path.expanduser("~/.zshrc.pre-oh-my-zsh")) as f:
    for line in f:
        if "ANTHROPIC_API_KEY" in line and "export" in line:
            os.environ["ANTHROPIC_API_KEY"] = line.strip().split("=", 1)[1]
            break

from clawvision.screen import ScreenController
from clawvision.vision.llm import VisionLLM
from clawvision.vision.ocr import OCREngine

RESULTS_DIR = Path("benchmark_results")
RESULTS_DIR.mkdir(exist_ok=True)


@dataclass
class TaskResult:
    name: str
    difficulty: str
    success: bool
    duration: float  # seconds
    api_calls: int
    screenshots: list[str] = field(default_factory=list)
    output: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    notes: str = ""


screen = ScreenController()
llm = VisionLLM()
ocr = OCREngine(llm)
results: list[TaskResult] = []
api_call_count = 0


def count_api_call():
    global api_call_count
    api_call_count += 1


def get_xhs_window():
    windows = screen.find_windows("Google Chrome")
    for w in windows:
        if "小红书" in w.title:
            return w
    return None


def activate_chrome():
    """Bring Chrome to foreground before any input operations."""
    screen.activate_app("Google Chrome")
    time.sleep(0.5)


def capture_xhs(filename: str) -> tuple[Image.Image, str]:
    """Capture XHS window and save screenshot."""
    w = get_xhs_window()
    if not w:
        raise RuntimeError("XHS window not found")
    img = screen.capture_window(w)
    path = str(RESULTS_DIR / filename)
    img.save(path)
    return img, path


def navigate_xhs_search(query: str):
    """Reliable search: use open_url to navigate directly (no keyboard needed)."""
    import urllib.parse
    encoded = urllib.parse.quote(query)
    url = f"https://www.xiaohongshu.com/search_result?keyword={encoded}&source=web_search_result_note"
    screen.open_url(url, "Google Chrome")
    time.sleep(3)  # Wait for results to load


# ═══════════════════════════════════════════════════════════════
# TASK 1 (Easy): Analyze homepage — identify page structure
# ═══════════════════════════════════════════════════════════════

def task1_analyze_homepage() -> TaskResult:
    """Analyze XHS homepage: identify page type, categories, note count."""
    result = TaskResult(name="Homepage Analysis", difficulty="Easy", success=False, duration=0, api_calls=0)
    start = time.time()
    call_start = api_call_count

    try:
        img, path = capture_xhs("task1_homepage.png")
        result.screenshots.append(path)

        count_api_call()
        analysis = llm.analyze_page(img,
            "Analyze this Xiaohongshu page precisely:\n"
            "1. Page type (homepage/search/detail/other)\n"
            "2. List ALL visible category tabs at the top\n"
            "3. Count the exact number of visible note cards\n"
            "4. For each note card visible, extract: title, author, like count\n"
            "5. Is the user logged in? (check for profile/notification indicators)\n"
            "Return as structured JSON."
        )
        result.output["analysis"] = analysis
        result.success = True
        result.notes = "Page analysis completed"

    except Exception as e:
        result.errors.append(str(e))

    result.duration = time.time() - start
    result.api_calls = api_call_count - call_start
    return result


# ═══════════════════════════════════════════════════════════════
# TASK 2 (Easy): Locate and precisely crop a single note card
# ═══════════════════════════════════════════════════════════════

def task2_crop_note_card() -> TaskResult:
    """Find the first note card and crop it precisely from the screenshot."""
    result = TaskResult(name="Precise Note Crop", difficulty="Easy", success=False, duration=0, api_calls=0)
    start = time.time()
    call_start = api_call_count

    try:
        img, path = capture_xhs("task2_full.png")
        result.screenshots.append(path)

        count_api_call()
        element = llm.locate_element(img, "the first note card in the top-left of the content grid")

        if element and element.get("found"):
            # Convert percentage to pixels
            x = int(element["x"] / 100 * img.width)
            y = int(element["y"] / 100 * img.height)
            w = int(element["width"] / 100 * img.width)
            h = int(element["height"] / 100 * img.height)

            # Crop with some padding
            pad = 10
            x1 = max(0, x - w // 2 - pad)
            y1 = max(0, y - h // 2 - pad)
            x2 = min(img.width, x + w // 2 + pad)
            y2 = min(img.height, y + h // 2 + pad)

            cropped = img.crop((x1, y1, x2, y2))
            crop_path = str(RESULTS_DIR / "task2_cropped_note.png")
            cropped.save(crop_path)
            result.screenshots.append(crop_path)

            # Verify the crop by analyzing it
            count_api_call()
            verification = llm.analyze_page(cropped,
                "Is this a properly cropped Xiaohongshu note card? "
                "Does it contain: cover image, title, author, like count? "
                "Rate the crop quality: perfect/good/partial/bad. Return JSON."
            )
            result.output["element_location"] = element
            result.output["crop_bbox"] = {"x1": x1, "y1": y1, "x2": x2, "y2": y2}
            result.output["verification"] = verification
            result.success = True
        else:
            result.errors.append(f"Element not found: {element}")

    except Exception as e:
        result.errors.append(str(e))

    result.duration = time.time() - start
    result.api_calls = api_call_count - call_start
    return result


# ═══════════════════════════════════════════════════════════════
# TASK 3 (Medium): Search a topic and extract results
# ═══════════════════════════════════════════════════════════════

def task3_search_and_extract() -> TaskResult:
    """Search for '露营装备' and extract structured note data."""
    result = TaskResult(name="Search & Extract", difficulty="Medium", success=False, duration=0, api_calls=0)
    start = time.time()
    call_start = api_call_count

    try:
        # Use URL-based navigation for reliable search
        navigate_xhs_search("露营装备")

        # Capture search results
        img2, path2 = capture_xhs("task3_search_results.png")
        result.screenshots.append(path2)

        # Step 4: Analyze results
        count_api_call()
        analysis = llm.analyze_page(img2,
            "This should be Xiaohongshu search results for '露营装备'. Analyze:\n"
            "1. Confirm this is a search results page (not homepage, not error)\n"
            "2. Extract EVERY visible note card with: title, author, like_count\n"
            "3. Count total visible notes\n"
            "4. Note any filters or sorting options visible\n"
            "Return as JSON with keys: is_search_results, query, notes[], total_visible, filters[]"
        )
        result.output["search_analysis"] = analysis
        result.success = True
        result.notes = "Search executed and results analyzed"

    except Exception as e:
        result.errors.append(str(e))

    result.duration = time.time() - start
    result.api_calls = api_call_count - call_start
    return result


# ═══════════════════════════════════════════════════════════════
# TASK 4 (Medium): Scroll and collect multiple pages of results
# ═══════════════════════════════════════════════════════════════

def task4_scroll_collect() -> TaskResult:
    """Scroll through search results and collect notes from multiple viewports."""
    result = TaskResult(name="Scroll & Collect", difficulty="Medium", success=False, duration=0, api_calls=0)
    start = time.time()
    call_start = api_call_count

    try:
        activate_chrome()
        w = get_xhs_window()
        all_notes = []

        for i in range(3):
            img, path = capture_xhs(f"task4_scroll_{i}.png")
            result.screenshots.append(path)

            count_api_call()
            page_notes = llm.analyze_page(img,
                f"Extract all visible Xiaohongshu note cards on this page (scroll position {i+1}/3).\n"
                "For each note: title, author, like_count.\n"
                "Return JSON array of notes only, no other text."
            )
            result.output[f"scroll_{i}"] = page_notes

            # Scroll down (Chrome must be active/foreground)
            import pyautogui
            pyautogui.scroll(-5)
            time.sleep(2)

        result.success = True
        result.notes = f"Collected notes from 3 scroll positions"

    except Exception as e:
        result.errors.append(str(e))

    result.duration = time.time() - start
    result.api_calls = api_call_count - call_start
    return result


# ═══════════════════════════════════════════════════════════════
# TASK 5 (Hard): Open note detail and extract full content
# ═══════════════════════════════════════════════════════════════

def task5_note_detail() -> TaskResult:
    """Click on a specific note, extract its full detail page content."""
    result = TaskResult(name="Note Detail Extraction", difficulty="Hard", success=False, duration=0, api_calls=0)
    start = time.time()
    call_start = api_call_count

    try:
        # First scroll back to top
        activate_chrome()
        w = get_xhs_window()
        screen.hotkey("command", "up")  # Scroll to top
        time.sleep(1)

        img, path = capture_xhs("task5_before_click.png")
        result.screenshots.append(path)

        # Find and click the first note
        count_api_call()
        note_el = llm.locate_element(img, "the first note card's cover image in the search results grid")
        if not note_el or not note_el.get("found"):
            result.errors.append("Cannot find first note to click")
            result.duration = time.time() - start
            result.api_calls = api_call_count - call_start
            return result

        click_x = w.x + int(note_el["x"] / 100 * w.width)
        click_y = w.y + int(note_el["y"] / 100 * w.height)
        screen.click(click_x, click_y)
        time.sleep(3)  # Wait for detail page/modal

        # Capture detail
        img2, path2 = capture_xhs("task5_note_detail.png")
        result.screenshots.append(path2)

        # Extract full detail
        count_api_call()
        detail = llm.analyze_page(img2,
            "Extract the full detail of this Xiaohongshu note:\n"
            "1. title\n"
            "2. author name and avatar description\n"
            "3. full text content of the note\n"
            "4. number of images in the note\n"
            "5. like count, favorite count, comment count\n"
            "6. top 3 comments if visible\n"
            "7. tags/hashtags if visible\n"
            "Return as structured JSON."
        )
        result.output["detail"] = detail

        # Close the detail (press Escape or click X)
        screen.press_key("escape")
        time.sleep(1)

        result.success = True
        result.notes = "Note detail extracted"

    except Exception as e:
        result.errors.append(str(e))

    result.duration = time.time() - start
    result.api_calls = api_call_count - call_start
    return result


# ═══════════════════════════════════════════════════════════════
# TASK 6 (Hard): Multi-topic comparison research report
# ═══════════════════════════════════════════════════════════════

def task6_research_report() -> TaskResult:
    """Compare two topics: search both, extract data, generate comparison report."""
    result = TaskResult(name="Research Report", difficulty="Hard", success=False, duration=0, api_calls=0)
    start = time.time()
    call_start = api_call_count

    try:
        w = get_xhs_window()
        topics_data = {}

        for topic in ["露营装备", "徒步路线"]:
            navigate_xhs_search(topic)

            img, path = capture_xhs(f"task6_{topic}.png")
            result.screenshots.append(path)

            count_api_call()
            analysis = llm.analyze_page(img,
                f"This is Xiaohongshu search results for '{topic}'. Extract:\n"
                "1. Total visible note count\n"
                "2. Top 5 notes with: title, author, like_count\n"
                "3. Common themes/keywords across note titles\n"
                "4. Average engagement level (high/medium/low based on like counts)\n"
                "Return as JSON."
            )
            topics_data[topic] = analysis

        # Generate comparison report
        count_api_call()
        report_prompt = (
            "Based on the following Xiaohongshu search data for two topics, "
            "write a concise research comparison report in Chinese:\n\n"
            f"Topic 1 '露营装备' data:\n{topics_data['露营装备']}\n\n"
            f"Topic 2 '徒步路线' data:\n{topics_data['徒步路线']}\n\n"
            "Report should include:\n"
            "1. 各话题热度对比\n"
            "2. 内容类型分析（图文/视频/攻略）\n"
            "3. 高互动笔记的共同特征\n"
            "4. 对内容创作者的建议\n"
            "Write in markdown format."
        )
        # Use LLM without image for text generation
        import anthropic
        client = anthropic.Anthropic()
        count_api_call()
        report_response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            messages=[{"role": "user", "content": report_prompt}],
        )
        report = report_response.content[0].text

        result.output["topics_data"] = topics_data
        result.output["report"] = report
        result.success = True
        result.notes = "Multi-topic comparison report generated"

    except Exception as e:
        result.errors.append(str(e))

    result.duration = time.time() - start
    result.api_calls = api_call_count - call_start
    return result


# ═══════════════════════════════════════════════════════════════
# Run all tasks
# ═══════════════════════════════════════════════════════════════

def run_all():
    print("=" * 60)
    print("ClawVision Benchmark: Xiaohongshu Research Tasks")
    print("=" * 60)

    tasks = [
        ("Task 1", task1_analyze_homepage),
        ("Task 2", task2_crop_note_card),
        ("Task 3", task3_search_and_extract),
        ("Task 4", task4_scroll_collect),
        ("Task 5", task5_note_detail),
        ("Task 6", task6_research_report),
    ]

    for name, func in tasks:
        print(f"\n{'─' * 40}")
        print(f"Running {name}: {func.__doc__.strip().split(chr(10))[0]}")
        print(f"{'─' * 40}")

        r = func()
        results.append(r)

        status = "✅ PASS" if r.success else "❌ FAIL"
        print(f"  {status} | {r.duration:.1f}s | {r.api_calls} API calls")
        if r.errors:
            for e in r.errors:
                print(f"  ⚠️  {e}")
        if r.screenshots:
            for s in r.screenshots:
                print(f"  📸 {s}")

    # Save results
    summary = {
        "total_tasks": len(results),
        "passed": sum(1 for r in results if r.success),
        "failed": sum(1 for r in results if not r.success),
        "total_duration": sum(r.duration for r in results),
        "total_api_calls": sum(r.api_calls for r in results),
        "tasks": [
            {
                "name": r.name,
                "difficulty": r.difficulty,
                "success": r.success,
                "duration": round(r.duration, 1),
                "api_calls": r.api_calls,
                "screenshots": r.screenshots,
                "errors": r.errors,
                "notes": r.notes,
            }
            for r in results
        ],
    }

    with open(RESULTS_DIR / "summary.json", "w") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    # Save detailed outputs
    for r in results:
        safe_name = r.name.lower().replace(" ", "_").replace("&", "and")
        with open(RESULTS_DIR / f"{safe_name}_output.json", "w") as f:
            json.dump(r.output, f, indent=2, ensure_ascii=False, default=str)

    print(f"\n{'=' * 60}")
    print(f"SUMMARY: {summary['passed']}/{summary['total_tasks']} passed")
    print(f"Total time: {summary['total_duration']:.1f}s")
    print(f"Total API calls: {summary['total_api_calls']}")
    print(f"Results saved to {RESULTS_DIR}/")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    run_all()
