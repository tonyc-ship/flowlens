"""Reusable DOM-first composer interaction helpers for browser automation."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass

from .bridge import TabBridge
from .executor import ActionAttemptSpec, ActionExecutionResult, execute_action_plan
from .verification import (
    DomTextAssessment,
    VerificationResult,
    assess_expected_text_state,
    dom_assessment_to_result,
    verify_dom_first,
)


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
    verification_status: str = ""
    verification_source: str = "dom"


@dataclass(frozen=True)
class ComposerSubmitResult:
    """Final DOM-first submit outcome after trying one or more strategies."""

    status: str
    attempts: tuple[SubmitAttemptResult, ...]
    final_assessment: DomTextAssessment | None
    execution: ActionExecutionResult | None = None


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
    vision_verifier: Callable[[SubmitAttemptResult, VerificationResult], Awaitable[VerificationResult]] | None = None,
) -> ComposerSubmitResult:
    """Submit a prompt, verify via DOM, and retry with a secondary strategy if needed."""
    attempts: list[SubmitAttemptResult] = []
    last_assessment: DomTextAssessment | None = None
    attempt_results_by_name: dict[str, SubmitAttemptResult] = {}

    async def _build_attempt(strategy: str, index: int) -> ActionAttemptSpec:
        attempt_name = f"{strategy}_{index}"

        async def _action() -> Mapping[str, object] | None:
            submit_result: Mapping[str, object] | None = None
            if strategy == "enter":
                await focus_chat_input(tab, input_result, settle_s=focus_settle_s)
                await tab.press_key("Enter", code="Enter")
                if on_submit_dispatched is not None:
                    await on_submit_dispatched(attempt_name, strategy, True, None)
                return None

            submit_result = await tab.click_chat_submit(list(spec.submit_selectors), anchor=dict(input_result or {}))
            submit_performed = bool(submit_result.get("clicked"))
            if on_submit_dispatched is not None:
                await on_submit_dispatched(attempt_name, strategy, submit_performed, submit_result)
            return submit_result

        async def _after(action_result: Mapping[str, object] | None) -> None:
            if strategy == "button" and action_result is not None and not action_result.get("clicked"):
                return
            await asyncio.sleep(post_submit_settle_s)
            if on_after_submit is not None:
                await on_after_submit(attempt_name, strategy)

        async def _verify(action_result: Mapping[str, object] | None) -> object:
            if strategy == "button" and action_result is not None and not action_result.get("clicked"):
                result = SubmitAttemptResult(
                    attempt=attempt_name,
                    strategy=strategy,
                    submit_performed=False,
                    submit_result=action_result,
                    dom_state=None,
                    assessment=None,
                    outcome="skipped",
                    verification_status="retry",
                    verification_source="dom",
                )
                attempt_results_by_name[attempt_name] = result
                if on_attempt_resolved is not None:
                    await on_attempt_resolved(result)
                return VerificationResult(
                    status="retry",
                    source="dom",
                    detail="Submit button was not clickable; try secondary strategy",
                )

            dom_state = await tab.get_chat_input_state(list(spec.input_selectors))
            assessment = assess_expected_text_state(dom_state, expected_text)
            nonlocal last_assessment
            last_assessment = assessment

            result = SubmitAttemptResult(
                attempt=attempt_name,
                strategy=strategy,
                submit_performed=True,
                submit_result=action_result,
                dom_state=dom_state,
                assessment=assessment,
                outcome="retry" if assessment.status == "contains_expected" else ("sent" if assessment.status == "empty" else "ambiguous"),
                verification_status="",
                verification_source="dom",
            )

            decision = await verify_dom_first(
                lambda: _return_dom_result(assessment),
                vision_verify=(
                    lambda dom_result: vision_verifier(result, dom_result)
                ) if vision_verifier is not None else None,
            )
            final_result = SubmitAttemptResult(
                attempt=result.attempt,
                strategy=result.strategy,
                submit_performed=result.submit_performed,
                submit_result=result.submit_result,
                dom_state=result.dom_state,
                assessment=result.assessment,
                outcome=_verification_status_to_outcome(decision.result.status),
                verification_status=decision.result.status,
                verification_source=decision.result.source,
            )
            attempt_results_by_name[attempt_name] = final_result
            if on_attempt_resolved is not None:
                await on_attempt_resolved(final_result)
            return decision.result

        return ActionAttemptSpec(
            name=attempt_name,
            strategy=strategy,
            action=_action,
            verify=_verify,
            after=_after,
        )

    plan = [
        await _build_attempt(strategy, index)
        for index, strategy in enumerate(submit_attempt_order(spec.submit_mode), start=1)
    ]
    execution = await execute_action_plan(plan)

    for record in execution.attempts:
        attempt_result = attempt_results_by_name.get(record.name)
        if attempt_result is not None:
            attempts.append(attempt_result)

    final_status = {
        "passed": "sent",
        "retry": "unsent",
        "ambiguous": "ambiguous",
        "failed": "unsent",
    }.get(execution.status, execution.status)
    return ComposerSubmitResult(
        status=final_status,
        attempts=tuple(attempts),
        final_assessment=last_assessment,
        execution=execution,
    )


async def _return_dom_result(assessment: DomTextAssessment):
    return dom_assessment_to_result(assessment)


def _verification_status_to_outcome(status: str) -> str:
    if status == "passed":
        return "sent"
    if status == "retry":
        return "retry"
    if status == "ambiguous":
        return "ambiguous"
    return "skipped"
