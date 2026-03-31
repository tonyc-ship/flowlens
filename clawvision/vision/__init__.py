"""Compatibility exports for the legacy `clawvision.vision` namespace."""

from ..perception import (
    AppleOCR,
    DetectedElement,
    GroundingModel,
    HybridDetector,
    LocalLLM,
    OCREngine,
    OWLv2Detector,
    TextRegion,
    VisionLLM,
    VisionRequestConfig,
    WhisperTranscriber,
    YOLOUIDetector,
)

__all__ = [
    "AppleOCR",
    "DetectedElement", "HybridDetector", "YOLOUIDetector", "OWLv2Detector",
    "GroundingModel",
    "LocalLLM",
    "OCREngine", "TextRegion",
    "VisionLLM", "VisionRequestConfig",
    "WhisperTranscriber",
]
