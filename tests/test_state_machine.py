"""End-to-end test for the state machine Skill architecture.

Tests:
1. State detection: Can the LLM + Skill correctly identify page states?
2. Grounding: Can grounding models locate elements described by Skill transitions?
3. Extraction: Can the LLM extract structured data using Skill extraction rules?
4. Orchestrator integration: Does the full pipeline work together?

Uses real XHS screenshots from f1_report/ and benchmark_results/.
Generates a visual HTML report with all intermediate results.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

# Load API key
with open(os.path.expanduser("~/.zshrc.pre-oh-my-zsh")) as f:
    for line in f:
        if "ANTHROPIC_API_KEY" in line and "export" in line:
            val = line.strip().split("=", 1)[1].strip().strip('"').strip("'")
            os.environ["ANTHROPIC_API_KEY"] = val
            break

from PIL import Image, ImageDraw, ImageFont

# ── Configuration ─────────────────────────────────────────────────

OUTPUT_DIR = Path("tests/eval_report")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
IMG_DIR = OUTPUT_DIR / "images"
IMG_DIR.mkdir(exist_ok=True)

# Real full-screen XHS screenshots
TEST_SCREENSHOTS = {
    # Note detail (modal overlay)
    "note_detail_modal": "f1_report/_temp_note_detail_1.png",
    # Note detail (another variant)
    "note_detail_modal_2": "f1_report/_temp_note_detail_2.png",
    # Search results
    "search_results_1": "f1_report/search_1_scroll_1.png",
    "search_results_2": "benchmark_results/task3_search_results.png",
    # Homepage / search with different content
    "search_results_3": "benchmark_results/task5_before_click.png",
    # Note detail from benchmark
    "note_detail_full": "benchmark_results/task5_note_detail.png",
}


def load_screenshots() -> dict[str, Image.Image]:
    images = {}
    for name, path in TEST_SCREENSHOTS.items():
        if os.path.exists(path):
            images[name] = Image.open(path)
            print(f"  Loaded {name}: {images[name].size} from {path}")
        else:
            print(f"  SKIP {name}: {path} not found")
    return images


# ── Test 1: State Detection ──────────────────────────────────────

def test_state_detection(images: dict[str, Image.Image]) -> list[dict]:
    """Test LLM-based page state detection."""
    from clawvision.skills.xiaohongshu_skill import XiaohongshuSkill
    from clawvision.vision.llm import VisionLLM

    skill = XiaohongshuSkill()
    llm = VisionLLM()
    prompt = skill.get_state_detection_prompt()

    expected = {
        "note_detail_modal": "note_detail",
        "note_detail_modal_2": "search_results",  # Actually shows search results grid
        "note_detail_full": "search_results",  # Actually shows search results (filename misleading)
        "search_results_1": "search_results",
        "search_results_2": "search_results",
        "search_results_3": "search_results",
    }

    results = []
    for name, img in images.items():
        t0 = time.time()
        response = llm.analyze_page(img, prompt, max_tokens=64)
        dt = time.time() - t0

        # Parse response
        states = skill.get_states()
        detected = "unknown"
        for state_name in states:
            if state_name in response.strip().lower():
                detected = state_name
                break

        exp = expected.get(name, "unknown")
        correct = detected == exp

        result = {
            "name": name,
            "expected": exp,
            "detected": detected,
            "correct": correct,
            "raw_response": response.strip(),
            "time_ms": int(dt * 1000),
        }
        results.append(result)
        mark = "PASS" if correct else "FAIL"
        print(f"  [{mark}] {name}: expected={exp}, detected={detected} ({dt*1000:.0f}ms)")
        print(f"         raw: {response.strip()[:80]}")

    return results


# ── Test 2: Grounding ────────────────────────────────────────────

def test_grounding(images: dict[str, Image.Image]) -> list[dict]:
    """Test grounding model on Skill-defined element descriptions."""
    from clawvision.vision.grounding import GroundingModel

    # Test queries per image — things the Skill would ask for
    test_cases = {
        "search_results_2": [
            ("the search input box at the top center of the page", "top-center"),
            ("the first note card in the search results", "content-area"),
            ("the XHS red logo in the top-left", "top-left"),
        ],
        "note_detail_modal": [
            ("the right arrow button (>) on the image to go to the next photo", "image-area"),
            ("the author's username or profile picture at the top of the note", "top-right-panel"),
            ("the red '关注' (follow) button next to the author name", "top-right-panel"),
            ("the like/heart icon at the bottom of the note content panel", "bottom-right-panel"),
        ],
        "search_results_3": [
            ("the search input box with the current query", "top-center"),
            ("the '图文' filter tab below the search bar", "top-center-tabs"),
        ],
    }

    # Try backends in order: uitars_mlx first, then uground_mlx, then claude
    backends = []
    uitars_path = os.path.expanduser("~/.clawvision/weights/UI-TARS-1.5-7B-6bit")
    if os.path.exists(uitars_path):
        backends.append("uitars_mlx")
    uground_path = os.path.expanduser("~/.clawvision/weights/uground-v1-2b-mlx")
    if os.path.exists(uground_path):
        backends.append("uground_mlx")
    backends.append("claude")

    results = []
    for backend in backends:
        print(f"\n  Backend: {backend}")
        gm = GroundingModel(backend=backend)

        for img_name, queries in test_cases.items():
            if img_name not in images:
                continue
            img = images[img_name]
            w, h = img.size

            for query, expected_region in queries:
                t0 = time.time()
                try:
                    result = gm.ground(img, query)
                    dt = time.time() - t0

                    if result:
                        # Check if the result is in a reasonable region
                        x_pct = result.x / w * 100
                        y_pct = result.y / h * 100
                        region_ok = _check_region(x_pct, y_pct, expected_region)

                        entry = {
                            "backend": backend,
                            "image": img_name,
                            "query": query,
                            "expected_region": expected_region,
                            "x": result.x, "y": result.y,
                            "x_pct": round(x_pct, 1), "y_pct": round(y_pct, 1),
                            "region_ok": region_ok,
                            "confidence": result.confidence,
                            "time_ms": int(dt * 1000),
                            "raw_output": result.raw_output[:200],
                        }

                        # Save annotated image
                        ann = _annotate_point(img, result.x, result.y, query[:40])
                        safe_query = re.sub(r'[^a-zA-Z0-9_]', '_', query[:20])
                        ann_name = f"ground_{backend}_{img_name}_{safe_query}.png"
                        ann.save(str(IMG_DIR / ann_name))
                        entry["annotated_image"] = f"images/{ann_name}"

                        mark = "PASS" if region_ok else "FAIL"
                        print(f"    [{mark}] {img_name}: '{query[:50]}' → ({result.x},{result.y}) = ({x_pct:.0f}%,{y_pct:.0f}%) [{expected_region}] {dt*1000:.0f}ms")
                    else:
                        entry = {
                            "backend": backend,
                            "image": img_name,
                            "query": query,
                            "expected_region": expected_region,
                            "x": None, "y": None,
                            "region_ok": False,
                            "time_ms": int(dt * 1000),
                            "raw_output": "None",
                        }
                        print(f"    [FAIL] {img_name}: '{query[:50]}' → None ({dt*1000:.0f}ms)")

                except Exception as e:
                    dt = time.time() - t0
                    entry = {
                        "backend": backend,
                        "image": img_name,
                        "query": query,
                        "expected_region": expected_region,
                        "x": None, "y": None,
                        "region_ok": False,
                        "time_ms": int(dt * 1000),
                        "error": str(e)[:200],
                    }
                    print(f"    [ERROR] {img_name}: '{query[:50]}' → {str(e)[:80]} ({dt*1000:.0f}ms)")

                results.append(entry)

    return results


def _check_region(x_pct: float, y_pct: float, expected: str) -> bool:
    """Check if coordinates are in the expected region of the page."""
    checks = {
        "top-left": x_pct < 30 and y_pct < 20,
        "top-center": 20 < x_pct < 80 and y_pct < 15,
        "top-center-tabs": 15 < x_pct < 80 and y_pct < 30,
        "top-right": x_pct > 70 and y_pct < 20,
        "content-area": 10 < x_pct < 90 and 10 < y_pct < 90,
        "image-area": x_pct < 65 and 10 < y_pct < 90,
        "top-right-panel": x_pct > 40 and y_pct < 30,
        "bottom-right-panel": x_pct > 40 and y_pct > 70,
        "bottom-center": 20 < x_pct < 80 and y_pct > 80,
    }
    return checks.get(expected, True)


def _annotate_point(
    img: Image.Image, x: int, y: int, label: str
) -> Image.Image:
    """Draw a crosshair + label on the image at (x, y)."""
    # Scale down for reasonable file sizes
    max_dim = 800
    w, h = img.size
    if max(w, h) > max_dim:
        scale = max_dim / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        x, y = int(x * scale), int(y * scale)

    ann = img.copy()
    draw = ImageDraw.Draw(ann)
    r = 15
    draw.ellipse([x - r, y - r, x + r, y + r], outline="red", width=3)
    draw.line([x - r * 2, y, x + r * 2, y], fill="red", width=2)
    draw.line([x, y - r * 2, x, y + r * 2], fill="red", width=2)
    # Label
    draw.text((x + r + 5, y - 10), label, fill="red")
    return ann


# ── Test 3: Extraction ───────────────────────────────────────────

def test_extraction(images: dict[str, Image.Image]) -> list[dict]:
    """Test LLM extraction using Skill-defined extraction rules."""
    from clawvision.skills.xiaohongshu_skill import XiaohongshuSkill
    from clawvision.vision.llm import VisionLLM

    skill = XiaohongshuSkill()
    llm = VisionLLM()

    # Test extraction on different states
    test_cases = [
        ("note_detail_modal", "note_detail", "note_content"),
        ("note_detail_modal", "note_detail", "comments"),
        ("note_detail_modal", "note_detail", "image_description"),
        ("search_results_2", "search_results", "cards"),
        ("search_results_2", "search_results", "search_info"),
    ]

    results = []
    for img_name, state, rule_name in test_cases:
        if img_name not in images:
            continue

        img = images[img_name]
        rules = skill.get_extraction_rules(state)
        rule = rules.get(rule_name)
        if not rule:
            continue

        t0 = time.time()
        raw = llm.analyze_page(img, rule.prompt, max_tokens=2048)
        dt = time.time() - t0

        # Try to parse JSON
        parsed = None
        m = re.search(r"[\[{].*[\]}]", raw, re.DOTALL)
        if m:
            try:
                parsed = json.loads(m.group())
            except json.JSONDecodeError:
                pass

        entry = {
            "image": img_name,
            "state": state,
            "rule": rule_name,
            "has_data": parsed is not None,
            "data": parsed,
            "raw_preview": raw[:500],
            "time_ms": int(dt * 1000),
        }
        results.append(entry)

        data_preview = json.dumps(parsed, ensure_ascii=False)[:200] if parsed else "(parse failed)"
        mark = "PASS" if parsed else "WARN"
        print(f"  [{mark}] {img_name}/{rule_name}: {data_preview} ({dt*1000:.0f}ms)")

    return results


# ── Test 4: Orchestrator Integration (offline) ───────────────────

def test_orchestrator_offline(images: dict[str, Image.Image]) -> list[dict]:
    """Test the orchestrator's state detection + extraction without screen control."""
    from clawvision.skills.xiaohongshu_skill import XiaohongshuSkill
    from clawvision.vision.grounding import GroundingModel
    from clawvision.vision.llm import VisionLLM
    from clawvision.workflows.orchestrator import WorkflowOrchestrator

    skill = XiaohongshuSkill()
    llm = VisionLLM()
    gm = GroundingModel(backend="claude")  # Claude for reliability
    orch = WorkflowOrchestrator(skill, gm, llm, debug_dir=str(IMG_DIR / "orchestrator"))

    results = []

    # Test: detect state, then extract all
    for img_name in ["search_results_2", "note_detail_modal"]:
        if img_name not in images:
            continue
        img = images[img_name]

        t0 = time.time()
        state = orch.detect_state(img)
        dt_detect = time.time() - t0

        # Get available info
        status = orch.get_status()

        # Extract all data for this state
        all_data = {}
        for rule_name in status["available_extractions"]:
            rec = orch.extract_data(rule_name, screenshot=img)
            all_data[rule_name] = {
                "has_data": rec.parsed_data is not None,
                "data": rec.parsed_data,
            }

        dt_total = time.time() - t0

        entry = {
            "image": img_name,
            "detected_state": state,
            "status": status,
            "extractions": all_data,
            "detect_time_ms": int(dt_detect * 1000),
            "total_time_ms": int(dt_total * 1000),
        }
        results.append(entry)
        print(f"  {img_name}: state={state}, extractions={list(all_data.keys())}, time={dt_total:.1f}s")

    return results


