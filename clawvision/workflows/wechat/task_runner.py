"""Task runner for summarizing a WeChat desktop conversation."""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

from ...core.watch import JSONLWatchSink, MemoryWatchSink, WatchEvent, WatchRuntime
from ...perception.llm import VisionLLM
from ...perception.media import MediaConfig, MediaProcessor
from ...platforms.wechat import WeChatConversationParser, WeChatDesktopApp
from ...platforms.wechat.models import WeChatMessage, WeChatParsedCapture
from ...platforms.wechat.vision_profiles import WECHAT_PARSE_FALLBACK, WECHAT_UI_SIMPLE_CHECK
from ...reasoning.tasks import StructuredTask
from .agent import WeChatSummaryAgent
from .reporting import write_html_report, write_markdown_report, write_summary_json


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _merge_messages(captures: list[WeChatParsedCapture]) -> list[WeChatMessage]:
    merged: list[WeChatMessage] = []
    seen: set[str] = set()
    for capture in reversed(captures):
        ordered = sorted(capture.messages, key=lambda item: -item.y_norm)
        for message in ordered:
            key = message.dedupe_key()
            if key in seen:
                continue
            seen.add(key)
            merged.append(message)
    return merged


def _merge_date_markers(captures: list[WeChatParsedCapture]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for capture in reversed(captures):
        for item in capture.date_markers:
            if item in seen:
                continue
            seen.add(item)
            merged.append(item)
    return merged


class WeChatChatSummaryRunner:
    """Execute a local-only WeChat chat summary task."""

    def __init__(
        self,
        *,
        output_root: str = "task_runs/wechat_summary",
        llm_backend: str = "qwen-local",
    ):
        self.output_root = Path(output_root)
        self.llm_backend = "qwen-local" if llm_backend != "qwen-local" else llm_backend
        self.media = MediaProcessor(
            MediaConfig(
                backend=self.llm_backend,
                use_apple_ocr=True,
                use_whisper=False,
                use_vision=True,
            )
        )
        self.vision = VisionLLM(backend=self.llm_backend)
        self.app = WeChatDesktopApp(vision=self.vision)
        self.parser = WeChatConversationParser(vision=self.vision)
        self.agent = WeChatSummaryAgent(self.media)
        self.watch_runtime: WatchRuntime | None = None

    async def run(self, task: StructuredTask) -> dict:
        task_dir = self.output_root / f"{task.slug()}_{_timestamp()}"
        captures_dir = task_dir / "captures"
        captures_dir.mkdir(parents=True, exist_ok=True)
        watch_path = task_dir / "watch_events.jsonl"
        memory_sink = MemoryWatchSink()
        self.watch_runtime = WatchRuntime(JSONLWatchSink(watch_path), memory_sink)

        t0 = time.perf_counter()
        payload = task.payload or {}
        conversation = str(payload.get("conversation") or "").strip()
        max_scroll_rounds = max(1, int(payload.get("max_scroll_rounds", 12)))
        min_capture_rounds = max(1, int(payload.get("min_capture_rounds", 3)))
        try:
            self._notify_user(
                title="ClawVision 正在接管微信",
                message="即将控制鼠标和键盘采集聊天记录，请暂时不要操作微信窗口。",
                subtitle=conversation or "当前会话",
            )
            self._watch(
                WatchEvent(
                    level="warning",
                    message="即将接管微信窗口、鼠标和键盘，请暂时不要手动操作。",
                    phase="control",
                    metadata={"control_active": True, "conversation": conversation},
                )
            )
            self._watch_action("model_preload", "预热本地 Qwen 2B / 9B 模型")
            self.vision.preload_local_model(WECHAT_UI_SIMPLE_CHECK.local_model_name)
            self.vision.preload_local_model(WECHAT_PARSE_FALLBACK.local_model_name)

            if conversation:
                self._watch_action("open_conversation", f"打开会话：{conversation}")
                open_result = self.app.open_conversation(conversation)
            else:
                self._watch_action("open_conversation", "复用当前已打开会话，必要时回退到首个可见会话")
                _, image, _ = self.app.capture_state()
                open_result = (
                    {"opened": False, "method": "current_conversation", "match": ""}
                    if self.app.conversation_visible(image)
                    else self.app.open_first_visible_conversation()
                )

            _, current_image, current_page = self.app.capture_state()
            if not self.app.conversation_visible(current_image, current_page):
                raise RuntimeError(
                    "Failed to open a visible WeChat conversation. "
                    "The window still looks like the conversation list or empty home view."
                )
            if conversation and not self.app.ensure_conversation_title(conversation):
                raise RuntimeError(f"Failed to open the requested WeChat conversation: {conversation}")

            captures: list[WeChatParsedCapture] = []
            decisions: list[dict] = []
            signature_seen: set[str] = set()
            known_message_keys: set[str] = set()
            consecutive_stale = 0

            for capture_index in range(max_scroll_rounds):
                screenshot_path = captures_dir / f"capture_{capture_index:02d}.jpg"
                self._watch_action("capture", f"采集第 {capture_index + 1} 屏可见聊天内容")
                _, image, ocr_page = self.app.capture_state()
                image.save(screenshot_path, quality=95)

                if not self.app.conversation_visible(image):
                    raise RuntimeError(
                        "WeChat right pane does not show an open conversation. "
                        "Pass a conversation name or open one before running the task."
                    )

                parsed = self.parser.parse_capture(
                    capture_index=capture_index,
                    screenshot_path=screenshot_path,
                    image=image,
                    ocr_page=ocr_page,
                )
                self._watch_think(
                    phase="parse",
                    observation=f"capture={capture_index:02d} parser={parsed.parser_mode}",
                    reasoning=f"messages={len(parsed.messages)} date_markers={len(parsed.date_markers)}",
                    decision="保留当前截图并继续判断是否需要上翻。",
                )
                captures.append(parsed)
                (captures_dir / f"capture_{capture_index:02d}.json").write_text(
                    json.dumps(parsed.to_dict(), ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

                new_messages = 0
                for message in parsed.messages:
                    key = message.dedupe_key()
                    if key in known_message_keys:
                        continue
                    known_message_keys.add(key)
                    new_messages += 1

                if parsed.page_signature in signature_seen or new_messages == 0:
                    consecutive_stale += 1
                else:
                    consecutive_stale = 0
                    signature_seen.add(parsed.page_signature)

                if capture_index + 1 < min_capture_rounds:
                    if capture_index + 1 < max_scroll_rounds:
                        self._watch_action("scroll", "最少采集屏数未达标，继续向上滚动")
                        self.app.scroll_history_up()
                    continue

                decision = self.agent.decide_collection(
                    task,
                    captures,
                    consecutive_stale=consecutive_stale,
                )
                decisions.append({
                    "capture_index": capture_index,
                    "continue_collection": decision.continue_collection,
                    "scroll_batches": decision.scroll_batches,
                    "reasoning": decision.reasoning,
                })
                self._watch_think(
                    phase="collect",
                    observation=f"capture={capture_index:02d} stale={consecutive_stale}",
                    reasoning=decision.reasoning,
                    decision=(
                        "继续向上滚动"
                        if consecutive_stale < 2 and decision.continue_collection
                        else "停止继续采集"
                    ),
                )
                if consecutive_stale >= 2 or not decision.continue_collection:
                    break
                self._watch_action("scroll", f"向上滚动 {max(4, decision.scroll_batches * 4)} 次")
                self.app.scroll_history_up(repeats=max(4, decision.scroll_batches * 4))

            merged_messages = _merge_messages(captures)
            date_markers = _merge_date_markers(captures)
            conversation_title = captures[-1].conversation_title if captures else conversation or "当前会话"
            self._watch_action("summarize", f"开始生成总结，累计消息 {len(merged_messages)} 条")
            summary_markdown = self.agent.summarize(
                task,
                conversation_title=conversation_title,
                messages=merged_messages,
                date_markers=date_markers,
            )
            self._watch_action("verify", "校验总结是否和可见聊天内容一致")
            verification = self.agent.verify_summary(
                summary_markdown=summary_markdown,
                captures=captures,
            ) if captures else ""
            if self._needs_summary_repair(verification):
                self._watch_action("repair_summary", "发现不稳结论，触发保守重写")
                summary_markdown = self.agent.repair_summary(
                    task,
                    conversation_title=conversation_title,
                    messages=merged_messages,
                    date_markers=date_markers,
                    summary_markdown=summary_markdown,
                    verification_feedback=verification,
                )
                verification = self.agent.verify_summary(
                    summary_markdown=summary_markdown,
                    captures=captures,
                ) if captures else verification

            summary_payload = {
                "task": {
                    "kind": str(task.kind),
                    "title": task.title,
                    "objective": task.objective,
                    "payload": task.payload,
                },
                "conversation": conversation_title,
                "open_result": open_result,
                "elapsed_s": round(time.perf_counter() - t0, 1),
                "llm_backend": self.llm_backend,
                "captures": [item.to_dict() for item in captures],
                "decisions": decisions,
                "unique_message_count": len(merged_messages),
                "date_markers": date_markers,
                "summary_markdown": summary_markdown,
                "verification": verification,
            }

            summary_json = write_summary_json(task_dir, summary_payload)
            report_md = write_markdown_report(task_dir, summary_markdown=summary_markdown, verification=verification)
            report_html = write_html_report(
                task_dir,
                title=task.title,
                summary_markdown=summary_markdown,
                verification=verification,
                captures=[item.to_dict() for item in captures],
            )
            self._watch(
                WatchEvent(
                    level="result",
                    message="微信会话总结完成。",
                    phase="done",
                    detail=f"conversation={conversation_title} unique_messages={len(merged_messages)}",
                    metadata={"control_active": False, "conversation": conversation_title},
                )
            )
            return {
                "task_dir": str(task_dir),
                "summary_json": str(summary_json),
                "report_md": str(report_md),
                "report_html": str(report_html),
                "watch_jsonl": str(watch_path),
                "conversation": conversation_title,
                "unique_message_count": len(merged_messages),
                "elapsed_s": round(time.perf_counter() - t0, 1),
            }
        finally:
            self._watch(
                WatchEvent(
                    level="warning",
                    message="微信自动化已释放输入控制，可以恢复手动操作。",
                    phase="control",
                    metadata={"control_active": False, "conversation": conversation},
                )
            )
            self._notify_user(
                title="ClawVision 已释放微信控制",
                message="聊天采集阶段已结束，现在可以恢复手动操作。",
                subtitle=conversation or "当前会话",
            )

    @staticmethod
    def _needs_summary_repair(verification: str) -> bool:
        checks = ("幻觉", "虚构", "不可信", "严重", "事实性错误", "不一致", "存疑", "遗漏风险", "错误", "矛盾", "冲突")
        return any(token in verification for token in checks)

    def _watch(self, event: WatchEvent) -> None:
        if self.watch_runtime is not None:
            self.watch_runtime.emit_nowait(event)

    def _watch_action(self, action_name: str, detail: str) -> None:
        if self.watch_runtime is not None:
            self.watch_runtime.action_nowait(action_name=action_name, detail=detail)

    def _watch_think(self, *, phase: str, observation: str, reasoning: str, decision: str) -> None:
        if self.watch_runtime is not None:
            self.watch_runtime.think_nowait(
                phase=phase,
                observation=observation,
                reasoning=reasoning,
                decision=decision,
            )

    def _notify_user(self, *, title: str, message: str, subtitle: str = "") -> None:
        try:
            self.app.controller.display_notification(title, message, subtitle=subtitle)
        except Exception:
            pass
