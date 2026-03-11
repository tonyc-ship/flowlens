"""Full evaluation of ClawVision pipeline components.

Generates a self-contained HTML report with all test results and images.
"""

from __future__ import annotations

import json
import os
import sys
import time
import base64
from pathlib import Path

# Load API key
with open(os.path.expanduser("~/.zshrc.pre-oh-my-zsh")) as f:
    for line in f:
        if "ANTHROPIC_API_KEY" in line and "export" in line:
            val = line.strip().split("=", 1)[1].strip().strip('"').strip("'")
            os.environ["ANTHROPIC_API_KEY"] = val
            break

from PIL import Image
import numpy as np

OUT = Path("tests/eval_report")
OUT.mkdir(parents=True, exist_ok=True)
IMG_DIR = OUT / "images"
IMG_DIR.mkdir(exist_ok=True)

# Test images
TESTS = {
    "note_detail_1": "f1_report/note_detail_1.png",
    "note_detail_2": "f1_report/note_detail_2.png",
    "search_1": "f1_report/search_1.png",
    "search_results": "benchmark_results/task3_search_results.png",
}

EXPECTED_TYPES = {
    "note_detail_1": "note_detail",
    "note_detail_2": "note_detail",
    "search_1": "search_results",
    "search_results": "search_results",
}

results = {"tests": [], "summary": {}}


def save_img(img: Image.Image, name: str) -> str:
    """Save image and return relative path."""
    path = IMG_DIR / f"{name}.png"
    img.save(str(path))
    return f"images/{name}.png"


def load_images():
    imgs = {}
    for name, path in TESTS.items():
        if os.path.exists(path):
            imgs[name] = Image.open(path)
    return imgs


# ══════════════════════════════════════════════════════════════
# TEST 1: Skill Page Type Detection
# ══════════════════════════════════════════════════════════════

def test_page_type(images):
    from clawvision.skills.xiaohongshu_skill import XiaohongshuSkill
    skill = XiaohongshuSkill()

    test = {"name": "Page Type Detection (Skill CV)", "items": [], "pass": True}

    for name, img in images.items():
        t0 = time.time()
        detected = skill.identify_page_type(img)
        dt = (time.time() - t0) * 1000
        expected = EXPECTED_TYPES.get(name, "unknown")
        ok = detected == expected
        if not ok:
            test["pass"] = False
        test["items"].append({
            "image": name,
            "detected": detected,
            "expected": expected,
            "correct": ok,
            "time_ms": round(dt),
        })
        print(f"  {'✓' if ok else '✗'} {name}: {detected} (expected {expected}, {dt:.0f}ms)")

    results["tests"].append(test)
    return test["pass"]


# ══════════════════════════════════════════════════════════════
# TEST 2: Skill Region Extraction
# ══════════════════════════════════════════════════════════════

