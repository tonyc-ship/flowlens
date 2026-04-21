"""HTML reporting helpers."""

from __future__ import annotations

from html import escape
from datetime import datetime
from pathlib import Path
import re


def markdown_styles() -> str:
    return """
.md-content{line-height:1.7}
.md-content h1,.md-content h2,.md-content h3,.md-content h4,.md-content h5,.md-content h6{margin:16px 0 8px;color:#222}
.md-content h1{font-size:24px}
.md-content h2{font-size:20px}
.md-content h3{font-size:17px}
.md-content p{margin:0 0 12px}
.md-content ul,.md-content ol{margin:8px 0 12px;padding-left:22px}
.md-content li{margin:4px 0}
.md-content blockquote{margin:12px 0;padding:10px 14px;border-left:4px solid #cbd5e1;background:#f8fafc;color:#334155}
.md-content code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;background:rgba(15,23,42,0.08);padding:2px 5px;border-radius:4px}
.md-content pre{background:#0f172a;color:#e2e8f0;padding:12px 14px;border-radius:8px;overflow:auto;margin:12px 0}
.md-content pre code{background:none;padding:0;color:inherit}
.md-content table{border-collapse:collapse;width:100%;margin:12px 0;font-size:13px}
.md-content th,.md-content td{border:1px solid #ddd;padding:8px 10px;text-align:left;vertical-align:top}
.md-content th{background:#f5f5f5}
.md-content hr{border:none;border-top:1px solid #ddd;margin:16px 0}
.md-content a{color:#1d4ed8;text-decoration:none}
.md-content a:hover{text-decoration:underline}
"""


def render_markdown(text: str) -> str:
    """Render a small, safe subset of Markdown to HTML."""
    if not text:
        return ""

    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    parts: list[str] = []
    i = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        if stripped.startswith("```"):
            fence = stripped[:3]
            code_lines: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith(fence):
                code_lines.append(lines[i])
                i += 1
            if i < len(lines):
                i += 1
            code_html = escape("\n".join(code_lines))
            parts.append(f"<pre><code>{code_html}</code></pre>")
            continue

        if re.fullmatch(r"[-*_]{3,}", stripped):
            parts.append("<hr>")
            i += 1
            continue

        heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading:
            level = len(heading.group(1))
            parts.append(f"<h{level}>{_render_inline(heading.group(2).strip())}</h{level}>")
            i += 1
            continue

        if _is_table_header(lines, i):
            table_html, i = _render_table(lines, i)
            parts.append(table_html)
            continue

        if stripped.startswith(">"):
            quote_lines: list[str] = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote_lines.append(lines[i].strip()[1:].lstrip())
                i += 1
            quote_html = render_markdown("\n".join(quote_lines))
            parts.append(f"<blockquote>{quote_html}</blockquote>")
            continue

        list_match = re.match(r"^([-+*]|\d+\.)\s+(.*)$", stripped)
        if list_match:
            list_html, i = _render_list(lines, i)
            parts.append(list_html)
            continue

        paragraph_lines = [stripped]
        i += 1
        while i < len(lines):
            nxt = lines[i].strip()
            if not nxt:
                break
            if nxt.startswith("```") or re.fullmatch(r"[-*_]{3,}", nxt):
                break
            if re.match(r"^(#{1,6})\s+", nxt):
                break
            if _is_table_header(lines, i):
                break
            if nxt.startswith(">"):
                break
            if re.match(r"^([-+*]|\d+\.)\s+", nxt):
                break
            paragraph_lines.append(nxt)
            i += 1
        joined = "<br>".join(_render_inline(chunk) for chunk in paragraph_lines)
        parts.append(f"<p>{joined}</p>")

    return "\n".join(parts)


def render_markdown_block(text: str, extra_class: str = "") -> str:
    if not text:
        return ""
    class_attr = "md-content"
    if extra_class:
        class_attr = f"{class_attr} {extra_class}"
    return f"<div class='{class_attr}'>{render_markdown(text)}</div>"


