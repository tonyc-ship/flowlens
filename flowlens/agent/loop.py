"""Core agent loop — LLM-driven browser automation.

The loop sends messages + tools to an LLM backend (Anthropic, OpenAI, or local
MLX models). When the LLM returns tool calls, we execute them and feed results
back. When the LLM returns only text, the task is complete.

Supports three backend families:
- Anthropic hosted models — native tool_use API
- OpenAI hosted models — Responses API function tools
- Local Qwen / UI-TARS — text-based tool calling via <tool_call> tags
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from ..core.bridge import ExtensionBridge, TabBridge, ensure_extension_connection
from ..core.auth import PROVIDER_KIMI, PROVIDER_OPENAI, PROVIDER_QWEN, resolve_model_provider
from ..core.process_metrics import append_jsonl, system_resource_snapshot
from ..core.runtime import task_runs_root
from ..knowledge.loader import detect_site, get_knowledge_for_url
from ..perception.media import (
    BACKEND_OPENAI,
    BACKEND_KIMI,
    BACKEND_QWEN_CLOUD,
    BACKEND_QWEN_LOCAL,
    BACKEND_SONNET,
    BACKEND_UI_TARS_LOCAL,
    DEFAULT_MODEL,
    DEFAULT_WHISPER_MODEL,
    MediaConfig,
    MediaProcessor,
)
from ..perception.local_llm import DEFAULT_LOCAL_IMAGE_MAX_DIM, LocalLLM
from .backends import create_backend
from .run_state import RunState
from .tool import Tool, ToolContext
from ..tools import build_tools
from ..tools.capability_packs import CAPABILITY_PACKS
from ..platforms.agent_profiles import (
    active_tool_names as profile_active_tool_names,
    append_report_extras,
    default_start_url_for_task,
    dynamic_extra_instructions as profile_dynamic_extra_instructions,
    state_command_for_site,
)


_BASE_SYSTEM_PROMPT = """\
You are a computer-use agent. You can work in a real Chrome browser, in macOS desktop apps, or both, depending on the task and the capability packs you activate.

## How to work

1. **Plan briefly**: Keep planning terse and action-oriented.
2. **Select the right surface**: Decide whether the task belongs in the browser, on the desktop, or both.
3. **Use progressive disclosure**: Start with capability-pack discovery. Inspect a pack before activating it when you need to understand its concrete tools.
4. **Observe efficiently**: Prefer site/app-specific tools when they exist. Take generic screenshots only when a more structured tool cannot answer the question or when you need to debug a failed action.
5. **Act**: Execute one action, then observe the result.
6. **Verify**: After important actions, take a screenshot to confirm they worked.
7. **Report**: When done, write a comprehensive, detailed report.

## Important rules

- Do not activate every capability pack up front. Activate only the packs you actually need.
- Use `list_capability_packs` first if the task surface is unclear.
- Use `describe_capability_pack` before activating a pack when the tool surface is not obvious from the pack summary.
- Do not take a generic starting screenshot when a site/app-specific tool can start the workflow directly.
- **Before manual clicking on generic browser pages**: Use `read_page` to find exact \
element coordinates. Do NOT guess coordinates from screenshots alone — use the \
(x,y) coordinates from read_page elements, clicking at the center of the \
element's bounding box.
- Prefer using site-specific extractors (extract_page_data) when available — \
they are faster and more reliable than reading raw DOM or manually clicking UI.
- Prefer site-specific macros (e.g. `xhs_topic_scan`, `xhs_read_note`, \
`xhs_search_notes`) over chaining navigate + click + extract_page_data. \
The per-site tools are named with the site prefix (`xhs_*`). \
Use raw `extract_page_data` only for low-level actions and DOM-only helpers.
- For WeChat chat tasks, prefer `wechat_collect_history` followed by \
`wechat_read_history_artifact`. Use `wechat_ocr_conversation_region` when \
you need exact text from one visible screen. Do not loop over \
`analyze_screenshot` on every saved chat screenshot unless the structured \
WeChat tools still leave a specific ambiguity.
- When site knowledge marks a command as PREFERRED, use that command before \
trying read_page + click/type fallbacks.
- Do not use standalone `wait` when a site-specific action already has a `wait_seconds` parameter. Keep waits short unless recovering from an error.
- If something goes wrong, take a screenshot to diagnose before retrying.
- If search results show "没找到相关内容" or similar empty state, wait 5-10 \
seconds and try again with a simpler query. This is usually anti-bot throttling.
- When using run_javascript, your code is executed via `new Function(code)()`. \
You MUST use `return` to get a value back. For example: \
`return document.title` or `return (function() { ... })()`.
- **Take a screenshot of every important page state** — especially note/article \
detail views. These screenshots will be included in the final report.
- For complex multi-step work, use `update_task_plan` to keep a concise checklist current.
- Earlier findings are persisted outside the live chat history. Use `read_run_state` \
or `read_saved_artifact` when you need to revisit older evidence instead of guessing from memory.
- Before writing the final report for a multi-source task, ground the report against \
the persisted run state instead of relying only on recent chat memory.

## Thinking out loud

At each turn, briefly explain:
- What you observe on the page or in the app window
- What you're about to do and why
- Any concerns or alternative approaches

This reasoning is recorded for analysis.

## Output format

