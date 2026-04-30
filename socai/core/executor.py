"""Reusable action -> verify -> retry/fallback execution helpers."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from .verification import VerificationResult


ActionFn = Callable[[], Awaitable[Any]]
VerifyFn = Callable[[Any], Awaitable[VerificationResult]]
HookFn = Callable[[Any], Awaitable[None]]


@dataclass(frozen=True)
class ActionAttemptSpec:
    """One executable attempt in a verified action plan."""

    name: str
    strategy: str
    action: ActionFn
    verify: VerifyFn
    before: HookFn | None = None
    after: HookFn | None = None


@dataclass(frozen=True)
class ActionAttemptRecord:
    """Result of executing a single attempt."""

    name: str
    strategy: str
    action_result: Any
    verification: VerificationResult
    elapsed_s: float


@dataclass(frozen=True)
class ActionExecutionResult:
    """Final result of a multi-attempt verified action plan."""

    status: str
    attempts: tuple[ActionAttemptRecord, ...]
    final_verification: VerificationResult


async def execute_action_plan(
    attempts: Sequence[ActionAttemptSpec],
    *,
    success_statuses: tuple[str, ...] = ("passed",),
    retry_statuses: tuple[str, ...] = ("retry",),
) -> ActionExecutionResult:
    """Run attempts in order until verification passes or no retry remains."""

    records: list[ActionAttemptRecord] = []
    last_result = VerificationResult(status="failed", source="executor", detail="No attempts provided")

    for attempt in attempts:
        t0 = time.perf_counter()
        action_result: Any = None
        try:
            if attempt.before is not None:
                await attempt.before(None)
            action_result = await attempt.action()
            if attempt.after is not None:
                await attempt.after(action_result)
            verification = await attempt.verify(action_result)
        except Exception as exc:  # pragma: no cover - defensive path
            verification = VerificationResult(
                status="failed",
                source="executor",
                detail=str(exc),
                payload={"error": str(exc)},
            )
        elapsed = time.perf_counter() - t0
        records.append(
            ActionAttemptRecord(
                name=attempt.name,
                strategy=attempt.strategy,
                action_result=action_result,
                verification=verification,
                elapsed_s=elapsed,
            )
        )
        last_result = verification

        if verification.status in success_statuses:
            return ActionExecutionResult(
                status=verification.status,
                attempts=tuple(records),
                final_verification=verification,
            )

        if verification.status not in retry_statuses:
            return ActionExecutionResult(
                status=verification.status,
                attempts=tuple(records),
                final_verification=verification,
            )

    return ActionExecutionResult(
        status=last_result.status,
        attempts=tuple(records),
        final_verification=last_result,
    )
