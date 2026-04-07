"""Core agent loop — LLM-driven browser automation.

The loop sends messages + tools to an LLM backend (Anthropic API or local
Qwen MLX). When the LLM returns tool calls, we execute them and feed results
back. When the LLM returns only text, the task is complete.

Supports two backends:
- Anthropic (claude-sonnet-4-6 etc.) — native tool_use API
- Local Qwen (qwen-local) — text-based tool calling via <tool_call> tags
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from pathlib import Path

from ..core.bridge import ExtensionBridge, TabBridge, ensure_extension_connection
from ..core.process_metrics import append_jsonl, system_resource_snapshot
from ..knowledge.loader import get_knowledge_for_url
from ..perception.media import (
    BACKEND_QWEN_LOCAL,
    BACKEND_SONNET,
    BACKEND_UI_TARS_LOCAL,
    DEFAULT_MODEL,
    MediaConfig,
    MediaProcessor,
)
from .backends import Backend, create_backend
from .tool import Tool, ToolContext
from .tools.browser import make_browser_tools
from .tools.site import ExtractSiteEntityTool, RunSiteActionTool
from .tools.vision import AnalyzeScreenshotTool, OcrScreenshotTool


_BASE_SYSTEM_PROMPT = """\
You are a browser automation agent. You control a real Chrome browser through \
tools to accomplish tasks on websites.

## How to work

1. **Plan first**: Before acting, briefly state your plan for the overall task. \
Break the task into logical steps. Revise the plan as you learn more.
2. **Observe**: Take a screenshot and prefer site-specific extractors when they exist. Use read_page only when there is no site-specific extractor for the current need or when you need exact coordinates for a manual fallback.
3. **Act**: Execute one action, then observe the result.
4. **Verify**: After important actions, take a screenshot to confirm they worked.
5. **Report**: When done, write a comprehensive, detailed report.

## Important rules

- Always take a screenshot at the start to see the current page.
- **Before manual clicking on generic pages**: Use read_page to find exact \
element coordinates. Do NOT guess coordinates from screenshots alone — use the \
(x,y) coordinates from read_page elements, clicking at the center of the \
element's bounding box.
- Prefer using site-specific extractors (extract_page_data) when available — \
they are faster and more reliable than reading raw DOM or manually clicking UI.
- Prefer `run_site_action` for common site flows like search + open + read_note \
on supported sites.
- Prefer `extract_site_entity` for full note/profile extraction when you are \
already on the relevant page state. Use raw `extract_page_data` only for \
low-level actions and DOM-only helpers.
- When site knowledge marks a command as PREFERRED, use that command before \
trying read_page + click/type fallbacks.
- Use the wait tool between actions to add natural delays (2-5 seconds). \
This is critical for avoiding anti-bot detection.
- If something goes wrong, take a screenshot to diagnose before retrying.
- If search results show "没找到相关内容" or similar empty state, wait 5-10 \
seconds and try again with a simpler query. This is usually anti-bot throttling.
- When using run_javascript, your code is executed via `new Function(code)()`. \
You MUST use `return` to get a value back. For example: \
`return document.title` or `return (function() { ... })()`.
- **Take a screenshot of every important page state** — especially note/article \
detail views. These screenshots will be included in the final report.

## Thinking out loud

At each turn, briefly explain:
- What you observe on the page
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
    site_knowledge: str = "",
    extra_instructions: str = "",
) -> str:
    parts = [_BASE_SYSTEM_PROMPT]

    if site_knowledge:
        parts.append(f"\n## Site Knowledge\n\n{site_knowledge}")

    if extra_instructions:
        parts.append(f"\n## Additional Instructions\n\n{extra_instructions}")

    return "\n".join(parts)


def _text_summary(content_blocks: list[dict], max_len: int = 500) -> str:
    """Summarize tool result content blocks for logging (omit base64 images)."""
    parts = []
    for block in content_blocks:
        if block.get("type") == "text":
            parts.append(block["text"][:max_len])
        elif block.get("type") == "image":
            parts.append("[image]")
    return " | ".join(parts)[:max_len]


