"""Vision LLM for high-level page understanding.

Supports hosted and local backends (selected via ``SOCAI_LLM_BACKEND`` env
var or constructor ``backend`` kwarg):

- ``"sonnet"`` / ``"anthropic"`` — Anthropic hosted vision models
- ``"openai"`` — OpenAI Responses API vision models
- ``"kimi"`` / ``"qwen"`` — OpenAI-compatible hosted vision models
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
from dataclasses import dataclass

import anthropic
from PIL import Image

from ..core.auth import (
    METHOD_API_KEY,
    PROVIDER_ANTHROPIC,
    PROVIDER_KIMI,
    PROVIDER_OPENAI,
    PROVIDER_QWEN,
    default_model_for_provider,
    provider_config,
    resolve_model_provider,
    resolve_provider_auth,
)
from ..core.runtime import load_runtime_env

logger = logging.getLogger(__name__)

BACKEND_SONNET = "sonnet"
BACKEND_OPENAI = "openai"
BACKEND_KIMI = "kimi"
BACKEND_QWEN_CLOUD = "qwen"
BACKEND_QWEN_LOCAL = "qwen-local"


def _resolve_backend(explicit: str | None = None) -> str:
    val = explicit or os.environ.get("SOCAI_LLM_BACKEND", "")
    val = val.strip().lower()
    if val in (BACKEND_OPENAI, "gpt", "openai"):
        return BACKEND_OPENAI
    if val in (BACKEND_KIMI, "moonshot", "kimi"):
        return BACKEND_KIMI
    if val in (BACKEND_QWEN_CLOUD, "dashscope"):
        return BACKEND_QWEN_CLOUD
    if val in (BACKEND_QWEN_LOCAL, "qwen", "local"):
        return BACKEND_QWEN_LOCAL
    return BACKEND_SONNET


@dataclass(frozen=True)
class VisionRequestConfig:
    """Execution-time tuning knobs for a single vision request."""

    name: str = "default"
    local_model_name: str | None = None
    max_image_pixels: int | None = None
    max_tokens: int = 1024
    crop_bounds: tuple[float, float, float, float] | None = None


class VisionLLM:
    """High-level visual understanding via Claude Vision or local MLX model."""

    def __init__(self, model: str = "claude-sonnet-4-6", *, backend: str | None = None):
        load_runtime_env()
        self.backend = _resolve_backend(backend)
        model_provider = resolve_model_provider(model)
        if self.backend == BACKEND_SONNET and model_provider == PROVIDER_OPENAI:
            self.backend = BACKEND_OPENAI
        elif self.backend == BACKEND_SONNET and model_provider == PROVIDER_KIMI:
            self.backend = BACKEND_KIMI
        elif self.backend == BACKEND_SONNET and model_provider == PROVIDER_QWEN:
            self.backend = BACKEND_QWEN_CLOUD
        if self.backend == BACKEND_OPENAI and resolve_model_provider(model) != PROVIDER_OPENAI:
            model = default_model_for_provider(PROVIDER_OPENAI)
        elif self.backend == BACKEND_KIMI and resolve_model_provider(model) != PROVIDER_KIMI:
            model = default_model_for_provider(PROVIDER_KIMI)
        elif self.backend == BACKEND_QWEN_CLOUD and resolve_model_provider(model) != PROVIDER_QWEN:
            model = default_model_for_provider(PROVIDER_QWEN)
        elif self.backend == BACKEND_SONNET and resolve_model_provider(model) == PROVIDER_OPENAI:
            model = default_model_for_provider(PROVIDER_ANTHROPIC)
        self.model = model
        self._anthropic_client = None
        self._openai_client = None
        self._openai_compat_client = None
        self._local_llms: dict[str, object] = {}
        logger.info("VisionLLM using backend: %s", self.backend)

    @property
    def client(self):
        """Lazy Anthropic client."""
        if self._anthropic_client is None:
            credential = resolve_provider_auth(PROVIDER_ANTHROPIC)
            kwargs = {}
            if credential is not None:
                if credential.method == METHOD_API_KEY:
                    kwargs["api_key"] = credential.secret
                else:
                    kwargs["auth_token"] = credential.secret
            self._anthropic_client = anthropic.Anthropic(**kwargs)
        return self._anthropic_client

    @property
    def openai_client(self):
        if self._openai_client is None:
            from openai import OpenAI

            credential = resolve_provider_auth(PROVIDER_OPENAI)
            kwargs = {}
            if credential is not None:
                kwargs["api_key"] = credential.secret
            self._openai_client = OpenAI(**kwargs)
        return self._openai_client

    @property
    def openai_compat_client(self):
        if self._openai_compat_client is None:
            from openai import OpenAI

            provider = {
                BACKEND_KIMI: PROVIDER_KIMI,
                BACKEND_QWEN_CLOUD: PROVIDER_QWEN,
            }.get(self.backend)
            if not provider:
                raise RuntimeError(f"No OpenAI-compatible provider for vision backend {self.backend!r}")

            config = provider_config(provider)
            credential = resolve_provider_auth(provider)
            kwargs = {}
            if credential is not None:
                kwargs["api_key"] = credential.secret
            if config and config.base_url:
                kwargs["base_url"] = config.base_url
            self._openai_compat_client = OpenAI(**kwargs)
        return self._openai_compat_client

    def _openai_compat_extra_body(self) -> dict:
        if self.backend == BACKEND_KIMI and self.model.startswith("kimi-k2.6"):
            return {"thinking": {"type": "disabled"}}
        if self.backend == BACKEND_QWEN_CLOUD:
            return {"enable_thinking": False}
        return {}

    MAX_IMAGE_BYTES = 4_800_000  # Stay under Anthropic's 5MB limit
    MAX_IMAGE_PIXELS = 1568  # Max dimension for efficient API usage

    def _get_local_llm(self, model_name: str | None = None):
        """Return a cached local LLM instance for the requested model."""
        from .local_llm import DEFAULT_LOCAL_MODEL, LocalLLM

        requested = model_name or DEFAULT_LOCAL_MODEL
        if model_name is not None:
            selected = requested
        else:
            selected = requested if LocalLLM.is_available(requested) else DEFAULT_LOCAL_MODEL
        if selected not in self._local_llms:
            self._local_llms[selected] = LocalLLM(selected)
        return self._local_llms[selected]

    @staticmethod
    def _apply_crop(image: Image.Image, config: VisionRequestConfig | None = None) -> Image.Image:
        if not config or not config.crop_bounds:
            return image
        left, top, right, bottom = config.crop_bounds
        width, height = image.size
        crop_box = (
            max(0, min(width, int(width * left))),
            max(0, min(height, int(height * top))),
            max(0, min(width, int(width * right))),
            max(0, min(height, int(height * bottom))),
        )
        if crop_box[2] <= crop_box[0] or crop_box[3] <= crop_box[1]:
            return image
        return image.crop(crop_box)

    def _prepare_image(self, image: Image.Image, config: VisionRequestConfig | None = None) -> Image.Image:
        """Resize image if needed to stay within API limits."""
        image = self._apply_crop(image, config)
        # Downscale if any dimension exceeds max
        max_pixels = config.max_image_pixels if config and config.max_image_pixels else self.MAX_IMAGE_PIXELS
        w, h = image.size
        if max_pixels and max(w, h) > max_pixels:
            scale = max_pixels / max(w, h)
            image = image.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        # Convert RGBA to RGB (smaller PNG)
        if image.mode == "RGBA":
            bg = Image.new("RGB", image.size, (255, 255, 255))
            bg.paste(image, mask=image.split()[3])
            image = bg
        return image

    def _image_to_base64(self, image: Image.Image, config: VisionRequestConfig | None = None) -> str:
        image = self._prepare_image(image, config)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        # If still too large, use JPEG
        if buffer.tell() > self.MAX_IMAGE_BYTES:
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=85)
        return base64.standard_b64encode(buffer.getvalue()).decode("utf-8")

    # ── Core dispatch ─────────────────────────────────────────

    def _call_vision(
        self,
        image: Image.Image,
        prompt: str,
        max_tokens: int = 1024,
        *,
        config: VisionRequestConfig | None = None,
    ) -> str:
        """Dispatch a vision call to the active backend."""
        effective_tokens = config.max_tokens if config and max_tokens == 1024 else max_tokens
        if self.backend == BACKEND_QWEN_LOCAL:
            img_b64 = self._image_to_base64(image, config)
            local_llm = self._get_local_llm(config.local_model_name if config else None)
            return local_llm.call_vision(
                img_b64,
                prompt,
                media_type="image/png",
                max_tokens=effective_tokens,
            )
        if self.backend == BACKEND_OPENAI:
            response = self.openai_client.responses.create(
                model=self.model,
                max_output_tokens=effective_tokens,
                input=[{
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        {
                            "type": "input_image",
                            "image_url": f"data:image/png;base64,{self._image_to_base64(image, config)}",
                            "detail": "high",
                        },
                    ],
                }],
            )
            return response.output_text
        if self.backend in {BACKEND_KIMI, BACKEND_QWEN_CLOUD}:
            request = {
                "model": self.model,
                "max_tokens": effective_tokens,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{self._image_to_base64(image, config)}"},
                        },
                    ],
                }],
            }
            extra_body = self._openai_compat_extra_body()
            if extra_body:
                request["extra_body"] = extra_body
            response = self.openai_compat_client.chat.completions.create(**request)
            message = response.choices[0].message
            content = getattr(message, "content", "")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                text_parts = []
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        text_parts.append(str(item.get("text", "")))
                    elif getattr(item, "type", None) == "text":
                        text_parts.append(str(getattr(item, "text", "")))
                return "\n".join(part for part in text_parts if part).strip()
            return ""
        # Anthropic backend
        response = self.client.messages.create(
            model=self.model,
            max_tokens=effective_tokens,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": self._image_to_base64(image, config),
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
        self,
        screenshot: Image.Image,
        question: str | None = None,
        *,
        max_tokens: int = 1024,
        config: VisionRequestConfig | None = None,
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
        return self._call_vision(screenshot, prompt, max_tokens=max_tokens, config=config)

    def locate_element(
        self,
        screenshot: Image.Image,
        element_description: str,
        *,
        config: VisionRequestConfig | None = None,
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

        text = self._call_vision(screenshot, prompt, max_tokens=256, config=config)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"found": False, "description": f"Failed to parse response: {text}"}

    def preload_local_model(self, model_name: str | None = None) -> None:
        """Eagerly load a local vision model for upcoming requests."""
        if self.backend != BACKEND_QWEN_LOCAL:
            return
        local_llm = self._get_local_llm(model_name)
        local_llm._ensure_loaded()

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
