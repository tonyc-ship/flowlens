"""Summaries, journaling, project memory, and query helpers for Observer."""

from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import datetime
from difflib import SequenceMatcher

import anthropic

from ..perception.media import BACKEND_QWEN_LOCAL, BACKEND_SONNET, MediaConfig, MediaProcessor
from .paths import ObserverPaths
from .store import ObserverStore

DEFAULT_TEXT_MODEL = "claude-sonnet-4-6"
EXTRACTION_MODEL = "claude-haiku-4-5"


def _is_similar_to_previous(ocr_text: str, prev_ocr_text: str, threshold: float = 0.85) -> bool:
    if not prev_ocr_text:
        return False
    return SequenceMatcher(None, ocr_text[:1000], prev_ocr_text[:1000]).ratio() > threshold


def _make_media(*, backend: str | None, model: str) -> MediaProcessor:
    return MediaProcessor(
        MediaConfig(
            model=model,
            backend=backend or "",
            use_apple_ocr=True,
            use_vision=True,
            use_whisper=False,
        )
    )


def _extract_content_summary(media: MediaProcessor, ocr_text: str, prev_summary: str = "") -> str:
    context_block = ""
    if prev_summary and prev_summary not in ("[no content]", "[minimal content]"):
        context_block = f"\n\nPrevious screen summary:\n{prev_summary}"
    prompt = f"""Summarize this desktop capture OCR in 2-3 sentences.

Rules:
- Keep key terms, names, and quotes in their original language
- Include a few exact original phrases in quotes so they remain searchable
- Focus on net-new information compared with the previous summary
- If this is mostly menus, chrome, or UI noise, return exactly: [minimal content]

OCR text:
{ocr_text}{context_block}
"""
    return media.call_text(prompt, max_tokens=200).strip()


def _describe_screenshot(media: MediaProcessor, screenshot_path: str, ocr_context: str = "") -> str | None:
    if not screenshot_path or not os.path.exists(screenshot_path):
        return None
    context_hint = ""
    if ocr_context:
        context_hint = f"\nOCR already captured this text: {ocr_context[:200]}\nFocus on what OCR misses."
    prompt = (
        "Describe what you see on this desktop screenshot. Focus on UI layout, "
        "visual content, charts, diagrams, or anything OCR misses. "
        f"Be concise (2-3 sentences).{context_hint}"
    )
    try:
        with open(screenshot_path, "rb") as handle:
            return media.describe_image(handle.read(), prompt, max_tokens=200).strip()
    except Exception:
        return None


def extract_summaries(
    paths: ObserverPaths,
    *,
    dry_run: bool = False,
    limit: int | None = None,
    llm_backend: str | None = None,
    with_vision: bool | None = None,
) -> dict:
    store = ObserverStore(paths)
    use_vision = (
        with_vision
        if with_vision is not None
        else (llm_backend or "").strip().lower() == BACKEND_QWEN_LOCAL
    )
    text_media = _make_media(backend=llm_backend, model=EXTRACTION_MODEL)
    vision_media = _make_media(backend=llm_backend, model=DEFAULT_TEXT_MODEL) if use_vision else None

    rows = store.pending_extractions(limit=limit, include_visual=use_vision)
    if not rows:
        return {
            "total": 0,
            "llm_calls": 0,
            "visual_calls": 0,
            "skipped_empty": 0,
            "skipped_dedup": 0,
        }

    last_processed = store.latest_processed_capture() or {}
    prev_ocr = str(last_processed.get("ocr_text") or "")
    prev_summary = str(last_processed.get("content_summary") or "")
    prev_visual = str(last_processed.get("visual_summary") or "")

    stats = {
        "total": len(rows),
        "llm_calls": 0,
        "visual_calls": 0,
        "skipped_empty": 0,
        "skipped_dedup": 0,
    }

    for row in rows:
        ocr_text = str(row.get("ocr_text") or "")
        screenshot_path = str(row.get("screenshot_path") or "")
        existing_summary = row.get("content_summary")
        visual_summary = row.get("visual_summary")
        is_dedup = False

        if existing_summary:
            content_summary = str(existing_summary)
        elif len(ocr_text.strip()) < 20:
            content_summary = "[no content]"
            stats["skipped_empty"] += 1
        elif _is_similar_to_previous(ocr_text, prev_ocr):
            content_summary = prev_summary
            visual_summary = prev_visual
            is_dedup = True
            stats["skipped_dedup"] += 1
        elif dry_run:
            content_summary = None
            stats["llm_calls"] += 1
        else:
            content_summary = _extract_content_summary(text_media, ocr_text, prev_summary)
            stats["llm_calls"] += 1

        if use_vision and not is_dedup and screenshot_path and os.path.exists(screenshot_path):
            if dry_run:
                stats["visual_calls"] += 1
            else:
                visual = _describe_screenshot(vision_media, screenshot_path, ocr_text) if vision_media else None
                if visual:
                    visual_summary = visual
                    stats["visual_calls"] += 1

        if not dry_run:
            store.update_capture_summaries(
                int(row["id"]),
                content_summary=content_summary,
                visual_summary=str(visual_summary) if visual_summary is not None else None,
            )
            prev_summary = content_summary or prev_summary
            prev_visual = str(visual_summary or prev_visual)
        prev_ocr = ocr_text
    return stats


