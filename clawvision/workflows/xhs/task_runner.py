"""Task runner for high-value Xiaohongshu workflows.

Sits between generic task definitions / reasoning and concrete XHS workflows.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from dataclasses import dataclass
from datetime import datetime
from html import escape
from pathlib import Path

from ...core.bridge import ExtensionBridge, ensure_extension_connection
from ...core.recorder import SessionRecorder
from ...core.reporting import markdown_styles, render_markdown_block
from ...perception.media import MediaProcessor
from ...platforms.xhs.browser import XHSBrowser
from ...platforms.xhs.capabilities import capability_catalog_markdown, capabilities_for_task
from ...reasoning.task_agent import ExecutionStrategy, TaskAgent, TaskAssessment, TaskUnderstanding
from ...reasoning.tasks import StructuredTask, TaskKind
from .research import ResearchConfig, XHSResearchAgent
from .user_analysis import UserAnalysisConfig, XHSUserAnalyzer


SITE_CONTEXT = (
    "Xiaohongshu (小红书), a Chinese social media platform with search results, "
    "topic pages, user profiles, image posts, and video posts."
)


class ReasoningLog:
    """Captures agent observations and decisions for auditability.

    When a bridge is provided and watch mode is active, entries are also
    sent to the Chrome extension's watch panel sidebar in real-time.
    """

    def __init__(self, bridge: "ExtensionBridge | None" = None):
        self._entries: list[dict] = []
        self._t0 = time.time()
        self._bridge = bridge

    def think(self, phase: str, observation: str, reasoning: str, decision: str, evidence: str = ""):
        entry = {
            "timestamp": round(time.time() - self._t0, 1),
            "phase": phase,
            "observation": observation[:300],
            "reasoning": reasoning[:500],
            "decision": decision[:300],
            "evidence": evidence[:600],
        }
        self._entries.append(entry)
        print(f"  [{entry['timestamp']:6.1f}s] [{phase}] {decision[:120]}")

        # Pipe to watch panel if active
        if self._bridge and self._bridge.watch_mode:
            asyncio.ensure_future(self._bridge.watch_log(
                "think",
                decision[:300],
                phase=phase,
                observation=observation[:300],
                reasoning=reasoning[:500],
                decision=decision[:300],
                evidence=evidence[:600],
            ))

    @property
    def entries(self) -> list[dict]:
        return self._entries


class ActionLog:
    """Low-level execution log.

    When a bridge is provided and watch mode is active, entries are also
    sent to the Chrome extension's watch panel sidebar in real-time.
    """

    def __init__(self, bridge: "ExtensionBridge | None" = None):
        self._entries: list[dict] = []
        self._t0 = time.time()
        self._bridge = bridge

    def log(self, action: str, detail: str = "", duration: float | None = None):
        elapsed = round(time.time() - self._t0, 1)
        suffix = f" ({duration:.2f}s)" if duration is not None else ""
        print(f"  [{elapsed:6.1f}s] {action}{suffix}: {detail[:140]}")
        self._entries.append({
            "elapsed_s": elapsed,
            "action": action,
            "detail": detail[:500],
            "duration_s": round(duration, 2) if duration is not None else None,
        })

        # Pipe to watch panel if active
        if self._bridge and self._bridge.watch_mode:
            asyncio.ensure_future(self._bridge.watch_log(
                "action",
                detail[:200],
                action_name=action,
                detail=detail[:500],
                duration=duration,
            ))

    @property
    def entries(self) -> list[dict]:
        return self._entries


@dataclass
class TaskRunArtifacts:
    task_dir: Path
    workflow_dir: Path
    report_html: Path
    report_json: Path
    session_gif: Path


def _task_now_slug() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _safe_rel(path: str | Path, root: Path) -> str:
    p = Path(path)
    try:
        return str(p.relative_to(root))
    except ValueError:
        return str(p)


def _collect_topic_screenshots(report: dict) -> list[str]:
    paths: list[str] = []
    for screenshot in report.get("screenshots", []):
        if screenshot:
            paths.append(screenshot)
    for note in report.get("notes", [])[:3]:
        screenshot = note.get("screenshot")
        if screenshot:
            paths.append(screenshot)
    deduped: list[str] = []
    seen = set()
    for path in paths:
        if path and path not in seen:
            deduped.append(path)
            seen.add(path)
    return deduped


def _collect_creator_screenshots(report: dict) -> list[str]:
    paths: list[str] = []
    profile = report.get("profile", {})
    if profile.get("screenshot"):
        paths.append(profile["screenshot"])
    for note in report.get("detailed_notes", [])[:3]:
        screenshot = note.get("screenshot")
        if screenshot:
            paths.append(screenshot)
    return paths


def _topic_summary(report: dict) -> dict:
    notes = report.get("notes", [])
    return {
        "topic": report.get("topic", ""),
        "keywords": report.get("keywords", []),
        "notes_count": len(notes),
        "coverage": report.get("coverage", {}),
        "note_titles": [n.get("title", "") for n in notes[:6]],
        "synthesis_excerpt": report.get("synthesis", "")[:1600],
        "timing": report.get("timing", {}),
    }


def _creator_summary(report: dict) -> dict:
    profile = report.get("profile", {})
    detailed = report.get("detailed_notes", [])
    return {
        "profile_name": profile.get("name", ""),
        "profile_url": report.get("profile_url", ""),
        "followers": profile.get("followers", ""),
        "all_posts": len(report.get("all_cards", [])),
        "detailed_notes": len(detailed),
        "sampling": report.get("sampling", {}),
        "top_post_titles": [n.get("title", "") for n in detailed[:6]],
        "analysis_excerpt": report.get("analysis", "")[:1600],
        "timing": report.get("timing", {}),
    }


def _workflow_name(task: StructuredTask) -> str:
    if task.kind == TaskKind.TOPIC_RESEARCH:
        return "xhs.research"
    if task.kind == TaskKind.CREATOR_GROWTH_BREAKDOWN:
        return "xhs.user_analysis"
    raise ValueError(f"Unsupported task kind: {task.kind}")


def _generate_html_report(
    *,
    task: StructuredTask,
    understanding: TaskUnderstanding,
    workflow_name: str,
    capability_catalog: list[dict],
    execution_strategy: ExecutionStrategy,
    workflow_report_path: Path,
    workflow_summary: dict,
    workflow_log: list[dict],
    screenshots: list[str],
    session_gif: Path,
    recording_stats: dict,
    action_log: list[dict],
    reasoning_log: list[dict],
    assessment: TaskAssessment,
    screenshot_checks: list[dict],
    total_time: float,
    task_dir: Path,
) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    screenshots_html = ""
    for path in screenshots:
        if Path(path).exists():
            screenshots_html += (
                f"<div class='card'><p class='meta'>{escape(Path(path).name)}</p>"
                f"<img class='screenshot' src='{escape(_safe_rel(path, task_dir))}'></div>"
            )

    session_html = ""
    if session_gif.exists():
        session_html = (
            f"<div class='card'><img class='screenshot' src='{escape(_safe_rel(session_gif, task_dir))}'>"
            f"<p class='meta'>{recording_stats.get('frames', 0)} frames / {recording_stats.get('duration_s', 0)}s</p></div>"
        )

    checks_html = ""
    for check in screenshot_checks:
        checks_html += (
            "<div class='card'>"
            f"<p><strong>{escape(check.get('label', 'screenshot'))}</strong></p>"
            f"<p>{escape(check.get('summary', ''))}</p>"
            f"{render_markdown_block(check.get('vision', ''), 'vision')}"
            "</div>"
        )

    reasoning_html = ""
    for entry in reasoning_log:
        reasoning_html += (
            "<div class='card' style='border-left:4px solid #2d6cdf'>"
            f"<div class='meta'>[{entry['timestamp']:.1f}s] {escape(entry['phase'])}</div>"
            f"<p><strong>Observed:</strong> {escape(entry['observation'])}</p>"
            f"{render_markdown_block(entry['reasoning'], 'vision') if entry.get('reasoning') else ''}"
            f"<p><strong>Decision:</strong> {escape(entry['decision'])}</p>"
            f"{render_markdown_block(entry['evidence'], 'vision') if entry.get('evidence') else ''}"
            "</div>"
        )

    action_text = "\n".join(
        f"[{entry['elapsed_s']:6.1f}s] {entry['action']}"
        + (f" ({entry['duration_s']:.2f}s)" if entry.get("duration_s") is not None else "")
        + f": {entry['detail']}"
        for entry in action_log
    )

    workflow_log_text = "\n".join(
        f"[{entry.get('step', '?')} {entry.get('elapsed_s', '?')}s] {entry.get('action', '')}: {entry.get('detail', '')}"
        for entry in workflow_log
    )

    workflow_link = escape(_safe_rel(workflow_report_path, task_dir))
    workflow_json = escape(json.dumps(workflow_summary, ensure_ascii=False, indent=2))
    criteria_json = escape(json.dumps(understanding.search_criteria, ensure_ascii=False, indent=2))
    capabilities_json = escape(json.dumps(capability_catalog, ensure_ascii=False, indent=2))
    strategy_json = escape(json.dumps(execution_strategy.__dict__, ensure_ascii=False, indent=2))

    return f"""<!DOCTYPE html><html><head><meta charset='utf-8'>
