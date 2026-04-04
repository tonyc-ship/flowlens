"""Manual single-note entity pipeline script for Xiaohongshu.

Prerequisites:
1. Load `chrome_extension/` as an unpacked extension in Chrome.
2. Log in to Xiaohongshu in that Chrome profile.
3. Run this script and click `Connect` in the extension popup.

Produces a detailed visual report under `test_carousel_output/`.

Usage:
    python tests/manual_xhs_carousel.py
    python tests/manual_xhs_carousel.py --note-type video --keyword "咖啡拉花教程"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from flowlens.core.bridge import ExtensionBridge
from flowlens.core.reporting import markdown_styles, render_markdown_block
from flowlens.perception.media import MediaProcessor
from flowlens.platforms.xhs.browser import XHSBrowser
from flowlens.platforms.xhs.entities import Comment, NoteCard, NoteEntity, NoteType
from flowlens.platforms.xhs.processor import NoteProcessor, ProcessorConfig


DEFAULT_OUTPUT_DIR = Path("test_carousel_output")


def esc(value: str) -> str:
    return str(value or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def clean_output_dir(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for name in ("screenshots", "images", "video_frames"):
        folder = output_dir / name
        if folder.exists():
            for child in folder.iterdir():
                if child.is_file():
                    child.unlink()
        folder.mkdir(exist_ok=True)


async def collect_note_comments(
    browser: XHSBrowser,
    max_comments: int,
    max_scrolls: int,
) -> list[Comment]:
    merged: list[Comment] = []
    for round_idx in range(max_scrolls + 1):
        raw_comments = await browser.extract_comments(
            max_comments=max_comments,
            prefer_hot=True,
        )
        merged = NoteEntity.merge_comments(
            [*merged, *[Comment.from_dom_dict(c) for c in raw_comments]]
        )
        if round_idx >= max_scrolls:
            break
        await browser.scroll_note(420)
        await asyncio.sleep(1)
    return merged[:max_comments]


def rank_targets(cards: list[NoteCard], preferred_type: str) -> list[NoteCard]:
    if preferred_type == "any":
        return [card for card in cards if card.title]

    wanted = NoteType(preferred_type)
    return sorted(
        [card for card in cards if card.title],
        key=lambda card: (
            0 if card.note_type == wanted else 1 if card.note_type == NoteType.UNKNOWN else 2,
            card.position,
        ),
    )


def generate_html_report(
    note: NoteEntity,
    timing: dict,
    screenshots: list[str],
    saved_images: list[str],
    saved_video_frames: list[str],
    total_dt: float,
    log_entries: list[dict],
    output_dir: Path,
    keyword: str,
) -> str:
    n_vis = sum(1 for img in note.images if img.vision_description)
    n_ocr = sum(1 for img in note.images if img.ocr_text)
    comp = note.completeness
    score = note.completeness_score
    image_map = {}
    for path_str in saved_images:
        path = Path(path_str)
        if "_img" not in path.stem:
            continue
        try:
            image_map[int(path.stem.split("_img")[-1])] = path
        except ValueError:
            continue
    video_frame_map = {index + 1: Path(path) for index, path in enumerate(saved_video_frames)}

    def screenshot_tag(path: str) -> str:
        rel = os.path.relpath(path, str(output_dir))
        return f'<img class="screenshot" src="{rel}" alt="{esc(Path(path).name)}">'

    parts = [
        "<!DOCTYPE html><html><head><meta charset='utf-8'>",
        "<title>XHS Single Note Entity Report</title>",
        "<style>",
        "body{font-family:-apple-system,sans-serif;max-width:1280px;margin:0 auto;padding:24px;line-height:1.6;color:#222;background:#f6f4ef}",
        "h1{color:#d62828;margin-bottom:8px}h2{margin-top:32px;border-bottom:2px solid #d62828;padding-bottom:6px}",
        ".card{background:#fff;border:1px solid #e7dfd3;border-radius:12px;padding:16px;margin:12px 0;box-shadow:0 4px 18px rgba(0,0,0,0.04)}",
        ".summary{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}",
        ".metric{background:#fff7ec;border:1px solid #f0d5aa;border-radius:10px;padding:12px}",
        ".meta{color:#6b7280;font-size:13px}",
        ".tag{display:inline-block;background:#ffe8e8;color:#b42318;border-radius:999px;padding:2px 8px;margin:2px;font-size:12px}",
        ".ok{color:#0a7d33;font-weight:600}.warn{color:#b54708;font-weight:600}",
        ".img-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}",
        ".img-card{background:#fff;border:1px solid #e7dfd3;border-radius:10px;padding:12px}",
        ".img-card img{max-width:100%;border-radius:8px;border:1px solid #e5e7eb}",
        "img.screenshot{max-width:100%;max-height:720px;border-radius:10px;border:1px solid #ddd;margin:10px 0}",
        ".ocr{background:#fffbe6;border:1px solid #f7d56b;border-radius:8px;padding:10px;font-size:12px;white-space:pre-wrap;max-height:220px;overflow:auto}",
        ".vision{background:#eef6ff;border:1px solid #b6d3ff;border-radius:8px;padding:10px;font-size:12px}",
        ".comment{background:#fbfbfb;border:1px solid #ececec;border-radius:8px;padding:10px;margin:8px 0}",
        ".comment .meta{display:block;margin-top:4px}",
        ".sub-comment{margin-top:8px;padding-left:12px;border-left:3px solid #f2d0d0}",
        ".code{background:#111827;color:#f9fafb;border-radius:8px;padding:12px;font-family:ui-monospace,monospace;font-size:12px;white-space:pre-wrap;max-height:360px;overflow:auto}",
        ".timing-table{border-collapse:collapse;width:100%;font-size:13px}",
        ".timing-table th,.timing-table td{border:1px solid #e5e7eb;padding:8px 10px;text-align:left}",
        ".timing-table th{background:#faf5eb}",
        markdown_styles(),
        "</style></head><body>",
        "<h1>XHS Single Note Entity Report</h1>",
        f"<p class='meta'>Generated: {time.strftime('%Y-%m-%d %H:%M:%S')} | Keyword: {esc(keyword)} | Total: {total_dt:.2f}s</p>",
    ]

    parts.append("<div class='summary'>")
    parts.append(f"<div class='metric'><strong>Title</strong><br>{esc(note.title)}</div>")
    parts.append(f"<div class='metric'><strong>Type</strong><br>{esc(note.note_type.value)}</div>")
    parts.append(f"<div class='metric'><strong>Completeness</strong><br>{score:.0%}</div>")
    parts.append(f"<div class='metric'><strong>Hot Comments</strong><br>{len(note.comments)}</div>")
    parts.append(f"<div class='metric'><strong>Vision / OCR</strong><br>{n_vis}/{len(note.images)} vision, {n_ocr}/{len(note.images)} ocr</div>")
    if note.video:
        parts.append(f"<div class='metric'><strong>Video Source</strong><br>{esc(note.video.stream_type or 'unresolved')}</div>")
    parts.append("</div>")

    parts.append("<div class='card'>")
    parts.append(f"<p><strong>Author:</strong> {esc(note.author_name)} | <strong>Likes:</strong> {esc(note.likes)} | <strong>Favorites:</strong> {esc(note.favorites)} | <strong>Comments:</strong> {esc(note.comments_count)}</p>")
    parts.append(f"<p><strong>URL:</strong> <a href='{esc(note.url)}' target='_blank'>{esc(note.url)}</a></p>")
    if note.author_url:
        parts.append(f"<p><strong>Author URL:</strong> <a href='{esc(note.author_url)}' target='_blank'>{esc(note.author_url)}</a></p>")
    if note.location or note.ip_location:
        parts.append(f"<p><strong>Location:</strong> {esc(note.location)} | <strong>IP:</strong> {esc(note.ip_location)}</p>")
    parts.append("<p>")
    for key, ok in comp.items():
        parts.append(f"<span class='{'ok' if ok else 'warn'}'>{esc(key)}: {'OK' if ok else 'MISSING'}</span> ")
    parts.append("</p>")
    if note.hashtags:
        parts.append("<p>" + " ".join(f"<span class='tag'>{esc(tag)}</span>" for tag in note.hashtags) + "</p>")
    if note.content:
        parts.append(f"<p>{esc(note.content[:1500])}</p>")
    parts.append("</div>")

    parts.append("<h2>Derived Signals</h2>")
    parts.append("<div class='card'>")
    parts.append(f"<p><strong>Format hints:</strong> {esc(', '.join(note.format_hints)) or 'n/a'}</p>")
    parts.append(f"<p><strong>Price mentions:</strong> {esc(', '.join(note.price_mentions)) or 'n/a'}</p>")
    parts.append(f"<p><strong>CTA phrases:</strong> {esc(' | '.join(note.cta_phrases)) or 'n/a'}</p>")
    if note.key_points:
        parts.append("<strong>Key points</strong><ol>")
        for point in note.key_points:
            parts.append(f"<li>{esc(point)}</li>")
        parts.append("</ol>")
    parts.append("</div>")

    if screenshots:
        parts.append("<h2>Screenshots</h2>")
        for shot in screenshots:
            parts.append(f"<div class='card'><p class='meta'>{esc(Path(shot).name)}</p>{screenshot_tag(shot)}</div>")

    if note.note_type == NoteType.VIDEO and note.video:
        video = note.video
        parts.append("<h2>Video Understanding</h2>")
        parts.append("<div class='card'>")
        parts.append(f"<p><strong>Resolved URL:</strong> {esc(video.resolved_url or video.url)}</p>")
        if video.all_source_urls():
            parts.append(f"<p><strong>All source URLs:</strong> {esc(' | '.join(video.all_source_urls()))}</p>")
        parts.append(f"<p><strong>Stream type:</strong> {esc(video.stream_type or 'unknown')}</p>")
        parts.append(f"<p><strong>Download error:</strong> {esc(video.download_error or 'none')}</p>")
        parts.append(f"<p><strong>Duration:</strong> {esc(video.duration_s)}</p>")
        if video.poster_description:
            parts.append("<strong>Poster vision</strong>")
            parts.append(render_markdown_block(video.poster_description, "vision"))
        if video.poster_ocr:
            parts.append(f"<div class='ocr'><strong>Poster OCR:</strong>\n{esc(video.poster_ocr[:1200])}</div>")
        if video.visual_summary:
            parts.append("<strong>Visual summary</strong>")
            parts.append(render_markdown_block(video.visual_summary, "vision"))
        if video.transcript_summary:
            parts.append("<strong>Transcript summary</strong>")
            parts.append(render_markdown_block(video.transcript_summary, "vision"))
        if video.transcript:
            parts.append(f"<div class='ocr'><strong>Transcript:</strong>\n{esc(video.transcript[:4000])}</div>")
        if video.frame_descriptions or saved_video_frames:
            parts.append("<h3>Video Frames</h3><div class='img-grid'>")
            for index, description in enumerate(video.frame_descriptions or [""] * len(saved_video_frames), start=1):
                parts.append("<div class='img-card'>")
                parts.append(f"<p><strong>Frame {index}</strong></p>")
                frame_path = video_frame_map.get(index)
                if frame_path:
                    rel = os.path.relpath(frame_path, str(output_dir))
                    parts.append(f'<img src="{rel}" alt="frame {index}">')
                if description:
                    parts.append("<strong>Vision</strong>")
                    parts.append(render_markdown_block(description[:1200], "vision"))
                parts.append("</div>")
            parts.append("</div>")
        parts.append("</div>")

    if note.images:
        parts.append(f"<h2>Media ({len(note.images)})</h2>")
        parts.append("<div class='img-grid'>")
        for img in note.images:
            parts.append("<div class='img-card'>")
            parts.append(f"<p><strong>Image {img.index + 1}</strong> {'(cover)' if img.is_cover else ''}</p>")
            saved = image_map.get(img.index + 1)
            if saved:
                rel = os.path.relpath(saved, str(output_dir))
                parts.append(f'<img src="{rel}" alt="image {img.index + 1}">')
            if img.ocr_text:
                parts.append(f"<div class='ocr'><strong>OCR:</strong>\n{esc(img.ocr_text[:1200])}</div>")
            if img.vision_description:
                parts.append("<strong>Vision</strong>")
                parts.append(render_markdown_block(img.vision_description[:1200], "vision"))
            parts.append("</div>")
        parts.append("</div>")

    if note.comments:
        parts.append("<h2>Hot Comments</h2>")
        for idx, comment in enumerate(note.hottest_comments(10), start=1):
            parts.append("<div class='comment'>")
            parts.append(f"<strong>{idx}. {esc(comment.username or '匿名')}</strong>: {esc(comment.text)}")
            parts.append(
                f"<span class='meta'>热度={comment.heat_score} | likes={comment.like_count} | replies={comment.reply_count} | time={esc(comment.time)}"
                f"{' | author' if comment.is_author_reply else ''}{' | pinned' if comment.is_pinned else ''}</span>"
            )
            for sub in comment.sub_comments[:3]:
                parts.append(
                    f"<div class='sub-comment'><strong>{esc(sub.username or '匿名')}</strong>: {esc(sub.text)}"
                    f"<span class='meta'>likes={sub.like_count} | time={esc(sub.time)}</span></div>"
                )
            parts.append("</div>")

    parts.append("<h2>Timing</h2>")
    parts.append("<table class='timing-table'><tr><th>Operation</th><th>Count</th><th>Total (s)</th><th>Avg (s)</th></tr>")
    for op, stats in sorted(timing.items()):
        parts.append(
            f"<tr><td>{esc(op)}</td><td>{stats['count']}</td><td>{stats['total_s']:.2f}</td><td>{stats['avg_s']:.2f}</td></tr>"
        )
    parts.append("</table>")

    parts.append("<h2>Execution Log</h2><div class='code'>")
    for entry in log_entries:
        detail = entry.get("detail", "")
        if entry.get("duration") is not None:
            detail = f"{detail} ({entry['duration']:.2f}s)"
        parts.append(f"{esc(entry.get('action', ''))}: {esc(detail)}\n")
    parts.append("</div>")

    parts.append("</body></html>")
    return "\n".join(parts)


async def run_note_entity_test(
    keyword: str,
    note_type: str,
    output_dir: Path,
    max_comments: int,
    comment_scrolls: int,
) -> Path:
    print("\n" + "=" * 64)
    print("  XHS Single Note Entity Pipeline Test")
    print("=" * 64)

    clean_output_dir(output_dir)

    bridge = ExtensionBridge(port=8765)
    browser = XHSBrowser(bridge)
    media = MediaProcessor()
    processor = NoteProcessor(
        browser,
        media,
        ProcessorConfig(max_images=10, use_ocr=True, use_vision=True, use_whisper=True),
    )

    log_entries: list[dict] = []
    t0_global = time.time()

    def log_fn(action: str, detail: str = "", duration: float | None = None):
        elapsed = time.time() - t0_global
        suffix = f" ({duration:.2f}s)" if duration is not None else ""
        print(f"  [{elapsed:6.1f}s] {action}{suffix}: {detail[:120]}")
        log_entries.append({"action": action, "detail": detail, "duration": duration})

    processor._log_fn = log_fn  # reuse the existing processor logging hook

    screenshots: list[str] = []
    try:
        await bridge.start()
        print("\n  >>> Click 'Connect' in the Chrome Extension popup <<<\n")
        await bridge.wait_for_connection(timeout=60)
        print("  Connected.")
        print("  Reloading extension to pick up latest content script...")
        await bridge.reload_extension()

        tab = await bridge.get_tab_info()
        if "xiaohongshu.com" not in tab.get("url", ""):
            print("  Navigating current tab to Xiaohongshu...")
            await browser.navigate("https://www.xiaohongshu.com")
        await asyncio.sleep(5)

        print(f"\n  Searching: {keyword}")
        await browser.navigate_to_search(keyword)
        preferred_filter = "视频" if note_type == "video" else None
        search_state = await browser.wait_for_search_results(
            preferred_filter=preferred_filter,
            timeout_s=24,
            poll_s=2,
        )
        print(
            "  Search state:"
            f" filter={search_state.get('active_filter') or 'unknown'}"
            f" cards={search_state.get('card_count', 0)}"
            f" loading={search_state.get('loading')}"
            f" no_results={search_state.get('has_no_results')}"
        )

        raw_cards = await browser.extract_search_cards()

        search_shot = await bridge.save_screenshot(output_dir / "screenshots" / "search_results.png")
        if search_shot:
            screenshots.append(search_shot)

        cards = [NoteCard.from_dom_dict(card) for card in raw_cards]
        print(f"  Found {len(cards)} cards")
        if not cards:
            raise RuntimeError("No search cards found")

        target = None
        note = None
        note_shot = ""
        for candidate in rank_targets(cards, note_type)[:8]:
            print(f"  Trying: {candidate.title[:60]} [{candidate.note_type.value}]")
            await browser.click_card(candidate.position)
            await asyncio.sleep(3)

            state = await browser.detect_state()
            if state.get("state") != "note_detail":
                continue

            raw_note = await browser.extract_note_content()
            candidate_note = NoteEntity.from_dom_dict(raw_note)

            if note_type == "any" or candidate_note.note_type == NoteType(note_type):
                target = candidate
                note = candidate_note
                break

            await browser.close_note()
            await asyncio.sleep(1.5)

        if target is None or note is None:
            raise RuntimeError(f"Failed to find a {note_type} note from the current search results")

        print(f"  Opening: {target.title[:60]} [{target.note_type.value}]")
        note_shot = await bridge.save_screenshot(output_dir / "screenshots" / "note_detail.png")
        if note_shot:
            screenshots.append(note_shot)

        note.source_keyword = keyword
        note.screenshot_path = note_shot or ""
        note_url = (await bridge.get_tab_info()).get("url", "") or note.url
        note.url = note_url

        print(f"  Note title: {note.title[:80]}")
        print(f"  Note type: {note.note_type.value}")

        t_total = time.time()
        await processor.process_note(note)

        t_comments = time.time()
        note.comments = await collect_note_comments(browser, max_comments, comment_scrolls)
        note.refresh_derived_fields()
        comments_dt = time.time() - t_comments
        processor.timing.record("comments_extract", comments_dt)
        log_fn(
            "comments",
            f"{len(note.comments)} comments, hottest={note.hottest_comments(1)[0].heat_score if note.comments else 0}",
            comments_dt,
        )

        comments_shot = await bridge.save_screenshot(output_dir / "screenshots" / "comments_panel.png")
        if comments_shot:
            screenshots.append(comments_shot)

        total_dt = time.time() - t_total
        saved_images = await processor.save_images(note, str(output_dir))
        saved_video_frames = await processor.save_video_frames(note, str(output_dir))
        timing = processor.timing.summary()

        results = {
            "keyword": keyword,
            "note_type": note.note_type.value,
            "target_card_title": target.title,
            "timing": timing,
            "total_pipeline_s": round(total_dt, 2),
            "completeness": note.completeness,
            "note": note.to_report_dict(),
            "screenshots": screenshots,
            "saved_images": saved_images,
            "saved_video_frames": saved_video_frames,
            "log": log_entries,
        }
        (output_dir / "test_results.json").write_text(
            json.dumps(results, indent=2, ensure_ascii=False)
        )

        html = generate_html_report(
            note=note,
            timing=timing,
            screenshots=screenshots,
            saved_images=saved_images,
            saved_video_frames=saved_video_frames,
            total_dt=total_dt,
            log_entries=log_entries,
            output_dir=output_dir,
            keyword=keyword,
        )
        report_path = output_dir / "report.html"
        report_path.write_text(html)

        print(f"\n  Report: {report_path}")
        print(f"  Hot comments: {len(note.comments)}")
        if note.video:
            print(f"  Video source: {note.video.resolved_url or note.video.url or 'N/A'}")
            print(f"  Transcript chars: {len(note.video.transcript)}")
            print(f"  Download error: {note.video.download_error or 'none'}")
        return report_path
    finally:
        try:
            await browser.close_note()
            await asyncio.sleep(1)
        except Exception:
            pass
        try:
            await bridge.stop()
        except Exception:
            pass


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--keyword", default="露营装备推荐")
    parser.add_argument("--note-type", choices=["any", "image", "video"], default="video")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--max-comments", type=int, default=20)
    parser.add_argument("--comment-scrolls", type=int, default=2)
    args = parser.parse_args()

    report_path = await run_note_entity_test(
        keyword=args.keyword,
        note_type=args.note_type,
        output_dir=Path(args.output_dir),
        max_comments=args.max_comments,
        comment_scrolls=args.comment_scrolls,
    )
    print(f"\n  Open: {report_path.absolute()}")


if __name__ == "__main__":
    asyncio.run(main())