def _make_media_for_model(model: str) -> MediaProcessor:
    normalized = str(model or DEFAULT_MODEL).strip()
    if normalized == "ui-tars-local" or normalized.startswith("UI-TARS"):
        return MediaProcessor(MediaConfig(backend=BACKEND_UI_TARS_LOCAL, model=normalized))
    if normalized == "qwen-local" or normalized.startswith("Qwen"):
        return MediaProcessor(MediaConfig(backend=BACKEND_QWEN_LOCAL, model=normalized))
    return MediaProcessor(MediaConfig(backend=BACKEND_SONNET, model=normalized or DEFAULT_MODEL))


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
    max_turns: int = 40,
    model: str = "claude-sonnet-4-6",
    extra_instructions: str = "",
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
        run_dir = Path("task_runs") / f"agent_{ts}_{slug}"
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
    if bridge is None:
        bridge = ExtensionBridge()
        await bridge.start()
        await ensure_extension_connection(bridge)
        own_bridge = True

    # Create a dedicated agent window so we don't hijack the user's tabs
    agent_window_id = None
    agent_tab_id = None
    if isinstance(bridge, ExtensionBridge):
        try:
            win = await bridge.create_background_window(
                url="about:blank", focused=False
            )
            agent_window_id = win.get("windowId")
            agent_tab_id = win.get("tabId")
            log("window", f"Created agent window {agent_window_id}, tab {agent_tab_id}")
        except Exception as e:
            log("window_error", f"Could not create agent window: {e} — using active tab")

    try:
        # If we created a dedicated tab, wrap bridge to scope to that tab
        scoped_bridge: ExtensionBridge | TabBridge = bridge
        if agent_tab_id and isinstance(bridge, ExtensionBridge):
            scoped_bridge = bridge.tab(agent_tab_id, window_id=agent_window_id)

        return await _agent_loop(
            task=task,
            bridge=scoped_bridge,
            run_dir=run_dir,
            max_turns=max_turns,
            model=model,
            extra_instructions=extra_instructions,
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
    bridge: ExtensionBridge | TabBridge,
    run_dir: Path,
    max_turns: int,
    model: str,
    extra_instructions: str,
    media,
    log,
    own_bridge: bool = False,
    parent_bridge: ExtensionBridge | None = None,
    agent_window_id: int | None = None,
) -> dict:
    ctx = ToolContext(run_dir=run_dir)

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

    # Build tools — extract_page_data needs the ExtensionBridge for send_command
    tools: list[Tool] = make_browser_tools(bridge, ext_bridge=ext_bridge)
    if media:
        tools.append(AnalyzeScreenshotTool(media=media))
        tools.append(OcrScreenshotTool(media=media))
    if media and ext_bridge is not None:
        tools.append(RunSiteActionTool(bridge, ext_bridge=ext_bridge, media=media))
        tools.append(ExtractSiteEntityTool(bridge, ext_bridge=ext_bridge, media=media))

    tool_map = {t.name: t for t in tools}
    api_tools = [t.to_api_schema() for t in tools]

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

    # Get initial site knowledge from current tab
    site_knowledge = ""
    try:
        info = await bridge.get_tab_info()
        current_url = info.get("url", "")
        if current_url:
            site_knowledge = get_knowledge_for_url(current_url)
    except Exception:
        pass

    system_prompt = _build_system_prompt(tools, site_knowledge, extra_instructions)

    # Initialize messages
    messages = [{"role": "user", "content": task}]

    backend = create_backend(model)
    screenshots = []
    turn = 0
    final_text = ""

    # ── Detailed reasoning log ──────────────────────────────────
    reasoning_log: list[dict] = []
    resource_snapshots: list[dict] = []
    task_start_time = time.time()

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

    log_entry({
        "type": "task_start",
        "task": task,
        "model": model,
        "max_turns": max_turns,
        "tools": [t.name for t in tools],
        "site_knowledge_loaded": bool(site_knowledge),
    })
    resource_entry("task_start", extra={"model": model, "max_turns": max_turns})

    log("start", f"Task: {task}")
    log("tools", f"Available: {[t.name for t in tools]}")
    await watch("info", f"Task started: {task[:80]}", phase="start")

    while turn < max_turns:
        turn += 1
        turn_start = time.time()
        log("turn", f"Turn {turn}/{max_turns}")
        await watch("info", f"Turn {turn}/{max_turns}", phase="turn")
        resource_entry("turn_start", turn_number=turn)

        # Call LLM backend
        api_start = time.time()
        try:
            response = backend.create_message(
                system=system_prompt,
                messages=messages,
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
            log("text", text[:200])
            if text.startswith("[Thinking] "):
                thinking = text[len("[Thinking] "):]
                thinking_texts.append(thinking)
                await watch("info", thinking[:200], phase="thinking",
                            reasoning=thinking[:500])
            else:
                visible_texts.append(text)
                final_text = text
                await watch("info", text[:200], phase="thinking",
                            reasoning=text[:500])

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
        log_entry({
            "type": "llm_response",
            "turn": turn,
            "api_duration_s": api_duration,
            "thinking": "\n".join(thinking_texts) if thinking_texts else None,
            "text": "\n".join(visible_texts) if visible_texts else None,
            "tool_calls": [
                {"name": tu.name, "input": tu.input}
                for tu in tool_use_blocks
            ] if tool_use_blocks else None,
            "stop_reason": response.stop_reason,
            "usage": usage_entry,
        })

        if not tool_use_blocks:
            if visible_texts:
                final_text = "\n".join(visible_texts)
            log("done", f"Task complete after {turn} turns")
            log_entry({"type": "task_complete", "turn": turn})
            break

        # Execute tools and collect results
        all_results: list[list[dict]] = []
        for tu in tool_use_blocks:
            tool_name = tu.name
            tool_input = tu.input

            log("tool_call", f"{tool_name}({json.dumps(tool_input, ensure_ascii=False)[:200]})")
            await watch("info", f"🔧 {tool_name}", phase="tool",
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
                    # Update site knowledge when navigating
                    if tool_name == "navigate":
                        url = tool_input.get("url", "")
                        new_knowledge = get_knowledge_for_url(url)
                        if new_knowledge and new_knowledge != site_knowledge:
                            site_knowledge = new_knowledge
                            system_prompt = _build_system_prompt(tools, site_knowledge, extra_instructions)
                            log("knowledge_update", f"Loaded knowledge for {url}")

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

                    log_entry({
                        "type": "tool_result",
                        "turn": turn,
                        "tool": tool_name,
                        "input": tool_input,
                        "duration_s": tool_duration,
                        "result_summary": _text_summary(result_content),
                    })
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

            all_results.append(result_content)

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
                "now. Include all findings, screenshots (using ![desc](filename.jpg) "
                "syntax), and analysis."
            ),
        })
        try:
            summary_resp = backend.create_message(
                system=system_prompt,
                messages=messages,
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
        "timestamp": datetime.now().isoformat(),
        "reasoning_log_file": "reasoning_log.jsonl",
        "resource_log_file": "resource_log.jsonl",
        "usage_summary": _summarize_usage(reasoning_log),
        "resource_summary": _summarize_resources(resource_snapshots),
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
        "reasoning_log": str(reasoning_log_path),
        "total_duration_s": total_duration,
    }


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