def _is_table_header(lines: list[str], index: int) -> bool:
    if index + 1 >= len(lines):
        return False
    header = lines[index].strip()
    separator = lines[index + 1].strip()
    if "|" not in header or "|" not in separator:
        return False
    cells = [cell.strip() for cell in separator.strip("|").split("|")]
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells if cell)


def _render_table(lines: list[str], index: int) -> tuple[str, int]:
    header_cells = _split_table_row(lines[index])
    index += 2  # skip header + separator
    body_rows: list[list[str]] = []
    while index < len(lines):
        stripped = lines[index].strip()
        if not stripped or "|" not in stripped:
            break
        body_rows.append(_split_table_row(lines[index]))
        index += 1

    header_html = "".join(f"<th>{_render_inline(cell)}</th>" for cell in header_cells)
    body_html = "".join(
        "<tr>" + "".join(f"<td>{_render_inline(cell)}</td>" for cell in row) + "</tr>"
        for row in body_rows
    )
    html = f"<table><thead><tr>{header_html}</tr></thead><tbody>{body_html}</tbody></table>"
    return html, index


def _split_table_row(row: str) -> list[str]:
    return [cell.strip() for cell in row.strip().strip("|").split("|")]


def _render_list(lines: list[str], index: int) -> tuple[str, int]:
    stripped = lines[index].strip()
    ordered = bool(re.match(r"^\d+\.\s+", stripped))
    tag = "ol" if ordered else "ul"
    items: list[str] = []
    pattern = r"^\d+\.\s+(.*)$" if ordered else r"^[-+*]\s+(.*)$"

    while index < len(lines):
        current = lines[index].strip()
        match = re.match(pattern, current)
        if not match:
            break
        items.append(f"<li>{_render_inline(match.group(1).strip())}</li>")
        index += 1

    return f"<{tag}>{''.join(items)}</{tag}>", index


def _render_inline(text: str) -> str:
    placeholders: dict[str, str] = {}
    counter = 0

    def stash(value: str) -> str:
        nonlocal counter
        key = f"\u0000MDPLACEHOLDER{counter}\u0000"
        counter += 1
        placeholders[key] = value
        return key

    escaped = escape(text)
    escaped = re.sub(
        r"`([^`]+)`",
        lambda match: stash(f"<code>{escape(match.group(1))}</code>"),
        escaped,
    )
    escaped = re.sub(
        r"\[([^\]]+)\]\((https?://[^\s)]+)\)",
        lambda match: (
            f"<a href=\"{escape(match.group(2), quote=True)}\" target=\"_blank\" rel=\"noreferrer\">"
            f"{match.group(1)}</a>"
        ),
        escaped,
    )
    escaped = re.sub(r"\*\*([^*\n]+)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"__([^_\n]+)__", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", escaped)
    escaped = re.sub(r"(?<!_)_([^_\n]+)_(?!_)", r"<em>\1</em>", escaped)

    for key, value in placeholders.items():
        escaped = escaped.replace(key, value)
    return escaped


# ---------------------------------------------------------------------------
# Agent run HTML report
# ---------------------------------------------------------------------------

