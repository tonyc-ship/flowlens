"""Smart element locator combining YOLO precision with LLM understanding.

Strategy:
1. YOLO detects all UI elements with pixel-precise bounding boxes
2. Group elements by size/position into semantic categories (cards, buttons, tabs, etc.)
3. LLM selects the correct element from candidates based on description
4. Return pixel-precise coordinates from YOLO, not LLM approximations

This solves the core weakness: LLM percentage coordinates have ~50px error,
while YOLO provides pixel-precise bounding boxes.
"""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont

from .detector import DetectedElement, YOLOUIDetector
from .llm import VisionLLM


@dataclass
class LocatedElement:
    """A precisely located UI element."""

    description: str
    x: int  # center x
    y: int  # center y
    bbox: tuple[int, int, int, int]  # x1, y1, x2, y2
    confidence: float
    method: str  # "yolo", "llm", "hybrid"


class SmartLocator:
    """Hybrid element locator: YOLO precision + LLM understanding.

    Much more accurate than pure LLM locate_element (pixel-precise vs ~50px error).
    """

    def __init__(self, yolo: YOLOUIDetector | None = None, llm: VisionLLM | None = None):
        self._yolo = yolo or YOLOUIDetector()
        self._llm = llm or VisionLLM()

    def locate(self, image: Image.Image, description: str) -> LocatedElement | None:
        """Find an element by description with pixel-precise coordinates.

        Steps:
        1. Run YOLO to get all element bounding boxes
        2. Classify elements by size into categories
        3. Create annotated image with numbered candidates
        4. Ask LLM to pick the correct numbered element
        5. Return YOLO's precise bbox for the selected element
        """
        # Step 1: Detect all elements
        elements = self._yolo.detect(image)
        if not elements:
            # Fallback to pure LLM
            return self._fallback_llm(image, description)

        # Step 2: Filter to likely candidates based on description keywords
        candidates = self._filter_candidates(elements, description, image)
        if not candidates:
            candidates = elements  # Use all if filtering found nothing

        # Limit to top 20 by confidence to keep the annotated image manageable
        candidates = sorted(candidates, key=lambda e: -e.confidence)[:20]

        # Step 3: Create annotated image with numbered boxes
        annotated = self._annotate_image(image, candidates)

        # Step 4: Ask LLM to pick the right one
        candidates_desc = "\n".join(
            f"  #{i+1}: size={e.width}x{e.height} at ({e.x},{e.y}) conf={e.confidence:.2f}"
            for i, e in enumerate(candidates)
        )

        prompt = (
            f"I'm looking for: '{description}'\n\n"
            f"The image has {len(candidates)} detected UI elements marked with red numbered boxes.\n"
            f"Element details:\n{candidates_desc}\n\n"
            "Which numbered element best matches the description? "
            "Reply with ONLY the number (e.g., '3'). "
            "If none match, reply 'NONE'."
        )

        response = self._llm.analyze_page(annotated, prompt)
        response = response.strip().strip("#").strip()

        try:
            idx = int(response) - 1
            if 0 <= idx < len(candidates):
                e = candidates[idx]
                return LocatedElement(
                    description=description,
                    x=e.center[0],
                    y=e.center[1],
                    bbox=e.bbox,
                    confidence=e.confidence,
                    method="hybrid",
                )
        except ValueError:
            pass

        # If LLM couldn't pick, fallback
        return self._fallback_llm(image, description)

    def locate_all_cards(self, image: Image.Image, min_area: int = 50000) -> list[LocatedElement]:
        """Find all note cards on the page (large elements).

        Filters YOLO detections by size and position to find content cards
        vs small UI elements and navigation bars.
        """
        elements = self._yolo.detect(image)
        img_w, img_h = image.size
        # Cards must be below the top nav/search area (y > 15% of image height)
        min_y = int(img_h * 0.15)

        cards = []
        for e in elements:
            area = e.width * e.height
            # Cards should be:
            #  - Large enough (>min_area px^2)
            #  - In the content area (top of bbox below 15% of image height)
            #  - Have card-like aspect ratio (height > width * 0.5)
            if (
                area >= min_area
                and e.bbox[1] > min_y
                and e.height > e.width * 0.5
                and e.height > 100
                and e.width > 100
            ):
                cards.append(LocatedElement(
                    description=f"card_{len(cards)+1}",
                    x=e.center[0],
                    y=e.center[1],
                    bbox=e.bbox,
                    confidence=e.confidence,
                    method="yolo",
                ))
        return cards

    def crop_element(self, image: Image.Image, element: LocatedElement) -> Image.Image:
        """Crop a region from the image corresponding to a LocatedElement.

        Args:
            image: The full-page PIL Image.
            element: A LocatedElement with a bbox (x1, y1, x2, y2).

        Returns:
            A cropped PIL Image of the element region.
        """
        x1, y1, x2, y2 = element.bbox
        # Clamp to image bounds
        w, h = image.size
        x1 = max(0, x1)
        y1 = max(0, y1)
        x2 = min(w, x2)
        y2 = min(h, y2)
        return image.crop((x1, y1, x2, y2))

    def _filter_candidates(
        self, elements: list[DetectedElement], description: str, image: Image.Image
    ) -> list[DetectedElement]:
        """Pre-filter elements by size heuristics based on description."""
        desc_lower = description.lower()
        img_w, img_h = image.size

        # Size-based heuristics
        if any(kw in desc_lower for kw in ["card", "note", "笔记", "卡片", "post"]):
            # Cards are large elements
            return [e for e in elements if e.width > img_w * 0.1 and e.height > img_h * 0.1]
        elif any(kw in desc_lower for kw in ["button", "按钮", "icon", "图标", "tab", "标签"]):
            # Buttons/icons are small
            return [e for e in elements if e.width < img_w * 0.15 and e.height < img_h * 0.15]
        elif any(kw in desc_lower for kw in ["search", "搜索", "input", "输入"]):
            # Search boxes are wide and near the top
            return [e for e in elements if e.width > img_w * 0.1 and e.y < img_h * 0.15]

        return elements

    def _annotate_image(
        self, image: Image.Image, elements: list[DetectedElement]
    ) -> Image.Image:
        """Draw numbered red boxes on image for LLM to reference."""
        annotated = image.copy()
        if annotated.mode == "RGBA":
            annotated = annotated.convert("RGB")
        draw = ImageDraw.Draw(annotated)

        for i, e in enumerate(elements):
            x1, y1, x2, y2 = e.bbox
            # Draw red rectangle
            draw.rectangle([x1, y1, x2, y2], outline="red", width=3)
            # Draw number label
            label = f"#{i+1}"
            # Background for label
            draw.rectangle([x1, y1 - 25, x1 + len(label) * 12, y1], fill="red")
            draw.text((x1 + 2, y1 - 23), label, fill="white")

        return annotated

    def _fallback_llm(self, image: Image.Image, description: str) -> LocatedElement | None:
        """Fallback to pure LLM location (less precise)."""
        result = self._llm.locate_element(image, description)
        if result and result.get("found"):
            w, h = image.size
            cx = int(result["x"] / 100 * w)
            cy = int(result["y"] / 100 * h)
            ew = int(result.get("width", 10) / 100 * w)
            eh = int(result.get("height", 10) / 100 * h)
            return LocatedElement(
                description=description,
                x=cx,
                y=cy,
                bbox=(cx - ew // 2, cy - eh // 2, cx + ew // 2, cy + eh // 2),
                confidence=result.get("confidence", 0.5),
                method="llm",
            )
        return None
