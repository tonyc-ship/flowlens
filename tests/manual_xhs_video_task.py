"""Manual task test: find and download a specific XHS video.

Task: "下载bloc1攀岩馆的这个月的v2线路合集视频"

Exercises the full pipeline:
  - Background window (no focus steal)
  - Search → browse → select → open note
  - Video processing (download, transcription, frame extraction, vision)
  - Comments extraction
  - Session recording (GIF)
  - Reasoning log (all decisions with context)
  - HTML report with everything
"""

from __future__ import annotations

import asyncio
import json
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime
from html import escape
from pathlib import Path

# ── Configuration ──────────────────────────────────────────────

TASK = "下载bloc1攀岩馆的这个月的v2线路合集视频"
SEARCH_KEYWORDS = [
    "bloc1攀岩馆 v2线路合集",
    "bloc1 v2线路 合集",
    "bloc1攀岩 v2",
]
OUTPUT_DIR = Path("task_video_output")
RECORD_INTERVAL = 2.5  # seconds between session recording frames


# ── Reasoning Logger ───────────────────────────────────────────

@dataclass
class ReasoningEntry:
    timestamp: float  # seconds since start
    phase: str  # e.g. "search", "select_note", "process_video"
    observation: str  # what the agent saw
    reasoning: str  # why it made a decision
    decision: str  # what action was taken
    evidence: str = ""  # supporting data (truncated)


class ReasoningLog:
    """Captures agent thinking/decisions for auditability."""

    def __init__(self):
        self._entries: list[ReasoningEntry] = []
        self._t0 = time.time()

    def think(
        self,
        phase: str,
        observation: str,
        reasoning: str,
        decision: str,
        evidence: str = "",
    ) -> None:
        entry = ReasoningEntry(
            timestamp=round(time.time() - self._t0, 1),
            phase=phase,
            observation=observation,
            reasoning=reasoning,
            decision=decision,
            evidence=evidence[:500],
        )
        self._entries.append(entry)
        # Also print to console
        print(f"  💭 [{entry.timestamp:6.1f}s] [{phase}]")
        print(f"     Observed: {observation[:120]}")
        print(f"     Reasoning: {reasoning[:120]}")
        print(f"     Decision: {decision[:120]}")

    @property
    def entries(self) -> list[ReasoningEntry]:
        return self._entries

    def to_dicts(self) -> list[dict]:
        return [
            {
                "timestamp": e.timestamp,
                "phase": e.phase,
                "observation": e.observation,
                "reasoning": e.reasoning,
                "decision": e.decision,
                "evidence": e.evidence,
            }
            for e in self._entries
        ]


# ── Action Logger ──────────────────────────────────────────────

class ActionLog:
    def __init__(self):
        self._entries: list[dict] = []
        self._t0 = time.time()

    def log(self, action: str, detail: str = "", duration: float | None = None):
        elapsed = round(time.time() - self._t0, 1)
        suffix = f" ({duration:.2f}s)" if duration else ""
        print(f"  [{elapsed:6.1f}s] {action}{suffix}: {detail[:150]}")
        self._entries.append({
            "elapsed_s": elapsed,
            "action": action,
            "detail": detail[:300],
            "duration_s": round(duration, 2) if duration else None,
        })

    @property
    def entries(self) -> list[dict]:
        return self._entries


# ── HTML Report Generator ──────────────────────────────────────

