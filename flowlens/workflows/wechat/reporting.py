"""Artifact writers for WeChat chat-summary runs."""

from __future__ import annotations

import json
from html import escape
from pathlib import Path

from ...core.reporting import markdown_styles, render_markdown_block


def write_summary_json(output_dir: Path, payload: dict) -> Path:
    path = output_dir / "summary.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_markdown_report(output_dir: Path, *, summary_markdown: str, verification: str) -> Path:
    path = output_dir / "report.md"
    text = summary_markdown.strip()
    if verification.strip():
        text += f"\n\n## 验证\n\n{verification.strip()}\n"
    path.write_text(text + ("\n" if not text.endswith("\n") else ""), encoding="utf-8")
    return path


def write_html_report(
    output_dir: Path,
    *,
    title: str,
    summary_markdown: str,
    verification: str,
    captures: list[dict],
) -> Path:
    cards = []
    for item in captures:
        screenshot = item.get("screenshot_path", "")
        notes = item.get("notes") or []
        note_html = "".join(f"<li>{escape(str(note))}</li>" for note in notes)
        cards.append(
            "<div class='card'>"
            f"<p><strong>Capture #{item.get('capture_index')}</strong> | "
            f"{escape(item.get('parser_mode', ''))} | "
            f"{len(item.get('messages', []))} messages</p>"
            f"<p>{escape(', '.join(item.get('date_markers', [])[:4]))}</p>"
            f"{'<img src=\"' + escape(Path(screenshot).name) + '\" class=\"shot\">' if screenshot else ''}"
            f"{'<ul>' + note_html + '</ul>' if notes else ''}"
            "</div>"
        )
    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{escape(title)}</title>
<style>
body{{font-family:system-ui;background:#f5f7fb;color:#1f2937;margin:0;padding:24px}}
.wrap{{max-width:1100px;margin:0 auto}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:16px}}
.card{{background:#fff;border:1px solid #dbe3ef;border-radius:12px;padding:14px}}
.shot{{width:100%;border-radius:10px;border:1px solid #e5e7eb}}
{markdown_styles()}
</style></head><body><div class="wrap">
<h1>{escape(title)}</h1>
{render_markdown_block(summary_markdown)}
<h2>验证</h2>
{render_markdown_block(verification)}
<h2>Captures</h2>
<div class="grid">{''.join(cards)}</div>
</div></body></html>"""
    path = output_dir / "report.html"
    path.write_text(html, encoding="utf-8")
    return path
