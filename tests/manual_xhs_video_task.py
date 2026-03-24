"""Manual task test: find and download a specific XHS video.

Uses the generic TaskAgent for ALL decision-making (LLM-driven):
  - Task understanding: LLM parses the user's intent
  - Search strategy: LLM generates keywords
  - Candidate evaluation: LLM ranks and picks search results
  - Note verification: LLM confirms the note matches before downloading
  - Completion check: LLM decides if the task is done or needs refinement

The agent does NOT hardcode any task-specific logic. It reasons through
each decision via Claude API calls, making it generic across all tasks.
"""

from __future__ import annotations

import asyncio
import base64
import json
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime
from html import escape
from pathlib import Path

# ── Configuration ──────────────────────────────────────────────

OUTPUT_DIR = Path("task_video_output")
RECORD_INTERVAL = 1.0  # denser recording for smoother GIF
MAX_KEYWORDS_TO_TRY = 4
MAX_NOTES_TO_CHECK = 3  # open up to N candidates if first isn't confident


# ── Reasoning Logger ───────────────────────────────────────────

class ReasoningLog:
    """Captures agent thinking/decisions for auditability."""

    def __init__(self):
        self._entries: list[dict] = []
        self._t0 = time.time()

    def think(self, phase: str, observation: str, reasoning: str, decision: str, evidence: str = ""):
        entry = {
            "timestamp": round(time.time() - self._t0, 1),
            "phase": phase,
            "observation": observation,
            "reasoning": reasoning,
            "decision": decision,
            "evidence": evidence[:500],
        }
        self._entries.append(entry)
        print(f"  [{entry['timestamp']:6.1f}s] [{phase}]")
        print(f"     Observed: {observation[:150]}")
        print(f"     Reasoning: {reasoning[:150]}")
        print(f"     Decision: {decision[:150]}")

    @property
    def entries(self) -> list[dict]:
        return self._entries


class ActionLog:
    def __init__(self):
        self._entries: list[dict] = []
        self._t0 = time.time()

    def log(self, action: str, detail: str = "", duration: float | None = None):
        elapsed = round(time.time() - self._t0, 1)
        suffix = f" ({duration:.2f}s)" if duration else ""
        print(f"  [{elapsed:6.1f}s] {action}{suffix}: {detail[:150]}")
        self._entries.append({
            "elapsed_s": elapsed, "action": action,
            "detail": detail[:300],
            "duration_s": round(duration, 2) if duration else None,
        })

    @property
    def entries(self) -> list[dict]:
        return self._entries


# ── HTML Report Generator ──────────────────────────────────────