def generate_html_report(
    *,
    task: str,
    note: dict,
    timing: dict,
    action_log: list[dict],
    reasoning_log: list[dict],
    screenshots: list[str],
    saved_video_path: str,
    saved_frames: list[str],
    session_gif: str,
    recording_stats: dict,
    total_time: float,
    note_url: str = "",
) -> str:
    """Generate comprehensive HTML report with all session data."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Build image section
    images_html = ""
    for i, desc in enumerate(note.get("image_descriptions", [])):
        ocr = (note.get("ocr_results", [{}] * (i + 1))[i] if i < len(note.get("ocr_results", [])) else {})
        ocr_text = ocr.get("text", "") if isinstance(ocr, dict) else ""
        images_html += f"""
        <div class='img-item'>
          <h4>Image {i+1}</h4>
          <div class='vision'>{escape(desc[:300])}</div>
          {"<div class='ocr'>" + escape(ocr_text[:300]) + "</div>" if ocr_text else ""}
        </div>"""

    # Build video section
    video_html = ""
    if note.get("transcript") or note.get("video_url"):
        video_html = "<h2>Video</h2><div class='card'>"
        if note.get("video_resolved_url"):
            video_html += f"<p><strong>URL:</strong> <a href='{escape(note['video_resolved_url'])}' target='_blank'>{escape(note['video_resolved_url'][:80])}</a></p>"
        if note.get("video_stream_type"):
            video_html += f"<p><strong>Stream:</strong> {escape(note['video_stream_type'])}</p>"
        if saved_video_path:
            video_html += f"<p class='ok'><strong>Downloaded:</strong> {escape(saved_video_path)}</p>"
        if note.get("video_download_error"):
            video_html += f"<p class='warn'><strong>Download error:</strong> {escape(note['video_download_error'])}</p>"

        # Poster description
        if note.get("cover_description"):
            video_html += f"<h3>Poster Description</h3><div class='vision'>{escape(note['cover_description'])}</div>"

        # Frame descriptions
        if saved_frames:
            video_html += "<h3>Video Frames</h3><div class='img-grid'>"
            frame_descs = note.get("video_frame_descriptions", [])
            for i, fp in enumerate(saved_frames):
                desc = frame_descs[i] if i < len(frame_descs) else ""
                video_html += f"""
                <div class='img-item'>
                  <img src='{escape(fp)}' class='note-img'>
                  <div class='vision'>{escape(desc[:200])}</div>
                </div>"""
            video_html += "</div>"

        # Visual summary
        if note.get("video_visual_summary"):
            video_html += f"<h3>Visual Summary</h3><div class='vision'>{escape(note['video_visual_summary'])}</div>"

        # Transcript
        if note.get("transcript"):
            video_html += f"<h3>Transcript</h3><div class='ocr'>{escape(note['transcript'][:4000])}</div>"
        if note.get("transcript_summary"):
            video_html += f"<h3>Transcript Summary</h3><div class='vision'>{escape(note['transcript_summary'])}</div>"
        video_html += "</div>"

    # Comments section
    comments_html = ""
    hot = note.get("hot_comments", [])
    if hot:
        comments_html = "<h2>Hot Comments</h2>"
        for c in hot[:10]:
            badge = " 📌" if c.get("is_pinned") else ""
            badge += " 👤" if c.get("is_author_reply") else ""
            comments_html += f"""
            <div class='card' style='padding:8px 12px'>
              <strong>{escape(c.get('username',''))}</strong>{badge}
              <span class='meta' style='float:right'>{escape(str(c.get('likes','')))} ❤️ | {escape(c.get('time',''))}</span>
              <p style='margin:4px 0'>{escape(c.get('text','')[:200])}</p>
            </div>"""

    # Reasoning section
    reasoning_html = "<h2>Agent Reasoning Log</h2>"
    for r in reasoning_log:
        reasoning_html += f"""
        <div class='card' style='border-left:4px solid #7c4dff;padding:10px 14px'>
          <div class='meta'>[{r['timestamp']:.1f}s] <strong>{escape(r['phase'])}</strong></div>
          <p style='margin:4px 0'><strong>Observed:</strong> {escape(r['observation'][:200])}</p>
          <p style='margin:4px 0'><strong>Reasoning:</strong> {escape(r['reasoning'][:300])}</p>
          <p style='margin:4px 0;color:#2e7d32'><strong>Decision:</strong> {escape(r['decision'][:200])}</p>
          {"<div class='meta'>Evidence: " + escape(r.get('evidence','')[:200]) + "</div>" if r.get('evidence') else ""}
        </div>"""

    # Timing section
    timing_html = "<table class='timing-table'><tr><th>Operation</th><th>Count</th><th>Total</th><th>Avg</th></tr>"
    for op, stats in sorted(timing.items()):
        timing_html += f"<tr><td>{escape(op)}</td><td>{stats['count']}</td><td>{stats['total_s']:.2f}s</td><td>{stats['avg_s']:.2f}s</td></tr>"
    timing_html += "</table>"

    # Action log
    log_lines = []
    for e in action_log:
        dur = f" ({e['duration_s']:.2f}s)" if e.get("duration_s") else ""
        log_lines.append(f"[{e['elapsed_s']:6.1f}s] {e['action']}{dur}: {e['detail']}")
    log_text = "\n".join(log_lines)

    # Screenshots
    ss_html = ""
    for sp in screenshots:
        if Path(sp).exists():
            rel = str(Path(sp).relative_to(OUTPUT_DIR)) if str(sp).startswith(str(OUTPUT_DIR)) else sp
            ss_html += f"<img src='{escape(rel)}' class='screenshot'><br>"

    # Session recording
    session_html = ""
    if session_gif and Path(session_gif).exists():
        rel = str(Path(session_gif).relative_to(OUTPUT_DIR)) if str(session_gif).startswith(str(OUTPUT_DIR)) else session_gif
        session_html = f"""
        <h2>Session Recording</h2>
        <div class='card'>
          <img src='{escape(rel)}' style='max-width:100%;border-radius:6px'>
          <p class='meta'>{recording_stats.get('frames',0)} frames over {recording_stats.get('duration_s',0)}s</p>
        </div>"""

    html = f"""<!DOCTYPE html><html><head><meta charset='utf-8'>
