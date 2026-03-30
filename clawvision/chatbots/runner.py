"""Workflow runner for fanning one prompt out to multiple chatbot sites."""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import subprocess
import time
from pathlib import Path

from PIL import Image

from ..agent.bridge import ExtensionBridge
from ..agent.composer import ComposerSpec, enter_text, submit_with_dom_verification, wait_for_input_ready
from ..vision.llm import VisionRequestConfig
from .cleanup import cleanup_orphaned_chrome_processes
from .models import ChatbotWindow
from .reporting import build_summary, write_html_report, write_summary, write_timing_breakdown
from .sites import CHATBOT_SITES
from .visible_verifier import ChatbotVisibleVerifier
from .vision_profiles import CHATBOT_COMPLEX_FALLBACK_CHECK, CHATBOT_PAGE_SIMPLE_CHECK

logger = logging.getLogger(__name__)

DEFAULT_CONNECT_TIMEOUT_S = 12.0
DEFAULT_PAGE_LOAD_TIMEOUT_S = 45.0
FAST_CONNECT_TIMEOUT_S = 0.75
INPUT_READY_POLL_INTERVAL_S = 0.25
TEXT_SETTLE_S = 0.12
POST_SUBMIT_SETTLE_S = 0.75


def _yes_no_prompt(text: str) -> str:
    return f"{text} Answer YES or NO only."


READY_CHECK_PROMPT = _yes_no_prompt("Is the prompt box visible and usable?")