_HTML_CSS = """
:root{--bg:#f6efe6;--ink:#171312;--muted:#6b625d;--line:rgba(23,19,18,0.1);--panel:rgba(255,252,247,0.78);--panel-strong:rgba(255,249,241,0.94);--accent:#db5f3b;--accent-2:#f0b24b;--deep:#c7542b;--lite:#2f7f73;--shadow:0 24px 80px rgba(89,59,33,0.14)}
*{box-sizing:border-box}html{scroll-behavior:smooth}
body{margin:0;font-family:"Avenir Next","PingFang SC","Hiragino Sans GB",sans-serif;color:var(--ink);background:radial-gradient(circle at 0% 0%,rgba(240,178,75,.22),transparent 30%),radial-gradient(circle at 100% 20%,rgba(219,95,59,.16),transparent 28%),linear-gradient(180deg,#f8f1e8 0%,#f2ebe1 48%,#efe8df 100%)}
a{color:inherit}
.page{width:min(1180px,calc(100vw - 32px));margin:0 auto;padding:24px 0 56px}
.hero{position:relative;overflow:hidden;border:1px solid rgba(23,19,18,.08);background:radial-gradient(circle at top left,rgba(255,255,255,.86),transparent 38%),linear-gradient(135deg,rgba(255,248,238,.94),rgba(250,234,215,.82));box-shadow:var(--shadow);border-radius:34px;padding:36px}
.hero::after{content:"";position:absolute;inset:auto -60px -80px auto;width:280px;height:280px;border-radius:999px;background:radial-gradient(circle,rgba(219,95,59,.22),transparent 66%);pointer-events:none}
.topline{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:24px;color:var(--muted);font-size:.92rem}
.hero-grid{display:grid;grid-template-columns:minmax(0,1.3fr) minmax(280px,.8fr);gap:28px;align-items:start}
.eyebrow{margin:0 0 10px;text-transform:uppercase;letter-spacing:.16em;font-size:.74rem;color:var(--accent);font-weight:700}
h1{margin:0;font-size:clamp(2.2rem,5vw,3.8rem);line-height:.98;letter-spacing:-.05em}
.hero p{margin:16px 0 0;font-size:1.02rem;line-height:1.7;color:var(--muted);max-width:62ch}
.pill-row{display:flex;flex-wrap:wrap;gap:10px;margin-top:18px}
.pill-row span{display:inline-flex;align-items:center;min-height:34px;padding:8px 12px;border-radius:999px;border:1px solid rgba(23,19,18,.08);background:rgba(255,255,255,.62);font-size:.92rem}
.hero-side{display:grid;gap:16px}
.meta-panel{border-radius:26px;padding:22px;background:rgba(255,255,255,.68);border:1px solid rgba(23,19,18,.08)}
.meta-panel h2{margin:0 0 14px;font-size:1.06rem;letter-spacing:-.02em}
.meta-list{display:grid;gap:10px;color:var(--muted);font-size:.95rem}
.meta-list strong{color:var(--ink);display:block;font-size:1rem;margin-bottom:2px}
.meta-panel a{color:var(--accent);text-decoration:none;font-weight:600}
.section{margin-top:28px;border-radius:28px;padding:28px;background:var(--panel);border:1px solid rgba(23,19,18,.08);backdrop-filter:blur(8px);box-shadow:0 18px 40px rgba(118,88,66,.08)}
.section-head{display:flex;justify-content:space-between;gap:20px;align-items:end;margin-bottom:20px;flex-wrap:wrap}
.section-head h2{margin:0;font-size:clamp(1.5rem,3vw,2.4rem);letter-spacing:-.04em}
.section-head p{margin:8px 0 0;color:var(--muted);line-height:1.7;max-width:60ch}
.stats-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}
.stat-card{padding:18px;border-radius:22px;background:var(--panel-strong);border:1px solid rgba(23,19,18,.08)}
.stat-card span{color:var(--muted);font-size:.84rem;text-transform:uppercase;letter-spacing:.12em}
.stat-card strong{display:block;margin-top:10px;font-size:2rem;letter-spacing:-.05em}
.stat-card p{margin:10px 0 0;color:var(--muted);line-height:1.6;font-size:.95rem}
.note-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:18px}
.note-card{overflow:hidden;border-radius:24px;background:rgba(255,255,255,.74);border:1px solid rgba(23,19,18,.08);display:flex;flex-direction:column}
.note-art{aspect-ratio:4/3;background:linear-gradient(135deg,rgba(219,95,59,.16),rgba(240,178,75,.22));overflow:hidden}
.note-art img{width:100%;height:100%;object-fit:cover;display:block}
.note-art-placeholder{display:grid;place-items:center;width:100%;height:100%;color:var(--muted);letter-spacing:.18em;text-transform:uppercase;font-size:.78rem}
.note-body{padding:18px;display:grid;gap:12px;flex:1}
.note-meta,.note-stats,.note-links,.note-tags{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
.index-tag{display:inline-flex;align-items:center;min-height:28px;border-radius:999px;padding:6px 10px;font-size:.8rem;border:1px solid rgba(23,19,18,.08);background:rgba(255,255,255,.74)}
.note-body h3{margin:0;font-size:1.08rem;letter-spacing:-.03em;line-height:1.25}
.note-author,.note-summary{margin:0;color:var(--muted);line-height:1.7}
.note-summary{font-size:.92rem}
.note-stats{color:var(--muted);font-size:.88rem}
.note-tags span{display:inline-flex;align-items:center;min-height:24px;padding:4px 8px;border-radius:999px;font-size:.78rem;border:1px solid rgba(23,19,18,.08);background:rgba(255,255,255,.74)}
.note-links a{text-decoration:none;color:var(--accent);font-weight:600;font-size:.88rem}
.report-section article{display:grid;gap:10px}
.report-body{line-height:1.8}
.report-body h2,.report-body h3,.report-body h4{margin:20px 0 6px;letter-spacing:-.03em}
.report-body h2{font-size:1.6rem}
.report-body h3{font-size:1.25rem}
.report-body p,.report-body li{color:var(--muted);line-height:1.85}
.report-body ul{margin:6px 0;padding-left:20px}
.table-wrap{overflow-x:auto;border-radius:18px;border:1px solid rgba(23,19,18,.08);background:rgba(255,255,255,.74);margin:12px 0}
table{width:100%;border-collapse:collapse;min-width:520px}
th,td{padding:12px 14px;text-align:left;border-bottom:1px solid rgba(23,19,18,.08)}
th{font-size:.84rem;text-transform:uppercase;letter-spacing:.1em;color:var(--muted);background:rgba(255,249,241,.88)}
.footer{margin-top:24px;color:var(--muted);font-size:.9rem;text-align:center}
@media(max-width:960px){.hero-grid,.stats-grid,.note-grid{grid-template-columns:1fr}}
@media(max-width:640px){.page{width:calc(100vw - 18px)}.hero,.section{border-radius:24px;padding:20px}}
"""


