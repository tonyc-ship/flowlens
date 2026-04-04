"""OCR layout primitives shared by desktop workflows."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable


@dataclass(frozen=True)
class NormalizedRegion:
    """Normalized region in image space using lower-left origin coordinates."""

    left: float
    bottom: float
    right: float
    top: float

    def contains(self, *, x: float, y: float) -> bool:
        return self.left <= x <= self.right and self.bottom <= y <= self.top


@dataclass(frozen=True)
class OCRLine:
    """One OCR text line with normalized coordinates."""

    text: str
    confidence: float
    x: float
    y: float
    w: float
    h: float

    @property
    def left(self) -> float:
        return self.x

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y

    @property
    def top(self) -> float:
        return self.y + self.h

    @property
    def center_x(self) -> float:
        return self.x + self.w / 2

    @property
    def center_y(self) -> float:
        return self.y + self.h / 2

    def within(self, region: NormalizedRegion) -> bool:
        return region.contains(x=self.center_x, y=self.center_y)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class OCRPage:
    """OCR result page with utility helpers."""

    lines: tuple[OCRLine, ...]
    width_px: int
    height_px: int

    @classmethod
    def from_results(
        cls,
        results: Iterable[dict],
        *,
        size_px: tuple[int, int],
    ) -> "OCRPage":
        lines: list[OCRLine] = []
        for item in results:
            bbox = item.get("bbox") or {}
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            lines.append(
                OCRLine(
                    text=text,
                    confidence=float(item.get("confidence") or 0.0),
                    x=float(bbox.get("x") or 0.0),
                    y=float(bbox.get("y") or 0.0),
                    w=float(bbox.get("w") or 0.0),
                    h=float(bbox.get("h") or 0.0),
                )
            )
        width_px, height_px = size_px
        return cls(tuple(lines), width_px=width_px, height_px=height_px)

    def sorted_top_to_bottom(self) -> list[OCRLine]:
        return sorted(self.lines, key=lambda item: (-item.center_y, item.left, -item.confidence))

    def within(self, region: NormalizedRegion) -> list[OCRLine]:
        return [line for line in self.lines if line.within(region)]

    def best_text_match(
        self,
        query: str,
        *,
        region: NormalizedRegion | None = None,
        exact: bool = False,
    ) -> OCRLine | None:
        query_text = query.strip().casefold()
        if not query_text:
            return None

        candidates = self.within(region) if region is not None else list(self.lines)
        best: tuple[int, float, OCRLine] | None = None
        for line in candidates:
            text = line.text.casefold()
            if exact:
                if text != query_text:
                    continue
                score = 3
            else:
                if text == query_text:
                    score = 3
                elif text.startswith(query_text):
                    score = 2
                elif query_text in text:
                    score = 1
                else:
                    continue
            rank = (score, line.confidence, line)
            if best is None or rank[:2] > best[:2]:
                best = rank
        return best[2] if best is not None else None

    def text_signature(self, *, region: NormalizedRegion | None = None) -> str:
        candidates = self.within(region) if region is not None else self.lines
        ordered = sorted(candidates, key=lambda item: (-item.center_y, item.center_x))
        return "\n".join(item.text for item in ordered)
