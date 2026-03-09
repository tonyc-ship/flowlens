"""UI element detection using local CV models.

This is the core differentiator over vanilla LLM-based approaches.
Provides fast, precise bounding boxes for UI elements without API calls.

Supported backends:
- OmniParser YOLOv8: UI-specific detector (~100ms on Apple Silicon MPS)
- OWLv2: Open-vocabulary detector for flexible text queries (~1s on MPS)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import numpy as np
from PIL import Image


@dataclass
class DetectedElement:
    """A detected UI element with bounding box and metadata."""

    label: str
    confidence: float
    x: int  # top-left x
    y: int  # top-left y
    width: int
    height: int

    @property
    def center(self) -> tuple[int, int]:
        return (self.x + self.width // 2, self.y + self.height // 2)

    @property
    def bbox(self) -> tuple[int, int, int, int]:
        """Return (x1, y1, x2, y2) format."""
        return (self.x, self.y, self.x + self.width, self.y + self.height)


class YOLOUIDetector:
    """UI element detector using OmniParser's fine-tuned YOLOv8.

    This model is specifically trained on screenshots to detect interactive
    UI elements: buttons, icons, text fields, cards, navigation elements, etc.

    Fast (~100ms on Apple Silicon MPS) and does not require API calls.

    Setup:
        pip install ultralytics
        huggingface-cli download microsoft/OmniParser-v2.0 --local-dir weights/
    """

    def __init__(self, weights_path: str | Path | None = None, confidence: float = 0.25):
        self._model = None
        self._weights_path = weights_path
        self.confidence = confidence

    def _download_weights(self) -> Path:
        """Download OmniParser YOLOv8 weights from HuggingFace."""
        weights_dir = Path.home() / ".clawvision" / "weights" / "icon_detect"
        # Check both possible filenames
        for fname in ("model.pt", "best.pt"):
            weights_file = weights_dir / fname
            if weights_file.exists():
                return weights_file

        print("Downloading OmniParser YOLOv8 UI detection weights...")
        weights_dir.mkdir(parents=True, exist_ok=True)

        from huggingface_hub import hf_hub_download
        hf_hub_download(
            repo_id="microsoft/OmniParser-v2.0",
            filename="icon_detect/model.pt",
            local_dir=weights_dir.parent,
        )
        weights_file = weights_dir / "model.pt"
        print(f"Weights downloaded to {weights_file}")
        return weights_file

    def _load_model(self):
        if self._model is not None:
            return

        from ultralytics import YOLO

        if self._weights_path:
            self._model = YOLO(str(self._weights_path))
        else:
            # Default: look for OmniParser weights in common locations
            candidates = [
                Path("weights/icon_detect/model.pt"),
                Path("weights/icon_detect/best.pt"),
                Path.home() / ".clawvision" / "weights" / "icon_detect" / "model.pt",
                Path.home() / ".clawvision" / "weights" / "icon_detect" / "best.pt",
            ]
            for path in candidates:
                if path.exists():
                    self._model = YOLO(str(path))
                    return

            # Auto-download weights as fallback
            weights_path = self._download_weights()
            self._model = YOLO(str(weights_path))

    def detect(self, image: Image.Image) -> list[DetectedElement]:
        """Detect all UI elements in a screenshot.

        Returns list of DetectedElement with bounding boxes and labels.
        """
        self._load_model()

        results = self._model.predict(
            source=image,
            conf=self.confidence,
            verbose=False,
            device="mps",  # Apple Silicon GPU
        )

        elements = []
        for result in results:
            boxes = result.boxes
            if boxes is None:
                continue
            for i in range(len(boxes)):
                xyxy = boxes.xyxy[i].cpu().numpy()
                x1, y1, x2, y2 = xyxy.astype(int)
                conf = float(boxes.conf[i].cpu())
                cls_id = int(boxes.cls[i].cpu())
                label = result.names.get(cls_id, f"class_{cls_id}")

                elements.append(
                    DetectedElement(
                        label=label,
                        confidence=conf,
                        x=x1,
                        y=y1,
                        width=x2 - x1,
                        height=y2 - y1,
                    )
                )

        # Sort by position: top-to-bottom, left-to-right
        elements.sort(key=lambda e: (e.y, e.x))
        return elements


class OWLv2Detector:
    """Open-vocabulary UI element detector using Google's OWLv2.

    Accepts text queries like "search button", "note card", "like icon"
    and returns bounding boxes. Works well on Apple Silicon MPS.

    Slower than YOLO (~1s) but more flexible — no pre-defined classes needed.

    Setup:
        pip install transformers torch
    """

    DEFAULT_MODEL = "google/owlv2-base-patch16-ensemble"

    def __init__(self, model_name: str | None = None):
        self._model = None
        self._processor = None
        self._model_name = model_name or self.DEFAULT_MODEL

    def _load_model(self):
        if self._model is not None:
            return

        import torch
        from transformers import Owlv2ForObjectDetection, Owlv2Processor

        self._processor = Owlv2Processor.from_pretrained(self._model_name)
        self._model = Owlv2ForObjectDetection.from_pretrained(self._model_name)

        # Use MPS if available, otherwise CPU
        if torch.backends.mps.is_available():
            self._device = "mps"
        else:
            self._device = "cpu"
        self._model = self._model.to(self._device)

    def detect(
        self,
        image: Image.Image,
        queries: list[str],
        confidence: float = 0.1,
    ) -> list[DetectedElement]:
        """Detect elements matching text queries.

        Args:
            image: Screenshot to analyze.
            queries: Text descriptions of elements to find,
                     e.g., ["search box", "note card", "like button"].
            confidence: Minimum confidence threshold (0.0-1.0).

        Returns:
            List of DetectedElement with matched query as label.
        """
        self._load_model()

        import torch

        # Ensure RGB format (screenshots may be RGBA)
        if image.mode != "RGB":
            image = image.convert("RGB")

        inputs = self._processor(text=[queries], images=image, return_tensors="pt")
        inputs = {k: v.to(self._device) for k, v in inputs.items()}

        with torch.no_grad():
            outputs = self._model(**inputs)

        target_sizes = torch.tensor([image.size[::-1]], device=self._device)

        # Use the correct post-processing method depending on transformers version
        if hasattr(self._processor, "post_process_grounded_object_detection"):
            results = self._processor.post_process_grounded_object_detection(
                outputs=outputs,
                target_sizes=target_sizes,
                threshold=confidence,
                text_labels=[queries],
            )[0]
        else:
            results = self._processor.post_process_object_detection(
                outputs=outputs,
                target_sizes=target_sizes,
                threshold=confidence,
            )[0]

        elements = []
        for idx in range(len(results["scores"])):
            score = results["scores"][idx]
            box = results["boxes"][idx]
            # Resolve label: use text_labels if available, otherwise fall back to index
            if "text_labels" in results and results["text_labels"] is not None:
                label = results["text_labels"][idx]
            else:
                label_id = int(results["labels"][idx].cpu())
                label = queries[label_id]
            x1, y1, x2, y2 = box.cpu().numpy().astype(int)
            elements.append(
                DetectedElement(
                    label=label,
                    confidence=float(score.cpu()),
                    x=x1,
                    y=y1,
                    width=x2 - x1,
                    height=y2 - y1,
                )
            )

        elements.sort(key=lambda e: (e.y, e.x))
        return elements


class HybridDetector:
    """Combines YOLO (fast, UI-specific) + OWLv2 (flexible, open-vocabulary).

    Strategy:
    - Use YOLO for fast detection of all UI elements (buttons, icons, fields)
    - Use OWLv2 when you need to find specific elements by description
    - Results are merged and deduplicated by IoU overlap
    """

    def __init__(
        self,
        yolo_weights: str | Path | None = None,
        owlv2_model: str | None = None,
    ):
        self._yolo: YOLOUIDetector | None = None
        self._owlv2: OWLv2Detector | None = None
        self._yolo_weights = yolo_weights
        self._owlv2_model = owlv2_model

    def _get_yolo(self) -> YOLOUIDetector:
        if self._yolo is None:
            self._yolo = YOLOUIDetector(self._yolo_weights)
        return self._yolo

    def _get_owlv2(self) -> OWLv2Detector:
        if self._owlv2 is None:
            self._owlv2 = OWLv2Detector(self._owlv2_model)
        return self._owlv2

    def detect_all(self, image: Image.Image, confidence: float = 0.25) -> list[DetectedElement]:
        """Detect all UI elements using YOLO (fast)."""
        return self._get_yolo().detect(image)

    def find_elements(
        self,
        image: Image.Image,
        queries: list[str],
        confidence: float = 0.1,
    ) -> list[DetectedElement]:
        """Find specific elements by description using OWLv2."""
        return self._get_owlv2().detect(image, queries, confidence)

    def detect_and_find(
        self,
        image: Image.Image,
        queries: list[str] | None = None,
        yolo_confidence: float = 0.25,
        owlv2_confidence: float = 0.1,
    ) -> list[DetectedElement]:
        """Run both detectors and merge results.

        Args:
            image: Screenshot to analyze.
            queries: Optional text queries for OWLv2. If None, only runs YOLO.
            yolo_confidence: Confidence threshold for YOLO.
            owlv2_confidence: Confidence threshold for OWLv2.
        """
        elements = self._get_yolo().detect(image)

        if queries:
            owlv2_elements = self._get_owlv2().detect(image, queries, owlv2_confidence)
            elements = self._merge_detections(elements, owlv2_elements)

        return elements

    @staticmethod
    def _iou(a: DetectedElement, b: DetectedElement) -> float:
        """Compute Intersection over Union between two elements."""
        ax1, ay1, ax2, ay2 = a.bbox
        bx1, by1, bx2, by2 = b.bbox

        ix1, iy1 = max(ax1, bx1), max(ay1, by1)
        ix2, iy2 = min(ax2, bx2), min(ay2, by2)

        if ix2 <= ix1 or iy2 <= iy1:
            return 0.0

        intersection = (ix2 - ix1) * (iy2 - iy1)
        area_a = (ax2 - ax1) * (ay2 - ay1)
        area_b = (bx2 - bx1) * (by2 - by1)
        union = area_a + area_b - intersection

        return intersection / union if union > 0 else 0.0

    @staticmethod
    def _merge_detections(
        primary: list[DetectedElement],
        secondary: list[DetectedElement],
        iou_threshold: float = 0.5,
    ) -> list[DetectedElement]:
        """Merge two detection lists, preferring primary for overlapping boxes."""
        merged = list(primary)

        for sec in secondary:
            is_duplicate = any(
                HybridDetector._iou(sec, pri) > iou_threshold for pri in primary
            )
            if not is_duplicate:
                merged.append(sec)

        merged.sort(key=lambda e: (e.y, e.x))
        return merged
