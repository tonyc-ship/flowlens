#!/usr/bin/env python
"""Test the XiaohongshuSkill on real screenshots.

Runs page type detection and region extraction on available screenshots,
saves debug images, and prints results.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PIL import Image

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from clawvision.skills import XiaohongshuSkill

DEBUG_DIR = str(project_root / "tests" / "skill_debug_output")


def test_page_type_detection(skill: XiaohongshuSkill):
    """Test page type detection on all available screenshots."""
    print("=" * 60)
    print("PAGE TYPE DETECTION")
    print("=" * 60)

    test_files = {
        # Expected: note_detail (full-screen modal with dark semi-transparent overlay)
        "f1_report/_temp_note_detail_1.png": "note_detail",
        # These are actually search results pages (misnamed in the f1_report dir)
        "f1_report/_temp_note_detail_2.png": "search_results",
        "f1_report/_temp_note_detail_3.png": "search_results",
        "f1_report/_temp_note_detail_4.png": "search_results",
        # Mobile-style note detail (tall aspect ratio, no overlay)
        "f1_report/note_detail_1.png": "note_detail",
        # Expected: search_results
        "f1_report/search_1.png": "search_results",
        "f1_report/search_2.png": "search_results",
        "f1_report/search_3.png": "search_results",
        "benchmark_results/task1_homepage.png": "search_results",
        "benchmark_results/task3_search_results.png": "search_results",
        "benchmark_results/task5_note_detail.png": "search_results",
    }

    correct = 0
    total = 0

    for rel_path, expected in test_files.items():
        full_path = project_root / rel_path
        if not full_path.exists():
            print(f"  SKIP  {rel_path} (not found)")
            continue

        img = Image.open(full_path)
        detected = skill.identify_page_type(img)
        match = detected == expected
        correct += int(match)
        total += 1
        status = "OK" if match else "FAIL"
        print(f"  [{status}]  {rel_path}")
        print(f"         expected={expected}, detected={detected}, size={img.size}")

    print(f"\nAccuracy: {correct}/{total}")
    return correct, total


def test_note_detail_extraction(skill: XiaohongshuSkill):
    """Test region extraction on a note detail screenshot."""
    print("\n" + "=" * 60)
    print("NOTE DETAIL REGION EXTRACTION")
    print("=" * 60)

    # Use the full-screen note detail with dark overlay
    candidates = [
        "f1_report/_temp_note_detail_1.png",
        "f1_report/_temp_note_detail_2.png",
    ]

    for rel_path in candidates:
        full_path = project_root / rel_path
        if not full_path.exists():
            continue

        print(f"\n  Source: {rel_path}")
        img = Image.open(full_path)
        print(f"  Size: {img.size}")

        debug_subdir = os.path.join(DEBUG_DIR, Path(rel_path).stem)
        regions = skill.extract_regions(img, "note_detail", debug_dir=debug_subdir)

        print(f"  Regions extracted: {len(regions)}")
        for name, crop in regions.items():
            print(f"    {name:20s} -> {crop.size[0]:5d} x {crop.size[1]:5d}")
            # Save cropped region
            crop.save(os.path.join(debug_subdir, f"region_{name}.png"))

        # Print extraction prompts
        prompts = skill.get_extraction_prompts("note_detail")
        print(f"  Extraction prompts available: {list(prompts.keys())}")


def test_search_results_extraction(skill: XiaohongshuSkill):
    """Test region extraction on search results screenshots."""
    print("\n" + "=" * 60)
    print("SEARCH RESULTS REGION EXTRACTION")
    print("=" * 60)

    candidates = [
        "f1_report/search_1.png",
        "benchmark_results/task1_homepage.png",
        "benchmark_results/task5_note_detail.png",
    ]

    for rel_path in candidates:
        full_path = project_root / rel_path
        if not full_path.exists():
            continue

        print(f"\n  Source: {rel_path}")
        img = Image.open(full_path)
        print(f"  Size: {img.size}")

        debug_subdir = os.path.join(DEBUG_DIR, Path(rel_path).stem)
        regions = skill.extract_regions(img, "search_results", debug_dir=debug_subdir)

        print(f"  Regions extracted: {len(regions)}")
        for name, crop in regions.items():
            print(f"    {name:20s} -> {crop.size[0]:5d} x {crop.size[1]:5d}")
            crop.save(os.path.join(debug_subdir, f"region_{name}.png"))


def test_end_to_end(skill: XiaohongshuSkill):
    """End-to-end: identify page type then extract regions."""
    print("\n" + "=" * 60)
    print("END-TO-END (detect + extract)")
    print("=" * 60)

    test_files = [
        "f1_report/_temp_note_detail_1.png",
        "f1_report/search_1.png",
        "benchmark_results/task1_homepage.png",
    ]

    for rel_path in test_files:
        full_path = project_root / rel_path
        if not full_path.exists():
            print(f"  SKIP  {rel_path}")
            continue

        img = Image.open(full_path)
        page_type = skill.identify_page_type(img)
        debug_subdir = os.path.join(DEBUG_DIR, f"e2e_{Path(rel_path).stem}")
        regions = skill.extract_regions(img, page_type, debug_dir=debug_subdir)

        print(f"  {rel_path}")
        print(f"    page_type={page_type}, regions={list(regions.keys())}")

        for name, crop in regions.items():
            crop.save(os.path.join(debug_subdir, f"region_{name}.png"))


if __name__ == "__main__":
    os.makedirs(DEBUG_DIR, exist_ok=True)
    skill = XiaohongshuSkill()

    test_page_type_detection(skill)
    test_note_detail_extraction(skill)
    test_search_results_extraction(skill)
    test_end_to_end(skill)

    print(f"\nDebug images saved to: {DEBUG_DIR}")
