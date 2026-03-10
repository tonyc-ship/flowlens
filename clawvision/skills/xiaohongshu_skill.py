"""Xiaohongshu-specific visual understanding skill.

Uses lightweight CV (OpenCV + numpy) to detect page structure:
- Note detail modal: white popup on dark semi-transparent overlay
- Search results: top bar + card grid
- Homepage: similar to search but with category tabs

No ML models needed -- just color/contrast segmentation and contour analysis.
"""

from __future__ import annotations

import os
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from .base import SiteSkill


class XiaohongshuSkill(SiteSkill):
    """Visual understanding skill for Xiaohongshu (xiaohongshu.com)."""

    name = "xiaohongshu"

    # ------------------------------------------------------------------ #
    #  Page type detection
    # ------------------------------------------------------------------ #

    def identify_page_type(self, screenshot: Image.Image) -> str:
        """Detect page type using simple CV heuristics.

        Detection strategy:
        - note_detail: Has a semi-transparent overlay visible as uniform gray
          edges (low variance, R~G~B, brightness clearly below white) that
          surround a brighter central modal.  Also detects mobile-style
          note detail (tall aspect ratio with author bar + action bar).
        - search_results: Mostly bright page with left sidebar + top search bar.
        - unknown: fallback.
        """
        arr = np.array(screenshot)
        gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
        h, w = gray.shape

        # --- Check 1: Semi-transparent overlay detection ---
        # The XHS note detail modal has a semi-transparent black overlay that
        # makes edge areas uniform gray (~190) with very low brightness variance.
        # Normal pages have white edges (255) or high-variance content.
        left_strip = gray[:, : max(1, int(w * 0.05))]
        left_mean = float(np.mean(left_strip))
        left_p10 = float(np.percentile(left_strip, 10))
        left_p90 = float(np.percentile(left_strip, 90))
        left_span = left_p90 - left_p10

        # Overlay signature: narrow brightness range AND noticeably below pure white
        has_overlay = left_span < 15 and left_mean < 210 and left_mean > 100

        if has_overlay:
            # Confirm: center should be brighter (the modal itself)
            center = gray[int(h * 0.15) : int(h * 0.85), int(w * 0.15) : int(w * 0.85)]
            center_bright_ratio = np.count_nonzero(center > 200) / center.size
            if center_bright_ratio > 0.10:
                return "note_detail"

        # --- Check 2: Mobile-style note detail (tall aspect, no overlay) ---
        # Mobile XHS note detail: aspect ratio > 1.2, has action bar at bottom
        # with like/fav/comment icons, and author bar at top.
        aspect = h / max(w, 1)
        if aspect > 1.2:
            # Check for bottom action bar: a horizontal strip near the bottom
            # that is mostly white/light with small icon-like dark spots
            bottom_strip = gray[int(h * 0.90) :, :]
            bottom_mean = float(np.mean(bottom_strip))
            if bottom_mean > 180:
                return "note_detail"

        # --- Check 3: Search results / homepage ---
        top_strip = arr[: int(h * 0.08), :, :]
        # XHS red brand color in top-left corner
        red_mask = (
            (top_strip[:, :, 0] > 180)
            & (top_strip[:, :, 1] < 100)
            & (top_strip[:, :, 2] < 100)
        )
        if np.count_nonzero(red_mask) > 50:
            return "search_results"

        # Fallback: mostly bright page -> search_results
        bright_ratio = np.count_nonzero(gray > 200) / gray.size
        if bright_ratio > 0.40:
            return "search_results"

        return "unknown"

    # ------------------------------------------------------------------ #
    #  Region extraction
    # ------------------------------------------------------------------ #

    def extract_regions(
        self,
        screenshot: Image.Image,
        page_type: str,
        *,
        debug_dir: str | None = None,
    ) -> dict[str, Image.Image]:
        """Extract semantic regions based on page type."""
        if page_type == "note_detail":
            return self._extract_note_detail(screenshot, debug_dir=debug_dir)
        elif page_type == "search_results":
            return self._extract_search_results(screenshot, debug_dir=debug_dir)
        else:
            return {"full": screenshot}

    # -- note detail --------------------------------------------------- #

    def _extract_note_detail(
        self, screenshot: Image.Image, *, debug_dir: str | None = None
    ) -> dict[str, Image.Image]:
        """Detect the white modal on dark overlay and split into sub-regions.

        Strategy:
        1. Detect overlay vs modal using variance-based segmentation
        2. Find the modal bounding box via contour analysis
        3. Split modal into left (media) and right (content) panels
        4. In right panel, heuristically split author / content / action bar
        """
        arr = np.array(screenshot)
        gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
        h, w = gray.shape

        _save = _debug_saver(debug_dir)

        # Detect if this is a full-screen modal (with overlay) or a cropped/mobile view.
        left_strip = gray[:, : max(1, int(w * 0.05))]
        has_overlay = float(np.percentile(left_strip, 90) - np.percentile(left_strip, 10)) < 15 and float(np.mean(left_strip)) < 210

        if has_overlay:
            # Full-screen with overlay: use local variance to find the modal.
            # The overlay area has low variance (uniform gray); the modal has
            # high variance (text, images, buttons).
            block_size = max(15, min(w, h) // 50) | 1  # must be odd
            local_mean = cv2.blur(gray.astype(np.float32), (block_size, block_size))
            local_sq_mean = cv2.blur((gray.astype(np.float32)) ** 2, (block_size, block_size))
            local_var = local_sq_mean - local_mean ** 2
            local_var = np.clip(local_var, 0, None)

            # The modal interior has much higher variance than the overlay
            var_threshold = np.percentile(local_var, 50)
            high_var_mask = (local_var > var_threshold).astype(np.uint8) * 255
            _save("01a_variance_mask", high_var_mask)

            # Also use brightness: modal background is white (>220) vs overlay gray (~191)
            _, bright_mask = cv2.threshold(gray, 215, 255, cv2.THRESH_BINARY)
            _save("01b_bright_mask", bright_mask)

            # Combine: modal pixels are EITHER high-variance OR very bright
            combined = cv2.bitwise_or(high_var_mask, bright_mask)

            # Aggressive morphological close to merge into one blob
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (50, 50))
            combined = cv2.morphologyEx(combined, cv2.MORPH_CLOSE, kernel)
            # Remove small noise
            kernel_small = cv2.getStructuringElement(cv2.MORPH_RECT, (20, 20))
            combined = cv2.morphologyEx(combined, cv2.MORPH_OPEN, kernel_small)
            _save("01c_combined_mask", combined)
        else:
            # No overlay: full-page note detail view.
            # Layout: left ~55% is main post, right ~45% is recommendations sidebar.
            # Bottom ~5% is engagement bar (comment input + like/fav/comment icons).
            return self._extract_fullpage_note(screenshot, gray, debug_dir=debug_dir)

        # Step 2: Find largest contour (the modal)
        contours, _ = cv2.findContours(combined, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return {"full": screenshot}

        # Filter to contours large enough to be a modal (>10% of image area)
        min_area = h * w * 0.10
        big_contours = [c for c in contours if cv2.contourArea(c) > min_area]
        if not big_contours:
            return {"full": screenshot}

        largest = max(big_contours, key=cv2.contourArea)
        mx, my, mw, mh = cv2.boundingRect(largest)

        # Debug: draw modal bounding box
        if debug_dir:
            debug_img = arr.copy()
            cv2.rectangle(debug_img, (mx, my), (mx + mw, my + mh), (0, 255, 0), 4)
            _save("02_modal_bbox", debug_img)

        crops: dict[str, Image.Image] = {}

        # Crop the full modal
        modal_img = screenshot.crop((mx, my, mx + mw, my + mh))
        crops["modal"] = modal_img

        # Step 3: Split modal into left (media) and right (content) panels.
        modal_gray = gray[my : my + mh, mx : mx + mw]
        split_x = self._find_vertical_split(modal_gray, mw, mh)

        if split_x is not None:
            crops["media"] = screenshot.crop((mx, my, mx + split_x, my + mh))
            right_x = mx + split_x
            crops["right_panel"] = screenshot.crop((right_x, my, mx + mw, my + mh))

            if debug_dir:
                debug_img2 = arr.copy()
                cv2.rectangle(debug_img2, (mx, my), (mx + split_x, my + mh), (255, 0, 0), 3)
                cv2.rectangle(debug_img2, (right_x, my), (mx + mw, my + mh), (0, 0, 255), 3)
                _save("03_left_right_split", debug_img2)

            right_crops = self._split_right_panel(
                screenshot, right_x, my, mx + mw, my + mh, gray, debug_dir=debug_dir
            )
            crops.update(right_crops)
        else:
            crops["content"] = modal_img

        return crops

    def _extract_fullpage_note(
        self, screenshot: Image.Image, gray: np.ndarray, *, debug_dir: str | None = None
    ) -> dict[str, Image.Image]:
        """Extract regions from a full-page (non-modal) note detail view.

        Layout:
        - Top ~4%: author bar (avatar, name, follow button)
        - Middle: post content (text, images, hashtags)
        - Right sidebar (~40%): recommended posts
        - Bottom ~5%: engagement bar (comment input + action icons)
        """
        _save = _debug_saver(debug_dir)
        h, w = gray.shape
        crops: dict[str, Image.Image] = {}

        # 1. Find bottom engagement bar: scan upward from the bottom for a
        #    thin bright strip with icon-sized dark elements.
        #    The engagement bar has a distinctive horizontal separator line above it.
        bottom_region = gray[int(h * 0.85) :, :]
        row_means = np.mean(bottom_region, axis=1)
        # Look for a brightness dip (the separator line) then stable bright area
        diffs = np.abs(np.diff(row_means))
        bar_y = int(h * 0.95)  # default
        for i in range(len(diffs) - 1, -1, -1):
            if diffs[i] > 8:
                bar_y = int(h * 0.85) + i
                break

        # 2. Find the sidebar boundary: column-variance analysis.
        #    Main content area (left) has text on white = low variance.
        #    Recommendation cards (right) have images = high variance.
        mid_gray = gray[int(h * 0.2) : int(h * 0.7), :]
        col_var = np.var(mid_gray, axis=0)
        k = max(5, w // 80)
        smoothed = np.convolve(col_var, np.ones(k) / k, mode="same")

        # Search between 40-70% of width for the split
        s_start = int(w * 0.40)
        s_end = int(w * 0.70)
        search = smoothed[s_start:s_end]
        if len(search) > 10:
            diff_v = np.diff(search)
            peak = np.argmax(np.abs(diff_v))
            sidebar_x = s_start + peak
        else:
            sidebar_x = int(w * 0.55)

        # 3. Find author bar: top of the main content area (left of sidebar).
        #    Author bar has avatar + name + follow button, usually ~4-6% of height.
        author_bar_h = int(h * 0.05)

        # Build crops
        crops["author_bar"] = screenshot.crop((0, 0, sidebar_x, author_bar_h))
        crops["content"] = screenshot.crop((0, author_bar_h, sidebar_x, bar_y))
        crops["action_bar"] = screenshot.crop((0, bar_y, w, h))
        crops["recommendations"] = screenshot.crop((sidebar_x, 0, w, bar_y))

        if debug_dir:
            debug_arr = np.array(screenshot).copy()
            cv2.line(debug_arr, (sidebar_x, 0), (sidebar_x, bar_y), (0, 255, 0), 3)
            cv2.line(debug_arr, (0, author_bar_h), (sidebar_x, author_bar_h), (255, 0, 0), 3)
            cv2.line(debug_arr, (0, bar_y), (w, bar_y), (0, 0, 255), 3)
            _save("02_fullpage_splits", debug_arr)

        return crops

    def _find_vertical_split(self, modal_gray: np.ndarray, mw: int, mh: int) -> int | None:
        """Find the x-coordinate that splits left media from right content panel.

        We look for a vertical line (within the middle 30-70% of the modal width)
        where there is a sharp change in the column-wise mean brightness.
        The media panel often has a photo (variable brightness) while the content
        panel is mostly white text on white background (high brightness).
        """
        # Compute column-wise mean brightness
        col_means = np.mean(modal_gray, axis=0)

        # Search range: 30% to 70% of modal width
        search_start = int(mw * 0.30)
        search_end = int(mw * 0.70)
        if search_end <= search_start + 10:
            return None

        search_region = col_means[search_start:search_end]

        # Look for the biggest jump in brightness between adjacent columns
        # (smoothed to avoid noise)
        kernel_size = max(5, mw // 100)
        smoothed = np.convolve(search_region, np.ones(kernel_size) / kernel_size, mode="same")
        diff = np.abs(np.diff(smoothed))

        if len(diff) == 0:
            return None

        # Also look for a column where brightness drops/rises sharply
        # indicating the boundary between image and white content area
        max_diff_idx = np.argmax(diff)
        max_diff_val = diff[max_diff_idx]

        # Threshold: the jump should be meaningful
        if max_diff_val < 10:
            # Alternative: look for a narrow vertical strip that is darker (divider line)
            # or just use the midpoint of the modal
            return mw // 2

        return search_start + max_diff_idx

    def _split_right_panel(
        self,
        screenshot: Image.Image,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        full_gray: np.ndarray,
        *,
        debug_dir: str | None = None,
    ) -> dict[str, Image.Image]:
        """Split the right content panel into author_bar, content, and action_bar.

        Heuristics:
        - author_bar: top ~8-12% of right panel (avatar + name + follow button)
        - action_bar: bottom ~8-12% (like/fav/comment/share icons)
        - content: everything between
        """
        _save = _debug_saver(debug_dir)

        panel_h = y2 - y1
        panel_w = x2 - x1

        if panel_h < 100 or panel_w < 100:
            return {}

        # Extract the right panel grayscale
        panel_gray = full_gray[y1:y2, x1:x2]

        # Compute row-wise mean brightness
        row_means = np.mean(panel_gray, axis=1)

        # Author bar: top portion.  Look for a horizontal divider line
        # (a row with notably lower brightness) in the top 20%.
        author_bar_end = self._find_horizontal_divider(row_means, 0, int(panel_h * 0.20), panel_h)
        if author_bar_end is None:
            author_bar_end = int(panel_h * 0.08)

        # Action bar: bottom portion.  Look for a divider in the bottom 20%.
        action_bar_start = self._find_horizontal_divider(
            row_means, int(panel_h * 0.80), panel_h, panel_h
        )
        if action_bar_start is None:
            action_bar_start = int(panel_h * 0.92)

        # Ensure valid bounds (no zero-height crops)
        author_bar_end = max(author_bar_end, 5)
        action_bar_start = min(action_bar_start, panel_h - 5)
        if action_bar_start <= author_bar_end:
            action_bar_start = panel_h - 5
            author_bar_end = min(author_bar_end, action_bar_start - 10)

        crops: dict[str, Image.Image] = {}
        if author_bar_end > 2:
            crops["author_bar"] = screenshot.crop((x1, y1, x2, y1 + author_bar_end))
        crops["content"] = screenshot.crop((x1, y1 + author_bar_end, x2, y1 + action_bar_start))
        if y2 - (y1 + action_bar_start) > 2:
            crops["action_bar"] = screenshot.crop((x1, y1 + action_bar_start, x2, y2))

        if debug_dir:
            debug_arr = np.array(screenshot).copy()
            # Draw horizontal lines for the splits
            cv2.line(debug_arr, (x1, y1 + author_bar_end), (x2, y1 + author_bar_end), (255, 0, 0), 2)
            cv2.line(
                debug_arr, (x1, y1 + action_bar_start), (x2, y1 + action_bar_start), (0, 0, 255), 2
            )
            _save("04_right_panel_splits", debug_arr)

        return crops

    def _find_horizontal_divider(
        self, row_means: np.ndarray, start: int, end: int, panel_h: int
    ) -> int | None:
        """Find a horizontal divider line (brightness dip) in the given row range."""
        if end <= start + 5:
            return None

        region = row_means[start:end]
        # Smooth
        kernel_size = max(3, len(region) // 20)
        smoothed = np.convolve(region, np.ones(kernel_size) / kernel_size, mode="same")

        # Look for the biggest brightness drop (divider lines are darker)
        diff = np.abs(np.diff(smoothed))
        if len(diff) == 0:
            return None

        max_idx = np.argmax(diff)
        if diff[max_idx] < 5:
            return None

        return start + max_idx

    # -- search results ------------------------------------------------ #

    def _extract_search_results(
        self, screenshot: Image.Image, *, debug_dir: str | None = None
    ) -> dict[str, Image.Image]:
        """Extract regions from a search results page.

        Layout:
        - left_sidebar: ~5-15% left side (navigation menu)
        - top_bar: top ~5-10% (search input + tabs)
        - card_grid: the rest (main content area with note cards)
        """
        _save = _debug_saver(debug_dir)

        arr = np.array(screenshot)
        gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
        h, w = gray.shape

        # Detect left sidebar: look for the boundary where the left nav ends.
        # The sidebar is narrower and often has a slightly different background.
        # XHS sidebar is typically ~15-22% of screen width on desktop.
        sidebar_x = self._find_sidebar_boundary(gray, w, h)

        # Detect top bar: look for a horizontal brightness boundary in the top 20%.
        col_strip = gray[:int(h * 0.20), sidebar_x:w]
        row_means_top = np.mean(col_strip, axis=1)

        # The search bar area often has tabs underneath, creating a brightness change
        # We look for the boundary between the header area and the content grid.
        top_bar_end = None
        if len(row_means_top) > 10:
            diff = np.abs(np.diff(row_means_top))
            # Find significant transitions
            threshold = np.mean(diff) + 2 * np.std(diff) if np.std(diff) > 0 else 5
            candidates = np.where(diff > max(threshold, 3))[0]
            if len(candidates) > 0:
                # Take the last significant transition in the top region
                # (below search bar + tabs)
                top_bar_end = int(candidates[-1])

        if top_bar_end is None or top_bar_end < int(h * 0.03):
            top_bar_end = int(h * 0.10)

        crops: dict[str, Image.Image] = {}
        crops["sidebar"] = screenshot.crop((0, 0, sidebar_x, h))
        crops["top_bar"] = screenshot.crop((sidebar_x, 0, w, top_bar_end))
        crops["card_grid"] = screenshot.crop((sidebar_x, top_bar_end, w, h))

        if debug_dir:
            debug_arr = arr.copy()
            cv2.line(debug_arr, (sidebar_x, 0), (sidebar_x, h), (0, 255, 0), 3)
            cv2.line(debug_arr, (sidebar_x, top_bar_end), (w, top_bar_end), (255, 0, 0), 3)
            _save("05_search_layout", debug_arr)

        return crops

    def _find_sidebar_boundary(self, gray: np.ndarray, w: int, h: int) -> int:
        """Find the right edge of the left sidebar.

        The sidebar occupies roughly 15-22% of the page width.
        We detect it by looking at column-wise brightness variance:
        the sidebar has relatively uniform brightness (menu items),
        while the card grid area has high variance (images).
        """
        # Sample the middle vertical strip to avoid top bar / bottom bar
        sample = gray[int(h * 0.3) : int(h * 0.7), :]
        col_variance = np.var(sample, axis=0)

        # Smooth
        kernel_size = max(5, w // 100)
        smoothed_var = np.convolve(col_variance, np.ones(kernel_size) / kernel_size, mode="same")

        # Look for a jump in variance in the 10-30% width range
        search_start = int(w * 0.05)
        search_end = int(w * 0.30)
        search_region = smoothed_var[search_start:search_end]

        if len(search_region) < 10:
            return int(w * 0.18)

        diff = np.diff(search_region)
        max_idx = np.argmax(diff)

        if diff[max_idx] > np.mean(col_variance) * 0.5:
            return search_start + max_idx

        # Fallback: use a reasonable default
        return int(w * 0.18)

    # ------------------------------------------------------------------ #
    #  Extraction prompts
    # ------------------------------------------------------------------ #

    def get_extraction_prompts(self, page_type: str) -> dict[str, str]:
        """Return optimized LLM prompts for each region."""
        if page_type == "note_detail":
            return {
                "author_bar": (
                    "Extract the author info from this UI region. "
                    "Return JSON: {\"name\": str, \"followers\": str or null, \"is_following\": bool}"
                ),
                "content": (
                    "Extract the full text content of this Xiaohongshu post. "
                    "Include the title, body text, and all hashtags. "
                    "Return JSON: {\"title\": str, \"body\": str, \"hashtags\": [str], \"date\": str or null}"
                ),
                "action_bar": (
                    "Extract the engagement metrics from this bottom bar. "
                    "Return JSON: {\"likes\": str, \"favorites\": str, \"comments\": str, \"shares\": str or null}"
                ),
                "media": (
                    "Describe the image/media content shown in this panel. "
                    "Return JSON: {\"description\": str, \"image_count\": int, \"current_index\": int or null}"
                ),
                "right_panel": (
                    "Extract all visible comments from this panel. "
                    "Return JSON array: [{\"username\": str, \"text\": str, \"likes\": str or null}]"
                ),
            }
        elif page_type == "search_results":
            return {
                "top_bar": (
                    "Extract the search query and active tab from this top bar. "
                    "Return JSON: {\"query\": str, \"active_tab\": str, \"tabs\": [str]}"
                ),
                "card_grid": (
                    "List all visible note cards in this grid. For each card extract: "
                    "title, author, likes count, and approximate position (top-left, top-right, etc). "
                    "Return JSON array: [{\"title\": str, \"author\": str, \"likes\": str, \"position\": str}]"
                ),
            }
        else:
            return {
                "full": "Describe this Xiaohongshu page. What type of content is shown?"
            }


def _debug_saver(debug_dir: str | None):
    """Return a helper function that saves debug images if debug_dir is set."""
    if debug_dir is None:
        return lambda name, img: None

    os.makedirs(debug_dir, exist_ok=True)

    def save(name: str, img_or_arr):
        if isinstance(img_or_arr, np.ndarray):
            if len(img_or_arr.shape) == 2:
                # Grayscale or mask
                cv2.imwrite(os.path.join(debug_dir, f"{name}.png"), img_or_arr)
            else:
                # Color: convert RGB to BGR for cv2
                cv2.imwrite(os.path.join(debug_dir, f"{name}.png"), cv2.cvtColor(img_or_arr, cv2.COLOR_RGB2BGR))
        elif isinstance(img_or_arr, Image.Image):
            img_or_arr.save(os.path.join(debug_dir, f"{name}.png"))

    return save
