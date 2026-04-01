"""Local LLM inference via MLX for Apple Silicon.

Provides a drop-in backend that matches MediaProcessor's call_text / call_vision
interface using Qwen3.5-9B-MLX-4bit (natively multimodal via early fusion).

Usage:
    from clawvision.perception.local_llm import LocalLLM

    llm = LocalLLM()                          # loads default model
    text = llm.call_text("Explain X")
    text = llm.call_vision(image_b64, "What is this?")
"""

from __future__ import annotations

import base64
import io
import logging
import os
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

WEIGHTS_DIR = Path.home() / ".clawvision" / "weights"
DEFAULT_LOCAL_MODEL = "Qwen3.5-9B-MLX-4bit"

# Singleton cache — loading a model takes several seconds and ~6GB RAM,
# so we keep exactly one loaded at a time.
_cache: dict[str, object] = {}
_locks: dict[str, threading.RLock] = {}


def _local_model_candidates(name: str) -> list[str]:
    candidates = [name]
    if "/" in name:
        candidates.append(name.rsplit("/", 1)[-1])
    return candidates


def _local_model_is_complete(local: Path) -> bool:
    safetensors = list(local.glob("*.safetensors"))
    if not safetensors:
        return False

    index_path = local / "model.safetensors.index.json"
    if not index_path.exists():
        return True

    try:
        import json

        index = json.loads(index_path.read_text())
        expected_total = int((index.get("metadata") or {}).get("total_size") or 0)
    except Exception:
        expected_total = 0

    if expected_total <= 0:
        return True

    actual_total = sum(path.stat().st_size for path in safetensors)
    return actual_total >= int(expected_total * 0.98)


def _resolve_model_path(name: str) -> str:
    """Return a local path if the model exists in WEIGHTS_DIR, else return name as-is."""
    for candidate in _local_model_candidates(name):
        local = WEIGHTS_DIR / candidate
        if local.is_dir() and _local_model_is_complete(local):
            return str(local)
    return name


