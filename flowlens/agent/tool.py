"""Minimal tool interface for the agent loop.

Each tool exposes:
- name / description / parameters  → sent to the Anthropic tool_use API
- execute()                        → called when the LLM picks this tool
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ToolContext:
    """Shared runtime state available to every tool invocation."""

    run_dir: Path
    screenshot_counter: int = 0
    screenshot_max_dim: int = 0  # 0 = no downscaling
    artifact_counter: int = 0
    processed_notes: dict = field(default_factory=dict)

    def next_screenshot_path(self, label: str = "screenshot") -> Path:
        self.screenshot_counter += 1
        path = self.run_dir / f"{self.screenshot_counter:03d}_{label}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def next_artifact_path(
        self,
        label: str = "artifact",
        *,
        suffix: str = ".json",
        subdir: str = "artifacts",
    ) -> Path:
        self.artifact_counter += 1
        safe_label = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in label).strip("_")
        safe_label = safe_label or "artifact"
        directory = self.run_dir / subdir
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{self.artifact_counter:03d}_{safe_label}{suffix}"


class Tool(ABC):
    """Base class for agent tools."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def description(self) -> str: ...

    @property
    @abstractmethod
    def parameters(self) -> dict:
        """JSON Schema object for the tool input (Anthropic format)."""
        ...

    @abstractmethod
    async def execute(self, params: dict, ctx: ToolContext) -> str | list:
        """Run the tool and return a result for the LLM.

        May return a plain string, or a list of Anthropic content blocks
        (e.g. [{"type": "text", ...}, {"type": "image", ...}]) when the
        tool wants to send images back to the model.
        """
        ...

    def to_api_schema(self) -> dict:
        """Format for the Anthropic ``tools`` parameter."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters,
        }
