"""Test carousel image collection + parallel OCR + Vision pipeline.

Prerequisites:
1. Load chrome_extension/ as unpacked extension in Chrome
2. Navigate to xiaohongshu.com and log in
3. Run this test script
4. Click 'Connect' in the extension popup

Produces: test_carousel_output/report.html with screenshots, images, OCR, Vision results.

Usage:
    python tests/test_carousel.py
"""

import asyncio
import base64
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from clawvision.agent.bridge import ExtensionBridge
from clawvision.agent.media import MediaProcessor
from clawvision.agent.xhs.browser import XHSBrowser
from clawvision.agent.xhs.entities import NoteEntity, NoteCard, ImageInfo
from clawvision.agent.xhs.processor import NoteProcessor, ProcessorConfig


OUTPUT_DIR = Path("test_carousel_output")


def generate_html_report(
    note: NoteEntity,
    timing: dict,
    screenshots: list[str],
    saved_images: list[str],
    total_dt: float,
    note_url: str = "",
) -> str:
    """Generate visual HTML report for manual review."""
    def _esc(s):
        return str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    parts = [
        "<!DOCTYPE html><html><head><meta charset='utf-8'>",
        "<title>Carousel + Image Pipeline Test Report</title>",
        "<style>",
        "body{font-family:-apple-system,sans-serif;max-width:1200px;margin:0 auto;padding:20px;line-height:1.6;color:#333;background:#fafafa}",
        "h1{color:#ff2442}h2{color:#333;border-bottom:2px solid #ff2442;padding-bottom:5px;margin-top:30px}",
        ".card{background:#fff;border:1px solid #eee;border-radius:8px;padding:16px;margin:12px 0;box-shadow:0 1px 3px rgba(0,0,0,0.05)}",
        ".meta{color:#888;font-size:13px}",
        ".tag{background:#fff0f0;color:#ff2442;padding:2px 8px;border-radius:12px;font-size:12px;margin:2px;display:inline-block}",
        "img.screenshot{max-width:100%;max-height:500px;border:1px solid #ddd;border-radius:6px;margin:8px 0}",
        "img.note-img{max-width:300px;max-height:400px;border:1px solid #ddd;border-radius:6px;margin:4px}",
        ".img-grid{display:flex;flex-wrap:wrap;gap:12px;margin:12px 0}",
        ".img-item{background:#fff;border:1px solid #eee;border-radius:8px;padding:12px;max-width:320px}",
        ".img-item img{max-width:100%;border-radius:4px}",
        ".ocr{background:#fffde7;border:1px solid #ffd54f;border-radius:6px;padding:8px;margin:6px 0;font-size:12px;white-space:pre-wrap;max-height:200px;overflow-y:auto}",
        ".vision{background:#e3f2fd;border:1px solid #90caf9;border-radius:6px;padding:8px;margin:6px 0;font-size:12px}",
        ".timing-table{border-collapse:collapse;width:100%;font-size:13px}",
        ".timing-table th,.timing-table td{border:1px solid #ddd;padding:6px 10px;text-align:left}",
        ".timing-table th{background:#f5f5f5}",
        ".timing-table tr:nth-child(even){background:#fafafa}",
        ".summary{background:#e8f5e9;padding:16px;border-radius:8px;margin:12px 0;font-size:14px}",
        ".warn{color:#e65100;font-weight:bold}",
        ".ok{color:#2e7d32;font-weight:bold}",
        "</style></head><body>",
    ]

    parts.append("<h1>Carousel + Image Pipeline Test Report</h1>")
    parts.append(f"<p class='meta'>Generated: {time.strftime('%Y-%m-%d %H:%M:%S')} | Total: {total_dt:.2f}s</p>")

    # Summary
    n_vis = sum(1 for img in note.images if img.vision_description)
    n_ocr = sum(1 for img in note.images if img.ocr_text)
    comp = note.completeness
    score = note.completeness_score

    parts.append("<div class='summary'>")
    parts.append(f"<strong>Note:</strong> {_esc(note.title)}<br>")
    if note_url:
        parts.append(f"<p><a href='{_esc(note_url)}' target='_blank'>Open note in browser</a></p>")
    parts.append(f"<strong>Type:</strong> {note.note_type.value} | <strong>Images:</strong> {len(note.images)}<br>")
    parts.append(f"<strong>Vision:</strong> {n_vis}/{len(note.images)} | <strong>OCR:</strong> {n_ocr}/{len(note.images)}<br>")
    parts.append(f"<strong>Completeness:</strong> {score:.0%} — ")
    for k, v in comp.items():
        cls = 'ok' if v else 'warn'
        if not v and k == 'comments':
            label = 'NOT COLLECTED'
        else:
            label = 'OK' if v else 'MISSING'
        parts.append(f"<span class='{cls}'>{k}: {label}</span> | ")
    parts.append("</div>")

    # Screenshots
    if screenshots:
        parts.append("<h2>Screenshots</h2>")
        for sp in screenshots:
            rel = os.path.relpath(sp, str(OUTPUT_DIR))
            parts.append(f"<p class='meta'>{os.path.basename(sp)}</p>")
            parts.append(f'<img class="screenshot" src="{rel}">')

    # Note content
    parts.append("<h2>Note Content (DOM)</h2>")
    parts.append("<div class='card'>")
    parts.append(f"<h3>{_esc(note.title)}</h3>")
    parts.append(f"<p class='meta'>Author: {_esc(note.author_name)} | Likes: {_esc(note.likes)} | "
                 f"Favorites: {_esc(note.favorites)} | Comments: {_esc(note.comments_count)}</p>")
    if note.hashtags:
        parts.append("<p>" + " ".join(f"<span class='tag'>{_esc(t)}</span>" for t in note.hashtags) + "</p>")
    if note.content:
        parts.append(f"<p>{_esc(note.content[:800])}</p>")
    parts.append("</div>")

    # Images with OCR + Vision
    parts.append(f"<h2>Images ({len(note.images)})</h2>")
    parts.append("<div class='img-grid'>")
    for i, img in enumerate(note.images):
        parts.append("<div class='img-item'>")
        parts.append(f"<p><strong>Image {img.index+1}</strong> {'(cover)' if img.is_cover else ''}</p>")

        # Show saved image if available
        img_path = None
        for sp in saved_images:
            if f"_img{img.index+1}." in sp:
                img_path = sp
                break
        if img_path:
            rel = os.path.relpath(img_path, str(OUTPUT_DIR))
            parts.append(f'<img class="note-img" src="{rel}">')

        if img.ocr_text:
            parts.append(f"<div class='ocr'><strong>OCR ({len(img.ocr_text)} chars):</strong>\n{_esc(img.ocr_text[:500])}</div>")
        else:
            parts.append("<div class='ocr'><span class='warn'>OCR: empty</span></div>")

        if img.vision_description:
            parts.append(f"<div class='vision'><strong>Vision:</strong> {_esc(img.vision_description[:300])}</div>")
        else:
            parts.append("<div class='vision'><span class='warn'>Vision: empty</span></div>")

        parts.append("</div>")
    parts.append("</div>")

    # Timing
    parts.append("<h2>Timing Breakdown</h2>")
    parts.append("<p class='meta'>Wall-time rows show actual elapsed time. Cumulative rows show sum of parallel operations.</p>")
    parts.append("<table class='timing-table'><tr><th>Operation</th><th>Type</th><th>Count</th><th>Total (s)</th><th>Avg (s)</th></tr>")
    wall_time_ops = {'image_download_batch', 'image_process_batch', 'process_note_media', 'carousel_flip', 'poster_download'}
    for op, stats in sorted(timing.items()):
        op_type = "wall" if op in wall_time_ops else "cumulative"
        row_style = " style='background:#e8f5e9'" if op_type == "wall" else ""
        parts.append(
            f"<tr{row_style}><td>{_esc(op)}</td><td>{op_type}</td><td>{stats['count']}</td>"
            f"<td>{stats['total_s']:.2f}</td><td>{stats['avg_s']:.2f}</td></tr>"
        )
    parts.append("</table>")

    parts.append("</body></html>")
    return "\n".join(parts)


