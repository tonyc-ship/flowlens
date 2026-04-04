"""Task-scoped backend selection policy for perception/reasoning."""

from __future__ import annotations

from dataclasses import dataclass


BACKEND_CLOUD = "sonnet"
BACKEND_LOCAL = "qwen-local"


def normalize_backend_choice(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"local", "qwen", "qwen-local"}:
        return BACKEND_LOCAL
    return BACKEND_CLOUD


@dataclass(frozen=True)
class TaskModelPolicy:
    """Fixed backend policy chosen once at task start."""

    mode: str
    reasoning_backend: str
    vision_backend: str
    label: str

    @classmethod
    def from_choice(cls, value: str | None) -> "TaskModelPolicy":
        backend = normalize_backend_choice(value)
        if backend == BACKEND_LOCAL:
            return cls(
                mode="local",
                reasoning_backend=BACKEND_LOCAL,
                vision_backend=BACKEND_LOCAL,
                label="Local Qwen 3.5 9B",
            )
        return cls(
            mode="cloud",
            reasoning_backend=BACKEND_CLOUD,
            vision_backend=BACKEND_CLOUD,
            label="Cloud Claude Sonnet",
        )
