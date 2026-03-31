"""Reusable DOM-first composer interaction helpers for browser automation."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass

from .bridge import TabBridge
from .verification import DomTextAssessment, assess_expected_text_state


@dataclass(frozen=True)
class ComposerSpec:
    """Site-specific selectors and submit preference for a prompt composer."""

    input_selectors: tuple[str, ...]
    submit_selectors: tuple[str, ...]
    submit_mode: str = "auto"


@dataclass(frozen=True)
class ComposerEntryResult:
    """Result of placing text into a composer."""

    method: str
    raw: Mapping[str, object]


@dataclass(frozen=True)
class SubmitAttemptResult:
    """One submit attempt and the DOM state observed afterwards."""

    attempt: str
    strategy: str
    submit_performed: bool
    submit_result: Mapping[str, object] | None
    dom_state: Mapping[str, object] | None
    assessment: DomTextAssessment | None
    outcome: str


@dataclass(frozen=True)
class ComposerSubmitResult:
    """Final DOM-first submit outcome after trying one or more strategies."""

    status: str
    attempts: tuple[SubmitAttemptResult, ...]
    final_assessment: DomTextAssessment | None


def submit_attempt_order(submit_mode: str) -> tuple[str, ...]:
    """Return the preferred submit strategy order for a site."""
    primary = "enter" if submit_mode == "enter" else "button"
    secondary = "button" if primary == "enter" else "enter"
    return (primary, secondary)


async def wait_for_input_ready(
    tab: TabBridge,
    selectors: Sequence[str],
    *,
    timeout_s: float,
    poll_interval_s: float,
) -> dict:
    """Poll until a compatible prompt box becomes available."""
    deadline = asyncio.get_running_loop().time() + timeout_s
    selector_list = list(selectors)
    while asyncio.get_running_loop().time() < deadline:
        result = await tab.find_chat_input(selector_list)
        if result and result.get("found"):
            return result
        await asyncio.sleep(poll_interval_s)
    return {"found": False}


async def focus_chat_input(
    tab: TabBridge,
    input_result: Mapping[str, object] | None,
    *,
    settle_s: float,
) -> None:
    """Focus a composer by clicking its best-known anchor point."""
    input_result = input_result or {}
    click_x = input_result.get("inputX") or input_result.get("x")
    click_y = input_result.get("inputY") or input_result.get("y")
    if click_x and click_y:
        await tab.click_at(int(click_x), int(click_y))
        await asyncio.sleep(settle_s)


async def enter_text(
    tab: TabBridge,
    spec: ComposerSpec,
    text: str,
    *,
    input_result: Mapping[str, object] | None = None,
    focus_settle_s: float,
) -> ComposerEntryResult:
    """Populate a prompt composer using DOM assignment first, keyboard second."""
    set_result = await tab.set_chat_input_text(list(spec.input_selectors), text)
    if set_result.get("ok"):
        return ComposerEntryResult(method="dom", raw=set_result)

    await focus_chat_input(tab, input_result, settle_s=focus_settle_s)
    await tab.type_text(text)
    return ComposerEntryResult(method="keyboard", raw={})


async def submit_with_dom_verification(
    tab: TabBridge,
    spec: ComposerSpec,
    expected_text: str,
    *,
    input_result: Mapping[str, object] | None = None,
    focus_settle_s: float,
    post_submit_settle_s: float,
    on_submit_dispatched: Callable[[str, str, bool, Mapping[str, object] | None], Awaitable[None]] | None = None,
    on_after_submit: Callable[[str, str], Awaitable[None]] | None = None,
    on_attempt_resolved: Callable[[SubmitAttemptResult], Awaitable[None]] | None = None,
) -> ComposerSubmitResult:
    """Submit a prompt, verify via DOM, and retry with a secondary strategy if needed."""
    attempts: list[SubmitAttemptResult] = []
    last_assessment: DomTextAssessment | None = None

    for index, strategy in enumerate(submit_attempt_order(spec.submit_mode), start=1):
        attempt_name = f"{strategy}_{index}"
        submit_result: Mapping[str, object] | None = None

        if strategy == "enter":
            await focus_chat_input(tab, input_result, settle_s=focus_settle_s)
            await tab.press_key("Enter", code="Enter")
            if on_submit_dispatched is not None:
                await on_submit_dispatched(attempt_name, strategy, True, None)
        else:
            submit_result = await tab.click_chat_submit(list(spec.submit_selectors), anchor=dict(input_result or {}))
            if not submit_result.get("clicked"):
                skipped_attempt = SubmitAttemptResult(
                    attempt=attempt_name,
                    strategy=strategy,
                    submit_performed=False,
                    submit_result=submit_result,
                    dom_state=None,
                    assessment=None,
                    outcome="skipped",
                )
                if on_submit_dispatched is not None:
                    await on_submit_dispatched(attempt_name, strategy, False, submit_result)
                attempts.append(skipped_attempt)
                if on_attempt_resolved is not None:
                    await on_attempt_resolved(skipped_attempt)
                continue
            if on_submit_dispatched is not None:
                await on_submit_dispatched(attempt_name, strategy, True, submit_result)

        await asyncio.sleep(post_submit_settle_s)
        if on_after_submit is not None:
            await on_after_submit(attempt_name, strategy)

        dom_state = await tab.get_chat_input_state(list(spec.input_selectors))
        assessment = assess_expected_text_state(dom_state, expected_text)
        last_assessment = assessment

        if assessment.status == "contains_expected":
            attempt_result = SubmitAttemptResult(
                attempt=attempt_name,
                strategy=strategy,
                submit_performed=True,
                submit_result=submit_result,
                dom_state=dom_state,
                assessment=assessment,
                outcome="retry",
            )
            attempts.append(attempt_result)
            if on_attempt_resolved is not None:
                await on_attempt_resolved(attempt_result)
            continue

        if assessment.status == "empty":
            attempt_result = SubmitAttemptResult(
                attempt=attempt_name,
                strategy=strategy,
                submit_performed=True,
                submit_result=submit_result,
                dom_state=dom_state,
                assessment=assessment,
                outcome="sent",
            )
            attempts.append(attempt_result)
            if on_attempt_resolved is not None:
                await on_attempt_resolved(attempt_result)
            return ComposerSubmitResult(status="sent", attempts=tuple(attempts), final_assessment=assessment)

        attempt_result = SubmitAttemptResult(
            attempt=attempt_name,
            strategy=strategy,
            submit_performed=True,
            submit_result=submit_result,
            dom_state=dom_state,
            assessment=assessment,
            outcome="ambiguous",
        )
        attempts.append(attempt_result)
        if on_attempt_resolved is not None:
            await on_attempt_resolved(attempt_result)
        return ComposerSubmitResult(status="ambiguous", attempts=tuple(attempts), final_assessment=assessment)

    return ComposerSubmitResult(status="unsent", attempts=tuple(attempts), final_assessment=last_assessment)
