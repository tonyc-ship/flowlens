"""Task-scoped backend selection policy for perception/reasoning."""

from __future__ import annotations

from dataclasses import dataclass


BACKEND_CLOUD = "sonnet"
BACKEND_OPENAI = "openai"
BACKEND_LOCAL = "qwen-local"
BACKEND_UI_TARS_LOCAL = "ui-tars-local"


def normalize_backend_choice(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"openai", "gpt", "gpt-5", "gpt-5.4"}:
        return BACKEND_OPENAI
    if normalized in {"ui-tars", "uitars", "ui-tars-local", "uitars-local"}:
        return BACKEND_UI_TARS_LOCAL
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
        if backend == BACKEND_UI_TARS_LOCAL:
            return cls(
                mode="local-specialized",
                reasoning_backend=BACKEND_UI_TARS_LOCAL,
                vision_backend=BACKEND_UI_TARS_LOCAL,
                label="Local UI-TARS 1.5 7B",
            )
        if backend == BACKEND_OPENAI:
            return cls(
                mode="cloud",
                reasoning_backend=BACKEND_OPENAI,
                vision_backend=BACKEND_OPENAI,
                label="OpenAI GPT",
            )
        return cls(
            mode="cloud",
            reasoning_backend=BACKEND_CLOUD,
            vision_backend=BACKEND_CLOUD,
            label="Cloud Claude Sonnet",
        )