def generate_html_report(
    *, task, note, timing, action_log, reasoning_log,
    screenshots, saved_video_path, saved_frames, session_gif,
    recording_stats, total_time, note_url="", task_understanding=None,
    candidate_evaluations=None, verification=None,
):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Task understanding section
    understanding_html = ""
    if task_understanding:
        understanding_html = f"""
        <h2>Task Understanding (LLM)</h2>
        <div class='card' style='border-left:4px solid #1565c0'>
          <p><strong>Goal:</strong> {escape(task_understanding.get('goal',''))}</p>
          <p><strong>Target type:</strong> {escape(task_understanding.get('target_type',''))}</p>
          <p><strong>Search criteria:</strong></p>
          <pre class='log' style='background:#e3f2fd;color:#333;max-height:200px'>{escape(json.dumps(task_understanding.get('search_criteria',{}), ensure_ascii=False, indent=2))}</pre>
          <p><strong>Keywords:</strong> {', '.join(escape(k) for k in task_understanding.get('search_keywords',[]))}</p>
          <p><strong>Success criteria:</strong> {escape(task_understanding.get('success_criteria',''))}</p>
        </div>"""

    # Candidate evaluations section
    evals_html = ""
    if candidate_evaluations:
        evals_html = "<h2>Candidate Evaluations (LLM)</h2>"
        for ev in candidate_evaluations:
            color = "#2e7d32" if ev.get("recommendation") == "open" else "#e65100" if ev.get("recommendation") == "skip" else "#f57f17"
            evals_html += f"""
            <div class='card' style='border-left:4px solid {color}'>
              <strong>[{ev.get('index','')}] {escape(ev.get('title',''))}</strong>
              <span class='meta' style='float:right'>Score: {ev.get('relevance_score',0):.2f} | {escape(ev.get('recommendation',''))}</span>
              <p>Matches: {', '.join(escape(r) for r in ev.get('match_reasons',[]))}</p>
              {"<p class='warn'>Concerns: " + ', '.join(escape(c) for c in ev.get('concerns',[])) + "</p>" if ev.get('concerns') else ""}
            </div>"""

    # Verification section
    verify_html = ""
    if verification:
        vcolor = "#2e7d32" if verification.get("matches_task") else "#e65100"
        verify_html = f"""
        <h2>Note Verification (LLM)</h2>
        <div class='card' style='border-left:4px solid {vcolor}'>
          <p><strong>Matches task:</strong> <span style='color:{vcolor}'>{verification.get('matches_task', False)}</span>
             (confidence: {verification.get('confidence', 0):.0%})</p>
          <p><strong>Details:</strong> {escape(verification.get('match_details',''))}</p>
          {"<p class='warn'>Missing: " + ', '.join(escape(m) for m in verification.get('missing_criteria',[])) + "</p>" if verification.get('missing_criteria') else ""}
          <p><strong>Should download:</strong> {verification.get('should_download', False)}</p>
          <p style='font-size:12px;color:#555'>{escape(verification.get('reasoning','')[:400])}</p>
        </div>"""

    # Video section
    video_html = ""
    if note.get("transcript") or note.get("video_url"):
        video_html = "<h2>Video</h2><div class='card'>"
        if note.get("video_resolved_url"):
            video_html += f"<p><strong>URL:</strong> <a href='{escape(note['video_resolved_url'])}' target='_blank'>{escape(note['video_resolved_url'][:80])}</a></p>"
        if saved_video_path:
            video_html += f"<p class='ok'><strong>Downloaded:</strong> {escape(saved_video_path)}</p>"
        if note.get("video_download_error"):
            video_html += f"<p class='warn'><strong>Error:</strong> {escape(note['video_download_error'])}</p>"
        if note.get("video_visual_summary"):
            video_html += f"<h3>Visual Summary</h3><div class='vision'>{escape(note['video_visual_summary'])}</div>"
        frame_descs = note.get("video_frame_descriptions", [])
        if saved_frames:
            video_html += "<h3>Keyframes</h3><div class='img-grid'>"
            for i, fp in enumerate(saved_frames):
                desc = frame_descs[i] if i < len(frame_descs) else ""
                video_html += f"<div class='img-item'><img src='{escape(fp)}' class='note-img'><div class='vision'>{escape(desc[:200])}</div></div>"
            video_html += "</div>"
        if note.get("transcript"):
            video_html += f"<h3>Transcript</h3><div class='ocr'>{escape(note['transcript'][:4000])}</div>"
        if note.get("transcript_summary"):
            video_html += f"<h3>Summary</h3><div class='vision'>{escape(note['transcript_summary'])}</div>"
        video_html += "</div>"

    # Comments
    comments_html = ""
    for c in note.get("hot_comments", [])[:10]:
        badge = " 📌" if c.get("is_pinned") else ""
        comments_html += f"""<div class='card' style='padding:8px 12px'>
          <strong>{escape(c.get('username',''))}</strong>{badge}
          <span class='meta' style='float:right'>{escape(str(c.get('likes','')))} ❤️</span>
          <p style='margin:4px 0'>{escape(c.get('text','')[:200])}</p></div>"""
    if comments_html:
        comments_html = "<h2>Hot Comments</h2>" + comments_html

    # Reasoning log
    reasoning_html = "<h2>Agent Reasoning Log</h2>"
    for r in reasoning_log:
        reasoning_html += f"""
        <div class='card' style='border-left:4px solid #7c4dff;padding:10px 14px'>
          <div class='meta'>[{r['timestamp']:.1f}s] <strong>{escape(r['phase'])}</strong></div>
          <p style='margin:4px 0'><strong>Observed:</strong> {escape(r['observation'][:200])}</p>
          <p style='margin:4px 0'><strong>Reasoning:</strong> {escape(r['reasoning'][:300])}</p>
          <p style='margin:4px 0;color:#2e7d32'><strong>Decision:</strong> {escape(r['decision'][:200])}</p>
        </div>"""

    # Timing
    timing_rows = "".join(
        f"<tr><td>{escape(op)}</td><td>{s['count']}</td><td>{s['total_s']:.2f}s</td><td>{s['avg_s']:.2f}s</td></tr>"
        for op, s in sorted(timing.items())
    )

    # Action log
    log_text = "\n".join(
        f"[{e['elapsed_s']:6.1f}s] {e['action']}" + (f" ({e['duration_s']:.2f}s)" if e.get('duration_s') else "") + f": {e['detail']}"
        for e in action_log
    )

    # Screenshots
    ss_html = ""
    for sp in screenshots:
        if Path(sp).exists():
            rel = str(Path(sp).relative_to(OUTPUT_DIR)) if str(sp).startswith(str(OUTPUT_DIR)) else sp
            ss_html += f"<img src='{escape(rel)}' class='screenshot'><br>"

    # Session GIF
    session_html = ""
    if session_gif and Path(session_gif).exists():
        rel = str(Path(session_gif).relative_to(OUTPUT_DIR)) if str(session_gif).startswith(str(OUTPUT_DIR)) else session_gif
        session_html = f"""<h2>Session Recording</h2><div class='card'>
          <img src='{escape(rel)}' style='max-width:100%;border-radius:6px'>
          <p class='meta'>{recording_stats.get('frames',0)} frames over {recording_stats.get('duration_s',0)}s</p></div>"""

    return f"""<!DOCTYPE html><html><head><meta charset='utf-8'>
<title>Task Report: {escape(task)}</title>
<style>
body{{font-family:-apple-system,sans-serif;max-width:1200px;margin:0 auto;padding:20px;line-height:1.6;color:#333;background:#fafafa}}
h1{{color:#ff2442}}h2{{color:#333;border-bottom:2px solid #ff2442;padding-bottom:5px;margin-top:30px}}
.card{{background:#fff;border:1px solid #eee;border-radius:8px;padding:16px;margin:12px 0;box-shadow:0 1px 3px rgba(0,0,0,0.05)}}
.meta{{color:#888;font-size:13px}}
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
.warn{{color:#e65100;font-weight:bold}}.ok{{color:#2e7d32;font-weight:bold}}
pre.log{{background:#263238;color:#eee;padding:16px;border-radius:8px;font-size:11px;overflow-x:auto;max-height:400px;overflow-y:auto}}
</style></head><body>
<h1>Task Report</h1>
<p class='meta'>Generated: {now} | Total: {total_time:.1f}s</p>
<div class='summary'>
  <strong>Task:</strong> {escape(task)}<br>
  <strong>Note:</strong> {escape(note.get('title',''))}<br>
  {"<p><a href='" + escape(note_url) + "' target='_blank'>Open note</a></p>" if note_url else ""}
  <strong>Type:</strong> {escape(note.get('type',''))} | <strong>Author:</strong> {escape(note.get('author',''))}<br>
  <strong>Likes:</strong> {escape(note.get('likes',''))} | <strong>Favorites:</strong> {escape(note.get('favorites',''))}<br>
  {"<p class='ok'>Video downloaded: " + escape(saved_video_path) + "</p>" if saved_video_path else "<p class='warn'>Video not downloaded</p>"}
</div>

{understanding_html}
{evals_html}
{verify_html}

<h2>Screenshots</h2><div class='card'>{ss_html}</div>
{session_html}
<h2>Note Content</h2><div class='card'><p>{escape(note.get('content','')[:2000])}</p></div>
{video_html}
{comments_html}
{reasoning_html}
<h2>Timing</h2><div class='card'><table class='timing-table'><tr><th>Operation</th><th>Count</th><th>Total</th><th>Avg</th></tr>{timing_rows}</table></div>
<h2>Execution Log</h2><pre class='log'>{escape(log_text)}</pre>
</body></html>"""


