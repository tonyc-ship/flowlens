"""UI-agnostic watch/event runtime for task execution."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class WatchEvent:
    """One structured watch event that can be rendered in multiple UIs."""

    level: str
    message: str
    phase: str = ""
    detail: str = ""
    observation: str = ""
    reasoning: str = ""
    decision: str = ""
    evidence: str = ""
    action_name: str = ""
    duration: float | None = None
    x: int | None = None
    y: int | None = None
    target: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)


class WatchSink(Protocol):
    async def emit(self, event: WatchEvent) -> None: ...


class MemoryWatchSink:
    """In-memory sink useful for debugging, tests, or later export."""

    def __init__(self) -> None:
        self.events: list[WatchEvent] = []

    async def emit(self, event: WatchEvent) -> None:
        self.events.append(event)


class BridgeWatchSink:
    """Bridge-backed sink that forwards events to the extension overlay."""

    def __init__(self, bridge) -> None:
        self.bridge = bridge

    async def emit(self, event: WatchEvent) -> None:
        await self.bridge.watch_log(
            event.level,
            event.message,
            phase=event.phase,
            detail=event.detail,
            observation=event.observation,
            reasoning=event.reasoning,
            decision=event.decision,
            evidence=event.evidence,
            action_name=event.action_name,
            duration=event.duration,
            x=event.x,
            y=event.y,
            target=event.target,
        )


class WatchRuntime:
    """Fan out structured watch events to one or more renderers/sinks."""

    def __init__(self, *sinks: WatchSink):
        self._sinks: list[WatchSink] = list(sinks)

    def add_sink(self, sink: WatchSink) -> None:
        self._sinks.append(sink)

    @property
    def sinks(self) -> tuple[WatchSink, ...]:
        return tuple(self._sinks)

    async def emit(self, event: WatchEvent) -> None:
        for sink in self._sinks:
            await sink.emit(event)

    def emit_nowait(self, event: WatchEvent) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self.emit(event))

    def think_nowait(
        self,
        *,
        phase: str,
        observation: str,
        reasoning: str,
        decision: str,
        evidence: str = "",
        message: str = "",
    ) -> None:
        self.emit_nowait(
            WatchEvent(
                level="think",
                message=message or decision[:300],
                phase=phase,
                observation=observation,
                reasoning=reasoning,
                decision=decision,
                evidence=evidence,
            )
        )

    def action_nowait(
        self,
        *,
        action_name: str,
        detail: str,
        duration: float | None = None,
        message: str = "",
    ) -> None:
        self.emit_nowait(
            WatchEvent(
                level="action",
                message=message or detail[:200] or action_name,
                action_name=action_name,
                detail=detail,
                duration=duration,
            )
        )

    def result_nowait(
        self,
        *,
        message: str,
        detail: str = "",
        duration: float | None = None,
    ) -> None:
        self.emit_nowait(
            WatchEvent(
                level="result",
                message=message,
                detail=detail,
                duration=duration,
            )
        )
