"""Base class for site-specific visual understanding skills."""

from __future__ import annotations

from PIL import Image


class SiteSkill:
    """Base class for site-specific visual understanding skills.

    A SiteSkill uses lightweight CV heuristics (color segmentation,
    contour detection, edge analysis) to identify page types and
    extract semantic regions from screenshots -- no ML models needed.
    """

    name: str = "base"

    def identify_page_type(self, screenshot: Image.Image) -> str:
        """Identify what type of page this is.

        Returns a string like 'note_detail', 'search_results', 'homepage', or 'unknown'.
        """
        raise NotImplementedError

    def extract_regions(
        self, screenshot: Image.Image, page_type: str, *, debug_dir: str | None = None
    ) -> dict[str, Image.Image]:
        """Extract semantic regions from the screenshot based on page type.

        Args:
            screenshot: PIL Image of the full page.
            page_type: Result from identify_page_type().
            debug_dir: If set, save intermediate CV images here for debugging.

        Returns:
            Dict mapping region name to cropped PIL Image.
        """
        raise NotImplementedError

    def get_extraction_prompts(self, page_type: str) -> dict[str, str]:
        """Return LLM prompts optimized for extracting data from each region."""
        raise NotImplementedError
