"""Base class for site-specific Skills (state machine architecture).

A Skill encodes site-specific knowledge as:
- Page states and how to detect them (via LLM, not pixel heuristics)
- Transitions between states (actions with semantic descriptions)
- Extraction rules per state (what data to pull and how)
- Grounding queries (natural language for locating UI elements)

The Skill does NOT do any pixel-level analysis. Element location is
delegated to grounding models; page understanding to LLMs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


@dataclass
class Transition:
    """A possible action that moves from one state to another."""

    name: str
    description: str  # Human-readable description of the action
    target_state: str  # State after this action
    grounding_query: str | None = None  # Query for grounding model to locate the element
    action_type: str = "click"  # click, scroll, type, press_key, wait
    action_params: dict[str, Any] = field(default_factory=dict)
    # e.g. {"key": "escape"} for press_key, {"direction": "down", "amount": 5} for scroll


@dataclass
class ExtractionRule:
    """What to extract from a page state."""

    prompt: str  # LLM prompt for extraction
    schema: dict[str, str] = field(default_factory=dict)  # Expected output schema
    region_hint: str | None = None  # Optional hint like "bottom 10% of the page"


@dataclass
class PageState:
    """A recognizable state of a web page."""

    name: str
    description: str  # How to recognize this state (for LLM)
    transitions: dict[str, Transition] = field(default_factory=dict)
    extraction_rules: dict[str, ExtractionRule] = field(default_factory=dict)


class SiteSkill:
    """Base class for site-specific skills.

    Subclasses define page states, transitions, and extraction rules.
    The skill itself is a pure knowledge container — no CV, no models.
    Execution is handled by the workflow orchestrator.
    """

    name: str = "base"
    site_url: str = ""

    def get_states(self) -> dict[str, PageState]:
        """Return all page states this skill knows about."""
        raise NotImplementedError

    def get_state_detection_prompt(self) -> str:
        """Return a prompt for the LLM to detect which state a page is in.

        The prompt should list all possible states and their visual signatures,
        so the LLM can classify a screenshot.
        """
        states = self.get_states()
        state_descriptions = "\n".join(
            f"- **{s.name}**: {s.description}" for s in states.values()
        )
        return (
            f"You are looking at a {self.name} page. "
            f"Classify the current page into one of these states:\n\n"
            f"{state_descriptions}\n\n"
            f"Respond with ONLY the state name (e.g., '{list(states.keys())[0]}') "
            f"and nothing else."
        )

    def get_transitions(self, state_name: str) -> dict[str, Transition]:
        """Get available transitions from a given state."""
        states = self.get_states()
        state = states.get(state_name)
        if state is None:
            return {}
        return state.transitions

    def get_extraction_rules(self, state_name: str) -> dict[str, ExtractionRule]:
        """Get extraction rules for a given state."""
        states = self.get_states()
        state = states.get(state_name)
        if state is None:
            return {}
        return state.extraction_rules

    def format_transition_menu(self, state_name: str) -> str:
        """Format available transitions as a menu for the LLM planner."""
        transitions = self.get_transitions(state_name)
        if not transitions:
            return f"No transitions available from state '{state_name}'."
        lines = [f"Available actions in state '{state_name}':"]
        for key, t in transitions.items():
            lines.append(f"  - {key}: {t.description} → {t.target_state}")
        return "\n".join(lines)
