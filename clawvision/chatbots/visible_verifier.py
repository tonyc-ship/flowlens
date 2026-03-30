"""Visible-window vision verification for chatbot fan-out workflows."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from .models import ChatbotWindow
from .vision_profiles import CHATBOT_COMPLEX_FALLBACK_CHECK, CHATBOT_PAGE_SIMPLE_CHECK

logger = logging.getLogger(__name__)


def parse_status_label(result: str) -> str:
    """Normalize a vision response into a workflow status label."""
    normalized = result.strip().upper()
    for label in ("GENERATING", "READY", "BLOCKED", "WRONG_SITE"):
        if label in normalized:
            return label
    if normalized.startswith("YES"):
        return "GENERATING"
    if normalized.startswith("NO"):
        return "READY"
    return "UNKNOWN"


def build_visible_submit_prompt(site_name: str, question: str) -> str:
    """Prompt tuned for real visible browser windows after submit."""
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


def _window_distance(bounds: dict[str, int], window: Any) -> int:
    return (
        abs(window.x - bounds.get("left", 0))
        + abs(window.y - bounds.get("top", 0))
        + abs(window.width - bounds.get("width", 0))
        + abs(window.height - bounds.get("height", 0))
    )


class ChatbotVisibleVerifier:
    """Workflow-scoped verifier for real, visible Chrome windows."""

    def __init__(self, *, macos, vision, output_dir: Path):
        self.macos = macos
        self.vision = vision
        self.output_dir = output_dir

    def _match_visible_windows(self, windows: list[ChatbotWindow]) -> list[tuple[ChatbotWindow, object | None]]:
        visible_windows = [
            window
            for window in self.macos.list_windows(app_name="Google Chrome", on_screen_only=True)
            if window.width >= 480 and window.height >= 360
        ]
        unused = visible_windows[:]
        matches: list[tuple[ChatbotWindow, object | None]] = []

        for cw in sorted(windows, key=lambda item: item.planned_bounds.get("left", 0)):
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

            best = min(unused, key=lambda window: _window_distance(cw.planned_bounds, window))
            unused.remove(best)
            matches.append((cw, best))

        return matches

    @staticmethod
    def _apply_status_label(cw: ChatbotWindow, label: str) -> None:
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

    def verify(
        self,
        windows: list[ChatbotWindow],
        question: str,
        *,
        stage: str,
        record_event=None,
    ) -> None:
        """Run visible-window verification and update per-window workflow state."""
        from ..agent.local_llm import LocalLLM

        if not LocalLLM.is_available():
            logger.warning("Skipping visible-window verification: local Qwen vision model not available")
            for cw in windows:
                cw.visible_logs.append(f"[{stage}] skipped: local qwen model unavailable")
            return

        self.macos.activate_app("Google Chrome")
        time.sleep(1.0)

        inspect_dir = self.output_dir / "visible_verification"
        inspect_dir.mkdir(parents=True, exist_ok=True)

        for cw, window in self._match_visible_windows(windows):
            if window is None:
                cw.visible_logs.append(f"[{stage}] no visible Chrome window matched planned bounds")
                if stage == "after_submit":
                    cw.status = "error"
                    cw.error = "No visible Chrome window matched planned bounds"
                continue

            image = self.macos.capture_window_info(window)
            path = inspect_dir / f"{cw.site.name.lower()}_{stage}_visible.png"
            image.save(path)
            cw.visible_screenshots.append(path)

            prompt = build_visible_submit_prompt(cw.site.name, question)
            t0 = time.perf_counter()
            result = self.vision.analyze_page(image, prompt, config=CHATBOT_PAGE_SIMPLE_CHECK)
            elapsed = time.perf_counter() - t0
            label = parse_status_label(result)
            cw.visible_logs.append(
                f"[{stage}][{elapsed:.1f}s][{CHATBOT_PAGE_SIMPLE_CHECK.name}] {result[:200]}"
            )

            if label not in ("GENERATING", "BLOCKED", "WRONG_SITE"):
                t1 = time.perf_counter()
                fallback_result = self.vision.analyze_page(
                    image,
                    prompt,
                    config=CHATBOT_COMPLEX_FALLBACK_CHECK,
                )
                fallback_elapsed = time.perf_counter() - t1
                fallback_label = parse_status_label(fallback_result)
                cw.visible_logs.append(
                    f"[{stage}][{fallback_elapsed:.1f}s][{CHATBOT_COMPLEX_FALLBACK_CHECK.name}] {fallback_result[:200]}"
                )
                if fallback_label != "UNKNOWN":
                    label = fallback_label

            if record_event is not None:
                record_event(
                    cw,
                    "visible_verification_finished",
                    stage=stage,
                    seconds=round(elapsed, 3),
                    label=label,
                )

            if stage == "after_submit":
                self._apply_status_label(cw, label)