# ── HTML Report Generation ───────────────────────────────────────

def generate_html_report(
    state_results: list[dict],
    grounding_results: list[dict],
    extraction_results: list[dict],
    orchestrator_results: list[dict],
) -> str:
    """Generate a visual HTML report."""

    def _esc(s):
        if s is None:
            return ""
        return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    html_parts = [
        "<!DOCTYPE html>",
        "<html><head><meta charset='utf-8'>",
        "<title>ClawVision State Machine Eval Report</title>",
        "<style>",
        "body { font-family: -apple-system, sans-serif; max-width: 1200px; margin: 0 auto; padding: 20px; }",
        "h1 { color: #333; } h2 { color: #555; border-bottom: 2px solid #eee; padding-bottom: 8px; }",
        "table { border-collapse: collapse; width: 100%; margin: 10px 0; }",
        "th, td { border: 1px solid #ddd; padding: 8px; text-align: left; font-size: 13px; }",
        "th { background: #f5f5f5; } tr:hover { background: #fafafa; }",
        ".pass { color: #2e7d32; font-weight: bold; } .fail { color: #c62828; font-weight: bold; }",
        ".warn { color: #f57f17; font-weight: bold; }",
        "img.thumb { max-width: 400px; max-height: 300px; border: 1px solid #ddd; }",
        "pre { background: #f5f5f5; padding: 10px; overflow-x: auto; font-size: 12px; max-height: 300px; }",
        ".summary { background: #e8f5e9; padding: 15px; border-radius: 8px; margin: 15px 0; }",
        ".summary.mixed { background: #fff3e0; }",
        "</style></head><body>",
        "<h1>ClawVision State Machine Architecture — Eval Report</h1>",
        f"<p>Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}</p>",
    ]

    # ── Summary ──
    state_pass = sum(1 for r in state_results if r["correct"])
    grounding_pass = sum(1 for r in grounding_results if r.get("region_ok"))
    extraction_pass = sum(1 for r in extraction_results if r.get("has_data"))

    total_tests = len(state_results) + len(grounding_results) + len(extraction_results)
    total_pass = state_pass + grounding_pass + extraction_pass
    css_class = "summary" if total_pass == total_tests else "summary mixed"

    html_parts.append(f'<div class="{css_class}">')
    html_parts.append(f"<strong>Overall: {total_pass}/{total_tests} tests passed</strong><br>")
    html_parts.append(f"State Detection: {state_pass}/{len(state_results)} | ")
    html_parts.append(f"Grounding: {grounding_pass}/{len(grounding_results)} | ")
    html_parts.append(f"Extraction: {extraction_pass}/{len(extraction_results)}")
    html_parts.append("</div>")

    # ── Test 1: State Detection ──
    html_parts.append("<h2>Test 1: State Detection (LLM + Skill)</h2>")
    html_parts.append("<table><tr><th>Image</th><th>Expected</th><th>Detected</th><th>Result</th><th>Time</th><th>Raw Response</th></tr>")
    for r in state_results:
        css = "pass" if r["correct"] else "fail"
        mark = "PASS" if r["correct"] else "FAIL"
        html_parts.append(
            f"<tr><td>{_esc(r['name'])}</td><td>{_esc(r['expected'])}</td>"
            f"<td>{_esc(r['detected'])}</td>"
            f'<td class="{css}">{mark}</td>'
            f"<td>{r['time_ms']}ms</td>"
            f"<td><code>{_esc(r['raw_response'][:100])}</code></td></tr>"
        )
    html_parts.append("</table>")

    # ── Test 2: Grounding ──
    html_parts.append("<h2>Test 2: Grounding (Element Location)</h2>")

    # Group by backend
    backends_seen = sorted(set(r.get("backend", "?") for r in grounding_results))
    for backend in backends_seen:
        backend_results = [r for r in grounding_results if r.get("backend") == backend]
        bp = sum(1 for r in backend_results if r.get("region_ok"))
        html_parts.append(f"<h3>{backend} ({bp}/{len(backend_results)} correct)</h3>")
        html_parts.append("<table><tr><th>Image</th><th>Query</th><th>Expected Region</th><th>Coords</th><th>Result</th><th>Time</th></tr>")
        for r in backend_results:
            css = "pass" if r.get("region_ok") else "fail"
            mark = "PASS" if r.get("region_ok") else "FAIL"
            coords = f"({r.get('x')}, {r.get('y')}) = ({r.get('x_pct',0):.0f}%, {r.get('y_pct',0):.0f}%)" if r.get("x") is not None else "None"
            html_parts.append(
                f"<tr><td>{_esc(r.get('image'))}</td>"
                f"<td>{_esc(r.get('query','')[:60])}</td>"
                f"<td>{_esc(r.get('expected_region'))}</td>"
                f"<td>{coords}</td>"
                f'<td class="{css}">{mark}</td>'
                f"<td>{r.get('time_ms',0)}ms</td></tr>"
            )
        html_parts.append("</table>")

        # Show annotated images
        for r in backend_results:
            if r.get("annotated_image"):
                html_parts.append(
                    f"<p><strong>{_esc(r.get('query','')[:50])}</strong> on {_esc(r.get('image'))}</p>"
                    f'<img class="thumb" src="{r["annotated_image"]}">'
                )

    # ── Test 3: Extraction ──
    html_parts.append("<h2>Test 3: LLM Extraction (Skill Rules)</h2>")
    html_parts.append("<table><tr><th>Image</th><th>State</th><th>Rule</th><th>Result</th><th>Time</th></tr>")
    for r in extraction_results:
        css = "pass" if r.get("has_data") else "warn"
        mark = "PASS" if r.get("has_data") else "WARN"
        html_parts.append(
            f"<tr><td>{_esc(r.get('image'))}</td>"
            f"<td>{_esc(r.get('state'))}</td>"
            f"<td>{_esc(r.get('rule'))}</td>"
            f'<td class="{css}">{mark}</td>'
            f"<td>{r.get('time_ms',0)}ms</td></tr>"
        )
    html_parts.append("</table>")

    # Show extraction data
    for r in extraction_results:
        if r.get("data"):
            html_parts.append(f"<h4>{_esc(r.get('image'))} / {_esc(r.get('rule'))}</h4>")
            html_parts.append(f"<pre>{_esc(json.dumps(r['data'], indent=2, ensure_ascii=False))}</pre>")

    # ── Test 4: Orchestrator ──
    html_parts.append("<h2>Test 4: Orchestrator Integration</h2>")
    for r in orchestrator_results:
        html_parts.append(f"<h3>{_esc(r.get('image'))}</h3>")
        html_parts.append(f"<p>Detected state: <strong>{_esc(r.get('detected_state'))}</strong> ({r.get('detect_time_ms',0)}ms)</p>")
        html_parts.append(f"<p>Total time: {r.get('total_time_ms',0)}ms</p>")
        html_parts.append(f"<pre>{_esc(json.dumps(r.get('status',{}), indent=2))}</pre>")
        for rule_name, data in r.get("extractions", {}).items():
            css = "pass" if data.get("has_data") else "warn"
            html_parts.append(f'<h4 class="{css}">{_esc(rule_name)}</h4>')
            if data.get("data"):
                html_parts.append(f"<pre>{_esc(json.dumps(data['data'], indent=2, ensure_ascii=False))}</pre>")

    html_parts.append("</body></html>")
    return "\n".join(html_parts)