def test_regions(images):
    from clawvision.skills.xiaohongshu_skill import XiaohongshuSkill
    skill = XiaohongshuSkill()

    test = {"name": "Region Extraction (Skill CV)", "items": [], "pass": True}

    for name, img in images.items():
        page_type = skill.identify_page_type(img)
        debug_dir = str(IMG_DIR / f"debug_{name}")
        t0 = time.time()
        regions = skill.extract_regions(img, page_type, debug_dir=debug_dir)
        dt = (time.time() - t0) * 1000

        region_info = []
        for rname, rimg in regions.items():
            rel_path = save_img(rimg, f"region_{name}_{rname}")
            w, h = rimg.size
            area_pct = (w * h) / (img.size[0] * img.size[1]) * 100
            region_info.append({
                "name": rname,
                "size": f"{w}x{h}",
                "area_pct": round(area_pct, 1),
                "image": rel_path,
            })

        # Save debug overlay if exists
        debug_files = []
        if os.path.exists(debug_dir):
            for f in sorted(os.listdir(debug_dir)):
                if f.endswith(".png"):
                    debug_files.append(f"debug_{name}/{f}")

        test["items"].append({
            "image": name,
            "page_type": page_type,
            "time_ms": round(dt),
            "regions": region_info,
            "debug_images": debug_files,
            "source_image": save_img(img.copy().resize(
                (img.size[0] // 3, img.size[1] // 3), Image.LANCZOS
            ), f"source_{name}"),
        })
        print(f"  {name} ({page_type}, {dt:.0f}ms): {[r['name'] for r in region_info]}")

    results["tests"].append(test)
    return True


# ══════════════════════════════════════════════════════════════
# TEST 3: OmniParser V2 YOLO Detection
# ══════════════════════════════════════════════════════════════

def test_omniparser_v2(images):
    v2_path = os.path.expanduser("~/.clawvision/weights/omniparser-v2/icon_detect/model.pt")
    if not os.path.exists(v2_path):
        print("  SKIP: V2 weights not found")
        results["tests"].append({"name": "OmniParser V2 YOLO", "items": [], "pass": False, "skip": True})
        return False

    from ultralytics import YOLO
    model = YOLO(v2_path)

    test = {"name": "OmniParser V2 YOLO Detection", "items": [], "pass": True}

    for name, img in images.items():
        t0 = time.time()
        res = model.predict(img, imgsz=640, conf=0.2, device="mps", verbose=False)
        dt = time.time() - t0

        boxes = res[0].boxes
        n = len(boxes)
        confs = [float(c) for c in boxes.conf] if n else []
        avg_conf = sum(confs) / len(confs) if confs else 0

        # Save annotated image
        annotated = res[0].plot()
        ann_path = save_img(Image.fromarray(annotated[:, :, ::-1]), f"yolov2_{name}")

        test["items"].append({
            "image": name,
            "elements": n,
            "avg_conf": round(avg_conf, 3),
            "time_s": round(dt, 2),
            "annotated_image": ann_path,
        })
        print(f"  {name}: {n} elements, conf={avg_conf:.2f}, {dt:.2f}s")

    results["tests"].append(test)
    return True


# ══════════════════════════════════════════════════════════════
# TEST 4: UGround-V1-2B Grounding (MLX)
# ══════════════════════════════════════════════════════════════

def test_uground(images):
    model_path = os.path.expanduser("~/.clawvision/weights/uground-v1-2b-mlx")
    if not os.path.exists(model_path):
        print("  SKIP: UGround model not found")
        results["tests"].append({"name": "UGround-V1-2B (MLX)", "items": [], "pass": False, "skip": True})
        return False

    try:
        from mlx_vlm import load, generate
        from mlx_vlm.prompt_utils import apply_chat_template
        from mlx_vlm.utils import load_config
    except ImportError:
        print("  SKIP: mlx-vlm not installed")
        results["tests"].append({"name": "UGround-V1-2B (MLX)", "items": [], "pass": False, "skip": True})
        return False

    print("  Loading UGround model...")
    t0 = time.time()
    model, processor = load(model_path)
    config = load_config(model_path)
    load_time = time.time() - t0
    print(f"  Model loaded in {load_time:.1f}s")

    test = {"name": "UGround-V1-2B Grounding (MLX)", "items": [], "pass": True, "load_time_s": round(load_time, 1)}

    queries = {
        "search_1": [
            ("the search input box", "top area"),
            ("the red Xiaohongshu logo", "top-left"),
            ("the first note card cover image", "upper content area"),
        ],
        "note_detail_1": [
            ("the author's profile avatar", "top-left of content"),
            ("the red follow button", "near author name"),
            ("the like/heart icon at the bottom", "bottom bar"),
        ],
    }

    for name, qs in queries.items():
        if name not in images:
            continue
        img = images[name]
        img_w, img_h = img.size
        img_path = TESTS[name]

        for query, expected_area in qs:
            prompt_text = f"In the screenshot, I want to click on {query}. Identify the precise coordinates (x, y)."
            prompt = apply_chat_template(processor, config, prompt_text, num_images=1)

            t0 = time.time()
            output = generate(model, processor, prompt, image=img_path, max_tokens=128, verbose=False)
            dt = time.time() - t0

            # Parse coordinates from output
            coords = None
            raw = (output.text if hasattr(output, 'text') else str(output)).strip()
            import re
            match = re.search(r"\((\d+)\s*,\s*(\d+)\)", raw)
            if match:
                cx, cy = int(match.group(1)), int(match.group(2))
                # UGround outputs in [0, 1000) range
                px = int(cx / 1000 * img_w)
                py = int(cy / 1000 * img_h)
                coords = {"raw_x": cx, "raw_y": cy, "pixel_x": px, "pixel_y": py}

                # Draw marker on image
                marked = img.copy()
                from PIL import ImageDraw
                draw = ImageDraw.Draw(marked)
                r = max(10, min(img_w, img_h) // 50)
                draw.ellipse([px - r, py - r, px + r, py + r], outline="red", width=4)
                draw.line([px - r * 2, py, px + r * 2, py], fill="red", width=3)
                draw.line([px, py - r * 2, px, py + r * 2], fill="red", width=3)
                safe_q = query[:20].replace(' ', '_').replace('/', '_')
                marked_path = save_img(marked.resize(
                    (img_w // 3, img_h // 3), Image.LANCZOS
                ), f"uground_{name}_{safe_q}")
            else:
                marked_path = None

            test["items"].append({
                "image": name,
                "query": query,
                "expected_area": expected_area,
                "raw_output": raw,
                "coords": coords,
                "time_s": round(dt, 1),
                "marked_image": marked_path,
            })
            coord_str = f"({coords['pixel_x']},{coords['pixel_y']})" if coords else "FAILED"
            print(f"  {name} | '{query[:40]}' → {coord_str} ({dt:.1f}s)")

    results["tests"].append(test)
    return True


# ══════════════════════════════════════════════════════════════
# TEST 5: End-to-End Extraction Comparison
# ══════════════════════════════════════════════════════════════

def test_extraction(images):
    from clawvision.skills.xiaohongshu_skill import XiaohongshuSkill
    from clawvision.vision.llm import VisionLLM

    skill = XiaohongshuSkill()
    llm = VisionLLM()

    test_name = "note_detail_1"
    if test_name not in images:
        print("  SKIP: note_detail_1 not available")
        return False

    img = images[test_name]
    test = {"name": "Extraction: Full Image vs Skill Regions", "items": [], "pass": True}

    extract_prompt = (
        "Extract from this Xiaohongshu note: title, author, full text content, "
        "hashtags, date, likes, favorites, comments count. Return JSON only."
    )

    # Method A: Full image
    print("  Method A: Full image → LLM...")
    t0 = time.time()
    result_a = llm.analyze_page(img, extract_prompt, max_tokens=2048)
    dt_a = time.time() - t0
    print(f"    Done in {dt_a:.1f}s")

    # Method B: Skill content region
    print("  Method B: Skill region → LLM...")
    t0 = time.time()
    page_type = skill.identify_page_type(img)
    regions = skill.extract_regions(img, page_type)
    content_img = regions.get("content", img)
    result_b = llm.analyze_page(content_img, extract_prompt, max_tokens=2048)
    dt_b = time.time() - t0
    print(f"    Done in {dt_b:.1f}s")

    test["items"] = [{
        "method_a": {"time_s": round(dt_a, 1), "result": result_a, "api_calls": 1},
        "method_b": {"time_s": round(dt_b, 1), "result": result_b, "api_calls": 1,
                     "region_size": f"{content_img.size[0]}x{content_img.size[1]}"},
        "speedup": f"{(1 - dt_b / dt_a) * 100:.0f}%" if dt_a > 0 else "N/A",
    }]
    print(f"  Method A: {dt_a:.1f}s | Method B: {dt_b:.1f}s | Speedup: {test['items'][0]['speedup']}")

    results["tests"].append(test)
    return True


# ══════════════════════════════════════════════════════════════
# HTML Report Generator
# ══════════════════════════════════════════════════════════════

def generate_html():
    html = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>ClawVision Pipeline Evaluation Report</title>
<style>
body { font-family: -apple-system, sans-serif; max-width: 1200px; margin: 0 auto; padding: 20px; background: #f5f5f5; }
h1 { color: #333; border-bottom: 3px solid #e74c3c; padding-bottom: 10px; }
h2 { color: #e74c3c; margin-top: 40px; }
.test-card { background: white; border-radius: 8px; padding: 20px; margin: 15px 0; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }
.pass { color: #27ae60; } .fail { color: #e74c3c; } .skip { color: #f39c12; }
table { border-collapse: collapse; width: 100%; margin: 10px 0; }
th, td { border: 1px solid #ddd; padding: 8px 12px; text-align: left; }
th { background: #f8f8f8; }
img { max-width: 100%; height: auto; border: 1px solid #ddd; border-radius: 4px; margin: 5px 0; }
.img-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 10px; }
.img-item { text-align: center; }
.img-item p { font-size: 12px; color: #666; margin: 4px 0; }
pre { background: #f8f8f8; padding: 12px; border-radius: 4px; overflow-x: auto; font-size: 13px; }
.metric { display: inline-block; background: #eee; padding: 4px 10px; border-radius: 12px; margin: 2px; font-size: 13px; }
</style></head><body>
<h1>ClawVision Pipeline Evaluation Report</h1>
<p>Generated: """ + time.strftime("%Y-%m-%d %H:%M") + "</p>\n"

    for test in results["tests"]:
        status = "skip" if test.get("skip") else ("pass" if test.get("pass") else "fail")
        icon = {"pass": "✓", "fail": "✗", "skip": "⏭"}[status]
        html += f'<div class="test-card"><h2><span class="{status}">{icon}</span> {test["name"]}</h2>\n'

        if test["name"].startswith("Page Type"):
            html += "<table><tr><th>Image</th><th>Detected</th><th>Expected</th><th>Time</th><th>Result</th></tr>\n"
            for item in test["items"]:
                ok = "✓" if item["correct"] else "✗"
                cls = "pass" if item["correct"] else "fail"
                html += f'<tr><td>{item["image"]}</td><td>{item["detected"]}</td><td>{item["expected"]}</td><td>{item["time_ms"]}ms</td><td class="{cls}">{ok}</td></tr>\n'
            html += "</table>\n"

        elif test["name"].startswith("Region"):
            for item in test["items"]:
                html += f'<h3>{item["image"]} ({item["page_type"]}, {item["time_ms"]}ms)</h3>\n'
                # Source image
                html += f'<p><strong>Source:</strong></p><img src="{item["source_image"]}" style="max-height:300px">\n'
                # Debug overlay
                for df in item.get("debug_images", []):
                    html += f'<p><strong>Debug:</strong> {df}</p><img src="{df}" style="max-height:300px">\n'
                # Regions
                html += '<div class="img-grid">\n'
                for r in item["regions"]:
                    html += f'<div class="img-item"><img src="{r["image"]}" style="max-height:250px"><p><strong>{r["name"]}</strong><br>{r["size"]} ({r["area_pct"]}%)</p></div>\n'
                html += '</div>\n'

        elif test["name"].startswith("OmniParser"):
            html += "<table><tr><th>Image</th><th>Elements</th><th>Avg Confidence</th><th>Time</th></tr>\n"
            for item in test["items"]:
                html += f'<tr><td>{item["image"]}</td><td>{item["elements"]}</td><td>{item["avg_conf"]}</td><td>{item["time_s"]}s</td></tr>\n'
            html += "</table>\n"
            html += '<div class="img-grid">\n'
            for item in test["items"]:
                html += f'<div class="img-item"><img src="{item["annotated_image"]}"><p>{item["image"]}</p></div>\n'
            html += '</div>\n'

        elif test["name"].startswith("UGround"):
            if test.get("load_time_s"):
                html += f'<p><span class="metric">Model load: {test["load_time_s"]}s</span></p>\n'
            html += "<table><tr><th>Image</th><th>Query</th><th>Expected Area</th><th>Output</th><th>Pixel Coords</th><th>Time</th></tr>\n"
            for item in test["items"]:
                coords = f"({item['coords']['pixel_x']}, {item['coords']['pixel_y']})" if item.get("coords") else "FAILED"
                html += f'<tr><td>{item["image"]}</td><td>{item["query"]}</td><td>{item["expected_area"]}</td><td><code>{item["raw_output"][:50]}</code></td><td>{coords}</td><td>{item["time_s"]}s</td></tr>\n'
            html += "</table>\n"
            # Show marked images
            html += '<div class="img-grid">\n'
            for item in test["items"]:
                if item.get("marked_image"):
                    html += f'<div class="img-item"><img src="{item["marked_image"]}"><p>{item["query"][:40]}</p></div>\n'
            html += '</div>\n'

        elif test["name"].startswith("Extraction"):
            for item in test["items"]:
                html += f'<p><span class="metric">Speedup: {item["speedup"]}</span></p>\n'
                html += '<h3>Method A: Full Image → LLM</h3>\n'
                html += f'<p><span class="metric">Time: {item["method_a"]["time_s"]}s</span> <span class="metric">API calls: {item["method_a"]["api_calls"]}</span></p>\n'
                html += f'<pre>{item["method_a"]["result"][:800]}</pre>\n'
                html += '<h3>Method B: Skill Region → LLM</h3>\n'
                html += f'<p><span class="metric">Time: {item["method_b"]["time_s"]}s</span> <span class="metric">API calls: {item["method_b"]["api_calls"]}</span> <span class="metric">Region: {item["method_b"]["region_size"]}</span></p>\n'
                html += f'<pre>{item["method_b"]["result"][:800]}</pre>\n'

        html += "</div>\n"

    html += "</body></html>"

    report_path = OUT / "eval_report.html"
    with open(report_path, "w") as f:
        f.write(html)
    print(f"\nReport saved to {report_path}")

    # Also save raw JSON
    with open(OUT / "eval_results.json", "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)


# ══════════════════════════════════════════════════════════════

def main():
    print("=" * 60)
    print("ClawVision Full Pipeline Evaluation")
    print("=" * 60)

    images = load_images()
    print(f"Test images: {list(images.keys())}\n")

    print("[1/5] Page Type Detection")
    test_page_type(images)

    print("\n[2/5] Region Extraction")
    test_regions(images)

    print("\n[3/5] OmniParser V2 YOLO")
    test_omniparser_v2(images)

    print("\n[4/5] UGround Grounding")
    test_uground(images)

    print("\n[5/5] Extraction Comparison")
    test_extraction(images)

    generate_html()
    print("\nDone! Open tests/eval_report/eval_report.html to review.")


if __name__ == "__main__":
    main()