def generate_agent_html_report(
    *,
    task: str,
    model: str,
    run_dir: "Path | str",
    final_text: str,
    site_results: list[dict],
    total_duration_s: float,
    turns: int,
    screenshots: list[str],
) -> str:
    """Generate a self-contained HTML report for an agent run."""
    run_dir = Path(run_dir)
    title_escaped = escape(task)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    # ── Collect unique note entities ─────────────────────────────────────────
    seen_ids: set[str] = set()
    notes: list[dict] = []
    for item in site_results:
        entity = item.get("entity") or {}
        if not entity:
            # flat note (older format)
            if item.get("title"):
                entity = item
        nid = entity.get("note_id", "")
        if not entity.get("title"):
            continue
        if nid and nid in seen_ids:
            continue
        if nid:
            seen_ids.add(nid)
        notes.append(entity)

    total_notes = len(notes)
    deep_notes = sum(1 for n in notes if n.get("completeness_score", 0) >= 0.8)
    duration_str = f"{int(total_duration_s // 60)}m {int(total_duration_s % 60)}s"

    # ── Stats grid ───────────────────────────────────────────────────────────
    stats_html = f"""
<div class="stats-grid">
  <article class="stat-card"><span>样本笔记</span><strong>{total_notes}</strong><p>本次实际读取的笔记数量。</p></article>
  <article class="stat-card"><span>深度完成</span><strong>{deep_notes}</strong><p>完整度分数 ≥ 0.8 的深读样本。</p></article>
  <article class="stat-card"><span>截图数</span><strong>{len(screenshots)}</strong><p>Agent 执行过程中保存的截图。</p></article>
  <article class="stat-card"><span>执行耗时</span><strong>{duration_str}</strong><p>从启动到生成报告的总时长。</p></article>
</div>"""

    # ── Note cards ───────────────────────────────────────────────────────────
    note_cards = []
    for i, note in enumerate(notes, 1):
        note_title = escape(note.get("title", "未命名"))
        author = escape(note.get("author", ""))
        likes = escape(str(note.get("likes", "") or note.get("likes_value", "") or ""))
        favs = escape(str(note.get("favorites", "") or note.get("favorites_value", "") or ""))
        comments = escape(str(note.get("comments_count", "") or note.get("comments_count_value", "") or ""))
        content_preview = escape((note.get("content") or "")[:120])
        tags_html = "".join(f"<span>{escape(t)}</span>" for t in (note.get("hashtags") or [])[:5])
        note_url = escape(note.get("url", ""))

        # Find cover image relative to run_dir
        images = note.get("images") or []
        img_html = ""
        if images:
            img_src = images[0].get("url", "")
            # prefer local webp if it exists
            note_id = note.get("note_id", "")
            local_webp = run_dir / "site_media" / note_id / "image_01.webp"
            if note_id and re.match(r'^[A-Za-z0-9_-]+$', note_id) and local_webp.exists():
                img_src = f"site_media/{note_id}/image_01.webp"
            if img_src:
                img_html = f'<img src="{escape(img_src)}" alt="{note_title}" loading="lazy">'

        placeholder = '<div class="note-art-placeholder">No Image</div>'
        art_html = f'<div class="note-art">{img_html if img_html else placeholder}</div>'

        stats_line = ""
        if likes or favs or comments:
            parts = []
            if likes:
                parts.append(f"赞 {likes}")
            if favs:
                parts.append(f"收藏 {favs}")
            if comments:
                parts.append(f"评论 {comments}")
            stats_line = f'<div class="note-stats">{"  ·  ".join(f"<span>{p}</span>" for p in parts)}</div>'

        link_html = f'<a href="{note_url}" target="_blank" rel="noreferrer">打开原帖</a>' if note_url else ""

        note_cards.append(f"""
<article class="note-card">
  {art_html}
  <div class="note-body">
    <div class="note-meta"><span class="index-tag">#{i}</span></div>
    <h3>{note_title}</h3>
    <p class="note-author">{author}</p>
    {"<p class='note-summary'>" + content_preview + "…</p>" if content_preview else ""}
    {stats_line}
    <div class="note-tags">{tags_html}</div>
    <div class="note-links">{link_html}</div>
  </div>
</article>""")

    notes_section = ""
    if note_cards:
        notes_section = f"""
<section class="section">
  <div class="section-head">
    <div><p class="eyebrow">Sample Board</p><h2>笔记样本</h2></div>
    <p>本次 agent 读取的 {total_notes} 篇笔记，含截图预览、互动数据和原帖跳转。</p>
  </div>
  <div class="note-grid">{"".join(note_cards)}</div>
</section>"""

    # ── Full report ──────────────────────────────────────────────────────────
    report_html = render_markdown(final_text) if final_text.strip() else "<p>（报告为空）</p>"

    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title_escaped} · FlowLens Report</title>
  <style>{_HTML_CSS}</style>