# ── Main ──────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("ClawVision State Machine Architecture — E2E Test")
    print("=" * 60)

    print("\nLoading test screenshots...")
    images = load_screenshots()
    if not images:
        print("ERROR: No test screenshots found!")
        sys.exit(1)

    # Run tests
    print("\n" + "=" * 60)
    print("TEST 1: State Detection (LLM + Skill)")
    print("=" * 60)
    state_results = test_state_detection(images)

    print("\n" + "=" * 60)
    print("TEST 2: Grounding (Element Location)")
    print("=" * 60)
    grounding_results = test_grounding(images)

    print("\n" + "=" * 60)
    print("TEST 3: LLM Extraction (Skill Rules)")
    print("=" * 60)
    extraction_results = test_extraction(images)

    print("\n" + "=" * 60)
    print("TEST 4: Orchestrator Integration (Offline)")
    print("=" * 60)
    orchestrator_results = test_orchestrator_offline(images)

    # Generate report
    print("\n" + "=" * 60)
    print("Generating HTML report...")
    html = generate_html_report(
        state_results, grounding_results, extraction_results, orchestrator_results
    )
    report_path = OUTPUT_DIR / "state_machine_report.html"
    with open(report_path, "w") as f:
        f.write(html)

    # Save raw results
    all_results = {
        "state_detection": state_results,
        "grounding": grounding_results,
        "extraction": extraction_results,
        "orchestrator": orchestrator_results,
    }
    with open(OUTPUT_DIR / "state_machine_results.json", "w") as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False, default=str)

    # Summary
    state_pass = sum(1 for r in state_results if r["correct"])
    grounding_pass = sum(1 for r in grounding_results if r.get("region_ok"))
    extraction_pass = sum(1 for r in extraction_results if r.get("has_data"))

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"  State Detection:  {state_pass}/{len(state_results)}")
    print(f"  Grounding:        {grounding_pass}/{len(grounding_results)}")
    print(f"  Extraction:       {extraction_pass}/{len(extraction_results)}")
    print(f"\nReport: {report_path}")
    print(f"Results: {OUTPUT_DIR / 'state_machine_results.json'}")


if __name__ == "__main__":
    main()
