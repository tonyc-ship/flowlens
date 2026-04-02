"""Reusable desktop window capture + OCR helpers."""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from ..debug import MacOSController, WindowInfo
from ..perception.apple_ocr import AppleOCR
from .ocr_layout import OCRLine, OCRPage, NormalizedRegion


@dataclass(frozen=True)
class DesktopCapture:
    """Window capture artifact saved to disk."""

    window: WindowInfo
    path: Path
    width_px: int
    height_px: int


class DesktopWindowSession:
    """App-scoped macOS window session with Retina-safe coordinate mapping."""

    def __init__(
        self,
        app_name: str,
        *,
        title_contains: str | None = None,
        controller: MacOSController | None = None,
        ocr: AppleOCR | None = None,
    ):
        self.app_name = app_name
        self.title_contains = title_contains
        self.controller = controller or MacOSController()
        self.ocr = ocr or AppleOCR()

    def activate(self) -> None:
        self.controller.activate_app(self.app_name)

    def resolve_window(self, *, visible_only: bool = True) -> WindowInfo:
        window = self.controller.best_window_for_app(
            self.app_name,
            title_contains=self.title_contains,
            visible_only=visible_only,
        )
        if window is None:
            raise RuntimeError(f"Could not find a visible window for app: {self.app_name}")
        return window

    def capture_image(self, *, visible_only: bool = True) -> tuple[WindowInfo, Image.Image]:
        window = self.resolve_window(visible_only=visible_only)
        image = self.controller.capture_window_info(window).convert("RGB")
        return window, image

    def capture_to_path(self, path: str | Path, *, visible_only: bool = True) -> DesktopCapture:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        window, image = self.capture_image(visible_only=visible_only)
        image.save(target)
        return DesktopCapture(window=window, path=target, width_px=image.width, height_px=image.height)

    def capture_ocr_page(self, *, visible_only: bool = True) -> tuple[WindowInfo, Image.Image, OCRPage]:
        window, image = self.capture_image(visible_only=visible_only)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        page = OCRPage.from_results(
            self.ocr.recognize(buffer.getvalue()),
            size_px=image.size,
        )
        return window, image, page

    @staticmethod
    def crop_image_region(image: Image.Image, region: NormalizedRegion) -> Image.Image:
        width, height = image.size
        box = (
            int(width * region.left),
            int(height * (1 - region.top)),
            int(width * region.right),
            int(height * (1 - region.bottom)),
        )
        return image.crop(box)

    @staticmethod
    def normalized_point_to_screen(window: WindowInfo, *, x: float, y: float) -> tuple[int, int]:
        """Map normalized image coordinates into macOS screen points.

        OCR/vision normalized coordinates are expressed in image space, but
        window geometry comes back in macOS points. Use window bounds, not
        screenshot pixels, so Retina captures still click the right place.
        """

        screen_x = window.x + int(window.width * x)
        screen_y = window.y + int(window.height * (1 - y))
        return screen_x, screen_y

    def click_relative(self, x: float, y: float, *, visible_only: bool = True, clicks: int = 1) -> tuple[int, int]:
        window = self.resolve_window(visible_only=visible_only)
        screen_x, screen_y = self.normalized_point_to_screen(window, x=x, y=y)
        self.controller.click(screen_x, screen_y, clicks=clicks)
        return screen_x, screen_y

    def click_ocr_line(self, line: OCRLine, *, visible_only: bool = True, clicks: int = 1) -> tuple[int, int]:
        window = self.resolve_window(visible_only=visible_only)
        screen_x, screen_y = self.normalized_point_to_screen(
            window,
            x=line.center_x,
            y=line.center_y,
        )
        self.controller.click(screen_x, screen_y, clicks=clicks)
        return screen_x, screen_y

    def click_text(
        self,
        query: str,
        *,
        region: NormalizedRegion | None = None,
        exact: bool = False,
        visible_only: bool = True,
        clicks: int = 1,
    ) -> OCRLine | None:
        _, _, page = self.capture_ocr_page(visible_only=visible_only)
        line = page.best_text_match(query, region=region, exact=exact)
        if line is None:
            return None
        self.click_ocr_line(line, visible_only=visible_only, clicks=clicks)
        return line

    def scroll_lines(
        self,
        line_delta: int,
        *,
        x: float,
        y: float,
        repeats: int = 1,
        visible_only: bool = True,
    ) -> tuple[int, int]:
        window = self.resolve_window(visible_only=visible_only)
        screen_x, screen_y = self.normalized_point_to_screen(window, x=x, y=y)
        self.controller.scroll(line_delta, x=screen_x, y=screen_y, repeats=repeats)
        return screen_x, screen_y