def generate_semantic_analysis(activities: list[dict], *, llm_backend: str | None = None) -> dict:
    if not activities:
        return {
            "tasks": [],
            "summary": "No activity data",
            "reflection": {"done_well": None, "to_improve": None},
            "project_memories": {},
        }

    activity_summary = []
    for act in activities[-50:]:
        item = {
            "time": str(act["timestamp"])[:19],
            "app": act.get("app_name") or "",
            "window": (act.get("window_title") or "")[:80],
        }
        if act.get("browser_url"):
            item["url"] = str(act["browser_url"])[:120]
        if act.get("ocr_text"):
            item["screen_text"] = str(act["ocr_text"])[:220]
        activity_summary.append(item)

    prompt = f"""Analyze these desktop activity records and produce a concise work review.

Activity data:
{json.dumps(activity_summary, ensure_ascii=False, indent=2)}

Return JSON only with this schema:
{{
  "tasks": [
    {{"name": "Short task name", "duration": "20 minutes", "status": "completed|in progress|on hold"}}
  ],
  "summary": "One-sentence summary under 20 words",
  "reflection": {{
    "done_well": "One thing that went well" | null,
    "to_improve": "One thing to improve" | null
  }},
  "project_memories": {{
    "Project Name": {{
      "status": "Current status",
      "key_decisions": ["Decision"],
      "next_steps": ["Next step"],
      "blockers": ["Blocker"]
    }}
  }}
}}
"""
    media = _make_media(backend=llm_backend, model=DEFAULT_TEXT_MODEL)
    raw = media.call_text(prompt, max_tokens=2048)
    parsed = media.extract_json(raw)
    if isinstance(parsed, dict):
        return parsed
    return {
        "tasks": [],
        "summary": "Analysis failed",
        "reflection": {"done_well": None, "to_improve": None},
        "project_memories": {},
    }


def aggregate_time_by_app(activities: list[dict]) -> dict[str, float]:
    time_by_app: defaultdict[str, float] = defaultdict(float)
    for current, next_item in zip(activities, activities[1:]):
        try:
            t1 = datetime.fromisoformat(str(current["timestamp"]).replace("+00:00", ""))
            t2 = datetime.fromisoformat(str(next_item["timestamp"]).replace("+00:00", ""))
        except ValueError:
            continue
        diff_minutes = (t2 - t1).total_seconds() / 60
        if diff_minutes <= 5:
            time_by_app[str(current.get("app_name") or "Unknown")] += diff_minutes
    return dict(sorted(time_by_app.items(), key=lambda item: -item[1]))


def generate_work_journal(
    paths: ObserverPaths,
    *,
    hours: int | None = None,
    use_llm: bool = True,
    llm_backend: str | None = None,
) -> str:
    store = ObserverStore(paths)
    activities = store.get_timeline(hours=hours)
    if not activities:
        return "No observer captures found."

    time_by_app = aggregate_time_by_app(activities)
    total_minutes = sum(time_by_app.values())
    semantic = {}
    if use_llm:
        semantic = generate_semantic_analysis(activities, llm_backend=llm_backend)
        if semantic.get("project_memories"):
            store.upsert_project_memories(semantic["project_memories"])

    lines = [
        "# ClawVision Observer Review",
        f"Duration: {total_minutes:.0f} min | Captures: {len(activities)}",
        "",
    ]
    if semantic.get("summary"):
        lines.append(f"**{semantic['summary']}**")
        lines.append("")
    if semantic.get("tasks"):
        lines.append("## Tasks")
        icons = {"completed": "✓", "in progress": "◐", "on hold": "○"}
        for task in semantic["tasks"][:5]:
            icon = icons.get(task.get("status", ""), "•")
            lines.append(f"  {icon} {task.get('name', '?')} ({task.get('duration', '')})")
        lines.append("")
    reflection = semantic.get("reflection") or {}
    if reflection.get("done_well") or reflection.get("to_improve"):
        lines.append("## Review")
        if reflection.get("done_well"):
            lines.append(f"  ✓ Done well: {reflection['done_well']}")
        if reflection.get("to_improve"):
            lines.append(f"  → To improve: {reflection['to_improve']}")
        lines.append("")
    memories = semantic.get("project_memories") or {}
    if memories:
        lines.append("## Project Memory")
        for project, data in memories.items():
            lines.append(f"  • {project}: {data.get('status', '')}")
            if data.get("next_steps"):
                lines.append(f"    next: {', '.join(data['next_steps'][:2])}")
        lines.append("")
    lines.append("## Time")
    if not time_by_app:
        lines.append("  Not enough adjacent captures yet to estimate time.")
    else:
        for app, minutes in list(time_by_app.items())[:6]:
            pct = (minutes / total_minutes * 100) if total_minutes else 0
            bar = "█" * int(pct / 10) + "░" * max(0, 10 - int(pct / 10))
            lines.append(f"  {bar} {app} {minutes:.0f}m")
    return "\n".join(lines)


