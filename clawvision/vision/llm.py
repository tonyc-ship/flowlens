"""Vision LLM for high-level page understanding.

Supports two backends (selected via ``CLAWVISION_LLM_BACKEND`` env var or
constructor ``backend`` kwarg):

- ``"sonnet"`` (default) — Anthropic Claude Vision API
- ``"qwen-local"`` — local Qwen3.5-9B-MLX-4bit via mlx-vlm

Used for:
- Identifying page type (search results, note detail, login, etc.)
- Understanding page content and state
- Deciding next actions
- Answering questions about what's visible on screen
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os

import anthropic
from PIL import Image

from ..runtime import load_runtime_env

logger = logging.getLogger(__name__)

BACKEND_SONNET = "sonnet"
BACKEND_QWEN_LOCAL = "qwen-local"


def _resolve_backend(explicit: str | None = None) -> str:
    val = explicit or os.environ.get("CLAWVISION_LLM_BACKEND", "")
    val = val.strip().lower()
    if val in (BACKEND_QWEN_LOCAL, "qwen", "local"):
        return BACKEND_QWEN_LOCAL
    return BACKEND_SONNET


class VisionLLM:
    """High-level visual understanding via Claude Vision or local MLX model."""

    def __init__(self, model: str = "claude-sonnet-4-6", *, backend: str | None = None):
        load_runtime_env()
        self.model = model
        self.backend = _resolve_backend(backend)
        self._anthropic_client = None
        self._local_llm = None
        logger.info("VisionLLM using backend: %s", self.backend)

    @property
    def client(self):
        """Lazy Anthropic client."""
        if self._anthropic_client is None:
            self._anthropic_client = anthropic.Anthropic()
        return self._anthropic_client

    @property
    def local_llm(self):
        """Lazy local LLM."""
        if self._local_llm is None:
            from ..agent.local_llm import LocalLLM
            self._local_llm = LocalLLM()
        return self._local_llm

    MAX_IMAGE_BYTES = 4_800_000  # Stay under Anthropic's 5MB limit
    MAX_IMAGE_PIXELS = 1568  # Max dimension for efficient API usage

    def _prepare_image(self, image: Image.Image) -> Image.Image:
        """Resize image if needed to stay within API limits."""
        # Downscale if any dimension exceeds max
        w, h = image.size
        if max(w, h) > self.MAX_IMAGE_PIXELS:
            scale = self.MAX_IMAGE_PIXELS / max(w, h)
            image = image.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        # Convert RGBA to RGB (smaller PNG)
        if image.mode == "RGBA":
            bg = Image.new("RGB", image.size, (255, 255, 255))
            bg.paste(image, mask=image.split()[3])
            image = bg
        return image

    def _image_to_base64(self, image: Image.Image) -> str:
        image = self._prepare_image(image)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        # If still too large, use JPEG
        if buffer.tell() > self.MAX_IMAGE_BYTES:
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=85)
        return base64.standard_b64encode(buffer.getvalue()).decode("utf-8")

    # ── Core dispatch ─────────────────────────────────────────

    def _call_vision(
        self, image: Image.Image, prompt: str, max_tokens: int = 1024,
    ) -> str:
        """Dispatch a vision call to the active backend."""
        if self.backend == BACKEND_QWEN_LOCAL:
            img_b64 = self._image_to_base64(image)
            return self.local_llm.call_vision(
                img_b64, prompt, media_type="image/png", max_tokens=max_tokens,
            )
        # Anthropic backend
        response = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": self._image_to_base64(image),
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        )
        return response.content[0].text

    # ── Public API (unchanged signatures) ─────────────────────

    def analyze_page(
        self, screenshot: Image.Image, question: str | None = None, *, max_tokens: int = 1024
    ) -> str:
        """Analyze a screenshot and return a description of the page.

        Args:
            screenshot: PIL Image of the screen/window.
            question: Optional specific question to answer about the page.
            max_tokens: Maximum tokens in the response (default 1024,
                        use 2048+ for detailed extraction).
        """
        prompt = question or (
            "Describe what you see on this screen. Identify:\n"
            "1. What application/website is this?\n"
            "2. What page/state is it in?\n"
            "3. What are the main content elements visible?\n"
            "4. What interactive elements (buttons, inputs, links) are available?\n"
            "Be concise and structured."
        )
        return self._call_vision(screenshot, prompt, max_tokens=max_tokens)

    def locate_element(
        self,
        screenshot: Image.Image,
        element_description: str,
    ) -> dict | None:
        """Ask the model to locate a UI element and return its approximate position.

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

        text = self._call_vision(screenshot, prompt, max_tokens=256)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"found": False, "description": f"Failed to parse response: {text}"}

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
