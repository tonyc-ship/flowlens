"""Xiaohongshu-specific agent behavior profile."""

from __future__ import annotations

import re


SITE_NAME = "xiaohongshu"
DEFAULT_START_URL = "https://www.xiaohongshu.com/explore"
STATE_COMMAND = "detect_state"


def task_matches(task: str) -> bool:
    lowered = str(task or "").lower()
    return "小红书" in str(task or "") or "xiaohongshu" in lowered or "xhs" in lowered


def _task_is_topic_research(task: str) -> bool:
    lowered = str(task or "").lower()
    return "task type: topic_research" in lowered or "话题研究" in str(task or "")


def dynamic_extra_instructions(task: str, site_name: str | None, page_state: str | None) -> str:
    if site_name != SITE_NAME and not task_matches(task):
        return ""

    parts: list[str] = []
    if task_matches(task):
        parts.append(
            "For Xiaohongshu research tasks, start with `xhs_topic_scan(query=...)` "
            "when it is available. Decide `include_media` yourself based on the task: "
            "set it to true when understanding the image or video content itself is needed "
            "to answer the user (e.g. summarizing what a video shows, reading text baked into images, "
            "comparing visuals across notes); leave it false when note titles, body text, and comments "
            "are enough. Note that `include_media=true` adds image OCR, image vision, video ASR and "
            "video frame sampling, so it costs extra time — use it when the task truly needs it. "
            "Screenshots of each note are saved as evidence either way. "
            "After one representative topic scan plus a few targeted reads if needed, write "
            "the final report instead of repeatedly searching/opening more notes. Otherwise navigate to Xiaohongshu and call "
            "`run_site_action(action='search_notes', query=...)`. Do not take a "
            "generic initial screenshot, do not analyze a screenshot before the "
            "first search, and never search for `小红书网页版` unless the user explicitly asks for that phrase."
        )
    if page_state in {"homepage", "search_results"}:
        parts.append(
            "On Xiaohongshu homepage/search pages, prefer `run_site_action(search_notes)` "
            "or `extract_site_entity(entity_type='search_cards')` over manual click/type fallbacks. "
            "After you choose a card, prefer `run_site_action(action='read_note', ...)`. Only use low-level "
            "manual tools if a site action explicitly returns manual_fallback_allowed=true."
        )
    if _task_is_topic_research(task):
        parts.append(
            "For topic research, search first, inspect the visible cards, and then open the most relevant notes "
            "one by one with `run_site_action(action='read_note', ...)`."
        )
    parts.append(
        "Each card in Xiaohongshu tool results comes with `note_id`, `title`, and an "
        "`already_analyzed` flag. Before opening a note, check whether its `note_id` is "
        "already marked `already_analyzed: true` or appears in the `already_analyzed_notes` "
        "list — if it does, reuse the prior result instead of re-opening. Target notes by "
        "`note_id` rather than `index` when a note_id is available; indexes shift across "
        "searches but note_ids are stable. `run_site_action(read_note)` will also "
        "server-side short-circuit (skipped=true) when the note_id was already extracted at "
        "the same or deeper level this run — respect that instead of passing force=true."
    )
    parts.append(
        "Tool responses include an `artifact_file` local-disk path for audit / reporting. "
        "This is NOT a URL — do not call `navigate(file://...)` or `fetch(...)` on it. The "
        "summary fields in the same response already contain what you need to reason over."
    )
    parts.append(
        "In the final Xiaohongshu report, embed each useful note screenshot using "
        "`![note title](screenshot_filename.png)`. Treat screenshots as primary evidence because direct "
        "Xiaohongshu links are often blocked or rate-limited. If you report post body text, use "
        "`entity.content` only; put image OCR/vision/video evidence in a separate column or label it as media evidence."
    )
    return "\n".join(parts)


