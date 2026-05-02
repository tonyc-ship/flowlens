"""Generic media processing utilities.

Provides LLM calls (text + vision), Apple OCR, whisper transcription,
and image utilities. Reusable across platforms.

LLM backend is selected via ``FLOWLENS_LLM_BACKEND`` env var or
``MediaConfig.backend``. For local backends, ``MediaConfig.model``
selects the concrete MLX model name:

- ``"sonnet"`` / ``"anthropic"`` — Anthropic hosted models
- ``"openai"`` — OpenAI Responses API
- ``"qwen-local"`` — local Qwen MLX model via mlx-vlm
- ``"ui-tars-local"`` — local UI-TARS MLX model via mlx-vlm
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import shutil
import tempfile
import time
import urllib.request
from dataclasses import dataclass, replace
from pathlib import Path

import anthropic

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

DEFAULT_MODEL = "claude-sonnet-4-6"
BACKEND_SONNET = "sonnet"
BACKEND_OPENAI = "openai"
BACKEND_KIMI = "kimi"
BACKEND_QWEN_CLOUD = "qwen"
BACKEND_QWEN_LOCAL = "qwen-local"
BACKEND_UI_TARS_LOCAL = "ui-tars-local"
DEFAULT_WHISPER_MODEL = "large-v3-turbo"


def _resolve_backend(explicit: str | None = None) -> str:
    """Return the active LLM backend name."""
    val = explicit or os.environ.get("FLOWLENS_LLM_BACKEND", "")
    val = val.strip().lower()
    if val in (BACKEND_OPENAI, "gpt", "openai"):
        return BACKEND_OPENAI
    if val in (BACKEND_KIMI, "moonshot", "kimi"):
        return BACKEND_KIMI
    if val in (BACKEND_QWEN_CLOUD, "dashscope"):
        return BACKEND_QWEN_CLOUD
    if val in (BACKEND_UI_TARS_LOCAL, "ui-tars", "uitars", "uitars-local"):
        return BACKEND_UI_TARS_LOCAL
    if val in (BACKEND_QWEN_LOCAL, "qwen-local", "local"):
        return BACKEND_QWEN_LOCAL
    return BACKEND_SONNET


@dataclass
class MediaConfig:
    model: str = DEFAULT_MODEL
    backend: str = ""  # "" = auto-detect from env
    use_apple_ocr: bool = True
    use_whisper: bool = True
    use_vision: bool = True
    whisper_model: str = DEFAULT_WHISPER_MODEL
    local_image_max_dim: int = 768


class MediaProcessor:
    """Generic media processor: LLM calls, OCR, transcription, image utils."""

    def __init__(self, config: MediaConfig | None = None):
        load_runtime_env()
        raw_config = config or MediaConfig()
        inferred_backend = _resolve_backend(raw_config.backend)
        model = str(raw_config.model or "").strip()
        model_provider = resolve_model_provider(model) if model else ""
        if inferred_backend == BACKEND_SONNET and model_provider == PROVIDER_OPENAI:
            inferred_backend = BACKEND_OPENAI
        elif inferred_backend == BACKEND_SONNET and model_provider == PROVIDER_KIMI:
            inferred_backend = BACKEND_KIMI
        elif inferred_backend == BACKEND_SONNET and model_provider == PROVIDER_QWEN:
            inferred_backend = BACKEND_QWEN_CLOUD
        elif inferred_backend == BACKEND_SONNET and model_provider == "local":
            if model == "ui-tars-local" or model.startswith("UI-TARS"):
                inferred_backend = BACKEND_UI_TARS_LOCAL
            elif model == "qwen-local" or model.startswith("Qwen"):
                inferred_backend = BACKEND_QWEN_LOCAL

        if inferred_backend == BACKEND_OPENAI:
            if not model or model == DEFAULT_MODEL or resolve_model_provider(model) != PROVIDER_OPENAI:
                model = default_model_for_provider(PROVIDER_OPENAI)
        elif inferred_backend == BACKEND_KIMI:
            if not model or model == DEFAULT_MODEL or resolve_model_provider(model) != PROVIDER_KIMI:
                model = default_model_for_provider(PROVIDER_KIMI)
        elif inferred_backend == BACKEND_QWEN_CLOUD:
            if not model or model == DEFAULT_MODEL or resolve_model_provider(model) != PROVIDER_QWEN:
                model = default_model_for_provider(PROVIDER_QWEN)
        elif inferred_backend == BACKEND_SONNET:
            if not model or resolve_model_provider(model) == PROVIDER_OPENAI:
                model = default_model_for_provider(PROVIDER_ANTHROPIC)

        self.config = replace(raw_config, model=model, backend=inferred_backend)
        self.backend = inferred_backend
        self._anthropic_client = None
        self._openai_client = None
        self._openai_compat_client = None
        self._local_llm = None
        self._ocr = None
        self._transcriber = None
        logger.info("MediaProcessor using backend: %s model=%s", self.backend, self.config.model)

    @property
    def client(self):
        """Lazy Anthropic client — only created when the sonnet backend is used."""
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
        """Lazy OpenAI client for text and vision via Responses API."""
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
        """Lazy OpenAI-compatible client for hosted vendors."""
        if self._openai_compat_client is None:
            from openai import OpenAI

            provider = {
                BACKEND_KIMI: PROVIDER_KIMI,
                BACKEND_QWEN_CLOUD: PROVIDER_QWEN,
            }.get(self.backend)
            if not provider:
                raise RuntimeError(f"No OpenAI-compatible provider for media backend {self.backend!r}")

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
        if self.backend == BACKEND_KIMI and self.config.model.startswith("kimi-k2.6"):
            return {"thinking": {"type": "disabled"}}
        if self.backend == BACKEND_QWEN_CLOUD:
            return {"enable_thinking": False}
        return {}

    @staticmethod
    def _chat_message_text(message) -> str:
        content = getattr(message, "content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    parts.append(str(item.get("text") or item.get("content") or ""))
                else:
                    parts.append(str(item))
            return "\n".join(part for part in parts if part)
        return str(content or "")

    @property
    def local_llm(self):
        """Lazy local LLM for any MLX-backed backend."""
        if self._local_llm is None:
            from .local_llm import (
                DEFAULT_LOCAL_IMAGE_MAX_DIM,
                DEFAULT_LOCAL_MODEL,
                DEFAULT_UI_TARS_MODEL,
                LocalLLM,
            )

            model_name = (self.config.model or "").strip()
            if not model_name or model_name == DEFAULT_MODEL:
                if self.backend == BACKEND_UI_TARS_LOCAL:
                    model_name = DEFAULT_UI_TARS_MODEL
                else:
                    model_name = DEFAULT_LOCAL_MODEL
            image_max_dim = int(self.config.local_image_max_dim or DEFAULT_LOCAL_IMAGE_MAX_DIM)
            self._local_llm = LocalLLM(model_name, max_image_dim=image_max_dim)
        return self._local_llm

    # ── LLM Calls ───────────────────────────────────────────────

    def call_text(self, prompt: str, max_tokens: int = 2048) -> str:
        t0 = time.perf_counter()
        logger.info(
            "media.call_text start backend=%s prompt_chars=%d max_tokens=%d",
            self.backend,
            len(prompt),
            max_tokens,
        )
        if self.backend in {BACKEND_QWEN_LOCAL, BACKEND_UI_TARS_LOCAL}:
            result = self.local_llm.call_text(prompt, max_tokens=max_tokens)
        elif self.backend == BACKEND_OPENAI:
            resp = self.openai_client.responses.create(
                model=self.config.model,
                input=prompt,
                max_output_tokens=max_tokens,
            )
            result = resp.output_text
        elif self.backend in {BACKEND_KIMI, BACKEND_QWEN_CLOUD}:
            request = {
                "model": self.config.model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
            }
            extra_body = self._openai_compat_extra_body()
            if extra_body:
                request["extra_body"] = extra_body
            resp = self.openai_compat_client.chat.completions.create(**request)
            result = self._chat_message_text(resp.choices[0].message)
        else:
            resp = self.client.messages.create(
                model=self.config.model,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            result = resp.content[0].text
        logger.info(
            "media.call_text done backend=%s elapsed=%.2fs output_chars=%d",
            self.backend,
            time.perf_counter() - t0,
            len(result),
        )
        return result

    def call_vision(
        self,
        image_b64: str,
        prompt: str,
        media_type: str = "image/jpeg",
        max_tokens: int = 1024,
    ) -> str:
        if "," in image_b64:
            image_b64 = image_b64.split(",", 1)[1]
        image_bytes = int(len(image_b64) * 3 / 4)
        t0 = time.perf_counter()
        logger.info(
            "media.call_vision start backend=%s image_bytes~=%d prompt_chars=%d max_tokens=%d media_type=%s",
            self.backend,
            image_bytes,
            len(prompt),
            max_tokens,
            media_type,
        )
        if self.backend in {BACKEND_QWEN_LOCAL, BACKEND_UI_TARS_LOCAL}:
            result = self.local_llm.call_vision(
                image_b64, prompt, media_type=media_type, max_tokens=max_tokens,
            )
        elif self.backend == BACKEND_OPENAI:
            resp = self.openai_client.responses.create(
                model=self.config.model,
                max_output_tokens=max_tokens,
                input=[{
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        {"type": "input_image", "image_url": f"data:{media_type};base64,{image_b64}", "detail": "high"},
                    ],
                }],
            )
            result = resp.output_text
        elif self.backend in {BACKEND_KIMI, BACKEND_QWEN_CLOUD}:
            request = {
                "model": self.config.model,
                "max_tokens": max_tokens,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{media_type};base64,{image_b64}",
                            },
                        },
                    ],
                }],
            }
            extra_body = self._openai_compat_extra_body()
            if extra_body:
                request["extra_body"] = extra_body
            resp = self.openai_compat_client.chat.completions.create(**request)
            result = self._chat_message_text(resp.choices[0].message)
        else:
            resp = self.client.messages.create(
                model=self.config.model,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_b64}},
                    {"type": "text", "text": prompt},
                ]}],
            )
            result = resp.content[0].text
        logger.info(
            "media.call_vision done backend=%s elapsed=%.2fs output_chars=%d",
            self.backend,
            time.perf_counter() - t0,
            len(result),
        )
        return result

    # ── OCR (Apple Vision Framework) ────────────────────────────

    @property
    def ocr(self):
        if self._ocr is None and self.config.use_apple_ocr:
            try:
                from .apple_ocr import AppleOCR
                self._ocr = AppleOCR()
            except ImportError:
                pass
        return self._ocr

    def ocr_image(self, img_bytes: bytes) -> str:
        """Extract text from image bytes via Apple OCR. Returns empty string on failure."""
        if not self.ocr:
            return ""
        try:
            return self.ocr.extract_text(img_bytes)
        except Exception:
            return ""

    # ── Transcription (whisper.cpp) ─────────────────────────────

    @property
    def transcriber(self):
        if self._transcriber is None and self.config.use_whisper:
            try:
                from .transcriber import WhisperTranscriber
                self._transcriber = WhisperTranscriber(model=self.config.whisper_model)
            except FileNotFoundError:
                pass
        return self._transcriber

    async def transcribe_video(
        self,
        video_url: str,
        language: str = "zh",
        referer: str = "",
        max_audio_seconds: int | None = 90,
        timeout_s: float = 300,
    ) -> str:
        """Download video and transcribe audio. Returns empty string on failure."""
        if not self.transcriber:
            return ""
        try:
            return await self.transcriber.download_and_transcribe(
                video_url,
                language,
                referer=referer,
                max_audio_seconds=max_audio_seconds,
                timeout_s=timeout_s,
            )
        except Exception:
            return ""

    async def extract_video_frames(
        self,
        source: str,
        referer: str = "",
        max_seconds: int = 60,
        num_frames: int = 4,
        timeout_s: float = 180,
    ) -> list[str]:
        """Sample video frames to local JPEG files. Returns frame paths."""
        from .transcriber import WhisperTranscriber

        try:
            return await WhisperTranscriber.extract_video_frames(
                source,
                referer=referer,
                max_seconds=max_seconds,
                num_frames=num_frames,
                timeout_s=timeout_s,
            )
        except Exception:
            return []

    # ── Image Utilities ─────────────────────────────────────────

    @staticmethod
    def detect_media_type(data: bytes) -> str:
        """Detect image MIME type from magic bytes."""
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return "image/webp"
        if data[:3] == b"\xff\xd8\xff":
            return "image/jpeg"
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return "image/png"
        if data[:4] in (b"GIF8",):
            return "image/gif"
        return "image/jpeg"

    @staticmethod
    def download_image(url: str, referer: str = "") -> bytes | None:
        """Download image from URL with optional referer header."""
        try:
            headers = {"User-Agent": "Mozilla/5.0"}
            if referer:
                headers["Referer"] = referer
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.read()
        except Exception:
            return None

    @staticmethod
    def download_file(url: str, referer: str = "", suffix: str = "") -> str:
        """Download a remote file to a temporary local path."""
        headers = {"User-Agent": "Mozilla/5.0"}
        if referer:
            headers["Referer"] = referer
        req = urllib.request.Request(url, headers=headers)
        fd, path = tempfile.mkstemp(suffix=suffix)
        Path(path).unlink(missing_ok=True)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp, open(path, "wb") as out:
                shutil.copyfileobj(resp, out)
            return path
        except Exception:
            Path(path).unlink(missing_ok=True)
            return ""

    @staticmethod
    def extract_json(text: str):
        """Extract JSON object or array from LLM response text."""
        m = re.search(r"[\[{][\s\S]*[\]}]", text)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass
        return None

    def describe_image(self, img_bytes: bytes, prompt: str, max_tokens: int = 512) -> str:
        """Describe an image using Vision API. Auto-detects media type."""
        img_b64 = base64.b64encode(img_bytes).decode()
        media_type = self.detect_media_type(img_bytes)
        return self.call_vision(img_b64, prompt, media_type, max_tokens)
