"""OCR text extraction from screenshots.

MVP: Uses Claude Vision API for OCR (simple, accurate, no extra deps).
Future: Swap in PaddleOCR for faster/cheaper local inference.
"""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image

from .llm import VisionLLM


@dataclass
class TextRegion:
    text: str
    x: int
    y: int
    width: int
    height: int
    confidence: float


class OCREngine:
    """Extract text from screenshots.

    MVP uses Claude Vision API. Can be swapped to PaddleOCR later
    for speed and cost savings.
    """

    def __init__(self, llm: VisionLLM | None = None):
        self.llm = llm or VisionLLM()

    def extract_all_text(self, image: Image.Image) -> str:
        """Extract all visible text from an image."""
        return self.llm.analyze_page(
            image,
            "Extract ALL visible text from this image. "
            "Preserve the layout structure. Return only the text, no commentary.",
        )

    def extract_structured(self, image: Image.Image, fields: list[str]) -> dict[str, str]:
        """Extract specific named fields from an image.

        Example: extract_structured(img, ["title", "likes", "author"])
        """
        fields_str = ", ".join(fields)
        response = self.llm.analyze_page(
            image,
            f"Extract these fields from the image: {fields_str}\n\n"
            "Respond in JSON format with field names as keys. "
            "Use null for fields not found. No markdown, just JSON.",
        )
        import json

        try:
            return json.loads(response)
        except json.JSONDecodeError:
            return {f: None for f in fields}