def format_project_memories(paths: ObserverPaths, project: str | None = None) -> str:
    memories = ObserverStore(paths).get_project_memories(project=project)
    if not memories:
        return "No project memories yet."

    if project and len(memories) == 1:
        name, data = next(iter(memories.items()))
        current = data.get("current", {})
        lines = [f"# {name}", f"Updated: {data.get('updated', '?')}", ""]
        lines.append(f"Status: {current.get('status', '?')}")
        if current.get("key_decisions"):
            lines.append(f"Key decisions: {', '.join(current['key_decisions'])}")
        if current.get("next_steps"):
            lines.append(f"Next steps: {', '.join(current['next_steps'])}")
        if current.get("blockers"):
            lines.append(f"Blockers: {', '.join(current['blockers'])}")
        return "\n".join(lines)

    lines = ["# Project Memories", ""]
    for name, data in memories.items():
        current = data.get("current", {})
        lines.append(f"  • {name}")
        lines.append(f"    {current.get('status', '?')} (updated {str(data.get('updated', '?'))[:10]})")
        if current.get("next_steps"):
            lines.append(f"    → {current['next_steps'][0]}")
        lines.append("")
    return "\n".join(lines)


def _tool_search_captures(store: ObserverStore, keyword: str, app_filter: str | None = None, hours: int | None = None, limit: int = 20) -> list[dict]:
    return store.search_captures(keyword, app_filter=app_filter, hours=hours, limit=limit)


def _tool_get_activity_timeline(store: ObserverStore, start_time: str | None = None, end_time: str | None = None, hours: int | None = None, limit: int = 30) -> list[dict]:
    timeline = store.get_timeline(hours=hours)
    results: list[dict] = []
    for item in timeline:
        timestamp = str(item["timestamp"])
        if start_time and timestamp < start_time:
            continue
        if end_time and timestamp > end_time:
            continue
        results.append(
            {
                "id": item["id"],
                "timestamp": timestamp,
                "app": item.get("app_name", ""),
                "window": item.get("window_title", ""),
                "summary": item.get("content_summary", "") or "",
                "visual": item.get("visual_summary", "") or "",
            }
        )
    return results[-limit:]


def _tool_get_project_memory(store: ObserverStore, project: str | None = None) -> dict:
    memories = store.get_project_memories(project=project)
    if not memories:
        return {"message": "No project memories stored yet."}
    overview = {}
    for name, data in memories.items():
        current = data.get("current", {})
        overview[name] = {
            "status": current.get("status", "?"),
            "updated": str(data.get("updated", "?"))[:19],
            "next_steps": current.get("next_steps", []),
            "blockers": current.get("blockers", []),
        }
    return overview


def _tool_get_time_distribution(store: ObserverStore, hours: int | None = None) -> dict:
    timeline = store.get_timeline(hours=hours)
    apps = aggregate_time_by_app(timeline)
    return {
        "total_minutes": round(sum(apps.values()), 1),
        "apps": {name: round(minutes, 1) for name, minutes in apps.items()},
    }


OBSERVER_TOOLS = [
    {
        "name": "search_captures",
        "description": "Search observer captures by keyword across app, window title, browser URL, OCR text, and summaries.",
        "input_schema": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string"},
                "app_filter": {"type": "string"},
                "hours": {"type": "integer"},
                "limit": {"type": "integer"},
            },
            "required": ["keyword"],
        },
    },
    {
        "name": "get_activity_timeline",
        "description": "Get captures in a time range with summaries.",
        "input_schema": {
            "type": "object",
            "properties": {
                "start_time": {"type": "string"},
                "end_time": {"type": "string"},
                "hours": {"type": "integer"},
                "limit": {"type": "integer"},
            },
        },
    },
    {
        "name": "get_project_memory",
        "description": "Read project memory extracted from observer journal runs.",
        "input_schema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
            },
        },
    },
    {
        "name": "get_time_distribution",
        "description": "Get total minutes spent by app.",
        "input_schema": {
            "type": "object",
            "properties": {
                "hours": {"type": "integer"},
            },
        },
    },
]


