"""Shared data models for WeChat desktop parsing."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class WeChatMessage:
    speaker: str
    side: str
    text: str
    timestamp: str = ""
    kind: str = "text"
    source_capture: int = 0
    y_norm: float = 0.0

    def dedupe_key(self) -> str:
        core = " ".join(self.text.split())
        return f"{self.side}|{self.speaker}|{self.timestamp}|{core[:160]}"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class WeChatParsedCapture:
    capture_index: int
    screenshot_path: str
    conversation_title: str
    parser_mode: str
    ocr_line_count: int
    page_signature: str
    date_markers: list[str] = field(default_factory=list)
    messages: list[WeChatMessage] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["messages"] = [item.to_dict() for item in self.messages]
        return payload
