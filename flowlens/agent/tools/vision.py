"""Vision and OCR tools for the agent loop."""

from __future__ import annotations

import base64
from pathlib import Path

from ..tool import Tool, ToolContext


def _resolve_screenshot_path(ctx: ToolContext, filename: str = "") -> Path | None:
    if filename:
        path = ctx.run_dir / filename
        return path if path.exists() else None

    images = sorted(
        path for path in ctx.run_dir.iterdir()
        if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
    )
    return images[-1] if images else None


class AnalyzeScreenshotTool(Tool):
    """Send the most recent screenshot to a vision LLM for analysis."""

    def __init__(self, *, media=None):
        self._media = media

    name = "analyze_screenshot"
    description = (
        "Analyze the most recent screenshot with vision AI. Ask a question about "
        "what's visible on screen — UI elements, text content, layout, etc. "
        "You must take a screenshot first before using this tool."
    )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "What you want to know about the screenshot (e.g. 'What search results are shown?', 'Is there an error message?')",
                },
                "screenshot_file": {
                    "type": "string",
                    "description": "Filename of the screenshot to analyze (from the run directory). If omitted, uses the most recent screenshot.",
                },
            },
            "required": ["question"],
        }

    async def execute(self, params: dict, ctx: ToolContext) -> str:
        question = params["question"]

        path = _resolve_screenshot_path(ctx, params.get("screenshot_file", ""))
        if path is None:
            return "No screenshots found. Take a screenshot first."

        b64_data = base64.b64encode(path.read_bytes()).decode()

        if self._media:
            result = self._media.call_vision(b64_data, question, max_tokens=2048)
            return result

        return f"Vision analysis not available. Screenshot saved at: {path.name}"


class OcrScreenshotTool(Tool):
    """Extract screenshot text via local OCR."""

    def __init__(self, *, media=None):
        self._media = media

    name = "ocr_screenshot"
    description = (
        "Extract text from a screenshot using OCR. Useful when you need exact "
        "strings instead of a semantic screenshot summary."
    )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "screenshot_file": {
                    "type": "string",
                    "description": "Filename of the screenshot to OCR. If omitted, uses the most recent screenshot.",
                },
            },
        }

    async def execute(self, params: dict, ctx: ToolContext) -> str:
        path = _resolve_screenshot_path(ctx, params.get("screenshot_file", ""))
        if path is None:
            return "No screenshots found. Take a screenshot first."
        if not self._media:
            return f"OCR not available. Screenshot saved at: {path.name}"
        text = self._media.ocr_image(path.read_bytes())
        return text if text.strip() else f"No OCR text found in {path.name}"
