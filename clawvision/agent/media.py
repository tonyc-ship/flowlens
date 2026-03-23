"""Generic media processing utilities.

Provides LLM calls (text + vision), Apple OCR, whisper transcription,
and image utilities. Reusable across platforms.
"""

from __future__ import annotations

import base64
import json
import re
import shutil
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import anthropic

from ..runtime import load_runtime_env

DEFAULT_MODEL = "claude-sonnet-4-6"


@dataclass
class MediaConfig:
    model: str = DEFAULT_MODEL
    use_apple_ocr: bool = True
    use_whisper: bool = True
    use_vision: bool = True
    whisper_model: str = "large-v3-turbo"


class MediaProcessor:
    """Generic media processor: LLM calls, OCR, transcription, image utils."""

    def __init__(self, config: MediaConfig | None = None):
        load_runtime_env()
        self.config = config or MediaConfig()
        self.client = anthropic.Anthropic()
        self._ocr = None
        self._transcriber = None

    # ── LLM Calls ───────────────────────────────────────────────

    def call_text(self, prompt: str, max_tokens: int = 2048) -> str:
        resp = self.client.messages.create(
            model=self.config.model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text

    def call_vision(
        self,
        image_b64: str,
        prompt: str,
        media_type: str = "image/jpeg",
        max_tokens: int = 1024,
    ) -> str:
        if "," in image_b64:
            image_b64 = image_b64.split(",", 1)[1]
        resp = self.client.messages.create(
            model=self.config.model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_b64}},
                {"type": "text", "text": prompt},
            ]}],
        )
        return resp.content[0].text

    # ── OCR (Apple Vision Framework) ────────────────────────────

    @property
    def ocr(self):
        if self._ocr is None and self.config.use_apple_ocr:
            from ..vision.apple_ocr import AppleOCR
            self._ocr = AppleOCR()
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
                from ..vision.transcriber import WhisperTranscriber
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
        from ..vision.transcriber import WhisperTranscriber

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
