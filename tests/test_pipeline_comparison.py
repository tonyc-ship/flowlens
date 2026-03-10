"""Compare old pipeline vs new Skill-based pipeline on real XHS screenshots.

Tests:
1. Page type detection accuracy
2. Region extraction quality (visual inspection via saved crops)
3. OmniParser V1 vs V2 element detection
4. UGround grounding accuracy (if available)
5. End-to-end: Skill regions + LLM extraction vs full-image LLM extraction
"""

from __future__ import annotations

import json
import os
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

from PIL import Image

OUTPUT_DIR = Path("tests/pipeline_comparison")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Test screenshots ──────────────────────────────────────────────

TEST_IMAGES = {
    "note_detail_1": "f1_report/note_detail_1.png",
    "note_detail_2": "f1_report/note_detail_2.png",
    "search_1": "f1_report/search_1.png",
    "search_results": "benchmark_results/task3_search_results.png",
}


def load_test_images() -> dict[str, Image.Image]:
    images = {}
    for name, path in TEST_IMAGES.items():
        if os.path.exists(path):
            images[name] = Image.open(path)
    return images


# ── Test 1: Page type detection ───────────────────────────────────

def test_page_type_detection(images: dict[str, Image.Image]):
    from clawvision.skills.xiaohongshu_skill import XiaohongshuSkill

    skill = XiaohongshuSkill()
    expected = {
        "note_detail_1": "note_detail",
        "note_detail_2": "note_detail",
        "search_1": "search_results",
        "search_results": "search_results",
    }

    print("\n" + "=" * 60)
    print("TEST 1: Page Type Detection (Skill CV)")
    print("=" * 60)

    correct = 0
    for name, img in images.items():
        t0 = time.time()
        detected = skill.identify_page_type(img)
        dt = time.time() - t0
        exp = expected.get(name, "unknown")
        match = "✓" if detected == exp else "✗"
        if detected == exp:
            correct += 1
        print(f"  {match} {name}: detected={detected} expected={exp} ({dt*1000:.0f}ms)")

    print(f"\n  Score: {correct}/{len(images)}")
    return correct == len(images)


# ── Test 2: Region extraction quality ─────────────────────────────

def test_region_extraction(images: dict[str, Image.Image]):
    from clawvision.skills.xiaohongshu_skill import XiaohongshuSkill

    skill = XiaohongshuSkill()

    print("\n" + "=" * 60)
    print("TEST 2: Region Extraction (Skill CV)")
    print("=" * 60)

    for name, img in images.items():
        page_type = skill.identify_page_type(img)
        debug_dir = str(OUTPUT_DIR / f"regions_{name}")
        t0 = time.time()
        regions = skill.extract_regions(img, page_type, debug_dir=debug_dir)
        dt = time.time() - t0

        print(f"\n  {name} ({page_type}, {dt*1000:.0f}ms):")
        for rname, rimg in regions.items():
            crop_path = OUTPUT_DIR / f"regions_{name}" / f"{rname}.png"
            rimg.save(str(crop_path))
            # Quality metric: crop should have reasonable size (not too small)
            w, h = rimg.size
            area_pct = (w * h) / (img.size[0] * img.size[1]) * 100
            print(f"    {rname}: {w}x{h} ({area_pct:.1f}% of image)")

    return True


# ── Test 3: OmniParser V2 vs V1 detection ─────────────────────────

def test_omniparser_v2(images: dict[str, Image.Image]):
    print("\n" + "=" * 60)
    print("TEST 3: OmniParser V2 YOLO Detection")
    print("=" * 60)

    v2_weights = os.path.expanduser("~/.clawvision/weights/omniparser-v2/icon_detect/model.pt")
    if not os.path.exists(v2_weights):
        print("  SKIP: V2 weights not found")
        return False

    from ultralytics import YOLO

    model = YOLO(v2_weights)

    for name, img in images.items():
        t0 = time.time()
        results = model.predict(img, imgsz=640, conf=0.2, device="mps", verbose=False)
        dt = time.time() - t0

        boxes = results[0].boxes
        n = len(boxes)
        confs = [float(c) for c in boxes.conf] if n > 0 else []
        avg_conf = sum(confs) / len(confs) if confs else 0

        print(f"  {name}: {n} elements, avg_conf={avg_conf:.2f}, time={dt:.2f}s")

        # Save annotated image
        annotated = results[0].plot()
        annotated_path = OUTPUT_DIR / f"v2_detect_{name}.png"
        Image.fromarray(annotated[:, :, ::-1]).save(str(annotated_path))

    return True