def save_video_locally(note: dict, output_dir: Path) -> str:
    dl_path = note.get("video_download_path", "")
    if not dl_path or not Path(dl_path).exists():
        return ""
    ext = Path(dl_path).suffix or ".mp4"
    dest = output_dir / "video" / f"video{ext}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(dl_path, dest)
    return str(dest)


def save_video_frames_locally(note: dict, output_dir: Path) -> list[str]:
    paths = []
    for i, fp in enumerate(note.get("video_frame_paths", [])):
        if fp and Path(fp).exists():
            dest = output_dir / "video_frames" / f"frame_{i:02d}.jpg"
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(fp, dest)
            paths.append(str(dest))
    return paths


# ── Main Task (generic, LLM-driven) ───────────────────────────

async def run_task(task: str):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

    from clawvision.agent.bridge import ExtensionBridge
    from clawvision.agent.media import MediaProcessor
    from clawvision.agent.recorder import SessionRecorder
    from clawvision.agent.task_agent import TaskAgent
    from clawvision.agent.xhs.browser import XHSBrowser
    from clawvision.agent.xhs.entities import Comment, NoteEntity
    from clawvision.agent.xhs.processor import NoteProcessor, ProcessorConfig

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "screenshots").mkdir(exist_ok=True)
    t0 = time.time()
    action = ActionLog()
    reasoning = ReasoningLog()
    screenshots = []

    print("=" * 60)
    print(f"  Task: {task}")
    print("=" * 60)

    # ── Step 1: LLM understands the task ──
    media = MediaProcessor()
    agent = TaskAgent(media)

    action.log("llm_understand", "Asking LLM to understand the task...")
    t_u = time.time()
    understanding = agent.understand_task(task)
    action.log("llm_understand_done", f"Goal: {understanding.goal}", time.time() - t_u)

    reasoning.think(
        "task_understanding",
        f"LLM parsed the task into structured criteria",
        f"Goal: {understanding.goal}. "
        f"Criteria: {json.dumps(understanding.search_criteria, ensure_ascii=False)[:200]}",
        f"Will search with keywords: {understanding.search_keywords}",
        evidence=understanding.raw_reasoning[:300],
    )

    print(f"\n  Goal: {understanding.goal}")
    print(f"  Type: {understanding.target_type}")
    print(f"  Criteria: {json.dumps(understanding.search_criteria, ensure_ascii=False)}")
    print(f"  Keywords: {understanding.search_keywords}")
    print(f"  Success: {understanding.success_criteria}\n")

    # ── Step 2: Connect to browser ──
    bridge = ExtensionBridge(port=8765)
    browser = XHSBrowser(bridge)
    await bridge.start()
    action.log("bridge_started", "WebSocket server listening")
    print("\n  >>> Click 'Connect' in the Chrome Extension popup <<<\n")
    await bridge.wait_for_connection(timeout=60)
    action.log("connected", "Extension connected")

    # Start session recording (1s interval for dense GIF)
    recorder = SessionRecorder(bridge, interval=RECORD_INTERVAL)
    await recorder.start()
    action.log("recording_started", f"Capturing every {RECORD_INTERVAL}s")

    # Background window
    bg_window_id = None
    try:
        win_info = await bridge.create_background_window(url="https://www.xiaohongshu.com")
        bg_window_id = win_info.get("windowId")
        action.log("background_window", f"Opened (id={bg_window_id})")
    except Exception as e:
        action.log("background_window_fallback", str(e))
        tab = await bridge.get_tab_info()
        if "xiaohongshu.com" not in tab.get("url", ""):
            await browser.navigate("https://www.xiaohongshu.com")
    await asyncio.sleep(5)

    # ── Step 3: Search and evaluate candidates (LLM-driven) ──
    best_candidate = None
    best_card_index = -1
    all_evaluations = []
    keywords = understanding.search_keywords[:MAX_KEYWORDS_TO_TRY]

    for kw_idx, keyword in enumerate(keywords):
        reasoning.think(
            "search",
            f"Keyword {kw_idx+1}/{len(keywords)}: '{keyword}'",
            "LLM-generated search keyword, navigating to XHS search",
            f"Search XHS for '{keyword}'",
        )
        action.log("search_start", keyword)
        await browser.navigate_to_search(keyword)
        await asyncio.sleep(3)

        sp = await bridge.save_screenshot(OUTPUT_DIR / "screenshots" / f"search_{kw_idx}.png")
        screenshots.append(str(sp))

        await browser.wait_for_search_results(timeout_s=15)
        cards_raw = await browser.extract_search_cards()
        action.log("search_results", f"{len(cards_raw)} cards for '{keyword}'")

        if not cards_raw:
            reasoning.think("search", f"No results for '{keyword}'", "Empty results", "Try next keyword")
            continue

        # Print cards
        for i, c in enumerate(cards_raw[:10]):
            print(f"    {i}: [{c.get('type','?')}] {c.get('title','')} | {c.get('likes','')}")

        # ── LLM evaluates candidates ──
        action.log("llm_evaluate", f"LLM evaluating {len(cards_raw)} candidates...")
        t_e = time.time()
        evaluations = agent.evaluate_candidates(cards_raw, understanding, keyword)
        action.log("llm_evaluate_done", f"{len(evaluations)} candidates scored", time.time() - t_e)
        all_evaluations.extend([{
            **{"keyword": keyword},
            "index": e.index, "title": e.title,
            "relevance_score": e.relevance_score,
            "match_reasons": e.match_reasons,
            "concerns": e.concerns,
            "recommendation": e.recommendation,
        } for e in evaluations])

        for e in evaluations:
            print(f"    -> [{e.index}] score={e.relevance_score:.2f} rec={e.recommendation} | {e.title}")
            if e.concerns:
                print(f"       Concerns: {e.concerns}")

        # Pick the best "open" candidate
        open_candidates = [e for e in evaluations if e.recommendation == "open"]
        if open_candidates:
            best_candidate = open_candidates[0]
            best_card_index = best_candidate.index
            reasoning.think(
                "select_candidate",
                f"LLM picked [{best_candidate.index}] '{best_candidate.title}' "
                f"(score={best_candidate.relevance_score:.2f})",
                f"Reasons: {', '.join(best_candidate.match_reasons)}",
                f"Open card {best_candidate.index} for verification",
                evidence=json.dumps(best_candidate.concerns) if best_candidate.concerns else "",
            )
            break

        # Collect "maybe" candidates but DON'T stop — try more keywords first
        maybe_candidates = [e for e in evaluations if e.recommendation == "maybe"]
        if maybe_candidates and not best_candidate:
            best_candidate = maybe_candidates[0]
            best_card_index = best_candidate.index
            reasoning.think(
                "no_strong_match",
                f"Best 'maybe': [{best_candidate.index}] '{best_candidate.title}' (score={best_candidate.relevance_score:.2f})",
                f"No strong match yet — will try remaining keywords before settling",
                f"Continue to keyword {kw_idx+2}" if kw_idx+1 < len(keywords) else "No more keywords, will use this maybe",
            )
            # Only break if this is the last keyword
            if kw_idx + 1 >= len(keywords):
                break
            # Otherwise continue searching — a better keyword might find the exact match

    if not best_candidate or best_card_index < 0:
        reasoning.think("abort", "No viable candidates across all keywords", "Task cannot proceed", "Generate failure report")
        await recorder.stop()
        gif_path = str(OUTPUT_DIR / "session.gif")
        recorder.save_gif(gif_path, fps=2.0)
        report = generate_html_report(
            task=task, note={"title": "NOT FOUND"}, timing={},
            action_log=action.entries, reasoning_log=reasoning.entries,
            screenshots=screenshots, saved_video_path="", saved_frames=[],
            session_gif=gif_path, recording_stats=recorder.summary(),
            total_time=time.time()-t0, task_understanding=understanding.__dict__,
            candidate_evaluations=all_evaluations,
        )
        (OUTPUT_DIR / "report.html").write_text(report)
        print(f"\n  Report: {OUTPUT_DIR / 'report.html'}")
        await bridge.stop()
        return

    # ── Step 4: Open note and verify with LLM ──
    action.log("open_note", f"Clicking card {best_card_index}: {best_candidate.title}")
    await browser.click_card(best_card_index)
    await asyncio.sleep(3)

    state = await browser.detect_state()
    action.log("state", str(state.get("state", "unknown")))

    sp = await bridge.save_screenshot(OUTPUT_DIR / "screenshots" / "note_detail.png")
    screenshots.append(str(sp))

    # Get note URL and content
    note_url = ""
    try:
        tab = await bridge.get_tab_info()
        note_url = tab.get("url", "")
    except Exception:
        pass

    raw = await browser.extract_note_content()
    note_entity = NoteEntity.from_dom_dict(raw)
    note_entity.url = note_url

    action.log("dom_extract", f"title='{note_entity.title}', type={note_entity.note_type.value}")

    # ── LLM verifies the note matches the task ──
    action.log("llm_verify", "LLM verifying note matches task...")
    t_v = time.time()

    # Get screenshot for visual verification
    screenshot_data = await bridge.capture_screenshot()
    screenshot_b64 = screenshot_data.split(",", 1)[1] if "," in screenshot_data else None

    verification = agent.verify_note(raw, understanding, screenshot_b64)
    action.log("llm_verify_done", f"matches={verification.matches_task}, confidence={verification.confidence:.0%}", time.time() - t_v)

    reasoning.think(
        "verify_note",
        f"LLM verification: matches={verification.matches_task}, confidence={verification.confidence:.0%}",
        verification.match_details[:200],
        f"{'Proceed to download' if verification.should_download else 'Should try alternatives' if verification.should_try_alternatives else 'Skip'}",
        evidence=verification.reasoning[:300],
    )

    # If verification fails, close note and try alternative keywords/candidates
    if not verification.should_download and verification.should_try_alternatives:
        reasoning.think(
            "verification_failed",
            f"Note didn't pass verification (confidence={verification.confidence:.0%})",
            f"Missing: {verification.missing_criteria}. Will close this note and try remaining keywords.",
            "Close note, search with next keyword",
        )
        await browser.close_note()
        await asyncio.sleep(1)

        # Try remaining keywords
        remaining_keywords = [k for k in keywords if k != keywords[0]]  # Skip already-used first keyword
        found_match = False
        for retry_kw in remaining_keywords:
            reasoning.think(
                "retry_search",
                f"Trying alternative keyword: '{retry_kw}'",
                "Previous result rejected by verification, searching with more specific terms",
                f"Search XHS for '{retry_kw}'",
            )
            action.log("retry_search", retry_kw)
            await browser.navigate_to_search(retry_kw)
            await asyncio.sleep(3)

            sp = await bridge.save_screenshot(OUTPUT_DIR / "screenshots" / f"retry_{retry_kw[:10]}.png")
            screenshots.append(str(sp))

            await browser.wait_for_search_results(timeout_s=15)
            cards_raw = await browser.extract_search_cards()
            action.log("retry_results", f"{len(cards_raw)} cards for '{retry_kw}'")

            if not cards_raw:
                continue

            for i, c in enumerate(cards_raw[:8]):
                print(f"    {i}: [{c.get('type','?')}] {c.get('title','')} | {c.get('likes','')}")

            # LLM evaluates
            evaluations = agent.evaluate_candidates(cards_raw, understanding, retry_kw)
            all_evaluations.extend([{
                "keyword": retry_kw, "index": e.index, "title": e.title,
                "relevance_score": e.relevance_score,
                "match_reasons": e.match_reasons, "concerns": e.concerns,
                "recommendation": e.recommendation,
            } for e in evaluations])

            open_candidates = [e for e in evaluations if e.recommendation == "open"]
            if not open_candidates:
                continue

            best_retry = open_candidates[0]
            reasoning.think(
                "retry_select",
                f"Found strong match: [{best_retry.index}] '{best_retry.title}' (score={best_retry.relevance_score:.2f})",
                f"Reasons: {', '.join(best_retry.match_reasons)}",
                f"Open card {best_retry.index}",
            )

            await browser.click_card(best_retry.index)
            await asyncio.sleep(3)

            sp = await bridge.save_screenshot(OUTPUT_DIR / "screenshots" / "note_detail.png")
            screenshots.append(str(sp))

            try:
                tab = await bridge.get_tab_info()
                note_url = tab.get("url", "")
            except Exception:
                pass

            raw = await browser.extract_note_content()
            note_entity = NoteEntity.from_dom_dict(raw)
            note_entity.url = note_url

            # Re-verify
            screenshot_data = await bridge.capture_screenshot()
            screenshot_b64 = screenshot_data.split(",", 1)[1] if "," in screenshot_data else None
            verification = agent.verify_note(raw, understanding, screenshot_b64)

            reasoning.think(
                "retry_verify",
                f"Verification: matches={verification.matches_task}, confidence={verification.confidence:.0%}",
                verification.match_details[:200],
                "Proceed to download" if verification.should_download else "Still not matching",
            )

            if verification.should_download or verification.matches_task:
                found_match = True
                break
            else:
                await browser.close_note()
                await asyncio.sleep(1)

        if not found_match:
            reasoning.think(
                "no_match_found",
                "Exhausted all keywords, no verified match found",
                "Will proceed with best available candidate for the report",
                "Generate report with partial results",
            )

    # ── Step 5: Process media (download, transcribe, frames, vision) ──
    config = ProcessorConfig(
        use_ocr=True, use_vision=True, use_whisper=True,
        cache_video_locally=True,
        max_transcription_seconds=120,
        transcription_timeout_s=300,
    )
    processor = NoteProcessor(browser, media, config)

    action.log("process_start", f"NoteProcessor starting for {note_entity.note_type.value} note")
    t_proc = time.time()
    await processor.process_note(note_entity)
    proc_time = time.time() - t_proc
    action.log("process_done", f"Media processing complete", proc_time)

    note_entity.refresh_derived_fields()

    reasoning.think(
        "media_processed",
        f"Video: transcript={len(note_entity.video.transcript) if note_entity.video else 0} chars, "
        f"frames={len(note_entity.video.frame_descriptions) if note_entity.video else 0}, "
        f"download={'YES' if note_entity.video and note_entity.video.download_path else 'NO'}"
        if note_entity.video else
        f"Images: {sum(1 for i in note_entity.images if i.vision_description)}/{len(note_entity.images)} with vision",
        "Media processing complete",
        "Proceed to comments and completion check",
    )

    # ── Step 6: Comments ──
    t_comm = time.time()
    raw_comments = await browser.extract_comments(max_comments=20, prefer_hot=True)
    comments = Comment.merge_many([Comment.from_dom_dict(c) for c in raw_comments])
    note_entity.comments = comments
    if len(comments) < 10:
        await browser.scroll_note(600)
        await asyncio.sleep(1.5)
        raw2 = await browser.extract_comments(max_comments=30, prefer_hot=True)
        note_entity.comments = NoteEntity.merge_comments(
            [*comments, *[Comment.from_dom_dict(c) for c in raw2]]
        )
    action.log("comments", f"{len(note_entity.comments)} comments", time.time() - t_comm)

    sp = await bridge.save_screenshot(OUTPUT_DIR / "screenshots" / "comments.png")
    screenshots.append(str(sp))

    # ── Step 7: LLM completion check ──
    note_dict = note_entity.to_report_dict()
    completion = agent.check_completion(understanding, [{
        "title": note_entity.title,
        "video_download_path": note_entity.video.download_path if note_entity.video else "",
        "verified": verification.matches_task,
        "confidence": verification.confidence,
    }])
    reasoning.think(
        "completion_check",
        f"LLM says: complete={completion.get('complete', False)}",
        completion.get("reasoning", "")[:200],
        completion.get("next_action", "done"),
    )
    action.log("completion", f"complete={completion.get('complete')}, next={completion.get('next_action')}")

    # ── Step 8: Save outputs ──
    await recorder.stop()
    action.log("recording_stopped", f"{recorder.frame_count} frames captured")

    saved_video = save_video_locally(note_dict, OUTPUT_DIR)
    saved_frames = save_video_frames_locally(note_dict, OUTPUT_DIR)

    gif_path = str(OUTPUT_DIR / "session.gif")
    recorder.save_gif(gif_path, fps=2.0, max_width=800)

    timing = processor.timing.summary() if hasattr(processor, 'timing') else {}
    total_time = time.time() - t0

    # Merge agent reasoning into the reasoning log
    for r in agent.reasoning_log:
        reasoning.think(
            f"llm_internal:{r['phase']}",
            r["prompt_summary"][:150],
            r["response_summary"][:200],
            "(logged from TaskAgent LLM call)",
        )

    report = generate_html_report(
        task=task, note=note_dict, timing=timing,
        action_log=action.entries, reasoning_log=reasoning.entries,
        screenshots=screenshots, saved_video_path=saved_video,
        saved_frames=saved_frames, session_gif=gif_path,
        recording_stats=recorder.summary(), total_time=total_time,
        note_url=note_url, task_understanding=understanding.__dict__,
        candidate_evaluations=all_evaluations,
        verification={
            "matches_task": verification.matches_task,
            "confidence": verification.confidence,
            "match_details": verification.match_details,
            "missing_criteria": verification.missing_criteria,
            "should_download": verification.should_download,
            "reasoning": verification.reasoning,
        },
    )
    (OUTPUT_DIR / "report.html").write_text(report)

    results = {
        "task": task, "keyword_used": keywords[0] if keywords else "",
        "note_url": note_url, "total_time_s": round(total_time, 1),
        "timing": timing,
        "task_understanding": understanding.__dict__,
        "candidate_evaluations": all_evaluations,
        "verification": {
            "matches_task": verification.matches_task,
            "confidence": verification.confidence,
            "match_details": verification.match_details,
            "should_download": verification.should_download,
        },
        "completion_check": completion,
        "completeness": note_entity.completeness,
        "note": note_dict,
        "reasoning": reasoning.entries,
        "action_log": action.entries,
        "screenshots": screenshots,
        "saved_video": saved_video,
        "saved_frames": saved_frames,
        "session_gif": gif_path,
        "recording_stats": recorder.summary(),
    }
    (OUTPUT_DIR / "test_results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))

    if bg_window_id:
        try:
            await bridge.send_command("close_window", {"windowId": bg_window_id})
        except Exception:
            pass
    await bridge.stop()

    print(f"\n{'='*60}")
    print(f"  TASK COMPLETE — {total_time:.1f}s")
    print(f"  Note: {note_entity.title}")
    print(f"  Verified: {verification.matches_task} ({verification.confidence:.0%})")
    print(f"  Video: {'DOWNLOADED' if saved_video else 'NO'}")
    print(f"  Recording: {recorder.frame_count} frames")
    print(f"  LLM calls: understand + evaluate + verify + completion = 4")
    print(f"  Report: {OUTPUT_DIR / 'report.html'}")
    print(f"{'='*60}")


if __name__ == "__main__":
    import sys
    task = sys.argv[1] if len(sys.argv) > 1 else "下载bloc1攀岩馆的这个月的v2线路合集视频"
    asyncio.run(run_task(task))
