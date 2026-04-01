"""Reusable DOM-first and DOM-first/vision-second verification helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping


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


@dataclass(frozen=True)
class VerificationResult:
    """Normalized outcome from a verification stage."""

    status: str
    source: str
    detail: str = ""
    payload: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class VerificationDecision:
    """Final decision after a DOM-first verification sequence."""

    result: VerificationResult
    dom_result: VerificationResult
    vision_result: VerificationResult | None = None


def dom_assessment_to_result(
    assessment: DomTextAssessment,
    *,
    source: str = "dom",
) -> VerificationResult:
    """Map a DOM text assessment into a generic verification result."""

    if assessment.status == "empty":
        return VerificationResult(status="passed", source=source, detail="Composer emptied after action")
    if assessment.status == "contains_expected":
        return VerificationResult(status="retry", source=source, detail="Expected text still present in DOM")
    if assessment.status == "ambiguous":
        return VerificationResult(status="ambiguous", source=source, detail="DOM state changed but is inconclusive")
    return VerificationResult(status="failed", source=source, detail="Expected composer/input was missing")


async def verify_dom_first(
    dom_verify: Callable[[], Awaitable[VerificationResult]],
    *,
    vision_verify: Callable[[VerificationResult], Awaitable[VerificationResult]] | None = None,
    vision_on_statuses: tuple[str, ...] = ("ambiguous", "failed"),
) -> VerificationDecision:
    """Run DOM verification first and only consult vision when needed."""

    dom_result = await dom_verify()
    if vision_verify is None or dom_result.status not in vision_on_statuses:
        return VerificationDecision(result=dom_result, dom_result=dom_result)

    vision_result = await vision_verify(dom_result)
    final = vision_result if vision_result.status not in {"skipped", "unknown"} else dom_result
    return VerificationDecision(result=final, dom_result=dom_result, vision_result=vision_result)


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
