"""Shared workflow data models for chatbot fan-out tasks."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ...platforms.chat.sites import ChatbotSite


@dataclass
class ChatbotWindow:
    """Runtime state for one chatbot window."""

    site: ChatbotSite
    tab_id: int = 0
    window_id: int = 0
    planned_bounds: dict[str, int] = field(default_factory=dict)
    status: str = "pending"
    error: str = ""
    screenshots: list[Path] = field(default_factory=list)
    vision_logs: list[str] = field(default_factory=list)
    visible_screenshots: list[Path] = field(default_factory=list)
    visible_logs: list[str] = field(default_factory=list)
    timeline: list[dict[str, object]] = field(default_factory=list)
