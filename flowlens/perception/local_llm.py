"""Local LLM inference via MLX for Apple Silicon.

Provides a drop-in backend that matches MediaProcessor's call_text / call_vision
interface using a local Qwen MLX model (natively multimodal via early fusion).

Usage:
    from flowlens.perception.local_llm import LocalLLM

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

WEIGHTS_DIR = Path.home() / ".flowlens" / "weights"
DEFAULT_LOCAL_MODEL = "Qwen3.5-9B-MLX-4bit"
DEFAULT_UI_TARS_MODEL = "UI-TARS-1.5-7B-6bit"

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
        expected_files = sorted(set((index.get("weight_map") or {}).values()))
    except Exception:
        expected_total = 0
        expected_files = []

    if expected_files and all((local / name).exists() for name in expected_files):
        return True

    if expected_total <= 0:
        return True

    actual_total = sum(path.stat().st_size for path in safetensors)
    if actual_total >= int(expected_total * 0.98):
        return True

    # Some local quantized repacks ship a stale upstream index.json with
    # original full-precision metadata. If the essential model files exist,
    # trust the local directory and let mlx-vlm load it directly.
    essentials = [
        local / "config.json",
        local / "tokenizer.json",
        local / "preprocessor_config.json",
    ]
    return actual_total > 0 and all(path.exists() for path in essentials)


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
        self._config = None

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
                cached = _cache[cache_key]
                if isinstance(cached, tuple) and len(cached) == 3:
                    self._model, self._processor, self._config = cached
                else:
                    self._model, self._processor = cached
                logger.info("Reusing cached model: %s (%s)", self.model_name, _perf_snapshot())
                return

            try:
                from mlx_vlm import load as vlm_load
                from mlx_vlm.utils import load_config
            except ImportError as exc:
                raise RuntimeError(
                    "Local LLM backend requires mlx-vlm. Install with "
                    '`pip install -e .` or install `mlx-vlm`, `mlx-lm`, and `modelscope` manually.'
                ) from exc

            path = _resolve_model_path(self.model_name)
            logger.info("Loading local model: %s (%s)", path, _perf_snapshot())
            t0 = time.perf_counter()
            self._model, self._processor = vlm_load(path)
            try:
                self._config = load_config(path)
            except Exception:
                self._config = None

            tokenizer = getattr(self._processor, "tokenizer", None)
            if getattr(self._processor, "chat_template", None) in (None, ""):
                chat_template = getattr(tokenizer, "chat_template", None)
                if chat_template:
                    self._processor.chat_template = chat_template

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
            _cache[cache_key] = (self._model, self._processor, self._config)

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
        return self.apply_chat_template(
            messages,
            add_generation_prompt=True,
        )

    def apply_chat_template(
        self,
        messages: list[dict],
        *,
        add_generation_prompt: bool = True,
    ) -> str:
        """Apply the model chat template with best-effort compatibility.

        Qwen models support ``enable_thinking`` while other local VLMs such as
        UI-TARS may expose a simpler signature. We try the richer path first and
        gracefully fall back to the generic Hugging Face-style call.
        """
        self._ensure_loaded()
        try:
            return self._processor.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=add_generation_prompt,
                enable_thinking=self.think,
            )
        except TypeError:
            return self._processor.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=add_generation_prompt,
            )
        except ValueError:
            if self._config is None:
                raise
            from mlx_vlm.prompt_utils import apply_chat_template as mlx_apply_chat_template

            num_images = 0
            for message in messages:
                content = message.get("content")
                if isinstance(content, list):
                    num_images += sum(
                        1
                        for item in content
                        if isinstance(item, dict) and item.get("type") == "image"
                    )

            return mlx_apply_chat_template(
                self._processor,
                self._config,
                messages,
                add_generation_prompt=add_generation_prompt,
                num_images=num_images,
            )

    def _generate(self, formatted_prompt: str, max_tokens: int, image: str | None = None) -> str:
        """Run generation and extract the answer text (thinking stripped)."""
        _, answer, _ = self._generate_with_thinking(formatted_prompt, max_tokens, image)
        return answer

    def _generate_with_thinking(
        self, formatted_prompt: str, max_tokens: int, image: str | None = None
    ) -> tuple[str, str, dict]:
        """Run generation and return (thinking_text, answer_text, metrics) separately.

        `metrics` contains prompt_tokens, generation_tokens, prompt_tps,
        generation_tps, prefill_s, generation_s, total_s — so callers can
        attribute API wall-time to prefill vs decode.
        """
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
        thinking, answer = _split_think_tags(text)
        prompt_tokens = int(getattr(result, "prompt_tokens", 0) or 0)
        generation_tokens = int(getattr(result, "generation_tokens", 0) or 0)
        prompt_tps = float(getattr(result, "prompt_tps", 0) or 0)
        generation_tps = float(getattr(result, "generation_tps", 0) or 0)
        prefill_s = round(prompt_tokens / prompt_tps, 3) if prompt_tps > 0 else 0.0
        generation_s = round(generation_tokens / generation_tps, 3) if generation_tps > 0 else 0.0
        metrics = {
            "prompt_tokens": prompt_tokens,
            "generation_tokens": generation_tokens,
            "prompt_tps": round(prompt_tps, 1),
            "generation_tps": round(generation_tps, 1),
            "prefill_s": prefill_s,
            "generation_s": generation_s,
            "total_s": round(elapsed, 3),
        }
        return thinking, answer, metrics

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


def _split_think_tags(text: str) -> tuple[str, str]:
    """Split <think>...</think> blocks from Qwen3.5 output.

    Returns (thinking_text, answer_text).

    Handles two cases:
    1. Full tags: <think>content</think>answer
    2. Partial (when <think> was in the prompt): content</think>answer
    """
    import re
    # Case 1: full <think>...</think> block
    match = re.search(r"<think>([\s\S]*?)</think>\s*", text)
    if match:
        thinking = match.group(1).strip()
        answer = text[:match.start()] + text[match.end():]
        return thinking, answer.strip()

    # Case 2: model output starts with thinking content, </think> appears later
    # (because <think> was already in the prompt template)
    if "</think>" in text:
        idx = text.index("</think>")
        thinking = text[:idx].strip()
        answer = text[idx + len("</think>"):].strip()
        return thinking, answer

    return "", text.strip()


def _strip_think_tags(text: str) -> str:
    """Remove <think>...</think> blocks from Qwen3.5 output."""
    _, answer = _split_think_tags(text)
    return answer


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
