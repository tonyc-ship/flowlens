"""Summary and report generation for chatbot fan-out workflows."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .models import ChatbotWindow

logger = logging.getLogger(__name__)


def build_summary(
    *,
    question: str,
    elapsed_s: float,
    vision_backend: str,
    preflight_cleanup: dict | None,
    visible_verification: bool,
    timeline: list[dict[str, object]],
    windows: list[ChatbotWindow],
) -> dict:
    """Serialize workflow state into a stable summary payload."""
    summary = {
        "question": question,
        "elapsed_s": round(elapsed_s, 1),
        "vision_backend": vision_backend,
        "preflight_cleanup": preflight_cleanup,
        "visible_verification": visible_verification,
        "timeline": timeline,
        "chatbots": [],
    }
    for cw in windows:
        summary["chatbots"].append({
            "name": cw.site.name,
            "status": cw.status,
            "error": cw.error,
            "tab_id": cw.tab_id,
            "window_id": cw.window_id,
            "planned_bounds": cw.planned_bounds,
            "screenshots": [str(path) for path in cw.screenshots],
            "vision_logs": cw.vision_logs,
            "visible_screenshots": [str(path) for path in cw.visible_screenshots],
            "visible_logs": cw.visible_logs,
            "timeline": cw.timeline,
        })
    return summary


def write_summary(output_dir: Path, summary: dict) -> Path:
    """Write the JSON summary beside workflow artifacts."""
    summary_path = output_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return summary_path


def write_timing_breakdown(output_dir: Path, summary: dict) -> Path:
    """Write a readable markdown timing breakdown beside the summary."""
    lines = ["# Timing Breakdown", "", f"Total elapsed: `{summary['elapsed_s']}s`", ""]

    lines.append("## Global Timeline")
    prev_t = None
    for item in summary.get("timeline", []):
        t = float(item.get("t", 0.0))
        delta = "" if prev_t is None else f" (+{t - prev_t:.3f}s)"
        detail = ", ".join(f"{k}={v}" for k, v in item.items() if k not in {"t", "event"})
        suffix = f" | {detail}" if detail else ""
        lines.append(f"- `{t:7.3f}s` {item.get('event')}{delta}{suffix}")
        prev_t = t

    for cb in summary.get("chatbots", []):
        lines.extend(["", f"## {cb['name']}", ""])
        prev_t = None
        for item in cb.get("timeline", []):
            t = float(item.get("t", 0.0))
            delta = "" if prev_t is None else f" (+{t - prev_t:.3f}s)"
            detail = ", ".join(f"{k}={v}" for k, v in item.items() if k not in {"t", "event"})
            suffix = f" | {detail}" if detail else ""
            lines.append(f"- `{t:7.3f}s` {item.get('event')}{delta}{suffix}")
            prev_t = t

    path = output_dir / "timing_breakdown.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_html_report(output_dir: Path, summary: dict) -> Path:
    """Generate an HTML report with screenshots and vision logs."""
    chatbot_cards = []
    for cb in summary["chatbots"]:
        screenshots_html = ""
        for screenshot_path in cb["screenshots"]:
            name = Path(screenshot_path).name
            screenshots_html += f'<img src="{name}" style="max-width:100%;margin:4px 0;border-radius:6px;">\n'

        visible_html = ""
        for screenshot_path in cb.get("visible_screenshots", []):
            path = Path(screenshot_path)
            rel = path.relative_to(output_dir) if path.is_relative_to(output_dir) else path.name
            visible_html += f'<img src="{rel}" style="max-width:100%;margin:4px 0;border-radius:6px;border:1px solid #324;">\n'

        vision_html = ""
        for log in cb["vision_logs"]:
            vision_html += f"<div class='vision-log'>{log}</div>\n"

        visible_log_html = ""
        for log in cb.get("visible_logs", []):
            visible_log_html += f"<div class='vision-log'>{log}</div>\n"

        status_class = "ok" if cb["status"] == "generating" else "warn" if cb["status"] != "error" else "err"
        chatbot_cards.append(f"""
        <div class="card {status_class}">
            <h2>{cb['name']}</h2>
            <div class="status">Status: <strong>{cb['status']}</strong></div>
            {"<div class='error'>Error: " + cb['error'] + "</div>" if cb['error'] else ""}
            <div class="status">Chrome window: <strong>{cb.get('window_id', '')}</strong></div>
            <div class="screenshots">{screenshots_html}</div>
            {"<h3>Visible Window Checks</h3><div class='screenshots'>" + visible_html + "</div>" if visible_html else ""}
            <details><summary>Vision Logs ({len(cb['vision_logs'])})</summary>
                {vision_html}
            </details>
            {"<details><summary>Visible Verification (" + str(len(cb.get('visible_logs', []))) + ")</summary>" + visible_log_html + "</details>" if cb.get('visible_logs') else ""}
        </div>
        """)

    cleanup = summary.get("preflight_cleanup") or {}
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Multi-Chat Report</title>
<style>
    body {{ font-family: system-ui; background: #1a1a2e; color: #e0e0e0; padding: 20px; margin: 0; }}
    h1 {{ color: #ff7e5c; margin-bottom: 4px; }}
    h3 {{ margin: 16px 0 8px; color: #ffd3c7; font-size: 0.95rem; }}
    .meta {{ color: #888; margin-bottom: 20px; }}
    .question {{ background: #16213e; padding: 16px; border-radius: 8px; margin-bottom: 20px;
                 border-left: 4px solid #ff7e5c; font-size: 1.1em; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(350px, 1fr)); gap: 16px; }}
    .card {{ background: #16213e; border-radius: 10px; padding: 16px; }}
    .card.ok {{ border-left: 4px solid #41d89b; }}
    .card.warn {{ border-left: 4px solid #f0ad4e; }}
    .card.err {{ border-left: 4px solid #e74c3c; }}
    .card h2 {{ margin-top: 0; color: #ff7e5c; }}
    .status {{ margin: 8px 0; }}
    .error {{ color: #e74c3c; margin: 8px 0; }}
    .screenshots img {{ box-shadow: 0 2px 8px rgba(0,0,0,0.4); }}
    .vision-log {{ font-size: 0.85em; color: #aaa; padding: 4px 0; border-bottom: 1px solid #2a2a4a; white-space: pre-wrap; }}
    details {{ margin-top: 12px; }}
    summary {{ cursor: pointer; color: #888; }}
</style></head><body>
<h1>Multi-Chatbot Question</h1>
<div class="meta">Elapsed: {summary['elapsed_s']}s | Vision: {summary['vision_backend']}</div>
<div class="meta">Preflight cleanup: matched {cleanup.get('matched', 0)} temp Chrome processes, remaining {len(cleanup.get('remaining', []))}</div>
<div class="question">{summary['question']}</div>
<div class="grid">{''.join(chatbot_cards)}</div>
</body></html>"""

    report_path = output_dir / "report.html"
    report_path.write_text(html, encoding="utf-8")
    logger.info("Report written: %s", report_path)
    return report_path