def _dispatch_tool(store: ObserverStore, name: str, input_data: dict) -> str:
    if name == "search_captures":
        result = _tool_search_captures(store, **input_data)
    elif name == "get_activity_timeline":
        result = _tool_get_activity_timeline(store, **input_data)
    elif name == "get_project_memory":
        result = _tool_get_project_memory(store, **input_data)
    elif name == "get_time_distribution":
        result = _tool_get_time_distribution(store, **input_data)
    else:
        result = {"error": f"Unknown tool: {name}"}
    return json.dumps(result, ensure_ascii=False)


def _run_ask_turn(client: anthropic.Anthropic, store: ObserverStore, system_prompt: str, messages: list[dict]) -> str:
    for _ in range(10):
        response = client.messages.create(
            model=DEFAULT_TEXT_MODEL,
            max_tokens=1200,
            system=system_prompt,
            tools=OBSERVER_TOOLS,
            messages=messages,
        )
        assistant_content = response.content
        messages.append({"role": "assistant", "content": assistant_content})
        tool_uses = [item for item in assistant_content if item.type == "tool_use"]
        if not tool_uses:
            for block in assistant_content:
                if hasattr(block, "text"):
                    return block.text
            return ""

        tool_results = []
        for tool_use in tool_uses:
            payload = _dispatch_tool(store, tool_use.name, dict(tool_use.input))
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use.id,
                    "content": payload,
                }
            )
        messages.append({"role": "user", "content": tool_results})
    return "(max tool turns reached)"


def ask_question(paths: ObserverPaths, question: str, *, llm_backend: str = BACKEND_SONNET) -> str:
    if (llm_backend or BACKEND_SONNET).strip().lower() != BACKEND_SONNET:
        raise ValueError("Observer ask currently requires the sonnet backend.")
    store = ObserverStore(paths)
    stats = store.stats()
    if stats["content_summary_count"] == 0:
        raise RuntimeError("No content summaries found. Run `clawvision observer extract` first.")

    system_prompt = f"""You are ClawVision Observer, a personal workflow assistant.

Current time: {datetime.now().strftime("%Y-%m-%d %H:%M")}
Total captures: {stats['capture_count']}, with summaries: {stats['content_summary_count']}

Use tools to search and browse the user's desktop activity history.
- Use search_captures for topic-specific questions
- Use get_activity_timeline for time-range questions
- Use get_project_memory for project status questions
- Use get_time_distribution for app usage questions

Answer in the same language as the user. Be concise and specific.
"""
    client = anthropic.Anthropic()
    messages = [{"role": "user", "content": question}]
    return _run_ask_turn(client, store, system_prompt, messages)


def run_query_repl(paths: ObserverPaths, *, llm_backend: str = BACKEND_SONNET) -> None:
    store = ObserverStore(paths)
    stats = store.stats()
    if stats["content_summary_count"] == 0:
        print("No content summaries found. Run `clawvision observer extract` first.")
        return
    if (llm_backend or BACKEND_SONNET).strip().lower() != BACKEND_SONNET:
        raise ValueError("Observer ask currently requires the sonnet backend.")

    print(f"ClawVision Observer Q&A ({stats['content_summary_count']} captures indexed)")
    print("Ask questions about your workflow. Type 'quit' to exit.\n")

    client = anthropic.Anthropic()
    system_prompt = f"""You are ClawVision Observer, a personal workflow assistant.

Current time: {datetime.now().strftime("%Y-%m-%d %H:%M")}
Total captures: {stats['capture_count']}, with summaries: {stats['content_summary_count']}

Use tools to search and browse the user's desktop activity history.
- Use search_captures for topic-specific questions
- Use get_activity_timeline for time-range questions
- Use get_project_memory for project status questions
- Use get_time_distribution for app usage questions

Answer in the same language as the user. Be concise and specific.
"""
    messages: list[dict] = []
    while True:
        try:
            question = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            return
        if not question:
            continue
        if question.lower() in {"quit", "exit", "q"}:
            print("Bye!")
            return
        messages.append({"role": "user", "content": question})
        if len(messages) > 60:
            messages = messages[-60:]
        answer = _run_ask_turn(client, store, system_prompt, messages)
        print(f"\nObserver: {answer}\n")