When you finish the task, write a **comprehensive final report** in markdown:
- Start with a brief executive summary
- Include structured data (tables, lists) for key findings
- **Embed screenshots inline** using markdown image syntax: `![description](filename.jpg)` \
(e.g. `![搜索结果](002_search_results.jpg)`). Do NOT use text-only references.
- Include links/URLs to the content you found
- Provide analysis, patterns, and insights — not just raw data
- End with conclusions or recommendations if applicable
"""


def _build_system_prompt(
    tools: list[Tool],
    capability_overview: str = "",
    site_knowledge: str = "",
    extra_instructions: str = "",
) -> str:
    parts = [_BASE_SYSTEM_PROMPT]

    if capability_overview:
        parts.append(f"\n## Capability Packs\n\n{capability_overview}")

    if site_knowledge:
        parts.append(f"\n## Site Knowledge\n\n{site_knowledge}")

    if extra_instructions:
        parts.append(f"\n## Additional Instructions\n\n{extra_instructions}")

    return "\n".join(parts)


def _capability_overview(
    tools: list[Tool],
    *,
    active_packs: set[str],
) -> str:
    grouped: dict[str, list[str]] = {}
    for tool in tools:
        pack_id = str(getattr(tool, "capability_pack", "") or "").strip()
        if not pack_id:
            continue
        grouped.setdefault(pack_id, []).append(tool.name)
    lines: list[str] = []
    for pack_id, spec in CAPABILITY_PACKS.items():
        status = "active" if pack_id in active_packs else "inactive"
        tool_names = ", ".join(sorted(grouped.get(pack_id, []))[:8]) or "No concrete tools available in this run."
        dependency_text = f" Depends on: {', '.join(spec.dependencies)}." if spec.dependencies else ""
        lines.append(
            f"- `{pack_id}` ({status}): {spec.summary}{dependency_text} "
            f"Use `describe_capability_pack(pack_id='{pack_id}')` to inspect tools. "
            f"Current tools: {tool_names}"
        )
    return "\n".join(lines)


_FLOWLENS_ENV_KEYS = (
    "FLOWLENS_LLM_BACKEND",
    "FLOWLENS_AGENT_CONTEXT_RECENT_MESSAGES",
    "FLOWLENS_AGENT_CONTEXT_MEMORY_CHARS",
    "FLOWLENS_SITE_MEDIA_BACKEND",
    "FLOWLENS_SITE_MEDIA_MODEL",
    "FLOWLENS_KIMI_MODEL",
    "FLOWLENS_QWEN_MODEL",
    "FLOWLENS_APP_DATA_DIR",
)


def _git_commit_sha() -> str:
    """Best-effort short git sha of the running flowlens checkout."""
    try:
        repo_root = Path(__file__).resolve().parents[2]
    except Exception:
        return ""
    try:
        out = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--short=12", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return ""


def _flowlens_package_version() -> str:
    try:
        from importlib.metadata import version  # stdlib since 3.8
        return version("flowlens")
    except Exception:
        return ""


def _runtime_environment(model: str) -> dict:
    """Snapshot of runtime info to pin every run to a concrete build.

    Captured once at task_start so later debugging can match a log bundle to an
    exact flowlens build, model backend, and the env-var-driven context tuning.
    """
    env_snapshot = {
        key: os.environ.get(key, "")
        for key in _FLOWLENS_ENV_KEYS
        if os.environ.get(key, "") != ""
    }
    return {
        "flowlens_version": _flowlens_package_version(),
        "git_commit": _git_commit_sha(),
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "model": model,
        "backend_provider": _infer_media_backend(model),
        "env": env_snapshot,
    }


def _tool_call_signature(tool_name: str, tool_input: dict) -> str:
    """Stable hash of (tool_name, tool_input) for repeat-call detection."""
    try:
        payload = json.dumps(tool_input or {}, sort_keys=True, ensure_ascii=False)
    except Exception:
        payload = str(tool_input)
    return hashlib.md5(f"{tool_name}::{payload}".encode("utf-8")).hexdigest()[:12]


def _text_summary(content_blocks: list[dict], max_len: int = 500) -> str:
    """Summarize tool result content blocks for logging (omit base64 images)."""
    parts = []
    for block in content_blocks:
        if block.get("type") == "text":
            parts.append(block["text"][:max_len])
        elif block.get("type") == "image":
            parts.append("[image]")
    return " | ".join(parts)[:max_len]


def _is_tool_result_message(message: dict) -> bool:
    content = message.get("content")
    if isinstance(content, str):
        return content.lstrip().startswith("[Tool result for ")
    if not isinstance(content, list):
        return False
    return any(
        isinstance(block, dict)
        and block.get("type") in {"tool_result", "function_call_output"}
        for block in content
    )


def _compact_memory_entries(entries: list[str], max_chars: int) -> str:
    if not entries or max_chars <= 0:
        return ""
    selected: list[str] = []
    total = 0
    for entry in reversed(entries):
        entry = str(entry or "").strip()
        if not entry:
            continue
        projected = total + len(entry) + 1
        if selected and projected > max_chars:
            break
        selected.append(entry[:max_chars] if not selected and len(entry) > max_chars else entry)
        total = min(projected, max_chars)
        if total >= max_chars:
            break
    return "\n".join(reversed(selected))[:max_chars]


def _prepare_messages_for_context(
    messages: list[dict],
    run_state: RunState | None,
    memory_entries: list[str],
    *,
    keep_recent_messages: int | None = None,
    memory_max_chars: int | None = None,
    compaction_info: dict | None = None,
) -> list[dict]:
    keep_recent = keep_recent_messages
    if keep_recent is None:
        keep_recent = int(os.environ.get("FLOWLENS_AGENT_CONTEXT_RECENT_MESSAGES", "12") or "12")
    memory_chars = memory_max_chars
    if memory_chars is None:
        memory_chars = int(os.environ.get("FLOWLENS_AGENT_CONTEXT_MEMORY_CHARS", "6000") or "6000")
    if compaction_info is not None:
        compaction_info.clear()
        compaction_info.update({
            "raw_messages": len(messages),
            "keep_recent": keep_recent,
            "memory_chars_budget": memory_chars,
            "compacted": False,
        })
    if len(messages) <= keep_recent + 2:
        return messages

    sections: list[str] = []
    if run_state is not None:
        state_block = run_state.context_block(max_chars=max(1200, memory_chars // 2))
        if state_block:
            sections.append("Structured run state from earlier turns:\n\n" + state_block)
    memory = _compact_memory_entries(memory_entries, memory_chars)
    if memory:
        sections.append(
            "Condensed event memory from earlier turns. Older raw tool messages were compacted "
            "to keep context small:\n\n" + memory
        )
    if not sections:
        return messages

    recent = list(messages[-keep_recent:])
    while recent and _is_tool_result_message(recent[0]):
        recent.pop(0)

    result = [
        messages[0],
        {
            "role": "user",
            "content": "\n\n".join(sections),
        },
        *recent,
    ]
    if compaction_info is not None:
        compaction_info["compacted"] = True
        compaction_info["effective_messages"] = len(result)
        compaction_info["dropped_messages"] = max(0, len(messages) - len(result))
        compaction_info["memory_entries_used"] = len(memory_entries)
    return result


def _log_excerpt(text: str, max_len: int = 200) -> str:
    text = str(text or "").replace("\n", "\\n").strip()
    if len(text) <= max_len:
        return text
    return text[:max_len] + "... [truncated]"


def _build_report_grounding_request(run_state: RunState) -> str:
    digest = run_state.report_grounding_context(max_chars=5000)
    return (
        "Before finalizing your report, ground it against the persisted run state. "
        "First inspect the saved state using `read_run_state(section='plan')` and "
        "`read_run_state(section='evidence')`. If one of the saved artifact paths "
        "looks important, use `read_saved_artifact(path='...')` to pull the full saved content. "
        "Do not reopen the website unless the saved state is clearly insufficient.\n\n"
        "Saved run-state digest:\n\n"
        f"{digest}"
    )


def _make_media_for_model(model: str) -> MediaProcessor:
    normalized = str(model or DEFAULT_MODEL).strip()
    if normalized == "ui-tars-local" or normalized.startswith("UI-TARS"):
        return MediaProcessor(MediaConfig(backend=BACKEND_UI_TARS_LOCAL, model=normalized))
    if normalized == "qwen-local" or normalized.startswith("Qwen"):
        return MediaProcessor(MediaConfig(backend=BACKEND_QWEN_LOCAL, model=normalized))
    provider = resolve_model_provider(normalized)
    if provider == PROVIDER_OPENAI:
        return MediaProcessor(MediaConfig(backend=BACKEND_OPENAI, model=normalized))
    if provider == PROVIDER_KIMI:
        return MediaProcessor(MediaConfig(backend=BACKEND_KIMI, model=normalized))
    if provider == PROVIDER_QWEN:
        return MediaProcessor(MediaConfig(backend=BACKEND_QWEN_CLOUD, model=normalized))
    return MediaProcessor(MediaConfig(backend=BACKEND_SONNET, model=normalized or DEFAULT_MODEL))


def _infer_media_backend(model_name: str) -> str:
    normalized = str(model_name or "").strip()
    if normalized == "ui-tars-local" or normalized.startswith("UI-TARS"):
        return BACKEND_UI_TARS_LOCAL
    if normalized == "qwen-local" or normalized.startswith("Qwen"):
        return BACKEND_QWEN_LOCAL
    provider = resolve_model_provider(normalized)
    if provider == PROVIDER_OPENAI:
        return BACKEND_OPENAI
    if provider == PROVIDER_KIMI:
        return BACKEND_KIMI
    if provider == PROVIDER_QWEN:
        return BACKEND_QWEN_CLOUD
    return BACKEND_SONNET


def _make_site_media_for_model(model: str) -> MediaProcessor:
    configured_model = str(os.environ.get("FLOWLENS_SITE_MEDIA_MODEL", "")).strip()
    configured_backend = str(os.environ.get("FLOWLENS_SITE_MEDIA_BACKEND", "")).strip()
    configured_whisper = str(os.environ.get("FLOWLENS_SITE_MEDIA_WHISPER_MODEL", "")).strip()
    configured_image_dim = int(
        os.environ.get("FLOWLENS_SITE_MEDIA_IMAGE_MAX_DIM", DEFAULT_LOCAL_IMAGE_MAX_DIM) or DEFAULT_LOCAL_IMAGE_MAX_DIM
    )

    if configured_model or configured_backend:
        chosen_model = configured_model or model
        chosen_backend = configured_backend or _infer_media_backend(chosen_model)
        return MediaProcessor(
            MediaConfig(
                backend=chosen_backend,
                model=chosen_model,
                whisper_model=configured_whisper or DEFAULT_WHISPER_MODEL,
                local_image_max_dim=configured_image_dim,
            )
        )

    normalized = str(model or DEFAULT_MODEL).strip()
    if normalized == "ui-tars-local" or normalized.startswith("UI-TARS"):
        return MediaProcessor(MediaConfig(backend=BACKEND_UI_TARS_LOCAL, model=normalized))
    if normalized == "qwen-local" or normalized.startswith("Qwen"):
        return MediaProcessor(
            MediaConfig(
                backend=BACKEND_QWEN_LOCAL,
                model=normalized,
                whisper_model=DEFAULT_WHISPER_MODEL,
                local_image_max_dim=configured_image_dim,
            )
        )

    if resolve_model_provider(normalized) in {PROVIDER_OPENAI, PROVIDER_KIMI, PROVIDER_QWEN}:
        return _make_media_for_model(model)

    preferred_local_model = "Qwen3.5-0.8B-8bit"
    if LocalLLM.is_available(preferred_local_model):
        return MediaProcessor(
            MediaConfig(
                backend=BACKEND_QWEN_LOCAL,
                model=preferred_local_model,
                whisper_model=DEFAULT_WHISPER_MODEL,
                local_image_max_dim=configured_image_dim,
            )
        )
    return _make_media_for_model(model)


def _recent_manual_fallback_allowed(messages: list[dict]) -> bool:
    recent = messages[-6:]
    for msg in recent:
        content = msg.get("content")
        text = str(content)
        if '"manual_fallback_allowed": true' in text:
            return True
    return False


def _select_active_tools(
    tools: list[Tool],
    *,
    site_name: str | None,
    page_state: str | None,
    task: str,
    messages: list[dict],
    active_capability_packs: set[str] | None = None,
) -> list[Tool]:
    active_pack_ids = set(active_capability_packs or set())
    pack_filtered = [
        tool
        for tool in tools
        if tool.always_available
        or not str(getattr(tool, "capability_pack", "") or "").strip()
        or not active_pack_ids
        or str(getattr(tool, "capability_pack", "") or "").strip() in active_pack_ids
    ]
    active_names = profile_active_tool_names(
        site_name,
        page_state,
        manual_allowed=_recent_manual_fallback_allowed(messages),
    )
    if not active_names:
        return pack_filtered

    selected = [tool for tool in pack_filtered if tool.name in active_names or tool.always_available]
    return selected or pack_filtered


async def _execute_tool(
    tool: Tool,
    params: dict,
    ctx: ToolContext,
) -> list[dict]:
    """Execute a tool and return Anthropic content blocks."""
    result = await tool.execute(params, ctx)

    if isinstance(result, str):
        return [{"type": "text", "text": result}]
    if isinstance(result, list):
        return result
    return [{"type": "text", "text": str(result)}]


async def run_agent(
    task: str,
    *,
    bridge: ExtensionBridge | TabBridge | None = None,
    run_dir: str | Path | None = None,
    max_turns: int = 30,
    model: str = "claude-sonnet-4-6",
    extra_instructions: str = "",
    start_url: str | None = None,
    use_browser: bool = True,
    initial_capability_packs: set[str] | list[str] | tuple[str, ...] | None = None,
    media=None,
    log_callback=None,
) -> dict:
    """Run the agent loop for a browser task.

    Args:
        task: Natural language task description.
        bridge: Browser bridge (created if not provided).
        run_dir: Directory for screenshots and artifacts.
        max_turns: Maximum LLM turns before stopping.
        model: Anthropic model ID.
        extra_instructions: Additional instructions appended to system prompt.
        media: MediaProcessor for vision tools (optional).
        log_callback: Optional callback(event, detail) for logging.

    Returns:
        dict with keys: result (str), screenshots (list), turns (int),
        run_dir (str), reasoning_log (str path)
    """
    # Setup run directory
    if run_dir is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        slug = task[:40].replace(" ", "_").replace("/", "_")
        run_dir = task_runs_root() / f"agent_{ts}_{slug}"
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    def log(event: str, detail: str = ""):
        if log_callback:
            log_callback(event, detail)
        else:
            print(f"  [agent] {event}: {detail}")

    if media is None:
        media = _make_media_for_model(model)

    # Setup bridge if not provided
    own_bridge = False
    if bridge is None and use_browser:
        bridge = ExtensionBridge()
        await bridge.start()
        await ensure_extension_connection(bridge)
        own_bridge = True

    # Create a dedicated agent window so we don't hijack the user's tabs
    agent_window_id = None
    agent_tab_id = None
    if use_browser and isinstance(bridge, ExtensionBridge):
        try:
            initial_url = start_url or default_start_url_for_task(task) or "about:blank"
            win = await bridge.create_background_window(url=initial_url, focused=False)
            agent_window_id = win.get("windowId")
            agent_tab_id = win.get("tabId")
            log("window", f"Created agent window {agent_window_id}, tab {agent_tab_id}")
        except Exception as e:
            log("window_error", f"Could not create agent window: {e} — using active tab")

    try:
        # If we created a dedicated tab, wrap bridge to scope to that tab
        scoped_bridge: ExtensionBridge | TabBridge | None = bridge
        if agent_tab_id and isinstance(bridge, ExtensionBridge):
            scoped_bridge = bridge.tab(agent_tab_id, window_id=agent_window_id)

        return await _agent_loop(
            task=task,
            bridge=scoped_bridge,
            run_dir=run_dir,
            max_turns=max_turns,
            model=model,
            extra_instructions=extra_instructions,
            initial_capability_packs=set(initial_capability_packs or set()),
            media=media,
            log=log,
            own_bridge=own_bridge,
            parent_bridge=bridge if isinstance(scoped_bridge, TabBridge) else None,
            agent_window_id=agent_window_id,
        )
    finally:
        cleanup_status: dict = {"stage": "cleanup"}
        # Clean up agent window
        if agent_window_id and isinstance(bridge, ExtensionBridge):
            try:
                await asyncio.shield(bridge.close_window(agent_window_id))
                log("cleanup", f"Closed agent window {agent_window_id}")
                cleanup_status["close_window"] = "ok"
            except BaseException as e:
                cleanup_status["close_window"] = "error"
                cleanup_status["close_window_error"] = str(e)
        if own_bridge:
            try:
                await asyncio.shield(bridge.stop())
                cleanup_status["bridge_stop"] = "ok"
            except BaseException as e:
                cleanup_status["bridge_stop"] = "error"
                cleanup_status["bridge_stop_error"] = str(e)
        try:
            snapshot = system_resource_snapshot(agent_window_id=agent_window_id)
            snapshot.update(cleanup_status)
            append_jsonl(run_dir / "resource_log.jsonl", snapshot)
        except Exception:
            pass


async def _agent_loop(
    *,
    task: str,
    bridge: ExtensionBridge | TabBridge | None,
    run_dir: Path,
    max_turns: int,
    model: str,
    extra_instructions: str,
    initial_capability_packs: set[str],
    media,
    log,
    own_bridge: bool = False,
    parent_bridge: ExtensionBridge | None = None,
    agent_window_id: int | None = None,
) -> dict:
    run_state = RunState(run_dir=run_dir, task=task, model=model)
    ctx = ToolContext(run_dir=run_dir, run_state=run_state, active_capability_packs=set(initial_capability_packs))

    # Downscale screenshots for local models (768px max dim → ~3s per image)
    is_local = (
        model in {"qwen-local", "ui-tars-local"}
        or model.startswith("Qwen")
        or model.startswith("UI-TARS")
    )
    if is_local:
        ctx.screenshot_max_dim = 768

    # The parent_bridge (or bridge itself if ExtensionBridge) is needed for
    # extension-level commands like extract_page_data and watch_log
    ext_bridge: ExtensionBridge | None = parent_bridge
    if ext_bridge is None and isinstance(bridge, ExtensionBridge):
        ext_bridge = bridge

    # Build tools from the unified registry (same surface as flowlens-mcp)
    site_media = _make_site_media_for_model(model) if ext_bridge is not None else None
    tools: list[Tool] = build_tools(
        bridge,
        ext_bridge=ext_bridge,
        media=media,
        site_media=site_media,
        include_browser=bridge is not None,
    )

    # ── Watch mode overlay helper ──────────────────────────────
    async def watch(level: str, message: str, **kwargs):
        """Send a log entry to the Chrome extension's watch overlay."""
        if ext_bridge is None:
            return
        try:
            await ext_bridge.watch_log(level, message, **kwargs)
        except Exception:
            pass  # Watch is best-effort

    # Enable watch mode if available
    if ext_bridge:
        try:
            await ext_bridge.enable_watch_mode()
        except Exception:
            pass

    # Initialize messages
    messages = [{"role": "user", "content": task}]

    site_name = None
    page_state = None
    site_knowledge = ""
    active_tools = tools
    api_tools = [t.to_api_schema() for t in active_tools]
    active_tool_names_logged: tuple[str, ...] = tuple()
    capability_overview = _capability_overview(tools, active_packs=ctx.active_capability_packs)
    system_prompt = _build_system_prompt(active_tools, capability_overview, site_knowledge, extra_instructions)

    backend = create_backend(model)
    screenshots = []
    turn = 0
    final_text = ""
    context_memory: list[str] = []
    report_grounding_requested = False
    report_grounding_observed = False
    last_state_read_turn = 0

    # ── Detailed reasoning log ──────────────────────────────────
    reasoning_log: list[dict] = []
    resource_snapshots: list[dict] = []
    task_start_time = time.time()
    # Rolling tally of (tool, input) signatures — used to surface loop-like
    # behavior in tool_result entries. Each entry records turn + repeat count
    # so debuggers can spot A/B oscillation without replaying the whole log.
    tool_call_signatures: dict[str, list[int]] = {}
    last_compaction_turn: int = 0

    def log_entry(entry: dict):
        entry["elapsed_s"] = round(time.time() - task_start_time, 2)
        entry["timestamp"] = datetime.now().isoformat()
        reasoning_log.append(entry)

    def resource_entry(stage: str, *, turn_number: int | None = None, extra: dict | None = None):
        try:
            snapshot = system_resource_snapshot(agent_window_id=agent_window_id)
        except Exception as e:
            snapshot = {"timestamp": datetime.now().isoformat(), "resource_error": str(e)}
        snapshot["stage"] = stage
        if turn_number is not None:
            snapshot["turn"] = turn_number
        if extra:
            snapshot.update(extra)
        resource_snapshots.append(snapshot)
        append_jsonl(run_dir / "resource_log.jsonl", snapshot)

    runtime_env = _runtime_environment(model)
    log_entry({
        "type": "task_start",
        "task": task,
        "model": model,
        "max_turns": max_turns,
        "tools": [t.name for t in tools],
        "site_knowledge_loaded": bool(site_knowledge),
        "runtime": runtime_env,
    })
    log(
        "env",
        f"flowlens={runtime_env.get('flowlens_version') or '?'} "
        f"git={runtime_env.get('git_commit') or '?'} "
        f"backend={runtime_env.get('backend_provider') or '?'}",
    )
    resource_entry("task_start", extra={"model": model, "max_turns": max_turns})

    log("start", f"Task: {task}")
    log("tools", f"Available: {[t.name for t in tools]}")
    await watch("session", f"Task started: {task[:120]}", phase="start")

    while turn < max_turns:
        turn += 1
        turn_start = time.time()
        log("turn", f"Turn {turn}/{max_turns}")
        await watch("info", f"Turn {turn}/{max_turns}", phase="turn")
        resource_entry("turn_start", turn_number=turn)

        current_url = ""
        try:
            if bridge is not None:
                info = await bridge.get_tab_info()
                current_url = str(info.get("url") or "")
        except Exception:
            current_url = ""
        detected_site = detect_site(current_url) if current_url else None
        detected_state = None
        state_command = state_command_for_site(detected_site)
        if state_command and ext_bridge is not None:
            try:
                detected = await ext_bridge.send_command(state_command)
                detected_state = str(detected.get("state") or "")
            except Exception:
                detected_state = None
        site_name = detected_site
        page_state = detected_state
        site_knowledge = get_knowledge_for_url(current_url, page_state=page_state) if current_url else ""
        dynamic_extra = profile_dynamic_extra_instructions(task, site_name, page_state)
        combined_extra = "\n\n".join(part for part in [extra_instructions, dynamic_extra] if part)
        active_tools = _select_active_tools(
            tools,
            site_name=site_name,
            page_state=page_state,
            task=task,
            messages=messages,
            active_capability_packs=ctx.active_capability_packs,
        )
        api_tools = [t.to_api_schema() for t in active_tools]
        capability_overview = _capability_overview(tools, active_packs=ctx.active_capability_packs)
        system_prompt = _build_system_prompt(active_tools, capability_overview, site_knowledge, combined_extra)

        active_tool_names = tuple(tool.name for tool in active_tools)
        if active_tool_names != active_tool_names_logged:
            active_tool_names_logged = active_tool_names
            log("tools", f"Active: {list(active_tool_names)}")
            log_entry({
                "type": "toolset_update",
                "turn": turn,
                "site": site_name,
                "page_state": page_state,
                "tools": list(active_tool_names),
            })

        # Call LLM backend
        api_start = time.time()
        compaction_info: dict = {}
        if hasattr(backend, "_previous_response_id"):
            request_messages = messages
        else:
            request_messages = _prepare_messages_for_context(
                messages, run_state, context_memory, compaction_info=compaction_info,
            )
        if compaction_info.get("compacted") and turn != last_compaction_turn:
            last_compaction_turn = turn
            log_entry({
                "type": "context_compacted",
                "turn": turn,
                **{k: v for k, v in compaction_info.items() if k != "compacted"},
            })
        try:
            response = backend.create_message(
                system=system_prompt,
                messages=request_messages,
                tools=api_tools,
                max_tokens=8192,
            )
        except Exception as e:
            log("api_error", str(e))
            log_entry({
                "type": "api_error",
                "turn": turn,
                "error": str(e),
            })
            final_text = f"API error: {e}"
            break

        api_duration = round(time.time() - api_start, 2)

        # Process response — use normalized LLMResponse
        messages.append({"role": "assistant", "content": backend.format_assistant_content(response)})

        tool_use_blocks = response.tool_calls
        text_blocks = response.text_blocks

        # Log LLM thinking separately from user-visible text so local-model
        # chain-of-thought does not leak into reports or message history.
        thinking_texts = []
        visible_texts = []
        for text in text_blocks:
            if text.startswith("[Thinking] "):
                thinking = text[len("[Thinking] "):]
                thinking_texts.append(thinking)
            else:
                visible_texts.append(text)
                final_text = text

        needs_grounding_request = (
            not tool_use_blocks
            and bool(visible_texts)
            and run_state.has_structured_state()
            and not report_grounding_requested
            and (turn - last_state_read_turn) > 1
        )

        for _thinking in thinking_texts:
            await watch("think", "Agent is planning the next step.", phase="thinking")

        if needs_grounding_request and visible_texts:
            draft_text = "\n".join(visible_texts)
            log("draft_report", _log_excerpt(draft_text))
            await watch(
                "info",
                "Draft report ready; grounding against saved state before finalizing.",
                phase="grounding",
            )
        else:
            for text in visible_texts:
                log("text", _log_excerpt(text))
                await watch("think", text[:240], phase="thinking", decision=text[:500])

        usage_entry: dict = {
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
        }
        # Local backend exposes prefill/decode breakdown; forward it so we
        # can diagnose why later turns slow down (prefill dominates with long
        # message histories).
        if response.metrics:
            usage_entry.update({
                "prefill_tokens": response.metrics.get("prompt_tokens"),
                "prefill_s": response.metrics.get("prefill_s"),
                "prefill_tps": response.metrics.get("prompt_tps"),
                "generation_tokens": response.metrics.get("generation_tokens"),
                "generation_s": response.metrics.get("generation_s"),
                "generation_tps": response.metrics.get("generation_tps"),
            })
        llm_entry: dict = {
            "type": "llm_response",
            "turn": turn,
            "api_duration_s": api_duration,
            "context_messages": len(request_messages),
            "raw_context_messages": len(messages),
            "stop_reason": response.stop_reason,
            "usage": usage_entry,
        }
        if thinking_texts:
            llm_entry["thinking"] = "\n".join(thinking_texts)
        if visible_texts:
            llm_entry["text"] = "\n".join(visible_texts)
        if tool_use_blocks:
            llm_entry["tool_calls"] = [
                {"name": tu.name, "input": tu.input}
                for tu in tool_use_blocks
            ]
        log_entry(llm_entry)
        run_state.note_assistant_turn(
            turn=turn,
            text="\n".join(visible_texts or text_blocks),
            tool_calls=[
                {"name": tu.name, "input": tu.input}
                for tu in tool_use_blocks
            ] if tool_use_blocks else [],
        )

        if not tool_use_blocks:
            if visible_texts:
                final_text = "\n".join(visible_texts)
            if needs_grounding_request and final_text:
                report_grounding_requested = True
                grounding_request = _build_report_grounding_request(run_state)
                messages.append({"role": "user", "content": grounding_request})
                log("grounding", "Requesting a run-state grounding pass before final report")
                log_entry({
                    "type": "report_grounding_requested",
                    "turn": turn,
                    "used_state_tools": report_grounding_observed,
                })
                await watch("info", "Grounding final report against saved run state.", phase="grounding")
                continue
            log("done", f"Task complete after {turn} turns")
            log_entry({
                "type": "task_complete",
                "turn": turn,
                "grounded_with_state_tools": report_grounding_observed,
            })
            break

        # Execute tools and collect results
        tool_map = {t.name: t for t in active_tools}
        all_results: list[list[dict]] = []
        for tu in tool_use_blocks:
            tool_name = tu.name
            tool_input = tu.input
            ctx.turn = turn
            ctx.active_tool_name = tool_name

            signature = _tool_call_signature(tool_name, tool_input)
            history = tool_call_signatures.setdefault(signature, [])
            history.append(turn)
            repeat_count = len(history)

            log("tool_call", f"{tool_name}({json.dumps(tool_input, ensure_ascii=False)[:200]})")
            if repeat_count >= 3:
                log(
                    "repeat_warning",
                    f"{tool_name} called {repeat_count}x with identical args (turns {history})",
                )
                log_entry({
                    "type": "repeat_tool_call",
                    "turn": turn,
                    "tool": tool_name,
                    "signature": signature,
                    "repeat_count": repeat_count,
                    "turns": list(history),
                })
            run_state.note_tool_call(turn=turn, tool_name=tool_name, tool_input=tool_input)
            if tool_name in {"read_run_state", "read_saved_artifact"}:
                report_grounding_observed = True
                last_state_read_turn = turn
            await watch("action", f"Calling {tool_name}", phase="tool",
                        action_name=tool_name,
                        detail=json.dumps(tool_input, ensure_ascii=False)[:200])

            tool = tool_map.get(tool_name)
            if tool is None:
                result_content = [{"type": "text", "text": f"Error: Unknown tool '{tool_name}'"}]
                log_entry({
                    "type": "tool_error",
                    "turn": turn,
                    "tool": tool_name,
                    "error": f"Unknown tool '{tool_name}'",
                })
            else:
                tool_start = time.time()
                try:
                    before_root_images = {
                        path.name
                        for path in run_dir.iterdir()
                        if path.is_file() and path.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")
                    }
                    result_content = await _execute_tool(tool, tool_input, ctx)

                    tool_duration = round(time.time() - tool_start, 2)

                    # Track any new top-level screenshots generated by tools
                    after_root_images = sorted(
                        path for path in run_dir.iterdir()
                        if path.is_file() and path.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")
                    )
                    for img in after_root_images:
                        if img.name in before_root_images:
                            continue
                        img_str = str(img)
                        if img_str not in screenshots:
                            screenshots.append(img_str)
                        ctx.register_artifact(
                            img,
                            label=img.stem,
                            artifact_kind="image",
                            summary=f"Screenshot captured by {tool_name}",
                            metadata={"category": "screenshot"},
                            source_tool=tool_name,
                        )

                    result_entry = {
                        "type": "tool_result",
                        "turn": turn,
                        "tool": tool_name,
                        "input": tool_input,
                        "duration_s": tool_duration,
                        "result_summary": _text_summary(result_content),
                    }
                    if repeat_count > 1:
                        result_entry["repeat_count"] = repeat_count
                    processed = getattr(ctx, "processed_notes", None)
                    if isinstance(processed, dict) and processed:
                        # Compact snapshot keyed by note_id so loop-style fixations
                        # on a single note_id (see A/B dedup oscillation) are
                        # visible without cross-referencing every artifact.
                        result_entry["processed_notes_snapshot"] = {
                            nid: {
                                "level": info.get("level", ""),
                                "include_media": bool(info.get("include_media", False)),
                            }
                            for nid, info in list(processed.items())[-10:]
                            if isinstance(info, dict)
                        }
                    log_entry(result_entry)
                    memory_input = json.dumps(tool_input, ensure_ascii=False)[:160]
                    context_memory.append(
                        f"- turn {turn} {tool_name}({memory_input}): "
                        f"{_text_summary(result_content, max_len=900)}"
                    )
                    if len(context_memory) > 80:
                        context_memory = context_memory[-80:]
                    run_state.note_tool_result(
                        turn=turn,
                        tool_name=tool_name,
                        tool_input=tool_input,
                        result_summary=_text_summary(result_content, max_len=900),
                        duration_s=tool_duration,
                    )
                    await watch(
                        "result",
                        _text_summary(result_content)[:360],
                        phase="tool_result",
                        action_name=tool_name,
                        duration=tool_duration,
                    )
                except Exception as e:
                    log("tool_error", f"{tool_name}: {e}")
                    result_content = [{"type": "text", "text": f"Error executing {tool_name}: {e}"}]
                    log_entry({
                        "type": "tool_error",
                        "turn": turn,
                        "tool": tool_name,
                        "input": tool_input,
                        "error": str(e),
                    })
                    await watch(
                        "error",
                        f"{tool_name}: {e}",
                        phase="tool_error",
                        action_name=tool_name,
                    )
                finally:
                    ctx.active_tool_name = ""

            all_results.append(result_content)
            ctx.active_tool_name = ""

        # Format tool results in backend-specific format and append
        messages.append(backend.format_tool_results(tool_use_blocks, all_results))

        # Small delay between turns for rate limiting
        await asyncio.sleep(0.5)
        resource_entry("turn_end", turn_number=turn, extra={"turn_duration_s": round(time.time() - turn_start, 2)})

    if turn >= max_turns:
        log("max_turns", f"Reached maximum {max_turns} turns — requesting final report")
        log_entry({"type": "max_turns_reached", "turn": turn})

        # Ask the LLM for a final report summarizing everything gathered
        messages.append({
            "role": "user",
            "content": (
                "You've reached the maximum number of turns. Based on everything "
                "you've gathered so far, please write your comprehensive final report "
                "now. Ground the report against the saved run state summary below. "
                "Include all findings, screenshots (using ![desc](filename.jpg) "
                "syntax), and analysis.\n\n"
                f"{run_state.report_grounding_context(max_chars=5000)}"
            ),
        })
        try:
            request_messages = (
                messages
                if hasattr(backend, "_previous_response_id")
                else _prepare_messages_for_context(messages, run_state, context_memory)
            )
            summary_resp = backend.create_message(
                system=system_prompt,
                messages=request_messages,
                tools=[],  # no tools — force text-only response
                max_tokens=8192,
            )
            if summary_resp.text_blocks:
                visible_summary = [
                    text for text in summary_resp.text_blocks
                    if not text.startswith("[Thinking] ")
                ]
                final_text = "\n".join(visible_summary or summary_resp.text_blocks)
                log("final_report", f"Generated {len(final_text)} chars")
                log_entry({
                    "type": "final_report_generated",
                    "turn": turn,
                    "chars": len(final_text),
                })
        except Exception as e:
            log("report_error", f"Could not generate final report: {e}")

    # ── Save outputs ────────────────────────────────────────────

    total_duration = round(time.time() - task_start_time, 2)
    resource_entry("task_end", turn_number=turn, extra={"total_duration_s": total_duration})

    # Collect site result artifacts before rendering the final markdown so we can
    # add reliable local screenshot evidence even when the model omits it.
    site_results = _collect_site_results(run_dir)
    final_text = run_state.render_markdown_appendix(final_text)
    final_text = append_report_extras(final_text, site_results)

    # Save final report
    report_path = run_dir / "report.md"
    report_path.write_text(final_text, encoding="utf-8")
    log("report", f"Saved to {report_path}")

    # Save detailed reasoning log
    reasoning_log_path = run_dir / "reasoning_log.jsonl"
    with open(reasoning_log_path, "w", encoding="utf-8") as f:
        for entry in reasoning_log:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # Save summary metadata
    log_path = run_dir / "agent_log.json"
    log_data = {
        "task": task,
        "model": model,
        "turns": turn,
        "total_duration_s": total_duration,
        "screenshots": screenshots,
        "run_dir": str(run_dir),
        "run_state_dir": str(run_state.state_dir),
        "timestamp": datetime.now().isoformat(),
        "reasoning_log_file": "reasoning_log.jsonl",
        "resource_log_file": "resource_log.jsonl",
        "usage_summary": _summarize_usage(reasoning_log),
        "resource_summary": _summarize_resources(resource_snapshots),
        "site_results": site_results,
    }
    log_path.write_text(json.dumps(log_data, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── Cleanup agent-created tabs ──────────────────────────────
    # (Agent doesn't create its own windows/tabs currently, but if
    #  it does in future via create_background_window, track and close them here)

    return {
        "result": final_text,
        "screenshots": screenshots,
        "turns": turn,
        "run_dir": str(run_dir),
        "run_state_dir": str(run_state.state_dir),
        "reasoning_log": str(reasoning_log_path),
        "total_duration_s": total_duration,
        "site_results": site_results,
    }


def _collect_site_results(run_dir: Path) -> list[dict]:
    """Collect all site result JSON artifacts produced during the agent run."""
    results_dir = run_dir / "site_results"
    if not results_dir.is_dir():
        return []
    collected = []
    for path in sorted(results_dir.iterdir()):
        if path.suffix != ".json" or not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            data["_source_file"] = path.name
            collected.append(data)
        except Exception:
            continue
    return collected


def _summarize_usage(reasoning_log: list[dict]) -> dict:
    """Summarize token usage and timing from the reasoning log."""
    total_input = 0
    total_output = 0
    api_time = 0.0
    tool_time = 0.0
    tool_counts: dict[str, int] = {}

    for entry in reasoning_log:
        if entry.get("type") == "llm_response":
            usage = entry.get("usage", {})
            total_input += usage.get("input_tokens", 0)
            total_output += usage.get("output_tokens", 0)
            api_time += entry.get("api_duration_s", 0)
            for tc in entry.get("tool_calls") or []:
                name = tc.get("name", "unknown")
                tool_counts[name] = tool_counts.get(name, 0) + 1
        elif entry.get("type") == "tool_result":
            tool_time += entry.get("duration_s", 0)

    return {
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "total_api_time_s": round(api_time, 2),
        "total_tool_time_s": round(tool_time, 2),
        "tool_call_counts": tool_counts,
    }


def _summarize_resources(resource_snapshots: list[dict]) -> dict:
    max_windowserver = 0.0
    max_chrome_windows = 0
    max_current_rss = 0.0
    max_observer_rss = 0.0
    for snapshot in resource_snapshots:
        windowserver = snapshot.get("windowserver") or {}
        chrome = snapshot.get("chrome") or {}
        current = snapshot.get("current_process") or {}
        observer = snapshot.get("observer") or {}
        max_windowserver = max(max_windowserver, float(windowserver.get("footprint_mb") or 0.0))
        max_chrome_windows = max(max_chrome_windows, int(chrome.get("window_count") or 0))
        max_current_rss = max(max_current_rss, float(current.get("rss_mb") or 0.0))
        max_observer_rss = max(max_observer_rss, float(observer.get("rss_mb") or 0.0))
    return {
        "snapshots": len(resource_snapshots),
        "max_windowserver_footprint_mb": round(max_windowserver, 2),
        "max_chrome_window_count": max_chrome_windows,
        "max_current_process_rss_mb": round(max_current_rss, 2),
        "max_observer_rss_mb": round(max_observer_rss, 2),
    }