class LocalLLM:
    """Local MLX-backed LLM for text and vision inference."""

    def __init__(self, model_name: str = DEFAULT_LOCAL_MODEL, *, think: bool = False):
        self.model_name = model_name
        self.think = think
        self._model = None
        self._processor = None

    def _ensure_loaded(self):
        """Lazy-load model + processor on first use."""
        if self._model is not None:
            return

        cache_key = self.model_name
        lock = _locks.setdefault(cache_key, threading.RLock())
        with lock:
            if self._model is not None:
                return
            if cache_key in _cache:
                self._model, self._processor = _cache[cache_key]
                logger.info("Reusing cached model: %s (%s)", self.model_name, _perf_snapshot())
                return

            try:
                from mlx_vlm import load as vlm_load
            except ImportError as exc:
                raise RuntimeError(
                    "Local LLM backend requires mlx-vlm. Install with "
                    '`pip install -e ".[local-llm]"` or install `mlx-vlm`, `mlx-lm`, and `modelscope` manually.'
                ) from exc

            path = _resolve_model_path(self.model_name)
            logger.info("Loading local model: %s (%s)", path, _perf_snapshot())
            t0 = time.perf_counter()
            self._model, self._processor = vlm_load(path)

            # Force slow (non-fast) image processor to avoid the
            # "Only returning PyTorch tensors" error from the fast processor.
            if hasattr(self._processor, "image_processor"):
                ip = self._processor.image_processor
                if getattr(ip, "is_fast", False):
                    from transformers import AutoImageProcessor
                    slow_ip = AutoImageProcessor.from_pretrained(path, use_fast=False)
                    self._processor.image_processor = slow_ip
                    logger.info("Switched to slow image processor for MLX compat")

            elapsed = time.perf_counter() - t0
            logger.info("Model loaded in %.1fs (%s)", elapsed, _perf_snapshot())
            _cache[cache_key] = (self._model, self._processor)

    def _format_prompt(self, text: str, *, image_path: str | None = None) -> str:
        """Wrap user content through the model's chat template.

        Qwen3.5 uses ChatML with optional <think> blocks.
        When think=False (default), we pass enable_thinking=False which
        inserts an empty <think></think> block to skip chain-of-thought.

        For vision calls, include an image reference so the template inserts
        the correct <|vision_start|><|image_pad|><|vision_end|> tokens.
        """
        self._ensure_loaded()
        if image_path:
            content = [
                {"type": "image", "image": f"file://{image_path}"},
                {"type": "text", "text": text},
            ]
        else:
            content = text
        messages = [{"role": "user", "content": content}]
        return self._processor.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=self.think,
        )

    def _generate(self, formatted_prompt: str, max_tokens: int, image: str | None = None) -> str:
        """Run generation and extract the answer text."""
        from mlx_vlm import generate as vlm_generate

        t0 = time.perf_counter()
        kwargs = dict(max_tokens=max_tokens, temperature=0.0)
        if image:
            kwargs["image"] = image
        call_type = "vision" if image else "text"
        perf_before = _perf_snapshot()
        logger.info(
            "local_llm.generate start type=%s model=%s max_tokens=%d prompt_chars=%d %s",
            call_type,
            self.model_name,
            max_tokens,
            len(formatted_prompt),
            perf_before,
        )
        lock = _locks.setdefault(self.model_name, threading.RLock())
        with lock:
            result = vlm_generate(
                self._model, self._processor,
                prompt=formatted_prompt,
                **kwargs,
            )
        elapsed = time.perf_counter() - t0
        text = result.text if hasattr(result, "text") else str(result)
        logger.info(
            "local_llm.generate done type=%s model=%s prompt_tok=%d gen_tok=%d elapsed=%.1fs tps=%.1f %s",
            call_type,
            self.model_name,
            getattr(result, "prompt_tokens", 0),
            getattr(result, "generation_tokens", 0),
            elapsed,
            getattr(result, "generation_tps", 0),
            _perf_snapshot(),
        )
        return _strip_think_tags(text)

    # ── Text generation ───────────────────────────────────────

    def call_text(self, prompt: str, max_tokens: int = 2048) -> str:
        """Generate text from a text-only prompt."""
        self._ensure_loaded()
        formatted = self._format_prompt(prompt)
        return self._generate(formatted, max_tokens)

    # ── Vision generation ─────────────────────────────────────

    def call_vision(
        self,
        image_b64: str,
        prompt: str,
        media_type: str = "image/jpeg",
        max_tokens: int = 1024,
    ) -> str:
        """Generate text from an image + text prompt."""
        self._ensure_loaded()

        # Strip data-URI prefix if present
        if "," in image_b64:
            image_b64 = image_b64.split(",", 1)[1]

        # Decode base64 → PIL → save to temp file (mlx-vlm takes file paths)
        img_bytes = base64.b64decode(image_b64)
        img_path = self._save_temp_image(img_bytes, media_type)

        formatted = self._format_prompt(prompt, image_path=img_path)
        text = self._generate(formatted, max_tokens, image=img_path)

        # Clean up temp file
        Path(img_path).unlink(missing_ok=True)
        return text

    def describe_image(self, img_bytes: bytes, prompt: str, max_tokens: int = 512) -> str:
        """Describe an image from raw bytes. Matches MediaProcessor.describe_image."""
        img_b64 = base64.b64encode(img_bytes).decode()
        media_type = _detect_media_type(img_bytes)
        return self.call_vision(img_b64, prompt, media_type, max_tokens)

    # ── Helpers ───────────────────────────────────────────────

    @staticmethod
    def _save_temp_image(img_bytes: bytes, media_type: str) -> str:
        """Save image bytes to a temp file and return the path."""
        from PIL import Image

        img = Image.open(io.BytesIO(img_bytes))

        # Downscale if too large (keep under 1568px max dim for efficiency)
        max_px = 1568
        w, h = img.size
        if max(w, h) > max_px:
            scale = max_px / max(w, h)
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

        # Convert RGBA to RGB
        if img.mode == "RGBA":
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[3])
            img = bg

        import os
        import tempfile

        fd, path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        img.save(path, format="PNG")
        return path

    @staticmethod
    def is_available(model_name: str = DEFAULT_LOCAL_MODEL) -> bool:
        """Check whether the local model weights are downloaded."""
        for candidate in _local_model_candidates(model_name):
            local = WEIGHTS_DIR / candidate
            if local.is_dir() and _local_model_is_complete(local):
                return True
        return False


def _strip_think_tags(text: str) -> str:
    """Remove <think>...</think> blocks from Qwen3.5 output."""
    import re
    # Remove thinking blocks (may appear at start of response)
    text = re.sub(r"<think>[\s\S]*?</think>\s*", "", text)
    return text.strip()


def _perf_snapshot() -> str:
    try:
        import psutil
    except Exception:
        return f"pid={os.getpid()}"
    try:
        proc = psutil.Process()
        rss_gb = proc.memory_info().rss / (1024 ** 3)
        threads = proc.num_threads()
        return f"pid={proc.pid} rss={rss_gb:.2f}GB threads={threads}"
    except Exception:
        return f"pid={os.getpid()}"


def _detect_media_type(data: bytes) -> str:
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