# ── Test 4: UGround grounding ─────────────────────────────────────

def test_uground(images: dict[str, Image.Image]):
    print("\n" + "=" * 60)
    print("TEST 4: UGround-V1-2B Grounding (MLX)")
    print("=" * 60)

    try:
        from mlx_vlm import load, generate
        from mlx_vlm.prompt_utils import apply_chat_template
        from mlx_vlm.utils import load_config
    except ImportError:
        print("  SKIP: mlx-vlm not installed")
        return False

    try:
        model, processor = load("mlx-community/UGround-V1-2B")
        config = load_config("mlx-community/UGround-V1-2B")
    except Exception as e:
        print(f"  SKIP: Cannot load model: {e}")
        return False

    test_queries = {
        "search_1": [
            "the search input box at the top of the page",
            "the first note card image in the search results",
            "the red Xiaohongshu logo in the top left",
        ],
        "note_detail_1": [
            "the follow button next to the author name",
            "the like/heart icon at the bottom of the page",
            "the author's profile picture/avatar",
        ],
    }

    for name, queries in test_queries.items():
        if name not in images:
            continue
        img = images[name]
        img_path = TEST_IMAGES[name]

        print(f"\n  {name} ({img.size[0]}x{img.size[1]}):")
        for q in queries:
            prompt_text = f"In the screenshot, I want to click on {q}. Identify the precise coordinates (x, y)."
            prompt = apply_chat_template(processor, config, prompt_text, num_images=1)

            t0 = time.time()
            output = generate(
                model, processor, prompt, image=img_path, max_tokens=128, verbose=False
            )
            dt = time.time() - t0
            print(f"    '{q[:50]}' → {output.strip()} ({dt:.1f}s)")

    return True


# ── Test 5: End-to-end extraction comparison ──────────────────────

def test_extraction_comparison(images: dict[str, Image.Image]):
    """Compare: full-image LLM analysis vs skill-cropped region LLM analysis."""
    from clawvision.skills.xiaohongshu_skill import XiaohongshuSkill
    from clawvision.vision.llm import VisionLLM

    print("\n" + "=" * 60)
    print("TEST 5: Extraction Quality (Full Image vs Skill Regions)")
    print("=" * 60)

    skill = XiaohongshuSkill()
    llm = VisionLLM()

    # Test on a note detail image
    test_name = "note_detail_1"
    if test_name not in images:
        print("  SKIP: note_detail_1 not available")
        return False

    img = images[test_name]

    # Method A: Full image analysis (old pipeline)
    print(f"\n  Method A: Full image → LLM")
    t0 = time.time()
    full_result = llm.analyze_page(
        img,
        "Extract from this Xiaohongshu note: title, author, full text content, "
        "hashtags, date, likes, favorites, comments count. Return JSON.",
        max_tokens=2048,
    )
    dt_a = time.time() - t0
    print(f"    Time: {dt_a:.1f}s")
    print(f"    Result (first 300 chars): {full_result[:300]}")

    # Method B: Skill regions → targeted LLM calls
    print(f"\n  Method B: Skill regions → targeted LLM")
    t0 = time.time()
    page_type = skill.identify_page_type(img)
    regions = skill.extract_regions(img, page_type)
    prompts = skill.get_extraction_prompts(page_type)

    region_results = {}
    for region_name in ["content", "action_bar", "author_bar"]:
        if region_name in regions and region_name in prompts:
            region_img = regions[region_name]
            result = llm.analyze_page(region_img, prompts[region_name])
            region_results[region_name] = result
            print(f"    {region_name}: {result[:150]}")

    dt_b = time.time() - t0
    print(f"    Total time: {dt_b:.1f}s")

    # Save results
    results = {
        "method_a": {"time": dt_a, "result": full_result},
        "method_b": {"time": dt_b, "results": region_results},
    }
    with open(OUTPUT_DIR / "extraction_comparison.json", "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    return True


# ── Main ──────────────────────────────────────────────────────────

def main():
    print("ClawVision Pipeline Comparison Test")
    print("=" * 60)

    images = load_test_images()
    print(f"Loaded {len(images)} test images: {list(images.keys())}")

    results = {}
    results["page_type"] = test_page_type_detection(images)
    results["region_extraction"] = test_region_extraction(images)
    results["omniparser_v2"] = test_omniparser_v2(images)
    results["uground"] = test_uground(images)
    results["extraction"] = test_extraction_comparison(images)

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for name, passed in results.items():
        status = "✓ PASS" if passed else "✗ FAIL/SKIP"
        print(f"  {status}: {name}")
    print(f"\nResults saved to {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
