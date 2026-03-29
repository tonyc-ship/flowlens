"""Reusable DOM-first verification helpers for browser automation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping


def compact_text(text: str) -> str:
    """Normalize text for robust DOM state comparisons."""
    return re.sub(r"\s+", "", str(text or "")).strip().lower()


@dataclass(frozen=True)
class DomTextAssessment:
    """Classification of an input/composer state from DOM data."""

    status: str
    found: bool
    empty: bool
    text: str
    text_length: int


def assess_expected_text_state(
    state: Mapping[str, object] | None,
    expected_text: str,
) -> DomTextAssessment:
    """Classify whether a DOM input still contains the expected text."""
    state = state or {}
    found = bool(state.get("found", False))
    text = str(state.get("text", "") or "")
    text_length = int(state.get("textLength", 0) or 0)
    empty = bool(state.get("empty", False))

    if not found:
        return DomTextAssessment(
            status="missing",
            found=False,
            empty=False,
            text=text,
            text_length=text_length,
        )

    if empty:
        return DomTextAssessment(
            status="empty",
            found=True,
            empty=True,
            text=text,
            text_length=text_length,
        )

    actual = compact_text(text)
    expected = compact_text(expected_text)
    contains_expected = False
    if actual and expected:
        contains_expected = expected in actual
        if not contains_expected and len(actual) >= max(8, int(len(expected) * 0.6)):
            contains_expected = actual in expected

    return DomTextAssessment(
        status="contains_expected" if contains_expected else "ambiguous",
        found=True,
        empty=False,
        text=text,
        text_length=text_length,
    )
