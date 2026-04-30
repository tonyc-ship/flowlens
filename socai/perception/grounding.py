"""Grounding model wrapper — locates UI elements from natural language queries.

Supports multiple backends:
- uitars_mlx (UI-TARS-1.5-7B via MLX — best local option)
- uground_mlx (UGround-V1-2B via MLX)
- ollama (any vision model)
- claude (Claude Vision API — most accurate, costs API calls)

All backends implement the same interface:
    ground(screenshot, query) -> (x, y) in pixel coordinates, or None
"""

from __future__ import annotations

import base64
import io
import json
import re
from dataclasses import dataclass

from PIL import Image


@dataclass
class GroundingResult:
    """Result of a grounding query."""

    x: int  # pixel x coordinate
    y: int  # pixel y coordinate
    confidence: float  # 0.0-1.0 (estimated)
    source: str  # which backend produced this
    raw_output: str = ""  # raw model output for debugging


class GroundingModel:
    """Unified interface for UI element grounding."""

    def __init__(self, backend: str = "auto"):
        """Initialize grounding model.

        Args:
            backend: "uitars_mlx", "uground_mlx", "ollama", "claude",
                     or "auto" (try in order).
        """
        self.backend = backend
        self._ollama_model: str | None = None
        self._uitars_model = None
        self._uitars_processor = None
        self._uitars_config = None
        self._uground_model = None
        self._uground_processor = None
        self._claude_llm = None

    @staticmethod
    def _image_to_base64(image: Image.Image, max_dim: int = 1024) -> str:
        """Convert PIL Image to base64 string, resizing if needed."""
        w, h = image.size
        if max(w, h) > max_dim:
            scale = max_dim / max(w, h)
            image = image.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        if image.mode == "RGBA":
            bg = Image.new("RGB", image.size, (255, 255, 255))
            bg.paste(image, mask=image.split()[3])
            image = bg
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode()

    @staticmethod
    def _save_temp_image(image: Image.Image, max_dim: int = 1024) -> str:
        """Save image to a temp file, return path."""
        import tempfile
        w, h = image.size
        if max(w, h) > max_dim:
            scale = max_dim / max(w, h)
            image = image.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        if image.mode == "RGBA":
            bg = Image.new("RGB", image.size, (255, 255, 255))
            bg.paste(image, mask=image.split()[3])
            image = bg
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        image.save(tmp.name)
        return tmp.name

    def ground(
        self, screenshot: Image.Image, query: str
    ) -> GroundingResult | None:
        """Locate a UI element described by query in the screenshot.

        Returns GroundingResult with pixel coordinates, or None if not found.
        """
        backends_to_try = (
            [self.backend] if self.backend != "auto"
            else ["uitars_mlx", "claude"]
        )

        for backend in backends_to_try:
            try:
                if backend == "uitars_mlx":
                    return self._ground_uitars(screenshot, query)
                elif backend == "ollama":
                    return self._ground_ollama(screenshot, query)
                elif backend == "uground_mlx":
                    return self._ground_uground(screenshot, query)
                elif backend == "claude":
                    return self._ground_claude(screenshot, query)
            except Exception as e:
                if self.backend != "auto":
                    raise
                # Auto mode: try next backend
                continue

        return None

    def _ground_uitars(
        self, screenshot: Image.Image, query: str
    ) -> GroundingResult | None:
        """Ground via UI-TARS-1.5-7B (MLX). Best local grounding model."""
        import os

        if self._uitars_model is None:
            from mlx_vlm import load
            from mlx_vlm.utils import load_config

            model_path = os.path.expanduser("~/.socai/weights/UI-TARS-1.5-7B-6bit")
            if not os.path.exists(model_path):
                raise RuntimeError(f"UI-TARS model not found at {model_path}")
            self._uitars_model, self._uitars_processor = load(model_path)
            self._uitars_config = load_config(model_path)

        from mlx_vlm import generate
        from mlx_vlm.prompt_utils import apply_chat_template

        orig_w, orig_h = screenshot.size
        # Resize to fit Metal buffer limits (max 1024px)
        max_dim = 1024
        img_path = self._save_temp_image(screenshot, max_dim=max_dim)

        # Calculate resized dimensions for coordinate mapping
        scale = min(1.0, max_dim / max(orig_w, orig_h))
        resized_w = int(orig_w * scale)
        resized_h = int(orig_h * scale)

        prompt_text = (
            f"In this screenshot, locate the UI element: \"{query}\"\n"
            f"Return the click point coordinates as (x, y)."
        )
        prompt = apply_chat_template(
            self._uitars_processor, self._uitars_config,
            prompt_text, num_images=1
        )

        output = generate(
            self._uitars_model, self._uitars_processor,
            prompt, image=img_path, max_tokens=128, verbose=False
        )
        raw = output.text if hasattr(output, "text") else str(output)

        # Clean up temp file
        try:
            os.unlink(img_path)
        except OSError:
            pass

        # Parse UI-TARS output: <|box_start|>(x,y)<|box_end|> or plain (x,y)
        coords = re.findall(r"\((\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\)", raw)
        if coords:
            rx, ry = float(coords[0][0]), float(coords[0][1])
            # Coordinates are relative to the resized image
            # Scale back to original image dimensions
            px = int(rx / resized_w * orig_w)
            py = int(ry / resized_h * orig_h)
            return GroundingResult(
                x=px, y=py, confidence=0.8, source="uitars_mlx", raw_output=raw
            )

        return None

    def _ground_ollama(
        self, screenshot: Image.Image, query: str
    ) -> GroundingResult | None:
        """Ground via ollama (UI-TARS or similar vision model)."""
        import httpx

        if self._ollama_model is None:
            # Discover available vision model
            resp = httpx.get("http://localhost:11434/api/tags", timeout=5)
            if resp.status_code != 200:
                raise RuntimeError("ollama not running")
            models = resp.json().get("models", [])
            vision_models = [
                m["name"] for m in models
                if any(kw in m["name"].lower() for kw in ["ui-tars", "uitars", "showui", "gui"])
            ]
            if not vision_models:
                # Try any vision model
                vision_models = [m["name"] for m in models]
            if not vision_models:
                raise RuntimeError("No ollama models available")
            self._ollama_model = vision_models[0]

        img_b64 = self._image_to_base64(screenshot)
        w, h = screenshot.size

        prompt = (
            f"In this screenshot, locate the UI element: \"{query}\"\n"
            f"Return the click point as coordinates in the format: "
            f"click(x, y) where x and y are pixel values. "
            f"The image is {w}x{h} pixels."
        )

        resp = httpx.post(
            "http://localhost:11434/api/generate",
            json={
                "model": self._ollama_model,
                "prompt": prompt,
                "images": [img_b64],
                "stream": False,
            },
            timeout=60,
        )

        if resp.status_code != 200:
            raise RuntimeError(f"ollama error: {resp.status_code}")

        output = resp.json().get("response", "")
        return self._parse_coordinates(output, w, h, "ollama")

    def _ground_uground(
        self, screenshot: Image.Image, query: str
    ) -> GroundingResult | None:
        """Ground via UGround-V1-2B (MLX)."""
        import os

        if self._uground_model is None:
            from mlx_vlm import load
            from mlx_vlm.utils import load_config

            model_path = os.path.expanduser("~/.socai/weights/uground-v1-2b-mlx")
            if not os.path.exists(model_path):
                raise RuntimeError(f"UGround model not found at {model_path}")
            self._uground_model, self._uground_processor = load(model_path)
            self._uground_config = load_config(model_path)

        from mlx_vlm import generate
        from mlx_vlm.prompt_utils import apply_chat_template

        w, h = screenshot.size
        img_path = self._save_temp_image(screenshot)

        prompt_text = (
            f"In the screenshot, I want to click on \"{query}\". "
            f"Identify the precise coordinates (x, y)."
        )
        prompt = apply_chat_template(
            self._uground_processor, self._uground_config,
            prompt_text, num_images=1
        )

        output = generate(
            self._uground_model, self._uground_processor,
            prompt, image=img_path, max_tokens=128, verbose=False
        )
        raw = output.text if hasattr(output, "text") else str(output)

        # UGround outputs coordinates in [0, 1000) range
        coords = re.findall(r"\(?\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*\)?", raw)
        if coords:
            rx, ry = float(coords[0][0]), float(coords[0][1])
            # UGround uses 0-999 range
            if rx <= 1.0 and ry <= 1.0:
                px, py = int(rx * w), int(ry * h)
            elif rx < 1000 and ry < 1000:
                px, py = int(rx / 1000 * w), int(ry / 1000 * h)
            else:
                px, py = int(rx), int(ry)
            return GroundingResult(
                x=px, y=py, confidence=0.7, source="uground_mlx", raw_output=raw
            )

        # Clean up temp file
        import os
        os.unlink(img_path)

        return None

    def _ground_claude(
        self, screenshot: Image.Image, query: str
    ) -> GroundingResult | None:
        """Ground via Claude Vision API (fallback)."""
        if self._claude_llm is None:
            from .llm import VisionLLM
            self._claude_llm = VisionLLM()

        result = self._claude_llm.locate_element(screenshot, query)
        if not result or not result.get("found"):
            return None

        w, h = screenshot.size
        try:
            x_val = float(result["x"])
            y_val = float(result["y"])
        except (ValueError, TypeError):
            return None
        px = int(x_val / 100 * w)
        py = int(y_val / 100 * h)

        return GroundingResult(
            x=px, y=py,
            confidence=result.get("confidence", 0.5),
            source="claude",
            raw_output=json.dumps(result),
        )

    @staticmethod
    def _parse_coordinates(
        output: str, img_w: int, img_h: int, source: str
    ) -> GroundingResult | None:
        """Parse coordinates from model output. Handles various formats."""
        # Try click(x, y) format
        m = re.search(r"click\s*\(\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*\)", output, re.I)
        if m:
            x, y = float(m.group(1)), float(m.group(2))
            if x <= 1.0 and y <= 1.0:
                x, y = x * img_w, y * img_h
            elif x < 1000 and y < 1000 and x > img_w:
                x, y = x / 1000 * img_w, y / 1000 * img_h
            return GroundingResult(
                x=int(x), y=int(y), confidence=0.6, source=source, raw_output=output
            )

        # Try (x, y) format
        m = re.search(r"\(\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*\)", output)
        if m:
            x, y = float(m.group(1)), float(m.group(2))
            if x <= 1.0 and y <= 1.0:
                x, y = x * img_w, y * img_h
            elif x < 1000 and y < 1000 and x > img_w:
                x, y = x / 1000 * img_w, y / 1000 * img_h
            return GroundingResult(
                x=int(x), y=int(y), confidence=0.5, source=source, raw_output=output
            )

        # Try x: N, y: N format
        mx = re.search(r"x\s*[:=]\s*(\d+(?:\.\d+)?)", output, re.I)
        my = re.search(r"y\s*[:=]\s*(\d+(?:\.\d+)?)", output, re.I)
        if mx and my:
            x, y = float(mx.group(1)), float(my.group(1))
            if x <= 1.0 and y <= 1.0:
                x, y = x * img_w, y * img_h
            return GroundingResult(
                x=int(x), y=int(y), confidence=0.4, source=source, raw_output=output
            )

        return None
