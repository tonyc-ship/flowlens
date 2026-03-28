"""Workflow runner for faning one prompt out to multiple chatbot sites."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from ..agent.bridge import ExtensionBridge
from .cleanup import cleanup_orphaned_chrome_processes
from .sites import CHATBOT_SITES, ChatbotSite

logger = logging.getLogger(__name__)

DEFAULT_CONNECT_TIMEOUT_S = 12.0
DEFAULT_PAGE_LOAD_TIMEOUT_S = 45.0


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
    ):
        self.bridge = bridge or ExtensionBridge(port=port)
        self._owns_bridge = bridge is None
        self.output_dir = output_dir or Path("task_runs/multi_chat")
        self.windows: list[ChatbotWindow] = [ChatbotWindow(site=site) for site in CHATBOT_SITES]
        self.cleanup_orphaned = cleanup_orphaned
        self.verify_visible_windows = verify_visible_windows
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

    async def _save_screenshot(self, cw: ChatbotWindow, label: str) -> Image.Image:
        """Capture, save, and return a screenshot for a chatbot window."""
        data_url = await self.bridge.capture_screenshot()
        img = self._screenshot_to_pil(data_url)
        path = self.output_dir / f"{cw.site.name.lower()}_{label}.png"
        img.save(path)
        cw.screenshots.append(path)
        return img

    async def _verify_with_vision(self, cw: ChatbotWindow, img: Image.Image, question: str) -> str:
        """Run a vision check and log the result."""
        logger.info("[%s] Vision check: %s", cw.site.name, question)
        t0 = time.perf_counter()
        result = self.vision.analyze_page(img, question)
        elapsed = time.perf_counter() - t0
        log_entry = f"[{elapsed:.1f}s] {question} -> {result[:200]}"
        cw.vision_logs.append(log_entry)
        logger.info("[%s] Vision result (%.1fs): %s", cw.site.name, elapsed, result[:200])
        return result

    async def _wait_for_extension(self) -> None:
        """Wait for the extension connection, waking Chrome if needed."""
        logger.info("Bridge started, waiting for extension connection...")
        try:
            await self.bridge.wait_for_connection(timeout=DEFAULT_CONNECT_TIMEOUT_S)
            logger.info("Extension connected")
            return
        except RuntimeError:
            logger.info("Extension not connected yet; launching Google Chrome to wake the runtime")

        subprocess.run(["open", "-a", "Google Chrome"], check=True)
        self.macos.activate_app("Google Chrome")
        await asyncio.sleep(2)
        await self.bridge.wait_for_connection(timeout=45)
        logger.info("Extension connected after waking Chrome")

    async def _wait_for_input_ready(self, cw: ChatbotWindow, timeout_s: float = DEFAULT_PAGE_LOAD_TIMEOUT_S) -> dict:
        """Poll until a chatbot input field becomes available."""
        deadline = time.perf_counter() + timeout_s
        while time.perf_counter() < deadline:
            result = await self.bridge.find_chat_input(cw.site.input_selectors)
            if result and result.get("found"):
                return result
            await asyncio.sleep(2)
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

    def _record_visible_verification(self, cw: ChatbotWindow, stage: str, inspection: dict) -> None:
        artifacts = inspection.get("artifacts", {})
        raw_image = artifacts.get("raw_image")
        if raw_image:
            cw.visible_screenshots.append(Path(raw_image))

        capture_verification = (inspection.get("capture_verification") or {}).get("parsed") or {}
        analysis = (inspection.get("analysis") or {}).get("parsed") or {}
        if capture_verification:
            cw.visible_logs.append(
                f"[{stage}] capture_verification={json.dumps(capture_verification, ensure_ascii=False)}"
            )
        if analysis:
            cw.visible_logs.append(
                f"[{stage}] analysis={json.dumps(analysis, ensure_ascii=False)}"
            )
        else:
            raw = (inspection.get("analysis") or {}).get("raw", "")
            if raw:
                cw.visible_logs.append(f"[{stage}] analysis_raw={raw[:240]}")

        if stage == "after_submit":
            self._apply_after_submit_verdict(cw, inspection)

    @staticmethod
    def _apply_after_submit_verdict(cw: ChatbotWindow, inspection: dict) -> None:
        analysis = inspection.get("analysis") or {}
        parsed = analysis.get("parsed") or {}
        notable = parsed.get("notable_elements") or []
        text_parts = [
            parsed.get("state_summary", ""),
            parsed.get("possible_blocker", ""),
            *[item for item in notable if isinstance(item, str)],
            analysis.get("raw", ""),
        ]
        combined = " ".join(part for part in text_parts if part).lower()

        if any(term in combined for term in ("usage limit", "message limit", "upgrade", "pay per message", "wait until it resets")):
            cw.status = "error"
            cw.error = "Claude usage limit / upgrade wall"
            return

        if any(term in combined for term in ("response displayed", "response generating", "ai response generating", "prompt sent")):
            cw.status = "generating"
            cw.error = ""
            return

        if any(term in combined for term in ("awaiting user input", "ready for user input", "idle", "welcome screen", "type a prompt")):
            cw.status = "error"
            cw.error = "Prompt did not submit"

    def _verify_visible_windows(self, question: str, *, stage: str) -> None:
        """Use the local visual-debug stack to verify real visible Chrome windows."""
        if not self.verify_visible_windows:
            return

        from ..agent.local_llm import LocalLLM
        from ..debug.visual_debug import CaptureTarget

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
                continue

            image = self.macos.capture_window_info(window)
            target = CaptureTarget(
                kind="window",
                target_id=window.window_id,
                label=f"{cw.site.name.lower()}_{stage}",
                x=window.x,
                y=window.y,
                width=window.width,
                height=window.height,
                owner=window.owner,
                title=window.title,
                capture_backend=window.capture_backend,
            )
            inspect_dir = self.output_dir / "visible_verification" / f"{cw.site.name.lower()}_{stage}"
            inspect_dir.mkdir(parents=True, exist_ok=True)
            inspection = self.visual_debugger.inspect_image(
                image,
                target=target,
                mode="general",
                question=(
                    f"This must be a real visible Google Chrome window for {cw.site.name}. "
                    f"Verify the correct site is open, the page is working, and whether the prompt "
                    f"'{question[:220]}' is visible or a response is generating."
                ),
                max_dim=768,
                max_tokens=128,
                save_dir=inspect_dir,
                verify_capture=True,
            )
            self._record_visible_verification(cw, stage, inspection)

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

        # Start bridge if we own it
        if self._owns_bridge:
            await self.bridge.start()
            await self._wait_for_extension()

        positions = self._compute_window_layout(len(self.windows))

        # 1. Open all windows
        logger.info("Opening %d chatbot windows...", len(self.windows))
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
            except Exception as exc:
                cw.status = "error"
                cw.error = str(exc)
                logger.error("[%s] Failed to create window: %s", cw.site.name, exc)

        # 2. Wait for initial page load and verify real visible windows.
        logger.info("Waiting for pages to load in visible Chrome windows...")
        await asyncio.sleep(10)
        self._verify_visible_windows(question, stage="after_open")

        # 3. For each chatbot: verify, enter question, submit
        for cw in self.windows:
            if cw.status == "error":
                continue
            try:
                await self._handle_chatbot(cw, question)
            except Exception as exc:
                cw.status = "error"
                cw.error = str(exc)
                logger.error("[%s] Error: %s", cw.site.name, exc)

        self._verify_visible_windows(question, stage="after_submit")
        self.macos.activate_app("Google Chrome")

        elapsed = time.perf_counter() - t0

        # 4. Build summary
        summary = {
            "question": question,
            "elapsed_s": round(elapsed, 1),
            "vision_backend": self._vision_backend,
            "preflight_cleanup": self.preflight_cleanup,
            "visible_verification": self.verify_visible_windows,
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
            })
        # Save summary
        summary_path = self.output_dir / "summary.json"
        summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
        logger.info("Done in %.1fs. Summary: %s", elapsed, summary_path)

        # Generate report
        self._write_report(summary)

        return summary

    async def _handle_chatbot(self, cw: ChatbotWindow, question: str) -> None:
        """Process a single chatbot: verify page, enter text, submit."""
        logger.info("[%s] Starting interaction...", cw.site.name)

        # Lock to this chatbot's tab
        await self.bridge.lock_active_tab(cw.tab_id)
        try:
            await asyncio.sleep(0.5)

            ready_result = await self._wait_for_input_ready(cw)
            if not ready_result.get("found"):
                logger.warning("[%s] Input selectors not ready after wait; falling back to screenshot + vision", cw.site.name)

            # Screenshot and verify page loaded
            img = await self._save_screenshot(cw, "01_loaded")
            vision_result = await self._verify_with_vision(
                cw, img,
                f"Is this the {cw.site.name} chatbot website? Is it loaded and ready for input? "
                f"Is the user logged in? Respond briefly.",
            )
            cw.status = "ready"
            cw.vision_logs.append(f"Ready check: {vision_result[:200]}")

            # Find and click the input field
            input_result = ready_result if ready_result.get("found") else {"found": False}
            if input_result.get("found"):
                logger.info("[%s] Input became ready via selector polling", cw.site.name)
            else:
                input_result = await self.bridge.find_chat_input(cw.site.input_selectors)
            if not input_result.get("found"):
                # Fallback: use vision to locate input
                logger.info("[%s] Selectors failed, trying vision to find input...", cw.site.name)
                location = self.vision.locate_element(img, "text input field or message box where I can type a question")
                if location and location.get("found"):
                    w, h = img.size
                    click_x = int(location["x"] * w / 100)
                    click_y = int(location["y"] * h / 100)
                    await self.bridge.click_at(click_x, click_y)
                    cw.vision_logs.append(f"Vision-located input at ({click_x}, {click_y})")
                else:
                    cw.status = "error"
                    cw.error = "Could not find input field"
                    return

            await asyncio.sleep(0.3)

            set_result = await self.bridge.set_chat_input_text(cw.site.input_selectors, question)
            if not set_result.get("ok"):
                click_x = input_result.get("inputX") or input_result.get("x")
                click_y = input_result.get("inputY") or input_result.get("y")
                if click_x and click_y:
                    await self.bridge.click_at(click_x, click_y)
                    await asyncio.sleep(0.2)
                await self.bridge.type_text(question)
            cw.status = "input_done"
            logger.info("[%s] Question entered", cw.site.name)

            await asyncio.sleep(0.5)

            # Screenshot and verify text was entered
            img = await self._save_screenshot(cw, "02_text_entered")
            await self._verify_with_vision(
                cw, img,
                f"Has the question been entered in the {cw.site.name} input field? "
                f"Can you see the text in the input area? Respond briefly.",
            )

            submit_result = await self.bridge.click_chat_submit(cw.site.submit_selectors)
            if submit_result.get("clicked"):
                logger.info("[%s] Submitted via visible send button", cw.site.name)
            else:
                logger.info("[%s] Send button not found, submitting via Enter key", cw.site.name)
                await self.bridge.press_key("Enter", code="Enter")
            cw.status = "submitted"
            logger.info("[%s] Question submitted", cw.site.name)

            # Wait and verify generation started
            await asyncio.sleep(3)
            img = await self._save_screenshot(cw, "03_generating")
            await self._verify_with_vision(
                cw, img,
                f"Is {cw.site.name} generating a response? Can you see any output text appearing? "
                f"Respond briefly.",
            )
            cw.status = "generating"
        finally:
            try:
                await self.bridge.release_active_tab()
            except Exception:
                logger.debug("[%s] Failed to release active tab", cw.site.name, exc_info=True)

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
) -> dict:
    """Blocking entry point for CLI / Tauri integration."""
    async def _run():
        runner = MultiChatRunner(
            port=port,
            output_dir=output_dir,
            vision_backend=vision_backend,
            cleanup_orphaned=cleanup_orphaned,
            verify_visible_windows=verify_visible_windows,
        )
        return await runner.run(question)

    return asyncio.run(_run())