</head>
<body>
  <main class="page">
    <section class="hero">
      <div class="topline">
        <span>FlowLens Report</span>
        <span>{now}</span>
      </div>
      <div class="hero-grid">
        <div>
          <p class="eyebrow">Agent Run</p>
          <h1>{title_escaped}</h1>
        </div>
        <aside class="hero-side">
          <section class="meta-panel">
            <h2>运行信息</h2>
            <div class="meta-list">
              <div><strong>模型</strong>{escape(model)}</div>
              <div><strong>轮次</strong>{turns} turns</div>
              <div><strong>耗时</strong>{duration_str}</div>
              <div><strong>报告</strong><a href="report.md" target="_blank" rel="noreferrer">查看 Markdown 原文</a></div>
            </div>
          </section>
        </aside>
      </div>
    </section>

    <section class="section">
      <div class="section-head">
        <div><p class="eyebrow">Overview</p><h2>运行概览</h2></div>
      </div>
      {stats_html}
    </section>

    {notes_section}

    <section class="section report-section">
      <div class="section-head">
        <div><p class="eyebrow">Full Report</p><h2>完整报告</h2></div>
        <p>由 agent 生成的完整分析报告。</p>
      </div>
      <article class="report-body">{report_html}</article>
    </section>

    <p class="footer">Generated by FlowLens · {now}</p>
  </main>
</body>
</html>"""

    return html
