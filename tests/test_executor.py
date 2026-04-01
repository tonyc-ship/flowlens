import unittest
from unittest import IsolatedAsyncioTestCase

from clawvision.core.executor import ActionAttemptSpec, execute_action_plan
from clawvision.core.verification import VerificationResult


class ActionExecutorTest(IsolatedAsyncioTestCase):
    async def test_execute_action_plan_retries_then_succeeds(self) -> None:
        calls: list[str] = []

        async def retry_action():
            calls.append("retry_action")
            return {"step": 1}

        async def retry_verify(_result):
            calls.append("retry_verify")
            return VerificationResult(status="retry", source="dom", detail="still pending")

        async def success_action():
            calls.append("success_action")
            return {"step": 2}

        async def success_verify(_result):
            calls.append("success_verify")
            return VerificationResult(status="passed", source="dom", detail="done")

        execution = await execute_action_plan(
            (
                ActionAttemptSpec("first", "primary", retry_action, retry_verify),
                ActionAttemptSpec("second", "fallback", success_action, success_verify),
            )
        )

        self.assertEqual(execution.status, "passed")
        self.assertEqual(len(execution.attempts), 2)
        self.assertEqual([record.strategy for record in execution.attempts], ["primary", "fallback"])
        self.assertEqual(
            calls,
            ["retry_action", "retry_verify", "success_action", "success_verify"],
        )

    async def test_execute_action_plan_stops_on_failed_verification(self) -> None:
        async def fail_action():
            return {"ok": False}

        async def fail_verify(_result):
            return VerificationResult(status="failed", source="vision", detail="wrong page")

        async def should_not_run():
            raise AssertionError("fallback attempt should not run after hard failure")

        execution = await execute_action_plan(
            (
                ActionAttemptSpec("first", "primary", fail_action, fail_verify),
                ActionAttemptSpec("second", "fallback", should_not_run, fail_verify),
            )
        )

        self.assertEqual(execution.status, "failed")
        self.assertEqual(len(execution.attempts), 1)
