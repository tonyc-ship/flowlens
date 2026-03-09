"""Claude Vision API for high-level page understanding.

Used for:
- Identifying page type (search results, note detail, login, etc.)
- Understanding page content and state
- Deciding next actions
- Answering questions about what's visible on screen
"""

from __future__ import annotations

import base64
import io

import anthropic
from PIL import Image


class VisionLLM:
    """High-level visual understanding via Claude Vision API."""

    def __init__(self, model: str = "claude-sonnet-4-6"):
        self.client = anthropic.Anthropic()
        self.model = model

    def _image_to_base64(self, image: Image.Image) -> str:
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return base64.standard_b64encode(buffer.getvalue()).decode("utf-8")

    def analyze_page(self, screenshot: Image.Image, question: str | None = None) -> str:
        """Analyze a screenshot and return a description of the page.

        Args:
            screenshot: PIL Image of the screen/window.
            question: Optional specific question to answer about the page.
        """
        prompt = question or (
            "Describe what you see on this screen. Identify:\n"
            "1. What application/website is this?\n"
            "2. What page/state is it in?\n"
            "3. What are the main content elements visible?\n"
            "4. What interactive elements (buttons, inputs, links) are available?\n"
            "Be concise and structured."
        )

        response = self.client.messages.create(
            model=self.model,
            max_tokens=1024,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": self._image_to_base64(screenshot),
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        )
        return response.content[0].text

    def locate_element(
        self,
        screenshot: Image.Image,
        element_description: str,
    ) -> dict | None:
        """Ask Claude to locate a UI element and return its approximate position.

        Returns dict with keys: found (bool), x, y, width, height, confidence, description
        """
        prompt = (
            f"Find the UI element described as: '{element_description}'\n\n"
            "If found, respond in this exact JSON format (no markdown):\n"
            '{"found": true, "x": <center_x_percent>, "y": <center_y_percent>, '
            '"width": <width_percent>, "height": <height_percent>, '
            '"confidence": <0.0-1.0>, "description": "<what you found>"}\n\n'
            "x, y, width, height should be percentages (0-100) of the image dimensions.\n"
            "If not found, respond: {\"found\": false, \"description\": \"<why not found>\"}"
        )

        response = self.client.messages.create(
            model=self.model,
            max_tokens=256,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": self._image_to_base64(screenshot),
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        )

        import json

        try:
            return json.loads(response.content[0].text)
        except json.JSONDecodeError:
            return {"found": False, "description": f"Failed to parse response: {response.content[0].text}"}

    def decide_action(self, screenshot: Image.Image, goal: str, history: list[str]) -> str:
        """Given a goal and action history, decide what to do next.

        Returns a natural language description of the next action.
        """
        history_text = "\n".join(f"  {i+1}. {a}" for i, a in enumerate(history)) if history else "  (none yet)"

        prompt = (
            f"Goal: {goal}\n\n"
            f"Actions taken so far:\n{history_text}\n\n"
            "Looking at the current screen state, what is the single next action to take?\n"
            "Be specific about what to click, type, or scroll. "
            "If the goal is already achieved, say 'DONE'."
        )

        return self.analyze_page(screenshot, prompt)
