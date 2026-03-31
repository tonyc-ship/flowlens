"""Perception stack: vision, OCR, grounding, transcription, and local models."""

from .apple_ocr import AppleOCR
from .detector import DetectedElement, HybridDetector, OWLv2Detector, YOLOUIDetector
from .grounding import GroundingModel
from .llm import VisionLLM, VisionRequestConfig
from .local_llm import DEFAULT_LOCAL_MODEL, LocalLLM
from .media import BACKEND_QWEN_LOCAL, BACKEND_SONNET, DEFAULT_MODEL, MediaConfig, MediaProcessor
from .ocr import OCREngine, TextRegion
from .transcriber import WhisperTranscriber

__all__ = [
    "AppleOCR",
    "DetectedElement", "HybridDetector", "OWLv2Detector", "YOLOUIDetector",
    "GroundingModel",
    "VisionLLM", "VisionRequestConfig",
    "DEFAULT_LOCAL_MODEL", "LocalLLM",
    "DEFAULT_MODEL", "BACKEND_SONNET", "BACKEND_QWEN_LOCAL", "MediaConfig", "MediaProcessor",
    "OCREngine", "TextRegion",
    "WhisperTranscriber",
]
