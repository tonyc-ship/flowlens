"""Workflow orchestrator — executes Skill state machines on real screens.

Connects three layers:
1. SiteSkill — provides state machine knowledge (states, transitions, extraction rules)
2. GroundingModel — locates UI elements from natural language descriptions
3. VisionLLM — page understanding, state detection, data extraction
4. ScreenController — screen capture and input simulation

The orchestrator:
- Detects the current page state (LLM + Skill)
- Executes transitions (grounding + screen control)
- Extracts data (LLM + Skill extraction rules)
- Tracks action history for debugging
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

from PIL import Image

from ..screen import ScreenController, WindowInfo
from ..skills.base import ExtractionRule, SiteSkill, Transition
from ..vision.grounding import GroundingModel, GroundingResult
from ..vision.llm import VisionLLM


@dataclass
class ActionRecord:
    """Record of a single action taken by the orchestrator."""

    timestamp: float
    state_before: str
    action_name: str
    grounding_query: str | None = None
    grounding_result: GroundingResult | None = None
    state_after: str | None = None
    screenshot_path: str | None = None
    error: str | None = None


@dataclass
class ExtractionRecord:
    """Record of data extracted from a page state."""

    timestamp: float
    state: str
    rule_name: str
    raw_response: str = ""
    parsed_data: dict | list | None = None
    screenshot_path: str | None = None


class WorkflowOrchestrator:
    """Executes Skill state machines on real screens."""

    def __init__(
        self,
        skill: SiteSkill,
        grounding: GroundingModel | None = None,
        llm: VisionLLM | None = None,
        screen: ScreenController | None = None,
        debug_dir: str | None = None,
    ):
        self.skill = skill
        self.grounding = grounding or GroundingModel(backend="auto")
        self.llm = llm or VisionLLM()
        self.screen = screen or ScreenController()
        self.debug_dir = debug_dir

        self.current_state: str = "unknown"
        self.action_history: list[ActionRecord] = []
        self.extraction_history: list[ExtractionRecord] = []
        self._step_counter = 0

    def _save_debug(self, name: str, image: Image.Image) -> str | None:
        """Save debug screenshot if debug_dir is set."""
        if not self.debug_dir:
            return None
        import os
        os.makedirs(self.debug_dir, exist_ok=True)
        self._step_counter += 1
        path = os.path.join(self.debug_dir, f"{self._step_counter:03d}_{name}.png")
        image.save(path)
        return path

    # ── State Detection ─────────────────────────────────────────────

    def detect_state(self, screenshot: Image.Image) -> str:
        """Detect the current page state using LLM + Skill knowledge."""
        prompt = self.skill.get_state_detection_prompt()
        response = self.llm.analyze_page(screenshot, prompt, max_tokens=64)

        # Match response to known state names
        states = self.skill.get_states()
        response_lower = response.strip().lower()

        for state_name in states:
            if state_name in response_lower:
                self.current_state = state_name
                return state_name

        self.current_state = "unknown"
        return "unknown"

    # ── Transition Execution ────────────────────────────────────────

    def execute_transition(
        self,
        transition_name: str,
        window: WindowInfo,
        *,
        target: str | None = None,
        type_text: str | None = None,
        screenshot: Image.Image | None = None,
    ) -> ActionRecord:
        """Execute a named transition from the current state.

        Args:
            transition_name: Name of the transition to execute.
            window: The target window.
            target: For parameterized transitions, e.g. "first" in "the {target} note card".
            type_text: Text to type for click_and_type actions.
            screenshot: Optional pre-captured screenshot (avoids re-capture).
        """
        transitions = self.skill.get_transitions(self.current_state)
        transition = transitions.get(transition_name)
        if transition is None:
            record = ActionRecord(
                timestamp=time.time(),
                state_before=self.current_state,
                action_name=transition_name,
                error=f"Unknown transition '{transition_name}' from state '{self.current_state}'",
            )
            self.action_history.append(record)
            return record

        record = ActionRecord(
            timestamp=time.time(),
            state_before=self.current_state,
            action_name=transition_name,
        )

        if screenshot is None:
            screenshot = self.screen.capture_window(window)

        self._save_debug(f"before_{transition_name}", screenshot)

        try:
            if transition.action_type == "click":
                self._do_click(transition, window, screenshot, target)
                record.grounding_query = self._resolve_query(transition.grounding_query, target)

            elif transition.action_type == "click_and_type":
                self._do_click(transition, window, screenshot, target)
                time.sleep(0.3)
                self.screen.hotkey("command", "a")
                time.sleep(0.1)
                if type_text:
                    self.screen.type_text(type_text)
                    time.sleep(0.2)
                    self.screen.press_key("enter")
                record.grounding_query = self._resolve_query(transition.grounding_query, target)

            elif transition.action_type == "scroll":
                params = transition.action_params
                direction = params.get("direction", "down")
                amount = params.get("amount", 3)
                clicks = -amount if direction == "down" else amount
                cx = window.x + window.width // 2
                cy = window.y + window.height // 2
                self.screen.scroll(clicks, x=cx, y=cy)

            elif transition.action_type == "press_key":
                key = transition.action_params.get("key", "escape")
                self.screen.press_key(key)

            elif transition.action_type == "wait":
                wait_time = transition.action_params.get("seconds", 2)
                time.sleep(wait_time)

            # Wait for page transition
            time.sleep(1.5)

            # Detect new state
            new_screenshot = self.screen.capture_window(window)
            self._save_debug(f"after_{transition_name}", new_screenshot)
            new_state = self.detect_state(new_screenshot)
            record.state_after = new_state

        except Exception as e:
            record.error = str(e)

        self.action_history.append(record)
        return record

    def _do_click(
        self,
        transition: Transition,
        window: WindowInfo,
        screenshot: Image.Image,
        target: str | None,
    ) -> GroundingResult:
        """Locate an element via grounding model and click it."""
        query = self._resolve_query(transition.grounding_query, target)
        if not query:
            raise RuntimeError(f"No grounding query for transition '{transition.name}'")

        result = self.grounding.ground(screenshot, query)
        if result is None:
            raise RuntimeError(f"Grounding failed for query: '{query}'")

        # Convert from image coordinates to screen coordinates
        img_w, img_h = screenshot.size
        # Handle Retina scaling: window.width/height are in points, image is in pixels
        scale_x = window.width / img_w
        scale_y = window.height / img_h
        screen_x = window.x + int(result.x * scale_x)
        screen_y = window.y + int(result.y * scale_y)

        self.screen.activate_app(window.owner)
        self.screen.click(screen_x, screen_y)

        return result

    @staticmethod
    def _resolve_query(query: str | None, target: str | None) -> str | None:
        """Replace {target} placeholder in grounding query."""
        if query is None:
            return None
        if target and "{target}" in query:
            return query.replace("{target}", target)
        return query

    # ── Data Extraction ─────────────────────────────────────────────

    def extract_data(
        self,
        rule_name: str,
        window: WindowInfo | None = None,
        screenshot: Image.Image | None = None,
    ) -> ExtractionRecord:
        """Extract data from the current state using a named extraction rule.

        Args:
            rule_name: Name of the extraction rule from the current state.
            window: Window to capture (if screenshot not provided).
            screenshot: Optional pre-captured screenshot.
        """
        rules = self.skill.get_extraction_rules(self.current_state)
        rule = rules.get(rule_name)
        if rule is None:
            return ExtractionRecord(
                timestamp=time.time(),
                state=self.current_state,
                rule_name=rule_name,
                raw_response=f"Unknown extraction rule '{rule_name}' for state '{self.current_state}'",
            )

        if screenshot is None and window is not None:
            screenshot = self.screen.capture_window(window)

        if screenshot is None:
            return ExtractionRecord(
                timestamp=time.time(),
                state=self.current_state,
                rule_name=rule_name,
                raw_response="No screenshot available",
            )

        debug_path = self._save_debug(f"extract_{rule_name}", screenshot)

        raw = self.llm.analyze_page(screenshot, rule.prompt, max_tokens=2048)
        parsed = self._parse_json(raw)

        record = ExtractionRecord(
            timestamp=time.time(),
            state=self.current_state,
            rule_name=rule_name,
            raw_response=raw,
            parsed_data=parsed,
            screenshot_path=debug_path,
        )
        self.extraction_history.append(record)
        return record

    def extract_all(
        self,
        window: WindowInfo | None = None,
        screenshot: Image.Image | None = None,
    ) -> dict[str, ExtractionRecord]:
        """Extract data for all rules in the current state."""
        rules = self.skill.get_extraction_rules(self.current_state)
        if screenshot is None and window is not None:
            screenshot = self.screen.capture_window(window)

        results = {}
        for rule_name in rules:
            results[rule_name] = self.extract_data(rule_name, screenshot=screenshot)
        return results

    @staticmethod
    def _parse_json(text: str) -> dict | list | None:
        """Best-effort JSON parsing from LLM output."""
        # Try to find JSON in the response
        # Try array first
        m = re.search(r"\[.*\]", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass
        # Try object
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass
        return None

    # ── Convenience Methods ─────────────────────────────────────────

    def get_status(self) -> dict:
        """Get current orchestrator status for debugging."""
        return {
            "current_state": self.current_state,
            "available_transitions": list(self.skill.get_transitions(self.current_state).keys()),
            "available_extractions": list(self.skill.get_extraction_rules(self.current_state).keys()),
            "actions_taken": len(self.action_history),
            "extractions_done": len(self.extraction_history),
        }

    def get_history_summary(self) -> str:
        """Get a human-readable summary of all actions and extractions."""
        lines = []
        for rec in self.action_history:
            status = "OK" if not rec.error else f"ERROR: {rec.error}"
            lines.append(
                f"  [{rec.state_before}] → {rec.action_name} → [{rec.state_after}] ({status})"
            )
        for rec in self.extraction_history:
            data_preview = str(rec.parsed_data)[:100] if rec.parsed_data else "(no data)"
            lines.append(f"  [{rec.state}] extract:{rec.rule_name} → {data_preview}")
        return "\n".join(lines) if lines else "(no history)"
