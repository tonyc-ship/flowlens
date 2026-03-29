"""Workflow runner for faning one prompt out to multiple chatbot sites."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from ..agent.bridge import ExtensionBridge
from ..agent.verification import assess_expected_text_state
from ..vision.llm import VisionRequestConfig
from .cleanup import cleanup_orphaned_chrome_processes
from .sites import CHATBOT_SITES, ChatbotSite
from .vision_diff import build_transition_composite
from .vision_profiles import (
    CHATBOT_COMPLEX_FALLBACK_CHECK,
    CHATBOT_INPUT_SIMPLE_CHECK,
    CHATBOT_PAGE_SIMPLE_CHECK,
)

logger = logging.getLogger(__name__)

DEFAULT_CONNECT_TIMEOUT_S = 12.0
DEFAULT_PAGE_LOAD_TIMEOUT_S = 45.0
FAST_CONNECT_TIMEOUT_S = 0.75
INPUT_READY_POLL_INTERVAL_S = 0.25
TEXT_SETTLE_S = 0.12
POST_SUBMIT_SETTLE_S = 0.75
SUBMIT_TRANSITION_CROP = (0.0, 0.42, 1.0, 1.0)


def _yes_no_prompt(text: str) -> str:
    return f"{text} Answer YES or NO only."

READY_CHECK_PROMPT = _yes_no_prompt("Is the prompt box visible and usable?")
TYPED_CHECK_PROMPT = _yes_no_prompt("Is typed text visible in the prompt box?")
SENT_CHECK_PROMPT = (
    "Answer YES only if the prompt has already been sent and is visible above the input box. "
    "If the text is only inside the input box, answer NO. Answer YES or NO only."
)


def _visible_submit_prompt(site_name: str, question: str) -> str:
    trimmed = question.strip().replace("\n", " ")[:180]
    return (
        f"Intended site: {site_name}. User prompt: {trimmed}\n"
        "Look at this visible browser window and answer with exactly one label:\n"
        "GENERATING = correct site, and the prompt has already been sent. Any of these count as GENERATING: "
        "the prompt appears as a sent chat bubble above the composer; the composer is mostly empty after sending; "
        "a stop button or stop square replaced the normal send arrow; a typing dot/spinner is visible; or any assistant reply text is visible.\n"
        "READY = correct site, but the full prompt is still sitting inside the composer waiting to be sent. "
        "A menu or popup is still READY only if the prompt remains unsent in the composer.\n"
        "BLOCKED = correct site, but there is a usage/login/paywall/blocker.\n"
        "WRONG_SITE = not the intended site.\n"
        "If a popup or dropdown is open but the prompt is already sent or a reply is generating, answer GENERATING.\n"
        "Focus on the newest message area and the composer near the bottom. Ignore old history above.\n"
        "If any distinctive excerpt of the user prompt moved above the composer, that counts as GENERATING.\n"
        "Answer with one label only."
    )


def _submit_transition_prompt(site_name: str, question: str) -> str:
    trimmed = question.strip().replace("\n", " ")[:180]
    return (
        f"Intended site: {site_name}. User prompt: {trimmed}\n"
        "This image has three panels of the same tab:\n"
        "LEFT = before pressing send.\n"
        "CENTER = after pressing send.\n"
        "RIGHT = pixel changes between them.\n"
        "Answer with exactly one label:\n"
        "GENERATING = the CENTER panel shows the prompt was sent or the assistant is responding.\n"
        "READY = the CENTER panel still shows the full prompt sitting in the composer unsent.\n"
        "BLOCKED = the correct site is open but a login, usage limit, or paywall blocks sending.\n"
        "WRONG_SITE = not the intended site.\n"
        "Count as GENERATING if the prompt moved above the composer, the composer mostly emptied, "
        "a stop button replaced send, a typing indicator is visible, or any assistant reply text is visible.\n"
        "Count as READY if the prompt still sits in the composer even if menus or popups are open.\n"
        "Focus on the composer and newest content near the bottom. Answer with one label only."
    )


# ── Window state tracking ─────────────────────────────────────


@dataclass
class ChatbotWindow:
    """Runtime state for one chatbot window."""

    site: ChatbotSite
    tab_id: int = 0
    window_id: int = 0
    planned_bounds: dict[str, int] = field(default_factory=dict)
    status: str = "pending"  # pending | loading | ready | input_done | submitted | generating | error
    error: str = ""
    screenshots: list[Path] = field(default_factory=list)
    vision_logs: list[str] = field(default_factory=list)
    visible_screenshots: list[Path] = field(default_factory=list)
    visible_logs: list[str] = field(default_factory=list)
    timeline: list[dict[str, object]] = field(default_factory=list)


# ── Orchestrator ──────────────────────────────────────────────


class MultiChatRunner:
    """Opens ChatGPT, Gemini, and Claude and enters a question in all three."""

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

        # Resolve vision backend: prefer local if available
        if vision_backend is None:
            from ..agent.local_llm import LocalLLM

            if LocalLLM.is_available():
                vision_backend = "qwen-local"
            else:
                vision_backend = "sonnet"
        self._vision_backend = vision_backend
        self._vision = None  # lazy
        self._macos = None
        self._visual_debugger = None
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
    def visual_debugger(self):
        if self._visual_debugger is None:
            from ..debug import VisualDebugger

            self._visual_debugger = VisualDebugger(llm_backend="qwen-local")
        return self._visual_debugger

    # ── Helpers ────────────────────────────────────────────────

    def _screenshot_to_pil(self, data_url: str) -> Image.Image:
        """Convert a base64 data URL from the bridge to a PIL Image."""
        b64 = data_url.split(",", 1)[1] if "," in data_url else data_url
        return Image.open(io.BytesIO(base64.b64decode(b64)))

    async def _save_screenshot(self, cw: ChatbotWindow, label: str, *, tab) -> Image.Image:
        """Capture, save, and return a screenshot for a chatbot window."""
        data_url = await tab.capture_screenshot()
        if not data_url:
            raise RuntimeError(f"Screenshot capture failed for {cw.site.name} ({label})")
        img = self._screenshot_to_pil(data_url)
        path = self.output_dir / f"{cw.site.name.lower()}_{label}.png"
        img.save(path)
        cw.screenshots.append(path)
        return img

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
        img: Image.Image,
        question: str,
        *,
        config: VisionRequestConfig | None = None,
    ) -> tuple[str, float, str]:
        """Run a vision check and log the result."""
        logger.info("[%s] Vision check: %s", cw.site.name, question)
        t0 = time.perf_counter()
        result = self.vision.analyze_page(img, question, config=config)
        elapsed = time.perf_counter() - t0
        profile_label = config.name if config else "default"
        log_entry = f"[{elapsed:.1f}s][{profile_label}] {question} -> {result[:200]}"
        cw.vision_logs.append(log_entry)
        logger.info("[%s] Vision result (%.1fs): %s", cw.site.name, elapsed, result[:200])
        return result, elapsed, profile_label

    async def _verify_submit_transition(
        self,
        cw: ChatbotWindow,
        before_img: Image.Image,
        after_img: Image.Image,
        question: str,
        *,
        attempt: str,
    ) -> str:
        """Verify that a submit attempt changed the tab from composer-filled to sent."""
        composite = build_transition_composite(
            before_img,
            after_img,
            crop_bounds=SUBMIT_TRANSITION_CROP,
            include_diff=True,
        )
        path = self.output_dir / f"{cw.site.name.lower()}_{attempt}_submit_transition.png"
        composite.save(path)
        cw.screenshots.append(path)

        prompt = _submit_transition_prompt(cw.site.name, question)
        t0 = time.perf_counter()
        result = self.vision.analyze_page(
            composite,
            prompt,
            config=CHATBOT_INPUT_SIMPLE_CHECK,
        )
        elapsed = time.perf_counter() - t0
        label = self._parse_status_label(result)
        cw.vision_logs.append(
            f"[{elapsed:.1f}s][{CHATBOT_INPUT_SIMPLE_CHECK.name}][{attempt}] {prompt[:160]} -> {result[:200]}"
        )

        if label not in ("GENERATING", "BLOCKED", "WRONG_SITE"):
            t1 = time.perf_counter()
            fallback_result = self.vision.analyze_page(
                composite,
                prompt,
                config=CHATBOT_COMPLEX_FALLBACK_CHECK,
            )
            fallback_elapsed = time.perf_counter() - t1
            fallback_label = self._parse_status_label(fallback_result)
            cw.vision_logs.append(
                f"[{fallback_elapsed:.1f}s][{CHATBOT_COMPLEX_FALLBACK_CHECK.name}][{attempt}] "
                f"{prompt[:160]} -> {fallback_result[:200]}"
            )
            if fallback_label != "UNKNOWN":
                label = fallback_label
                elapsed += fallback_elapsed

        self._record_window_event(
            cw,
            "submit_transition_check_finished",
            attempt=attempt,
            label=label,
            seconds=round(elapsed, 3),
        )
        return label

    async def _focus_chat_input(self, tab, input_result: dict) -> None:
        click_x = input_result.get("inputX") or input_result.get("x")
        click_y = input_result.get("inputY") or input_result.get("y")
        if click_x and click_y:
            await tab.click_at(click_x, click_y)
            await asyncio.sleep(TEXT_SETTLE_S)

    async def _wait_for_extension(self) -> None:
        """Wait for the extension connection, waking Chrome if needed."""
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

    async def _wait_for_input_ready(self, cw: ChatbotWindow, tab, timeout_s: float = DEFAULT_PAGE_LOAD_TIMEOUT_S) -> dict:
        """Poll until a chatbot input field becomes available."""
        deadline = time.perf_counter() + timeout_s
        while time.perf_counter() < deadline:
            result = await tab.find_chat_input(cw.site.input_selectors)
            if result and result.get("found"):
                return result
            await asyncio.sleep(INPUT_READY_POLL_INTERVAL_S)
        return {"found": False}

    @staticmethod
    def _window_distance(bounds: dict[str, int], window) -> int:
        return (
            abs(window.x - bounds.get("left", 0))
            + abs(window.y - bounds.get("top", 0))
            + abs(window.width - bounds.get("width", 0))
            + abs(window.height - bounds.get("height", 0))
        )

    def _match_visible_windows(self) -> list[tuple[ChatbotWindow, object | None]]:
        """Match planned chatbot bounds to currently visible Chrome windows."""
        visible_windows = [
            window
            for window in self.macos.list_windows(app_name="Google Chrome", on_screen_only=True)
            if window.width >= 480 and window.height >= 360
        ]
        unused = visible_windows[:]
        matches: list[tuple[ChatbotWindow, object | None]] = []
        for cw in sorted(self.windows, key=lambda item: item.planned_bounds.get("left", 0)):
            title_match = next(
                (
                    window
                    for window in unused
                    if cw.site.name.lower() in (window.title or "").lower()
                ),
                None,
            )
            if title_match is not None:
                unused.remove(title_match)
                matches.append((cw, title_match))
                continue
            if not unused:
                matches.append((cw, None))
                continue
            best = min(unused, key=lambda window: self._window_distance(cw.planned_bounds, window))
            unused.remove(best)
            matches.append((cw, best))
        return matches

    @staticmethod
    def _parse_status_label(result: str) -> str:
        normalized = result.strip().upper()
        for label in ("GENERATING", "READY", "BLOCKED", "WRONG_SITE"):
            if label in normalized:
                return label
        if normalized.startswith("YES"):
            return "GENERATING"
        if normalized.startswith("NO"):
            return "READY"
        return "UNKNOWN"

    def _apply_visible_status_label(self, cw: ChatbotWindow, label: str) -> None:
        if label == "GENERATING":
            cw.status = "generating"
            cw.error = ""
            return
        if label == "BLOCKED":
            cw.status = "error"
            cw.error = "Blocked by login / usage / paywall"
            return
        if label == "WRONG_SITE":
            cw.status = "error"
            cw.error = "Visible window did not match intended chatbot site"
            return
        if label in ("READY", "UNKNOWN"):
            cw.status = "error"
            cw.error = "Prompt appears unsent or page stayed in ready state"

    def _verify_visible_windows(self, question: str, *, stage: str) -> None:
        """Verify the actual visible Chrome windows with a lightweight local vision pass."""
        if not self.verify_visible_windows:
            return

        from ..agent.local_llm import LocalLLM

        if not LocalLLM.is_available():
            logger.warning("Skipping visible-window verification: local Qwen vision model not available")
            for cw in self.windows:
                cw.visible_logs.append(f"[{stage}] skipped: local qwen model unavailable")
            return

        self.macos.activate_app("Google Chrome")
        time.sleep(1.0)

        for cw, window in self._match_visible_windows():
            if window is None:
                cw.visible_logs.append(f"[{stage}] no visible Chrome window matched planned bounds")
                if stage == "after_submit":
                    cw.status = "error"
                    cw.error = "No visible Chrome window matched planned bounds"
                continue

            image = self.macos.capture_window_info(window)
            inspect_dir = self.output_dir / "visible_verification"
            inspect_dir.mkdir(parents=True, exist_ok=True)
            path = inspect_dir / f"{cw.site.name.lower()}_{stage}_visible.png"
            image.save(path)
            cw.visible_screenshots.append(path)
            t0 = time.perf_counter()
            result = self.vision.analyze_page(
                image,
                _visible_submit_prompt(cw.site.name, question),
                config=CHATBOT_PAGE_SIMPLE_CHECK,
            )
            elapsed = time.perf_counter() - t0
            label = self._parse_status_label(result)
            cw.visible_logs.append(
                f"[{stage}][{elapsed:.1f}s][{CHATBOT_PAGE_SIMPLE_CHECK.name}] {result[:200]}"
            )
            if label not in ("GENERATING", "BLOCKED", "WRONG_SITE"):
                t1 = time.perf_counter()
                fallback_result = self.vision.analyze_page(
                    image,
                    _visible_submit_prompt(cw.site.name, question),
                    config=CHATBOT_COMPLEX_FALLBACK_CHECK,
                )
                fallback_elapsed = time.perf_counter() - t1
                fallback_label = self._parse_status_label(fallback_result)
                cw.visible_logs.append(
                    f"[{stage}][{fallback_elapsed:.1f}s][{CHATBOT_COMPLEX_FALLBACK_CHECK.name}] {fallback_result[:200]}"
                )
                if fallback_label != "UNKNOWN":
                    label = fallback_label
            self._record_window_event(
                cw,
                "visible_verification_finished",
                stage=stage,
                seconds=round(elapsed, 3),
                label=label,
            )
            if stage == "after_submit":
                self._apply_visible_status_label(cw, label)

    # ── Screen geometry ───────────────────────────────────────

    def _compute_window_layout(self, count: int = 3) -> list[dict]:
        """Compute side-by-side window positions.

        Uses reasonable defaults for a typical display. The windows fill
        most of the screen width with a small margin.
        """
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

        # Leave room for dock / menu bar
        top = 28  # macOS menu bar
        usable_h = screen_h - top - 4
        win_w = screen_w // count
        positions = []
        for i in range(count):
            positions.append({
                "left": i * win_w,
                "top": top,
                "width": win_w,
                "height": usable_h,
            })
        return positions

    # ── Core flow ─────────────────────────────────────────────

    async def run(self, question: str) -> dict:
        """Open all chatbot windows and enter the question.

        Returns a summary dict with status per chatbot.
        """
        self.output_dir.mkdir(parents=True, exist_ok=True)
        t0 = time.perf_counter()
        self._run_started_at = t0
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

        # Start bridge if we own it
        if self._owns_bridge:
            self._record_global_event("bridge_starting")
            await self.bridge.start()
            await self._wait_for_extension()

        positions = self._compute_window_layout(len(self.windows))

        # 1. Open all windows
        logger.info("Opening %d chatbot windows...", len(self.windows))
        self._record_global_event("window_opening_started", count=len(self.windows))
        for cw, pos in zip(self.windows, positions):
            cw.planned_bounds = pos
            try:
                result = await self.bridge.create_background_window(
                    cw.site.url,
                    lock=False,
                    focused=True,
                    width=pos["width"],
                    height=pos["height"],
                    left=pos["left"],
                    top=pos["top"],
                )
                cw.tab_id = result["tabId"]
                cw.window_id = result["windowId"]
                cw.status = "loading"
                logger.info("[%s] Window created: tab=%d win=%d", cw.site.name, cw.tab_id, cw.window_id)
                self._record_window_event(
                    cw,
                    "window_created",
                    tab_id=cw.tab_id,
                    window_id=cw.window_id,
                    left=pos["left"],
                    top=pos["top"],
                    width=pos["width"],
                    height=pos["height"],
                )
            except Exception as exc:
                cw.status = "error"
                cw.error = str(exc)
                logger.error("[%s] Failed to create window: %s", cw.site.name, exc)
                self._record_window_event(cw, "window_create_failed", error=str(exc))
        self._record_global_event("window_opening_finished")

        # 2. Launch per-chatbot work in parallel.
        logger.info("Bootstrapping chatbot tabs...")
        # 3. For each chatbot: enter question and submit in parallel.
        tasks = []
        scheduled_windows = []
        self._record_global_event("chatbot_tasks_starting")
        for cw in self.windows:
            if cw.status == "error":
                continue
            scheduled_windows.append(cw)
            tasks.append(asyncio.create_task(self._handle_chatbot(cw, question)))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        self._record_global_event("chatbot_tasks_finished")
        for cw, result in zip(scheduled_windows, results):
            if isinstance(result, Exception):
                cw.status = "error"
                cw.error = str(result)
                logger.error("[%s] Error: %s", cw.site.name, result)
                self._record_window_event(cw, "task_failed", error=str(result))

        if self._simple_vision_ready_task is not None:
            await self._simple_vision_ready_task

        if self.verify_visible_windows:
            self._record_global_event("visible_verification_started", stage="after_submit")
            self._verify_visible_windows(question, stage="after_submit")
            self._record_global_event("visible_verification_finished", stage="after_submit")
        else:
            self._record_global_event("visible_verification_skipped", reason="disabled")

        if self.close_windows_on_finish:
            self._record_global_event("window_cleanup_started")
            await self._close_created_windows()
            self._record_global_event("window_cleanup_finished")
        else:
            self.macos.activate_app("Google Chrome")

        elapsed = time.perf_counter() - t0
        self._record_global_event("run_finished", elapsed_s=round(elapsed, 3))

        # 4. Build summary
        summary = {
            "question": question,
            "elapsed_s": round(elapsed, 1),
            "vision_backend": self._vision_backend,
            "preflight_cleanup": self.preflight_cleanup,
            "visible_verification": self.verify_visible_windows,
            "timeline": self._timeline,
            "chatbots": [],
        }
        for cw in self.windows:
            summary["chatbots"].append({
                "name": cw.site.name,
                "status": cw.status,
                "error": cw.error,
                "tab_id": cw.tab_id,
                "window_id": cw.window_id,
                "planned_bounds": cw.planned_bounds,
                "screenshots": [str(p) for p in cw.screenshots],
                "vision_logs": cw.vision_logs,
                "visible_screenshots": [str(p) for p in cw.visible_screenshots],
                "visible_logs": cw.visible_logs,
                "timeline": cw.timeline,
            })
        # Save summary
        summary_path = self.output_dir / "summary.json"
        summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
        logger.info("Done in %.1fs. Summary: %s", elapsed, summary_path)
        self._write_timing_breakdown(summary)

        # Generate report
        self._write_report(summary)

        return summary

    async def _handle_chatbot(self, cw: ChatbotWindow, question: str) -> None:
        """Process a single chatbot: verify page, enter text, submit."""
        logger.info("[%s] Starting interaction...", cw.site.name)
        self._record_window_event(cw, "interaction_started")
        tab = self.bridge.tab(cw.tab_id, window_id=cw.window_id)

        ready_result = await self._wait_for_input_ready(cw, tab)
        if not ready_result.get("found"):
            logger.warning("[%s] Input selectors not ready after wait; falling back to screenshot + vision", cw.site.name)
            self._record_window_event(cw, "input_ready_timeout")
        else:
            self._record_window_event(
                cw,
                "input_ready",
                selector=ready_result.get("selector", ""),
            )
        cw.status = "ready"

        # Find and click the input field
        input_result = ready_result if ready_result.get("found") else {"found": False}
        if input_result.get("found"):
            logger.info("[%s] Input became ready via selector polling", cw.site.name)
            self._record_window_event(cw, "input_selector_confirmed")
        else:
            input_result = await tab.find_chat_input(cw.site.input_selectors)
        if not input_result.get("found"):
            # Fallback: use vision to locate input
            logger.info("[%s] Selectors failed, trying vision to find input...", cw.site.name)
            self._record_window_event(cw, "input_fallback_vision_started")
            if self._simple_vision_ready_task is not None:
                await self._simple_vision_ready_task
                self._record_window_event(cw, "simple_model_ready")
            img = await self._save_screenshot(cw, "01_loaded", tab=tab)
            self._record_window_event(cw, "loaded_screenshot_saved")
            ready_result_text, ready_elapsed, ready_profile = await self._verify_with_vision(
                cw,
                img,
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
            cw.vision_logs.append(f"Ready check: {ready_result_text[:200]}")
            location = self.vision.locate_element(
                img,
                "text input field or message box where I can type a question",
                config=CHATBOT_COMPLEX_FALLBACK_CHECK,
            )
            if location and location.get("found"):
                w, h = img.size
                click_x = int(location["x"] * w / 100)
                click_y = int(location["y"] * h / 100)
                await tab.click_at(click_x, click_y)
                cw.vision_logs.append(f"Vision-located input at ({click_x}, {click_y})")
                self._record_window_event(cw, "input_fallback_vision_succeeded", x=click_x, y=click_y)
            else:
                cw.status = "error"
                cw.error = "Could not find input field"
                self._record_window_event(cw, "input_fallback_vision_failed")
                return

        set_result = await tab.set_chat_input_text(cw.site.input_selectors, question)
        if not set_result.get("ok"):
            await self._focus_chat_input(tab, input_result)
            await tab.type_text(question)
            self._record_window_event(cw, "question_typed_via_keyboard")
        else:
            self._record_window_event(
                cw,
                "question_set_via_dom",
                applied_text_length=set_result.get("appliedTextLength", 0),
                has_text=bool(set_result.get("hasText", False)),
            )
        cw.status = "input_done"
        logger.info("[%s] Question entered", cw.site.name)
        self._record_window_event(cw, "question_entered")

        await asyncio.sleep(TEXT_SETTLE_S)
        await self._save_screenshot(cw, "02_text_entered", tab=tab)
        self._record_window_event(cw, "text_entered_screenshot_saved")

        primary_strategy = "enter" if cw.site.submit_mode == "enter" else "button"
        attempt_order = [primary_strategy]
        if primary_strategy == "enter":
            attempt_order.append("button")
        else:
            attempt_order.append("enter")

        final_label = "UNKNOWN"
        for attempt_index, strategy in enumerate(attempt_order, start=1):
            attempt_name = f"{strategy}_{attempt_index}"

            if strategy == "enter":
                await self._focus_chat_input(tab, input_result)
                await tab.press_key("Enter", code="Enter")
                self._record_window_event(cw, "submitted_via_enter", attempt=attempt_name)
            else:
                submit_result = await tab.click_chat_submit(cw.site.submit_selectors, anchor=input_result)
                self._record_window_event(
                    cw,
                    "submitted_via_button",
                    attempt=attempt_name,
                    clicked=bool(submit_result.get("clicked")),
                    hint=submit_result.get("hint", ""),
                    input_emptied=bool(submit_result.get("inputEmptied", False)),
                )
                if not submit_result.get("clicked"):
                    self._record_window_event(cw, "submit_button_not_found", attempt=attempt_name)
                    continue

            cw.status = "submitted"
            self._record_window_event(cw, "question_submitted", attempt=attempt_name)
            await asyncio.sleep(POST_SUBMIT_SETTLE_S)
            after_img = await self._save_screenshot(cw, f"03_after_submit_{attempt_name}", tab=tab)
            self._record_window_event(cw, "post_submit_screenshot_saved", attempt=attempt_name)

            post_state = await tab.get_chat_input_state(cw.site.input_selectors)
            assessment = assess_expected_text_state(post_state, question)
            self._record_window_event(
                cw,
                "post_submit_dom_state",
                attempt=attempt_name,
                found=assessment.found,
                empty=assessment.empty,
                text_length=assessment.text_length,
                status=assessment.status,
            )
            if assessment.status == "contains_expected":
                final_label = "READY"
                logger.warning("[%s] Submit attempt %s left the prompt in the composer", cw.site.name, attempt_name)
                self._record_window_event(cw, "submit_retry_needed", attempt=attempt_name, label=final_label)
                continue

            if assessment.status == "empty":
                final_label = "GENERATING"
                cw.status = "generating"
                cw.error = ""
                self._record_window_event(cw, "submit_dom_confirmed_sent", attempt=attempt_name)
                break

            final_label = "UNKNOWN"
            self._record_window_event(cw, "submit_dom_state_ambiguous", attempt=attempt_name)
            break

        if cw.status != "generating" and not cw.error:
            if self.verify_visible_windows:
                cw.status = "submitted"
            else:
                cw.status = "error"
                cw.error = f"Prompt state stayed ambiguous after submit attempts ({final_label})"
        self._record_window_event(cw, "interaction_finished", status=cw.status)

    def _write_timing_breakdown(self, summary: dict) -> None:
        """Write a readable markdown timing breakdown beside the summary."""
        lines = ["# Timing Breakdown", "", f"Total elapsed: `{summary['elapsed_s']}s`", ""]

        lines.append("## Global Timeline")
        prev_t = None
        for item in summary.get("timeline", []):
            t = float(item.get("t", 0.0))
            delta = "" if prev_t is None else f" (+{t - prev_t:.3f}s)"
            detail = ", ".join(f"{k}={v}" for k, v in item.items() if k not in {"t", "event"})
            suffix = f" | {detail}" if detail else ""
            lines.append(f"- `{t:7.3f}s` {item.get('event')}{delta}{suffix}")
            prev_t = t

        for cb in summary.get("chatbots", []):
            lines.extend(["", f"## {cb['name']}", ""])
            prev_t = None
            for item in cb.get("timeline", []):
                t = float(item.get("t", 0.0))
                delta = "" if prev_t is None else f" (+{t - prev_t:.3f}s)"
                detail = ", ".join(f"{k}={v}" for k, v in item.items() if k not in {"t", "event"})
                suffix = f" | {detail}" if detail else ""
                lines.append(f"- `{t:7.3f}s` {item.get('event')}{delta}{suffix}")
                prev_t = t

        path = self.output_dir / "timing_breakdown.md"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ── Report ────────────────────────────────────────────────

    def _write_report(self, summary: dict) -> None:
        """Generate an HTML report with screenshots and vision logs."""
        chatbot_cards = []
        for cb in summary["chatbots"]:
            screenshots_html = ""
            for sp in cb["screenshots"]:
                name = Path(sp).name
                screenshots_html += f'<img src="{name}" style="max-width:100%;margin:4px 0;border-radius:6px;">\n'

            visible_html = ""
            for sp in cb.get("visible_screenshots", []):
                path = Path(sp)
                rel = path.relative_to(self.output_dir) if path.is_relative_to(self.output_dir) else path.name
                visible_html += f'<img src="{rel}" style="max-width:100%;margin:4px 0;border-radius:6px;border:1px solid #324;">\n'

            vision_html = ""
            for log in cb["vision_logs"]:
                vision_html += f"<div class='vision-log'>{log}</div>\n"

            visible_log_html = ""
            for log in cb.get("visible_logs", []):
                visible_log_html += f"<div class='vision-log'>{log}</div>\n"

            status_class = "ok" if cb["status"] == "generating" else "warn" if cb["status"] != "error" else "err"
            chatbot_cards.append(f"""
            <div class="card {status_class}">
                <h2>{cb['name']}</h2>
                <div class="status">Status: <strong>{cb['status']}</strong></div>
                {"<div class='error'>Error: " + cb['error'] + "</div>" if cb['error'] else ""}
                <div class="status">Chrome window: <strong>{cb.get('window_id', '')}</strong></div>
                <div class="screenshots">{screenshots_html}</div>
                {"<h3>Visible Window Checks</h3><div class='screenshots'>" + visible_html + "</div>" if visible_html else ""}
                <details><summary>Vision Logs ({len(cb['vision_logs'])})</summary>
                    {vision_html}
                </details>
                {"<details><summary>Visible Verification (" + str(len(cb.get('visible_logs', []))) + ")</summary>" + visible_log_html + "</details>" if cb.get('visible_logs') else ""}
            </div>
            """)

        cleanup = summary.get("preflight_cleanup") or {}
        html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Multi-Chat Report</title>
<style>
    body {{ font-family: system-ui; background: #1a1a2e; color: #e0e0e0; padding: 20px; margin: 0; }}
    h1 {{ color: #ff7e5c; margin-bottom: 4px; }}
    h3 {{ margin: 16px 0 8px; color: #ffd3c7; font-size: 0.95rem; }}
    .meta {{ color: #888; margin-bottom: 20px; }}
    .question {{ background: #16213e; padding: 16px; border-radius: 8px; margin-bottom: 20px;
                 border-left: 4px solid #ff7e5c; font-size: 1.1em; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(350px, 1fr)); gap: 16px; }}
    .card {{ background: #16213e; border-radius: 10px; padding: 16px; }}
    .card.ok {{ border-left: 4px solid #41d89b; }}
    .card.warn {{ border-left: 4px solid #f0ad4e; }}
    .card.err {{ border-left: 4px solid #e74c3c; }}
    .card h2 {{ margin-top: 0; color: #ff7e5c; }}
    .status {{ margin: 8px 0; }}
    .error {{ color: #e74c3c; margin: 8px 0; }}
    .screenshots img {{ box-shadow: 0 2px 8px rgba(0,0,0,0.4); }}
    .vision-log {{ font-size: 0.85em; color: #aaa; padding: 4px 0; border-bottom: 1px solid #2a2a4a; white-space: pre-wrap; }}
    details {{ margin-top: 12px; }}
    summary {{ cursor: pointer; color: #888; }}
</style></head><body>
<h1>Multi-Chatbot Question</h1>
<div class="meta">Elapsed: {summary['elapsed_s']}s | Vision: {summary['vision_backend']}</div>
<div class="meta">Preflight cleanup: matched {cleanup.get('matched', 0)} temp Chrome processes, remaining {len(cleanup.get('remaining', []))}</div>
<div class="question">{summary['question']}</div>
<div class="grid">{''.join(chatbot_cards)}</div>
</body></html>"""

        report_path = self.output_dir / "report.html"
        report_path.write_text(html)
        logger.info("Report written: %s", report_path)


# ── Sync entry point ──────────────────────────────────────────


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
