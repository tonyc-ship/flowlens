"""Test local LLM backend (Qwen3.5-9B-MLX-4bit) vs Sonnet.

Usage:
    # Text-only test (both backends)
    python tests/manual_local_llm.py

    # Vision test (both backends, requires a screenshot)
    python tests/manual_local_llm.py --vision

    # Local-only (skip Sonnet)
    python tests/manual_local_llm.py --local-only
"""

from __future__ import annotations

import argparse
import base64
import io
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clawvision.agent.media import MediaProcessor, MediaConfig
from clawvision.agent.local_llm import LocalLLM
from clawvision.vision.llm import VisionLLM


def test_text(backend: str, prompt: str) -> dict:
    """Run a text-only test on the given backend."""
    cfg = MediaConfig(backend=backend)
    mp = MediaProcessor(config=cfg)

    print(f"\n{'='*60}")
    print(f"TEXT TEST — backend={backend}")
    print(f"{'='*60}")
    print(f"Prompt: {prompt[:100]}...")

    t0 = time.perf_counter()
    result = mp.call_text(prompt, max_tokens=256)
    elapsed = time.perf_counter() - t0

    print(f"Response ({elapsed:.2f}s):\n{result[:500]}")
    return {"backend": backend, "type": "text", "elapsed": elapsed, "response": result}


def test_vision(backend: str, image_path: str, prompt: str) -> dict:
    """Run a vision test on the given backend."""
    print(f"\n{'='*60}")
    print(f"VISION TEST — backend={backend}")
    print(f"{'='*60}")

    img_bytes = Path(image_path).read_bytes()
    img_b64 = base64.b64encode(img_bytes).decode()

    # Detect media type
    if img_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        media_type = "image/png"
    elif img_bytes[:3] == b"\xff\xd8\xff":
        media_type = "image/jpeg"
    else:
        media_type = "image/png"

    cfg = MediaConfig(backend=backend)
    mp = MediaProcessor(config=cfg)

    print(f"Image: {image_path} ({len(img_bytes)/1024:.0f}KB)")
    print(f"Prompt: {prompt[:100]}...")

    t0 = time.perf_counter()
    result = mp.call_vision(img_b64, prompt, media_type=media_type, max_tokens=512)
    elapsed = time.perf_counter() - t0

    print(f"Response ({elapsed:.2f}s):\n{result[:500]}")
    return {"backend": backend, "type": "vision", "elapsed": elapsed, "response": result}


def test_vision_llm(backend: str, image_path: str) -> dict:
    """Test VisionLLM class with the given backend."""
    from PIL import Image

    print(f"\n{'='*60}")
    print(f"VISION_LLM TEST — backend={backend}")
    print(f"{'='*60}")

    img = Image.open(image_path)
    vlm = VisionLLM(backend=backend)

    t0 = time.perf_counter()
    result = vlm.analyze_page(img)
    elapsed = time.perf_counter() - t0

    print(f"Response ({elapsed:.2f}s):\n{result[:500]}")
    return {"backend": backend, "type": "vision_llm", "elapsed": elapsed, "response": result}


def main():
    parser = argparse.ArgumentParser(description="Test local vs remote LLM backends")
    parser.add_argument("--vision", action="store_true", help="Include vision tests")
    parser.add_argument("--image", type=str, default=None, help="Path to test image")
    parser.add_argument("--local-only", action="store_true", help="Only test local backend")
    args = parser.parse_args()

    # Check local model availability
    if not LocalLLM.is_available():
        print("ERROR: Local model not found. Download it first:")
        print("  modelscope download --model mlx-community/Qwen3.5-9B-MLX-4bit \\")
        print("    --local_dir ~/.clawvision/weights/Qwen3.5-9B-MLX-4bit")
        sys.exit(1)

    text_prompt = (
        "You are analyzing a Xiaohongshu (小红书) note about camping gear (露营装备). "
        "Generate 3 search keywords in Chinese that would help find similar content. "
        "Return as JSON array."
    )

    backends = ["qwen-local"] if args.local_only else ["sonnet", "qwen-local"]
    results = []

    # Text tests
    for b in backends:
        results.append(test_text(b, text_prompt))

    # Vision tests
    if args.vision:
        image_path = args.image
        if not image_path:
            # Try to find any recent screenshot in task_runs
            candidates = sorted(Path("task_runs").rglob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
            if candidates:
                image_path = str(candidates[0])
            else:
                print("\nNo test image found. Pass --image <path> or skip --vision.")
                sys.exit(1)

        vision_prompt = "Describe what you see in this image. Be concise."
        for b in backends:
            results.append(test_vision(b, image_path, vision_prompt))
            results.append(test_vision_llm(b, image_path))

    # Summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    for r in results:
        print(f"  {r['backend']:12s} | {r['type']:10s} | {r['elapsed']:6.2f}s | {r['response'][:60]}...")


if __name__ == "__main__":
    main()
