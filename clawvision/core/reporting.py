"""HTML reporting helpers."""

from __future__ import annotations

from html import escape
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