def active_tool_names(page_state: str | None, *, manual_allowed: bool) -> set[str] | None:
    active_names = {"navigate", "go_back"}

    if page_state in {None, "", "homepage", "search_results"}:
        active_names.update({"extract_page_data", "run_site_action", "extract_site_entity", "xhs_topic_scan"})
        if page_state == "search_results":
            active_names.add("scroll")
        if manual_allowed:
            active_names.update({
                "click", "type_text", "press_key", "read_page", "run_javascript",
                "screenshot", "analyze_screenshot", "ocr_screenshot", "wait",
            })
        return active_names

    if page_state == "note_detail":
        active_names.update({"extract_page_data", "run_site_action", "extract_site_entity"})
        if manual_allowed:
            active_names.update({
                "click", "read_page", "run_javascript",
                "screenshot", "analyze_screenshot", "ocr_screenshot", "wait",
            })
        return active_names

    if page_state == "profile_page":
        active_names.update({"extract_page_data", "run_site_action", "extract_site_entity", "xhs_topic_scan"})
        if manual_allowed:
            active_names.update({
                "click", "read_page", "run_javascript",
                "screenshot", "analyze_screenshot", "ocr_screenshot", "wait",
            })
        return active_names

    return None


def append_note_screenshot_index(report: str, site_results: list[dict]) -> str:
    """Ensure XHS reports include local note screenshots as visual evidence."""
    used_targets = set(re.findall(r"!\[[^\]]*\]\(([^)]+)\)", report or ""))
    used_note_keys: set[str] = set()
    items: list[dict] = []
    entities: list[dict] = []

    def note_key(entity: dict) -> str:
        note_id = str(entity.get("note_id") or "").strip()
        if note_id:
            return f"id:{note_id}"
        url = str(entity.get("url") or entity.get("resolved_url") or "").strip()
        match = re.search(r"xiaohongshu\.com/(?:explore|discovery/item)/([^/?#]+)", url)
        if match:
            return f"id:{match.group(1)}"
        if url:
            return f"url:{url.split('#', 1)[0]}"
        title = str(entity.get("title") or "").strip().lower()
        author = str(entity.get("author") or "").strip().lower()
        return f"title:{title}|author:{author}" if title and author else ""

    def collect_entity(entity: dict):
        if entity.get("screenshot"):
            entities.append(entity)

    for result in site_results:
        entity = result.get("entity")
        if isinstance(entity, dict):
            collect_entity(entity)
        notes = result.get("notes")
        if isinstance(notes, list):
            for note in notes:
                note_entity = note.get("entity") if isinstance(note, dict) else None
                if isinstance(note_entity, dict):
                    collect_entity(note_entity)

    for entity in entities:
        screenshot = str(entity.get("screenshot") or "").strip()
        key = note_key(entity)
        if screenshot in used_targets and key:
            used_note_keys.add(key)

    def add_entity(entity: dict):
        screenshot = str(entity.get("screenshot") or "").strip()
        if not screenshot or screenshot in used_targets:
            return
        key = note_key(entity)
        if key and key in used_note_keys:
            return
        items.append({
            "screenshot": screenshot,
            "title": entity.get("title") or entity.get("note_id") or "笔记截图",
            "author": entity.get("author") or "",
            "url": entity.get("url") or entity.get("resolved_url") or "",
        })
        used_targets.add(screenshot)
        if key:
            used_note_keys.add(key)

    for entity in entities:
        add_entity(entity)

    if not items:
        return report

    lines = [
        "",
        "## 笔记截图索引",
        "",
    ]
    for item in items:
        title = str(item["title"] or "笔记截图")
        meta = f" - {item['author']}" if item.get("author") else ""
        lines.append(f"### {title}{meta}")
        lines.append(f"![{_markdown_alt(title)}]({item['screenshot']})")
        if item.get("url"):
            lines.append(f"[笔记链接]({item['url']})")
        lines.append("")

    return (report or "").rstrip() + "\n" + "\n".join(lines).rstrip() + "\n"


def _markdown_alt(text: str) -> str:
    return re.sub(r"[\[\]\n\r]+", " ", str(text or "")).strip()[:80] or "笔记截图"