class MultiChatRunner:
    """Open ChatGPT, Gemini, and Claude and send the same question to each one."""

    def __init__(
        self,
        bridge: ExtensionBridge | None = None,
        *,
        port: int = 8765,
        vision_backend: str | None = None,
        output_dir: Path | None = None,
        cleanup_orphaned: bool = True,
        verify_visible_windows: bool = True,
        close_windows_on_finish: bool = False,
    ):
        self.bridge = bridge or ExtensionBridge(port=port)
        self._owns_bridge = bridge is None
        self.output_dir = output_dir or Path("task_runs/multi_chat")
        self.windows: list[ChatbotWindow] = [ChatbotWindow(site=site) for site in CHATBOT_SITES]
        self.cleanup_orphaned = cleanup_orphaned
        self.verify_visible_windows = verify_visible_windows
        self.close_windows_on_finish = close_windows_on_finish
        self.preflight_cleanup: dict | None = None

        if vision_backend is None:
            from ..agent.local_llm import LocalLLM

            vision_backend = "qwen-local" if LocalLLM.is_available() else "sonnet"

        self._vision_backend = vision_backend
        self._vision = None
        self._macos = None
        self._visible_verifier = None
        self._simple_vision_ready_task = None
        self._run_started_at = 0.0
        self._timeline: list[dict[str, object]] = []

    @property
    def vision(self):
        if self._vision is None:
            from ..vision.llm import VisionLLM

            self._vision = VisionLLM(backend=self._vision_backend)
        return self._vision

    @property
    def macos(self):
        if self._macos is None:
            from ..debug import MacOSController

            self._macos = MacOSController()
        return self._macos

    @property
    def visible_verifier(self) -> ChatbotVisibleVerifier:
        if self._visible_verifier is None:
            self._visible_verifier = ChatbotVisibleVerifier(
                macos=self.macos,
                vision=self.vision,
                output_dir=self.output_dir,
            )
        return self._visible_verifier

    def _screenshot_to_pil(self, data_url: str) -> Image.Image:
        b64 = data_url.split(",", 1)[1] if "," in data_url else data_url
        return Image.open(io.BytesIO(base64.b64decode(b64)))

    async def _save_screenshot(self, cw: ChatbotWindow, label: str, *, tab) -> Image.Image:
        data_url = await tab.capture_screenshot()
        if not data_url:
            raise RuntimeError(f"Screenshot capture failed for {cw.site.name} ({label})")
        image = self._screenshot_to_pil(data_url)
        path = self.output_dir / f"{cw.site.name.lower()}_{label}.png"
        image.save(path)
        cw.screenshots.append(path)
        return image

    def _elapsed_s(self) -> float:
        if not self._run_started_at:
            return 0.0
        return time.perf_counter() - self._run_started_at

    def _record_global_event(self, event: str, **fields: object) -> None:
        self._timeline.append({"t": round(self._elapsed_s(), 3), "event": event, **fields})

    def _record_window_event(self, cw: ChatbotWindow, event: str, **fields: object) -> None:
        cw.timeline.append({"t": round(self._elapsed_s(), 3), "event": event, **fields})

    async def _close_created_windows(self) -> None:
        for cw in self.windows:
            if not cw.window_id:
                continue
            try:
                await self.bridge.close_window(cw.window_id)
                self._record_window_event(cw, "window_closed")
            except Exception as exc:
                self._record_window_event(cw, "window_close_failed", error=str(exc))
                logger.warning("[%s] Failed to close window %s: %s", cw.site.name, cw.window_id, exc)

    async def _verify_with_vision(
        self,
        cw: ChatbotWindow,
        image: Image.Image,
        question: str,
        *,
        config: VisionRequestConfig | None = None,
    ) -> tuple[str, float, str]:
        logger.info("[%s] Vision check: %s", cw.site.name, question)
        t0 = time.perf_counter()
        result = self.vision.analyze_page(image, question, config=config)
        elapsed = time.perf_counter() - t0
        profile_label = config.name if config else "default"
        log_entry = f"[{elapsed:.1f}s][{profile_label}] {question} -> {result[:200]}"
        cw.vision_logs.append(log_entry)
        logger.info("[%s] Vision result (%.1fs): %s", cw.site.name, elapsed, result[:200])
        return result, elapsed, profile_label

    async def _wait_for_extension(self) -> None:
        logger.info("Bridge started, waiting for extension connection...")
        self._record_global_event("extension_wait_started")
        try:
            await self.bridge.wait_for_connection(
                timeout=FAST_CONNECT_TIMEOUT_S,
                warmup_active_tab=False,
            )
            logger.info("Extension connected")
            self._record_global_event("extension_connected", wake_chrome=False)
            return
        except RuntimeError:
            logger.info("Extension not connected yet; launching Google Chrome to wake the runtime")
            self._record_global_event("extension_wait_retry", reason="fast_timeout")

        subprocess.run(["open", "-a", "Google Chrome"], check=True)
        self._record_global_event("chrome_wake_requested")
        await self.bridge.wait_for_connection(timeout=DEFAULT_CONNECT_TIMEOUT_S, warmup_active_tab=False)
        logger.info("Extension connected after waking Chrome")
        self._record_global_event("extension_connected", wake_chrome=True)

    def _compute_window_layout(self, count: int = 3) -> list[dict]:
        try:
            from ..debug.macos import MacOSController

            controller = MacOSController()
            displays = controller.list_displays()
            if displays:
                main = displays[0]
                screen_w = main.width
                screen_h = main.height
            else:
                screen_w, screen_h = 2560, 1440
        except Exception:
            screen_w, screen_h = 2560, 1440

        top = 28
        usable_h = screen_h - top - 4
        win_w = screen_w // count
        return [
            {
                "left": index * win_w,
                "top": top,
                "width": win_w,
                "height": usable_h,
            }
            for index in range(count)
        ]

    async def run(self, question: str) -> dict:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._run_started_at = time.perf_counter()
        self._timeline = []
        self._record_global_event("run_started", question_len=len(question))

        if self.cleanup_orphaned:
            self.preflight_cleanup = cleanup_orphaned_chrome_processes()
            logger.info("Preflight cleanup: %s", self.preflight_cleanup)
        else:
            self.preflight_cleanup = {
                "matched": 0,
                "terminated": [],
                "force_killed": [],
                "remaining": [],
            }
        self._record_global_event(
            "preflight_cleanup_done",
            matched=self.preflight_cleanup.get("matched", 0),
            remaining=len(self.preflight_cleanup.get("remaining", [])),
        )

        if self._vision_backend == "qwen-local":
            self._record_global_event(
                "simple_model_preload_started",
                model=CHATBOT_PAGE_SIMPLE_CHECK.local_model_name,
            )
            self._simple_vision_ready_task = asyncio.create_task(
                asyncio.to_thread(
                    self.vision.preload_local_model,
                    CHATBOT_PAGE_SIMPLE_CHECK.local_model_name,
                )
            )
        else:
            self._simple_vision_ready_task = None

        if self._owns_bridge:
            self._record_global_event("bridge_starting")
            await self.bridge.start()
            await self._wait_for_extension()

        positions = self._compute_window_layout(len(self.windows))
        logger.info("Opening %d chatbot windows...", len(self.windows))
        self._record_global_event("window_opening_started", count=len(self.windows))
        for cw, position in zip(self.windows, positions, strict=True):
            cw.planned_bounds = position
            try:
                result = await self.bridge.create_background_window(
                    cw.site.url,
                    lock=False,
                    focused=True,
                    width=position["width"],
                    height=position["height"],
                    left=position["left"],
                    top=position["top"],
                )
                cw.tab_id = result["tabId"]
                cw.window_id = result["windowId"]
                cw.status = "loading"
                self._record_window_event(
                    cw,
                    "window_created",
                    tab_id=cw.tab_id,
                    window_id=cw.window_id,
                    left=position["left"],
                    top=position["top"],
                    width=position["width"],
                    height=position["height"],
                )
            except Exception as exc:
                cw.status = "error"
                cw.error = str(exc)
                logger.error("[%s] Failed to create window: %s", cw.site.name, exc)
                self._record_window_event(cw, "window_create_failed", error=str(exc))
        self._record_global_event("window_opening_finished")

        self._record_global_event("chatbot_tasks_starting")
        scheduled_windows = [cw for cw in self.windows if cw.status != "error"]
        results = await asyncio.gather(
            *(asyncio.create_task(self._handle_chatbot(cw, question)) for cw in scheduled_windows),
            return_exceptions=True,
        )
        self._record_global_event("chatbot_tasks_finished")
        for cw, result in zip(scheduled_windows, results, strict=True):
            if isinstance(result, Exception):
                cw.status = "error"
                cw.error = str(result)
                logger.error("[%s] Error: %s", cw.site.name, result)
                self._record_window_event(cw, "task_failed", error=str(result))

        if self._simple_vision_ready_task is not None:
            await self._simple_vision_ready_task

        if self.verify_visible_windows:
            self._record_global_event("visible_verification_started", stage="after_submit")
            self.visible_verifier.verify(
                self.windows,
                question,
                stage="after_submit",
                record_event=self._record_window_event,
            )
            self._record_global_event("visible_verification_finished", stage="after_submit")
        else:
            self._record_global_event("visible_verification_skipped", reason="disabled")

        if self.close_windows_on_finish:
            self._record_global_event("window_cleanup_started")
            await self._close_created_windows()
            self._record_global_event("window_cleanup_finished")
        else:
            self.macos.activate_app("Google Chrome")

        elapsed = time.perf_counter() - self._run_started_at
        self._record_global_event("run_finished", elapsed_s=round(elapsed, 3))

        summary = build_summary(
            question=question,
            elapsed_s=elapsed,
            vision_backend=self._vision_backend,
            preflight_cleanup=self.preflight_cleanup,
            visible_verification=self.verify_visible_windows,
            timeline=self._timeline,
            windows=self.windows,
        )
        summary_path = write_summary(self.output_dir, summary)
        write_timing_breakdown(self.output_dir, summary)
        write_html_report(self.output_dir, summary)
        logger.info("Done in %.1fs. Summary: %s", elapsed, summary_path)
        return summary

    async def _handle_chatbot(self, cw: ChatbotWindow, question: str) -> None:
        logger.info("[%s] Starting interaction...", cw.site.name)
        self._record_window_event(cw, "interaction_started")
        tab = self.bridge.tab(cw.tab_id, window_id=cw.window_id)
        composer = ComposerSpec(
            input_selectors=tuple(cw.site.input_selectors),
            submit_selectors=tuple(cw.site.submit_selectors),
            submit_mode=cw.site.submit_mode,
        )

        ready_result = await wait_for_input_ready(
            tab,
            composer.input_selectors,
            timeout_s=DEFAULT_PAGE_LOAD_TIMEOUT_S,
            poll_interval_s=INPUT_READY_POLL_INTERVAL_S,
        )
        if not ready_result.get("found"):
            logger.warning("[%s] Input selectors not ready after wait; falling back to screenshot + vision", cw.site.name)
            self._record_window_event(cw, "input_ready_timeout")
        else:
            self._record_window_event(cw, "input_ready", selector=ready_result.get("selector", ""))
        cw.status = "ready"

        input_result = ready_result if ready_result.get("found") else {"found": False}
        if input_result.get("found"):
            self._record_window_event(cw, "input_selector_confirmed")
        else:
            input_result = await tab.find_chat_input(list(composer.input_selectors))

        if not input_result.get("found"):
            logger.info("[%s] Selectors failed, trying vision to find input...", cw.site.name)
            self._record_window_event(cw, "input_fallback_vision_started")
            if self._simple_vision_ready_task is not None:
                await self._simple_vision_ready_task
                self._record_window_event(cw, "simple_model_ready")

            image = await self._save_screenshot(cw, "01_loaded", tab=tab)
            self._record_window_event(cw, "loaded_screenshot_saved")
            ready_result_text, ready_elapsed, ready_profile = await self._verify_with_vision(
                cw,
                image,
                READY_CHECK_PROMPT,
                config=CHATBOT_PAGE_SIMPLE_CHECK,
            )
            self._record_window_event(
                cw,
                "ready_check_finished",
                seconds=round(ready_elapsed, 3),
                profile=ready_profile,
                result=ready_result_text[:32],
            )
            location = self.vision.locate_element(
                image,
                "text input field or message box where I can type a question",
                config=CHATBOT_COMPLEX_FALLBACK_CHECK,
            )
            if location and location.get("found"):
                click_x = int(location["x"] * image.size[0] / 100)
                click_y = int(location["y"] * image.size[1] / 100)
                await tab.click_at(click_x, click_y)
                input_result = {
                    "found": True,
                    "x": click_x,
                    "y": click_y,
                    "inputX": click_x,
                    "inputY": click_y,
                }
                cw.vision_logs.append(f"Vision-located input at ({click_x}, {click_y})")
                self._record_window_event(cw, "input_fallback_vision_succeeded", x=click_x, y=click_y)
            else:
                cw.status = "error"
                cw.error = "Could not find input field"
                self._record_window_event(cw, "input_fallback_vision_failed")
                return

        entry_result = await enter_text(
            tab,
            composer,
            question,
            input_result=input_result,
            focus_settle_s=TEXT_SETTLE_S,
        )
        if entry_result.method == "dom":
            self._record_window_event(
                cw,
                "question_set_via_dom",
                applied_text_length=entry_result.raw.get("appliedTextLength", 0),
                has_text=bool(entry_result.raw.get("hasText", False)),
            )
        else:
            self._record_window_event(cw, "question_typed_via_keyboard")

        cw.status = "input_done"
        self._record_window_event(cw, "question_entered")

        await asyncio.sleep(TEXT_SETTLE_S)
        await self._save_screenshot(cw, "02_text_entered", tab=tab)
        self._record_window_event(cw, "text_entered_screenshot_saved")

        async def _after_submit(attempt_name: str, _strategy: str) -> None:
            await self._save_screenshot(cw, f"03_after_submit_{attempt_name}", tab=tab)
            self._record_window_event(cw, "post_submit_screenshot_saved", attempt=attempt_name)

        async def _on_submit_dispatched(
            attempt_name: str,
            strategy: str,
            submit_performed: bool,
            submit_result: dict | None,
        ) -> None:
            if strategy == "enter":
                self._record_window_event(cw, "submitted_via_enter", attempt=attempt_name)
            else:
                submit_result = submit_result or {}
                self._record_window_event(
                    cw,
                    "submitted_via_button",
                    attempt=attempt_name,
                    clicked=bool(submit_result.get("clicked")),
                    hint=submit_result.get("hint", ""),
                    input_emptied=bool(submit_result.get("inputEmptied", False)),
                )
                if not submit_performed:
                    self._record_window_event(cw, "submit_button_not_found", attempt=attempt_name)
                    return

            cw.status = "submitted"
            self._record_window_event(cw, "question_submitted", attempt=attempt_name)

        async def _on_attempt_resolved(attempt_result) -> None:
            if attempt_result.assessment is None:
                return

            assessment = attempt_result.assessment
            self._record_window_event(
                cw,
                "post_submit_dom_state",
                attempt=attempt_result.attempt,
                found=assessment.found,
                empty=assessment.empty,
                text_length=assessment.text_length,
                status=assessment.status,
            )

            if attempt_result.outcome == "retry":
                logger.warning("[%s] Submit attempt %s left the prompt in the composer", cw.site.name, attempt_result.attempt)
                self._record_window_event(
                    cw,
                    "submit_retry_needed",
                    attempt=attempt_result.attempt,
                    label="READY",
                )
                return

            if attempt_result.outcome == "sent":
                cw.status = "generating"
                cw.error = ""
                self._record_window_event(cw, "submit_dom_confirmed_sent", attempt=attempt_result.attempt)
                return

            self._record_window_event(cw, "submit_dom_state_ambiguous", attempt=attempt_result.attempt)

        submit_result = await submit_with_dom_verification(
            tab,
            composer,
            question,
            input_result=input_result,
            focus_settle_s=TEXT_SETTLE_S,
            post_submit_settle_s=POST_SUBMIT_SETTLE_S,
            on_submit_dispatched=_on_submit_dispatched,
            on_after_submit=_after_submit,
            on_attempt_resolved=_on_attempt_resolved,
        )

        if cw.status != "generating" and not cw.error:
            if self.verify_visible_windows:
                cw.status = "submitted"
            else:
                cw.status = "error"
                cw.error = f"Prompt state stayed unresolved after submit attempts ({submit_result.status})"
        self._record_window_event(cw, "interaction_finished", status=cw.status)


def run_multi_chat_sync(
    question: str,
    *,
    port: int = 8765,
    output_dir: Path | None = None,
    vision_backend: str | None = None,
    cleanup_orphaned: bool = True,
    verify_visible_windows: bool = True,
    close_windows_on_finish: bool = False,
) -> dict:
    """Blocking entry point for CLI / Tauri integration."""

    async def _run():
        runner = MultiChatRunner(
            port=port,
            output_dir=output_dir,
            vision_backend=vision_backend,
            cleanup_orphaned=cleanup_orphaned,
            verify_visible_windows=verify_visible_windows,
            close_windows_on_finish=close_windows_on_finish,
        )
        return await runner.run(question)

    return asyncio.run(_run())