<title>{escape(task.title)}</title>
<style>
body{{font-family:-apple-system,sans-serif;max-width:1200px;margin:0 auto;padding:20px;line-height:1.6;color:#333;background:#fafafa}}
h1{{color:#ff2442}}h2{{color:#333;border-bottom:2px solid #ff2442;padding-bottom:5px;margin-top:30px}}
.card{{background:#fff;border:1px solid #eee;border-radius:10px;padding:16px;margin:12px 0;box-shadow:0 1px 3px rgba(0,0,0,0.05)}}
.meta{{color:#888;font-size:13px}}
.summary{{background:#fff5f7;border:1px solid #ffd1dc;border-radius:12px;padding:16px}}
.vision{{background:#e8f2ff;border:1px solid #b7d3ff;border-radius:8px;padding:10px;margin:8px 0;white-space:pre-wrap}}
img.screenshot{{max-width:100%;max-height:720px;border:1px solid #ddd;border-radius:8px}}
pre.log{{background:#1f2937;color:#f3f4f6;padding:14px;border-radius:8px;font-size:12px;overflow:auto;max-height:420px;white-space:pre-wrap}}
pre.json{{background:#f6f8fa;color:#222;padding:14px;border-radius:8px;font-size:12px;overflow:auto;white-space:pre-wrap}}
.ok{{color:#137333;font-weight:600}} .warn{{color:#b3261e;font-weight:600}}
{markdown_styles()}
</style></head><body>
<h1>{escape(task.title)}</h1>
<p class='meta'>Generated {now} | Workflow {escape(workflow_name)} | Total {total_time:.1f}s</p>

<div class='summary'>
  <p><strong>Objective:</strong> {escape(task.objective)}</p>
  <p><strong>Assessment:</strong>
    <span class='{"ok" if assessment.complete else "warn"}'>{assessment.complete}</span>
    (confidence: {assessment.confidence:.0%})
  </p>
  {render_markdown_block(assessment.reasoning, 'vision')}
  <p><strong>Workflow report:</strong> <a href='{workflow_link}' target='_blank'>{workflow_link}</a></p>
</div>

<h2>Task Definition</h2>
<div class='card'>
  <p><strong>Kind:</strong> {escape(task.kind)}</p>
  <p><strong>Payload:</strong></p>
  <pre class='json'>{escape(json.dumps(task.payload, ensure_ascii=False, indent=2))}</pre>
  <p><strong>Questions:</strong></p>
  <pre class='json'>{escape(json.dumps(task.questions, ensure_ascii=False, indent=2))}</pre>
  <p><strong>Success criteria:</strong></p>
  <pre class='json'>{escape(json.dumps(task.success_criteria, ensure_ascii=False, indent=2))}</pre>
</div>

<h2>Task Understanding</h2>
<div class='card'>
  <p><strong>Goal:</strong> {escape(understanding.goal)}</p>
  <p><strong>Target type:</strong> {escape(understanding.target_type)}</p>
  <p><strong>Search keywords:</strong> {escape(', '.join(understanding.search_keywords))}</p>
  <p><strong>Success criteria:</strong> {escape(understanding.success_criteria)}</p>
  <pre class='json'>{criteria_json}</pre>
</div>

<h2>Capability Catalog</h2>
<div class='card'><pre class='json'>{capabilities_json}</pre></div>

<h2>Execution Strategy</h2>
<div class='card'><pre class='json'>{strategy_json}</pre></div>

<h2>Workflow Summary</h2>
<div class='card'><pre class='json'>{workflow_json}</pre></div>

<h2>Screenshot Verification</h2>
{checks_html or "<div class='card'>No screenshot verification captured.</div>"}

<h2>Session Recording</h2>
{session_html or "<div class='card'>No session recording available.</div>"}

<h2>Key Screenshots</h2>
{screenshots_html or "<div class='card'>No screenshots collected.</div>"}

<h2>Assessment</h2>
<div class='card'>
  <p><strong>Strengths:</strong></p>
  <pre class='json'>{escape(json.dumps(assessment.strengths, ensure_ascii=False, indent=2))}</pre>
  <p><strong>Gaps:</strong></p>
  <pre class='json'>{escape(json.dumps(assessment.gaps, ensure_ascii=False, indent=2))}</pre>
  <p><strong>Next actions:</strong></p>
  <pre class='json'>{escape(json.dumps(assessment.next_actions, ensure_ascii=False, indent=2))}</pre>
</div>

<h2>Agent Reasoning Log</h2>
{reasoning_html or "<div class='card'>No reasoning log.</div>"}

<h2>Workflow Execution Log</h2>
<pre class='log'>{escape(workflow_log_text)}</pre>

<h2>Task Action Log</h2>
<pre class='log'>{escape(action_text)}</pre>
</body></html>"""


class XHSTaskRunner:
    """Runs structured XHS tasks via generic task planning + XHS workflows."""

    def __init__(self, output_root: str = "task_runs", port: int = 8765, record_interval: float = 1.0, watch: bool = False):
        self.output_root = Path(output_root)
        self.port = port
        self.record_interval = record_interval
        self.watch = watch

    async def run(self, task: StructuredTask) -> dict:
        task_dir = self.output_root / f"{task.slug()}_{_task_now_slug()}"
        workflow_dir = task_dir / "workflow"
        task_dir.mkdir(parents=True, exist_ok=True)
        workflow_dir.mkdir(parents=True, exist_ok=True)

        bridge = ExtensionBridge(port=self.port)
        action = ActionLog(bridge=bridge)
        reasoning = ReasoningLog(bridge=bridge)
        media = MediaProcessor()
        task_agent = TaskAgent(media, site_context=SITE_CONTEXT)
        bridge.on_log(lambda a, d="": action.log(f"bridge:{a}", d))
        browser = XHSBrowser(bridge)
        recorder = SessionRecorder(bridge, interval=self.record_interval)
        screenshots: list[str] = []
        screenshot_checks: list[dict] = []
        workflow_report: dict = {}
        workflow_summary: dict = {}
        understanding = TaskUnderstanding(goal=task.objective)
        assessment = TaskAssessment(reasoning="Task run did not finish cleanly.")
        execution_strategy = ExecutionStrategy()
        workflow_name = _workflow_name(task)
        capability_specs = [spec.to_dict() for spec in capabilities_for_task(task.kind)]
        total_t0 = time.time()
        session_gif = task_dir / "session.gif"
        recording_stats = {"frames": 0, "duration_s": 0}
        automation_window_id: int | None = None
        automation_tab_id: int | None = None

        try:
            # ── Phase 1: Open browser window ASAP ────────────────────
            # Start the bridge and connect to the extension first so the
            # user sees the browser window immediately.  LLM planning
            # runs in parallel while the page loads.
            await bridge.start()
            action.log("bridge_started", f"ws://localhost:{self.port}")
            print("\n  >>> Waiting for Chrome Extension to connect. <<<\n")
            await ensure_extension_connection(
                bridge,
                require_watch=self.watch,
                timeout=120,
                warmup_active_tab=False,
            )
            action.log("extension_connected", "using the currently loaded extension runtime")
            if self.watch:
                bg_window = await bridge.create_watch_window(url="https://www.xiaohongshu.com")
                action.log(
                    "watch_window",
                    f"window={bg_window.get('windowId')} tab={bg_window.get('tabId')} watch=true sidePanel={bg_window.get('sidePanel')} overlay=true",
                )
            else:
                bg_window = await bridge.create_background_window(url="https://www.xiaohongshu.com", minimized=False)
                action.log(
                    "background_window",
                    f"window={bg_window.get('windowId')} tab={bg_window.get('tabId')}",
                )
            automation_window_id = bg_window.get("windowId")
            automation_tab_id = bg_window.get("tabId")
            await bridge.lock_active_tab(automation_tab_id)

            # ── Phase 2: LLM planning while page loads ───────────────
            # The page needs a few seconds to render anyway, so use that
            # time for task understanding and execution strategy planning.
            navigate_task = asyncio.ensure_future(browser.navigate("https://www.xiaohongshu.com"))

            action.log("task_understand_start", task.title)
            t0 = time.time()
            understanding = task_agent.understand_task(task.to_prompt())
            action.log("task_understand_done", understanding.goal, time.time() - t0)
            reasoning.think(
                "task_understanding",
                f"Structured task: {task.kind}",
                f"Goal={understanding.goal}; keywords={understanding.search_keywords}",
                f"Dispatch to {workflow_name}",
                evidence=understanding.raw_reasoning[:500],
            )

            t0 = time.time()
            execution_strategy = task_agent.plan_execution(
                task.to_prompt(),
                str(task.kind),
                capability_catalog_markdown(task.kind),
            )
            action.log("strategy_planned", execution_strategy.mode, time.time() - t0)
            reasoning.think(
                "execution_strategy",
                f"Capabilities available for {task.kind}",
                execution_strategy.reasoning,
                (
                    f"mode={execution_strategy.mode}, keyword_count={execution_strategy.keyword_count}, "
                    f"lite={execution_strategy.lite_note_count}, deep={execution_strategy.deep_note_count}, "
                    f"timeline={execution_strategy.timeline_sample_count}"
                ),
            )

            # Wait for navigation to finish (likely already done by now)
            await navigate_task
            # Brief settle time for XHS SPA rendering
            await asyncio.sleep(1.5)
            action.log("navigate_home", "https://www.xiaohongshu.com")

            await recorder.start()
            action.log("recording_started", f"interval={self.record_interval}s")

            if task.kind == TaskKind.TOPIC_RESEARCH:
                merged_keywords = [
                    *task.payload.get("preset_keywords", []),
                    *understanding.search_keywords,
                ]
                keywords = [keyword for keyword in dict.fromkeys(merged_keywords) if keyword]
                config = ResearchConfig(
                    max_keywords=int(task.payload.get("max_keywords", execution_strategy.keyword_count)),
                    max_cards_per_keyword=int(task.payload.get("max_cards_per_keyword", execution_strategy.cards_per_keyword)),
                    max_lite_notes=int(task.payload.get("max_lite_notes", execution_strategy.lite_note_count)),
                    max_deep_notes=int(task.payload.get("max_deep_notes", execution_strategy.deep_note_count)),
                    lite_comment_count=int(task.payload.get("lite_comment_count", execution_strategy.lite_comment_count)),
                    max_comments_per_note=int(task.payload.get("deep_comment_count", execution_strategy.deep_comment_count)),
                )
                reasoning.think(
                    "workflow_dispatch",
                    f"Topic={task.payload.get('topic', '')}",
                    f"Will run research with keywords={keywords[:config.max_keywords]} and cards_per_keyword={config.max_cards_per_keyword}",
                    "Start xhs.research workflow",
                )
                workflow = XHSResearchAgent(
                    output_dir=str(workflow_dir),
                    config=config,
                    browser=browser,
                    media=media,
                    manage_bridge_lifecycle=False,
                )
                t0 = time.time()
                workflow_report = await workflow.research(
                    topic=task.payload["topic"],
                    keywords=keywords[:config.max_keywords],
                )
                action.log("workflow_done", f"{workflow_name} complete", time.time() - t0)
                screenshots = _collect_topic_screenshots(workflow_report)
                workflow_summary = _topic_summary(workflow_report)
            elif task.kind == TaskKind.CREATOR_GROWTH_BREAKDOWN:
                config = UserAnalysisConfig(
                    max_scroll_rounds=int(task.payload.get("max_scroll_rounds", 15)),
                    max_timeline_samples=int(task.payload.get("max_timeline_samples", execution_strategy.timeline_sample_count)),
                    max_deep_notes=int(task.payload.get("max_deep_notes", execution_strategy.deep_sample_count)),
                    lite_comment_count=int(task.payload.get("lite_comment_count", execution_strategy.lite_comment_count)),
                    max_comments_per_note=int(task.payload.get("deep_comment_count", execution_strategy.deep_comment_count)),
                )
                reasoning.think(
                    "workflow_dispatch",
                    f"Profile={task.payload.get('profile_url', '')}",
                    f"Will run creator analysis with timeline_samples={config.max_timeline_samples} and deep_reads={config.max_deep_notes}",
                    "Start xhs.user_analysis workflow",
                )
                workflow = XHSUserAnalyzer(
                    output_dir=str(workflow_dir),
                    config=config,
                    browser=browser,
                    media=media,
                    manage_bridge_lifecycle=False,
                )
                t0 = time.time()
                workflow_report = await workflow.analyze(task.payload["profile_url"])
                action.log("workflow_done", f"{workflow_name} complete", time.time() - t0)
                screenshots = _collect_creator_screenshots(workflow_report)
                workflow_summary = _creator_summary(workflow_report)
            else:
                raise ValueError(f"Unsupported task kind: {task.kind}")

            screenshot_checks = await self._verify_screenshots(media, screenshots, task, action)
            assessment = task_agent.assess_workflow_result(understanding, workflow_name, workflow_summary)
            reasoning.think(
                "task_assessment",
                f"Workflow summary captured for {workflow_name}",
                assessment.reasoning,
                f"Complete={assessment.complete}, confidence={assessment.confidence:.0%}",
                evidence=json.dumps({
                    "strengths": assessment.strengths,
                    "gaps": assessment.gaps,
                    "next_actions": assessment.next_actions,
                }, ensure_ascii=False)[:600],
            )

        finally:
            await recorder.stop()
            recorder.save_gif(session_gif, fps=2.0, max_width=900)
            recording_stats = recorder.summary()
            try:
                await bridge.release_active_tab()
            except Exception:
                pass
            if automation_tab_id is not None:
                try:
                    await bridge.close_tab(automation_tab_id)
                except Exception:
                    pass

        total_time = time.time() - total_t0
        report_html = task_dir / "report.html"
        report_json = task_dir / "report.json"
        html = _generate_html_report(
            task=task,
            understanding=understanding,
            workflow_name=workflow_name,
            capability_catalog=capability_specs,
            execution_strategy=execution_strategy,
            workflow_report_path=workflow_dir / "report.html",
            workflow_summary=workflow_summary,
            workflow_log=workflow_report.get("log", []),
            screenshots=screenshots,
            session_gif=session_gif,
            recording_stats=recording_stats,
            action_log=action.entries,
            reasoning_log=[
                *reasoning.entries,
                *[
                    {
                        "timestamp": r["timestamp"],
                        "phase": f"llm:{r['phase']}",
                        "observation": r["prompt_summary"],
                        "reasoning": r["response_summary"],
                        "decision": "(logged from TaskAgent)",
                        "evidence": "",
                    }
                    for r in task_agent.reasoning_log
                ],
            ],
            assessment=assessment,
            screenshot_checks=screenshot_checks,
            total_time=total_time,
            task_dir=task_dir,
        )
        report_html.write_text(html)

        result = {
            "task": {
                "kind": task.kind,
                "title": task.title,
                "objective": task.objective,
                "payload": task.payload,
                "questions": task.questions,
                "success_criteria": task.success_criteria,
            },
            "understanding": understanding.__dict__,
            "workflow_name": workflow_name,
            "capability_catalog": capability_specs,
            "execution_strategy": execution_strategy.__dict__,
            "workflow_summary": workflow_summary,
            "workflow_report_dir": str(workflow_dir),
            "assessment": assessment.__dict__,
            "screenshot_checks": screenshot_checks,
            "screenshots": screenshots,
            "session_gif": str(session_gif),
            "recording_stats": recording_stats,
            "action_log": action.entries,
            "reasoning_log": reasoning.entries,
            "workflow_log": workflow_report.get("log", []),
            "total_time_s": round(total_time, 1),
        }
        report_json.write_text(json.dumps(result, ensure_ascii=False, indent=2))

        try:
            await asyncio.wait_for(bridge.stop(), timeout=5)
        except Exception:
            action.log("bridge_stop_timeout", "Bridge stop timed out after report save")

        print(f"\n{'=' * 64}")
        print(f"  TASK COMPLETE — {total_time:.1f}s")
        print(f"  Task: {task.title}")
        print(f"  Workflow: {workflow_name}")
        print(f"  Assessment: {assessment.complete} ({assessment.confidence:.0%})")
        print(f"  Report: {report_html}")
        print(f"{'=' * 64}")

        return result

    async def _verify_screenshots(
        self,
        media: MediaProcessor,
        screenshots: list[str],
        task: StructuredTask,
        action: ActionLog,
    ) -> list[dict]:
        checks: list[dict] = []
        for index, path in enumerate(screenshots[:2], start=1):
            image_path = Path(path)
            if not image_path.exists():
                continue
            prompt = (
                f"这是小红书任务“{task.title}”执行过程中的截图。"
                "请用中文简要判断当前页面处于什么阶段，页面主要内容是什么，"
                "以及它是否看起来符合这个任务应该出现的页面状态。"
            )
            t0 = time.time()
            vision = media.describe_image(image_path.read_bytes(), prompt, max_tokens=384)
            action.log("vision_verify", image_path.name, time.time() - t0)
            checks.append({
                "label": f"Screenshot {index}: {image_path.name}",
                "path": str(image_path),
                "summary": "Claude Vision verification of workflow screenshot.",
                "vision": vision,
            })
        return checks