async def test_carousel_pipeline():
    """Test: search, open note, collect carousel images, parallel OCR + Vision, generate report."""

    print("\n" + "=" * 60)
    print("  Carousel + Parallel Image Pipeline Test")
    print("=" * 60)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "screenshots").mkdir(exist_ok=True)
    # Clean previous images to avoid stale files
    img_dir = OUTPUT_DIR / "images"
    if img_dir.exists():
        for f in img_dir.iterdir():
            f.unlink()
    img_dir.mkdir(exist_ok=True)

    # Setup
    bridge = ExtensionBridge(port=8765)
    browser = XHSBrowser(bridge)
    media = MediaProcessor()

    proc_config = ProcessorConfig(
        max_images=10,
        use_ocr=True,
        use_vision=True,
        vision_concurrency=3,
    )

    log_entries = []
    t0_global = time.time()

    def log_fn(action, detail="", duration=None):
        elapsed = time.time() - t0_global
        dur_str = f" ({duration:.2f}s)" if duration is not None else ""
        log_entries.append({"action": action, "detail": detail, "duration": duration})
        print(f"  [{elapsed:5.1f}s] {action}{dur_str}: {detail[:100]}")

    processor = NoteProcessor(browser, media, proc_config, log_fn=log_fn)
    screenshots = []

    # Connect
    await bridge.start()
    print("\n  >>> Click 'Connect' in the Chrome Extension popup <<<\n")
    await bridge.wait_for_connection(timeout=60)
    print("  Connected!")

    # Note: extension code is loaded when Chrome starts.
    # To pick up code changes, restart Chrome before running the test.

    # Open background window for XHS (doesn't steal user's focus)
    print("  Opening background window for XHS...")
    try:
        win_info = await bridge.create_background_window(
            url="https://www.xiaohongshu.com",
        )
        bg_window_id = win_info.get("windowId")
        print(f"  Background window opened (id={bg_window_id})")
    except Exception as e:
        print(f"  Background window failed ({e}), using current tab")
        bg_window_id = None
        # Fallback: navigate current tab to XHS
        tab = await bridge.get_tab_info()
        if "xiaohongshu.com" not in tab.get("url", ""):
            await browser.navigate("https://www.xiaohongshu.com")
    await asyncio.sleep(5)  # Wait for XHS to fully load + content script

    # Search
    keyword = "露营装备推荐"
    print(f"\n  Searching: {keyword}")
    await browser.navigate_to_search(keyword)
    await asyncio.sleep(3)

    # Screenshot search results
    sp = await bridge.save_screenshot(OUTPUT_DIR / "screenshots" / "search_results.png")
    if sp:
        screenshots.append(sp)

    # Extract cards
    raw_cards = await browser.extract_search_cards()
    if not raw_cards:
        await asyncio.sleep(3)
        raw_cards = await browser.extract_search_cards()

    cards = [NoteCard.from_dom_dict(c) for c in raw_cards]
    print(f"  Found {len(cards)} cards")

    if not cards:
        print("  ERROR: No cards found!")
        await bridge.stop()
        return

    for c in cards[:5]:
        print(f"    {c.position}: {c.title[:40]} | {c.likes}")

    # Pick first image-type note
    target = None
    for c in cards:
        if c.note_type.value != "video" and c.title:
            target = c
            break
    if not target:
        target = cards[0]

    print(f"\n  Opening: {target.title[:50]}")
    await browser.click_card(target.position)
    await asyncio.sleep(3)

    state = await browser.detect_state()
    print(f"  State: {state.get('state')}")

    if state.get("state") != "note_detail":
        print("  ERROR: Failed to open note detail")
        await bridge.stop()
        return

    # Screenshot note
    sp = await bridge.save_screenshot(OUTPUT_DIR / "screenshots" / "note_detail.png")
    if sp:
        screenshots.append(sp)

    # DOM extraction
    raw_note = await browser.extract_note_content()
    note = NoteEntity.from_dom_dict(raw_note)
    note.source_keyword = keyword

    # Capture note URL for report traceability
    tab_info = await bridge.get_tab_info()
    note_url = tab_info.get("url", "")

    print(f"\n  Note: {note.title[:50]}")
    print(f"  Type: {note.note_type.value}")
    print(f"  DOM images: {len(note.images)} | indicator: {note.image_count}")

    # THE MAIN TEST: NoteProcessor handles everything
    print(f"\n  --- NoteProcessor.process_note() ---")
    t_total = time.time()
    await processor.process_note(note)
    total_dt = time.time() - t_total

    # Save images to disk for the report
    saved_images = await processor.save_images(note, str(OUTPUT_DIR))

    # Results summary
    n_vis = sum(1 for img in note.images if img.vision_description)
    n_ocr = sum(1 for img in note.images if img.ocr_text)
    print(f"\n  --- Results ---")
    print(f"  Images: {len(note.images)} | Vision: {n_vis} | OCR: {n_ocr}")
    print(f"  Pipeline time: {total_dt:.2f}s")

    for img in note.images:
        ocr_len = len(img.ocr_text) if img.ocr_text else 0
        vis_len = len(img.vision_description) if img.vision_description else 0
        print(f"  [{img.index+1}] OCR: {ocr_len} chars | Vision: {vis_len} chars")

    # Timing
    timing = processor.timing.summary()
    print(f"\n  --- Timing ---")
    for op, stats in sorted(timing.items()):
        print(f"  {op:30s}  n={stats['count']:2d}  total={stats['total_s']:6.2f}s  avg={stats['avg_s']:.2f}s")

    # Generate HTML report
    html = generate_html_report(note, timing, screenshots, saved_images, total_dt, note_url)
    html_path = OUTPUT_DIR / "report.html"
    html_path.write_text(html)
    print(f"\n  Report: {html_path}")

    # Save JSON results too
    results = {
        "note_title": note.title,
        "note_url": note_url,
        "note_type": note.note_type.value,
        "image_count": len(note.images),
        "images": [
            {
                "index": img.index,
                "is_cover": img.is_cover,
                "ocr_chars": len(img.ocr_text),
                "has_vision": bool(img.vision_description),
                "vision_preview": img.vision_description[:150] if img.vision_description else "",
                "ocr_preview": img.ocr_text[:150].replace('\n', ' ') if img.ocr_text else "",
            }
            for img in note.images
        ],
        "timing": timing,
        "total_pipeline_s": round(total_dt, 2),
        "completeness": note.completeness,
    }
    with open(OUTPUT_DIR / "test_results.json", "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    # Close
    await browser.close_note()
    await asyncio.sleep(1)
    # Close background window if we opened one
    if bg_window_id:
        try:
            await bridge.close_window(bg_window_id)
            print("  Background window closed.")
        except Exception:
            pass
    await bridge.stop()

    print(f"\n  DONE — {total_dt:.2f}s for {len(note.images)} images, {n_ocr} OCR, {n_vis} vision")
    print(f"  Open: {html_path.absolute()}")


if __name__ == "__main__":
    asyncio.run(test_carousel_pipeline())
