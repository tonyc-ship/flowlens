"""Apple Vision Framework OCR — free, fast, local text extraction.

Uses macOS VNRecognizeTextRequest for CJK + English text recognition.
~50-200ms per image, zero API cost.
"""

from __future__ import annotations

from pathlib import Path

import Quartz
import Vision


class AppleOCR:
    """Text extraction using Apple's Vision framework."""

    def __init__(self, languages: list[str] | None = None):
        self.languages = languages or ["zh-Hans", "zh-Hant", "en"]

    def _load_cgimage(self, source: str | bytes | Path) -> object:
        """Load image as CGImage from file path or bytes."""
        if isinstance(source, (str, Path)):
            path = str(source)
            url = Quartz.CFURLCreateWithFileSystemPath(
                None, path, Quartz.kCFURLPOSIXPathStyle, False
            )
            provider = Quartz.CGDataProviderCreateWithURL(url)
            if path.lower().endswith(".png"):
                return Quartz.CGImageCreateWithPNGDataProvider(
                    provider, None, True, Quartz.kCGRenderingIntentDefault
                )
            else:
                return Quartz.CGImageCreateWithJPEGDataProvider(
                    provider, None, True, Quartz.kCGRenderingIntentDefault
                )
        else:
            # bytes
            data = Quartz.CFDataCreate(None, source, len(source))
            provider = Quartz.CGDataProviderCreateWithCFData(data)
            # Try JPEG first, then PNG
            img = Quartz.CGImageCreateWithJPEGDataProvider(
                provider, None, True, Quartz.kCGRenderingIntentDefault
            )
            if img is None:
                img = Quartz.CGImageCreateWithPNGDataProvider(
                    provider, None, True, Quartz.kCGRenderingIntentDefault
                )
            return img

    def recognize(self, source: str | bytes | Path) -> list[dict]:
        """Recognize text in image. Returns list of {text, confidence, bbox}."""
        cgimage = self._load_cgimage(source)
        if cgimage is None:
            return []

        handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(
            cgimage, {}
        )

        request = Vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
        request.setRecognitionLanguages_(self.languages)
        request.setUsesLanguageCorrection_(True)

        success, error = handler.performRequests_error_([request], None)
        if not success:
            return []

        results = []
        for obs in request.results() or []:
            candidate = obs.topCandidates_(1)
            if candidate:
                text = candidate[0].string()
                conf = candidate[0].confidence()
                bbox = obs.boundingBox()
                results.append({
                    "text": text,
                    "confidence": round(conf, 3),
                    "bbox": {
                        "x": round(bbox.origin.x, 3),
                        "y": round(bbox.origin.y, 3),
                        "w": round(bbox.size.width, 3),
                        "h": round(bbox.size.height, 3),
                    },
                })

        return results

    def extract_text(self, source: str | bytes | Path) -> str:
        """Extract all text from image as a single string."""
        results = self.recognize(source)
        return "\n".join(r["text"] for r in results)