<title>Task Report: {escape(task)}</title>
<style>
body{{font-family:-apple-system,sans-serif;max-width:1200px;margin:0 auto;padding:20px;line-height:1.6;color:#333;background:#fafafa}}
h1{{color:#ff2442}}h2{{color:#333;border-bottom:2px solid #ff2442;padding-bottom:5px;margin-top:30px}}
.card{{background:#fff;border:1px solid #eee;border-radius:8px;padding:16px;margin:12px 0;box-shadow:0 1px 3px rgba(0,0,0,0.05)}}
.meta{{color:#888;font-size:13px}}
.tag{{background:#fff0f0;color:#ff2442;padding:2px 8px;border-radius:12px;font-size:12px;margin:2px;display:inline-block}}
img.screenshot{{max-width:100%;max-height:500px;border:1px solid #ddd;border-radius:6px;margin:8px 0}}
img.note-img{{max-width:300px;max-height:400px;border:1px solid #ddd;border-radius:6px;margin:4px}}
.img-grid{{display:flex;flex-wrap:wrap;gap:12px;margin:12px 0}}
.img-item{{background:#fff;border:1px solid #eee;border-radius:8px;padding:12px;max-width:320px}}
.img-item img{{max-width:100%;border-radius:4px}}
.ocr{{background:#fffde7;border:1px solid #ffd54f;border-radius:6px;padding:8px;margin:6px 0;font-size:12px;white-space:pre-wrap;max-height:200px;overflow-y:auto}}
.vision{{background:#e3f2fd;border:1px solid #90caf9;border-radius:6px;padding:8px;margin:6px 0;font-size:12px}}
.timing-table{{border-collapse:collapse;width:100%;font-size:13px}}
.timing-table th,.timing-table td{{border:1px solid #ddd;padding:6px 10px;text-align:left}}
.timing-table th{{background:#f5f5f5}}
.summary{{background:#e8f5e9;padding:16px;border-radius:8px;margin:12px 0;font-size:14px}}
.warn{{color:#e65100;font-weight:bold}}
.ok{{color:#2e7d32;font-weight:bold}}
pre.log{{background:#263238;color:#eee;padding:16px;border-radius:8px;font-size:11px;overflow-x:auto;max-height:400px;overflow-y:auto}}
</style></head><body>
<h1>Task Report</h1>
<p class='meta'>Generated: {now} | Total: {total_time:.1f}s</p>
<div class='summary'>
  <strong>Task:</strong> {escape(task)}<br>
  <strong>Note:</strong> {escape(note.get('title',''))}<br>
  {"<p><a href='" + escape(note_url) + "' target='_blank'>Open note in browser</a></p>" if note_url else ""}
  <strong>Type:</strong> {escape(note.get('type',''))} |
  <strong>Author:</strong> {escape(note.get('author',''))}<br>
  <strong>Likes:</strong> {escape(note.get('likes',''))} |
  <strong>Favorites:</strong> {escape(note.get('favorites',''))} |
  <strong>Comments:</strong> {escape(note.get('comments_count',''))}<br>
  <strong>Completeness:</strong> content={'note.get("has_content",False)'} media={'note.get("has_media",False)'}<br>
  {"<p class='ok'>Video downloaded: " + escape(saved_video_path) + "</p>" if saved_video_path else "<p class='warn'>Video not downloaded</p>"}
</div>

<h2>Screenshots</h2>
<div class='card'>{ss_html}</div>

{session_html}

<h2>Note Content</h2>
<div class='card'>
  <p>{escape(note.get('content','')[:2000])}</p>
  <p>{''.join("<span class='tag'>" + escape(h) + "</span>" for h in note.get('hashtags',[]))}</p>
</div>

{images_html}
{video_html}
{comments_html}

{reasoning_html}

<h2>Timing</h2>
<div class='card'>{timing_html}</div>

<h2>Execution Log</h2>
<pre class='log'>{escape(log_text)}</pre>

</body></html>"""
    return html


# ── Video saver ────────────────────────────────────────────────

def save_video_locally(note: dict, output_dir: Path) -> str:
    """Copy downloaded video to output directory. Returns path or empty string."""
    dl_path = note.get("video_download_path", "")
    if not dl_path or not Path(dl_path).exists():
        return ""
    ext = Path(dl_path).suffix or ".mp4"
    dest = output_dir / "video" / f"video{ext}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(dl_path, dest)
    return str(dest)


def save_video_frames_locally(note: dict, output_dir: Path) -> list[str]:
    """Copy extracted frames to output directory."""
    paths = []
    for i, fp in enumerate(note.get("video_frame_paths", [])):
        if fp and Path(fp).exists():
            dest = output_dir / "video_frames" / f"frame_{i:02d}.jpg"
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(fp, dest)
            paths.append(str(dest))
    return paths


# ── Main Task ──────────────────────────────────────────────────

async def run_task():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

    from clawvision.agent.bridge import ExtensionBridge
    from clawvision.agent.media import MediaProcessor
    from clawvision.agent.recorder import SessionRecorder
    from clawvision.agent.xhs.browser import XHSBrowser
    from clawvision.agent.xhs.entities import NoteEntity, NoteCard
    from clawvision.agent.xhs.processor import NoteProcessor, ProcessorConfig

    # Setup
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "screenshots").mkdir(exist_ok=True)
    t0 = time.time()
    action = ActionLog()
    reasoning = ReasoningLog()
    screenshots = []

    print("=" * 60)
    print(f"  Task: {TASK}")
    print("=" * 60)

    # Connect
    bridge = ExtensionBridge(port=8765)
    browser = XHSBrowser(bridge)
    await bridge.start()
    action.log("bridge_started", "WebSocket server listening")

    print("\n  >>> Click 'Connect' in the Chrome Extension popup <<<\n")
    await bridge.wait_for_connection(timeout=60)
    action.log("connected", "Extension connected")

    # Start session recording
    recorder = SessionRecorder(bridge, interval=RECORD_INTERVAL)
    await recorder.start()
    action.log("recording_started", f"Capturing every {RECORD_INTERVAL}s")

    # Open background window
    bg_window_id = None
    try:
        win_info = await bridge.create_background_window(
            url="https://www.xiaohongshu.com",
        )
        bg_window_id = win_info.get("windowId")
        action.log("background_window", f"Opened (id={bg_window_id})")
        reasoning.think(
            "setup", "Background window created successfully",
            "Using background window to avoid stealing user's focus",
            "Proceed with search in background window",
        )
    except Exception as e:
        action.log("background_window_fallback", f"Failed ({e}), using current tab")
        reasoning.think(
            "setup", f"Background window failed: {e}",
            "Extension may have stale code; fall back to current tab",
            "Use current tab for browsing",
        )
        tab = await bridge.get_tab_info()
        if "xiaohongshu.com" not in tab.get("url", ""):
            await browser.navigate("https://www.xiaohongshu.com")
    await asyncio.sleep(5)

    # --- Search phase ---
    found_note = None
    used_keyword = ""

    for keyword in SEARCH_KEYWORDS:
        reasoning.think(
            "search",
            f"Trying keyword: '{keyword}'",
            f"Searching for bloc1 climbing gym's V2 route compilation. "
            f"This is keyword {SEARCH_KEYWORDS.index(keyword)+1}/{len(SEARCH_KEYWORDS)}.",
            f"Navigate to XHS search with keyword '{keyword}'",
        )

        action.log("search_start", keyword)
        await browser.navigate_to_search(keyword)
        await asyncio.sleep(3)

        # Screenshot search results
        sp = await bridge.save_screenshot(OUTPUT_DIR / "screenshots" / f"search_{keyword[:10]}.png")
        screenshots.append(str(sp))
        action.log("search_screenshot", str(sp))

        # Wait for results
        state = await browser.wait_for_search_results(timeout_s=15)
        cards_raw = await browser.extract_search_cards()
        action.log("search_results", f"{len(cards_raw)} cards found for '{keyword}'")

        if not cards_raw:
            reasoning.think(
                "search",
                f"No results for '{keyword}'",
                "Try next keyword",
                "Skip to next search term",
            )
            continue

        # Filter for video posts about bloc1 V2
        cards = [NoteCard.from_dom_dict(c) for c in cards_raw]

        # Print cards for visibility
        for i, c in enumerate(cards[:8]):
            print(f"    {i}: [{c.note_type.value}] {c.title} | {c.likes}")

        # Look for the best match
        best_idx = -1
        best_score = 0
        best_reason = ""
        for i, card in enumerate(cards):
            title_lower = card.title.lower()
            is_video = card.note_type.value == "video"
            has_bloc1 = "bloc1" in title_lower or "bloc 1" in title_lower
            has_v2 = "v2" in title_lower
            has_route = "线路" in title_lower or "路线" in title_lower

            # Scoring
            score = 0
            reasons = []
            if has_bloc1:
                score += 3
                reasons.append("has bloc1")
            if has_v2:
                score += 2
                reasons.append("has v2")
            if has_route:
                score += 1
                reasons.append("has 线路")
            if is_video:
                score += 1
                reasons.append("is video")
            if "合集" in title_lower:
                score += 1
                reasons.append("has 合集")

            if score >= 3 and (best_idx < 0 or score > best_score):
                best_idx = i
                best_score = score
                best_reason = ", ".join(reasons)

        if best_idx >= 0:
            found_note = cards[best_idx]
            used_keyword = keyword
            reasoning.think(
                "select_note",
                f"Card {best_idx}: '{found_note.title}' ({found_note.note_type.value})",
                f"Best match (score={best_score}): {best_reason}",
                f"Open card {best_idx} for full extraction",
                evidence=json.dumps({"title": found_note.title, "type": found_note.note_type.value, "likes": found_note.likes}),
            )
            break
        else:
            # If no strong match, pick the most relevant video
            video_cards = [c for c in cards if c.note_type.value == "video"]
            if video_cards:
                found_note = video_cards[0]
                used_keyword = keyword
                best_idx = cards.index(found_note)
                reasoning.think(
                    "select_note",
                    f"No strong match, but found {len(video_cards)} video cards",
                    "Pick first video card as best available option",
                    f"Open video card: '{found_note.title}'",
                )
                break

            reasoning.think(
                "search",
                f"No video cards found for '{keyword}' ({len(cards)} total cards)",
                "Try next keyword for better results",
                "Continue to next search term",
            )

    if not found_note:
        action.log("no_match", "Could not find matching note across all keywords")
        reasoning.think(
            "abort",
            "All keywords exhausted, no matching video found",
            "Task cannot proceed without a target note",
            "Generate report with search-only results",
        )
        # Save what we have
        await recorder.stop()
        gif_path = str(OUTPUT_DIR / "session.gif")
        recorder.save_gif(gif_path)

        report_html = generate_html_report(
            task=TASK,
            note={"title": "NOT FOUND"},
            timing={},
            action_log=action.entries,
            reasoning_log=reasoning.to_dicts(),
            screenshots=screenshots,
            saved_video_path="",
            saved_frames=[],
            session_gif=gif_path,
            recording_stats=recorder.summary(),
            total_time=time.time() - t0,
        )
        (OUTPUT_DIR / "report.html").write_text(report_html)
        print(f"\n  Report: {OUTPUT_DIR / 'report.html'}")
        await bridge.stop()
        return

    # --- Open note ---
    action.log("open_note", f"Clicking card {best_idx}: {found_note.title}")
    await browser.click_card(best_idx)
    await asyncio.sleep(3)

    # Detect state
    state = await browser.detect_state()
    action.log("state", str(state.get("state", "unknown")))

    # Screenshot note detail
    sp = await bridge.save_screenshot(OUTPUT_DIR / "screenshots" / "note_detail.png")
    screenshots.append(str(sp))

    # Get note URL
    note_url = ""
    try:
        tab = await bridge.get_tab_info()
        note_url = tab.get("url", "")
    except Exception:
        pass

    # Extract note content from DOM
    raw = await browser.extract_note_content()
    note_entity = NoteEntity.from_dom_dict(raw)
    note_entity.source_keyword = used_keyword
    note_entity.url = note_url

    action.log("dom_extract", f"title='{note_entity.title}', type={note_entity.note_type.value}, images={note_entity.image_count}")

    reasoning.think(
        "note_opened",
        f"Note opened: '{note_entity.title}' by {note_entity.author_name}. "
        f"Type: {note_entity.note_type.value}. Likes: {note_entity.likes}",
        f"{'Video note — will process video (download, transcribe, frame analysis)' if note_entity.note_type.value == 'video' else 'Image note — will process images (OCR, Vision)'}",
        "Proceed to NoteProcessor.process_note() for media enrichment",
    )

    # --- Process media (images/video/OCR/vision/transcription) ---
    media = MediaProcessor()
    config = ProcessorConfig(
        use_ocr=True,
        use_vision=True,
        use_whisper=True,
        cache_video_locally=True,  # Download video to local file
        max_transcription_seconds=120,
        transcription_timeout_s=300,
    )
    processor = NoteProcessor(browser, media, config)

    action.log("process_start", f"NoteProcessor starting for {note_entity.note_type.value} note")
    t_proc = time.time()
    await processor.process_note(note_entity)
    proc_time = time.time() - t_proc
    action.log("process_done", f"Media processing complete in {proc_time:.1f}s")

    note_entity.refresh_derived_fields()

    reasoning.think(
        "media_processed",
        f"Processing complete. "
        + (f"Video: transcript={len(note_entity.video.transcript) if note_entity.video else 0} chars, "
           f"frames={len(note_entity.video.frame_descriptions) if note_entity.video else 0}, "
           f"download={'YES' if note_entity.video and note_entity.video.download_path else 'NO'}"
           if note_entity.video else
           f"Images: {sum(1 for i in note_entity.images if i.vision_description)}/{len(note_entity.images)} vision"),
        "Check completeness and collect comments",
        "Proceed to comments extraction",
        evidence=json.dumps(note_entity.completeness),
    )

    # --- Comments ---
    action.log("comments_start", "Extracting comments")
    t_comm = time.time()
    raw_comments = await browser.extract_comments(max_comments=20, prefer_hot=True)

    from clawvision.agent.xhs.entities import Comment
    comments = Comment.merge_many([Comment.from_dom_dict(c) for c in raw_comments])
    note_entity.comments = comments
    comm_time = time.time() - t_comm
    action.log("comments_done", f"{len(comments)} comments ({comm_time:.1f}s)")

    # Scroll for more comments
    if len(comments) < 10:
        await browser.scroll_note(600)
        await asyncio.sleep(1.5)
        raw2 = await browser.extract_comments(max_comments=30, prefer_hot=True)
        comments2 = Comment.merge_many([Comment.from_dom_dict(c) for c in raw2])
        note_entity.comments = NoteEntity.merge_comments([*comments, *comments2])
        action.log("comments_scroll", f"After scroll: {len(note_entity.comments)} total")

    # Screenshot comments
    sp = await bridge.save_screenshot(OUTPUT_DIR / "screenshots" / "comments_panel.png")
    screenshots.append(str(sp))

    # --- Stop recording ---
    await recorder.stop()
    action.log("recording_stopped", f"{recorder.frame_count} frames captured")

    # --- Save outputs ---
    note_dict = note_entity.to_report_dict()

    # Save video locally
    saved_video = save_video_locally(note_dict, OUTPUT_DIR)
    if saved_video:
        action.log("video_saved", saved_video)
    else:
        action.log("video_not_saved", note_dict.get("video_download_error", "no download path"))

    # Save video frames
    saved_frames = save_video_frames_locally(note_dict, OUTPUT_DIR)
    if saved_frames:
        action.log("frames_saved", f"{len(saved_frames)} frames")

    # Save session GIF
    gif_path = str(OUTPUT_DIR / "session.gif")
    recorder.save_gif(gif_path, fps=1.0, max_width=800)
    action.log("session_gif", gif_path)

    # Timing from processor
    timing = processor.timing.summary() if hasattr(processor, 'timing') else {}

    # Add comments timing
    timing["comments_extract"] = {"count": 1, "total_s": round(comm_time, 2), "avg_s": round(comm_time, 2)}

    total_time = time.time() - t0

    # Generate HTML report
    report_html = generate_html_report(
        task=TASK,
        note=note_dict,
        timing=timing,
        action_log=action.entries,
        reasoning_log=reasoning.to_dicts(),
        screenshots=screenshots,
        saved_video_path=saved_video,
        saved_frames=saved_frames,
        session_gif=gif_path,
        recording_stats=recorder.summary(),
        total_time=total_time,
        note_url=note_url,
    )
    (OUTPUT_DIR / "report.html").write_text(report_html)

    # Save JSON
    results = {
        "task": TASK,
        "keyword_used": used_keyword,
        "note_url": note_url,
        "total_time_s": round(total_time, 1),
        "timing": timing,
        "completeness": note_entity.completeness,
        "note": note_dict,
        "reasoning": reasoning.to_dicts(),
        "action_log": action.entries,
        "screenshots": screenshots,
        "saved_video": saved_video,
        "saved_frames": saved_frames,
        "session_gif": gif_path,
        "recording_stats": recorder.summary(),
    }
    (OUTPUT_DIR / "test_results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2)
    )

    # Cleanup background window
    if bg_window_id:
        try:
            await bridge.send_command("close_window", {"windowId": bg_window_id})
            action.log("bg_window_closed", f"id={bg_window_id}")
        except Exception:
            pass

    await bridge.stop()

    # --- Summary ---
    print(f"\n{'='*60}")
    print(f"  TASK COMPLETE — {total_time:.1f}s total")
    print(f"  Note: {note_entity.title}")
    print(f"  Type: {note_entity.note_type.value}")
    if note_entity.video:
        print(f"  Video: {'DOWNLOADED' if saved_video else 'NOT DOWNLOADED'}")
        print(f"  Transcript: {len(note_entity.video.transcript)} chars")
        print(f"  Frames: {len(saved_frames)}")
    print(f"  Comments: {len(note_entity.comments)}")
    print(f"  Session recording: {recorder.frame_count} frames")
    print(f"  Report: {OUTPUT_DIR / 'report.html'}")
    print(f"{'='*60}")


if __name__ == "__main__":
    asyncio.run(run_task())
