"""Vision and OCR tools for the agent loop."""

from __future__ import annotations

import base64
from pathlib import Path

from ..tool import Tool, ToolContext


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

        # Find the screenshot to analyze
        filename = params.get("screenshot_file")
        if filename:
            path = ctx.run_dir / filename
        else:
            pngs = sorted(ctx.run_dir.glob("*.png"))
            if not pngs:
                return "No screenshots found. Take a screenshot first."
            path = pngs[-1]

        if not path.exists():
            return f"Screenshot not found: {path.name}"

        b64_data = base64.b64encode(path.read_bytes()).decode()

        if self._media:
            result = self._media.call_vision(b64_data, question, max_tokens=2048)
            return result

        return f"Vision analysis not available. Screenshot saved at: {path.name}"
