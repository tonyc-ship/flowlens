"""Background desktop capture loop for Observer."""

from __future__ import annotations

import io
import json
import os
import plistlib
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from collections import deque
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter

from ..core.runtime import load_runtime_env
from ..debug import MacOSController
from ..perception.apple_ocr import AppleOCR
from ..perception.local_llm import LocalLLM
from ..perception.media import BACKEND_QWEN_LOCAL, MediaConfig, MediaProcessor
from .paths import LAUNCH_AGENT_LABEL, ObserverPaths, REPO_ROOT
from .store import ObserverStore

OBSERVER_VISION_MODEL = "Qwen3.5-2B-6bit"


@dataclass(frozen=True)
class CaptureDiff:
    regions: list[dict[str, int]]
    changed_area_ratio: float
    source: str


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"", "0", "false", "no", "off"}


def _merge_regions(regions: list[dict[str, int]], *, gap: int = 24) -> list[dict[str, int]]:
    pending = sorted(regions, key=lambda item: (item["y"], item["x"]))
    merged: list[dict[str, int]] = []
    for item in pending:
        x1 = item["x"]
        y1 = item["y"]
        x2 = item["x"] + item["w"]
        y2 = item["y"] + item["h"]
        for target in merged:
            tx1 = target["x"] - gap
            ty1 = target["y"] - gap
            tx2 = target["x"] + target["w"] + gap
            ty2 = target["y"] + target["h"] + gap
            overlaps = not (x2 < tx1 or x1 > tx2 or y2 < ty1 or y1 > ty2)
            if overlaps:
                nx1 = min(target["x"], x1)
                ny1 = min(target["y"], y1)
                nx2 = max(target["x"] + target["w"], x2)
                ny2 = max(target["y"] + target["h"], y2)
                target.update({"x": nx1, "y": ny1, "w": nx2 - nx1, "h": ny2 - ny1})
                break
        else:
            merged.append(dict(item))
    return merged


def _connected_mask_regions(mask: Image.Image, *, min_pixels: int) -> tuple[list[tuple[int, int, int, int]], int]:
    width, height = mask.size
    pixels = mask.load()
    visited: set[tuple[int, int]] = set()
    boxes: list[tuple[int, int, int, int]] = []
    changed_pixels = 0

    for y in range(height):
        for x in range(width):
            if pixels[x, y] == 0:
                continue
            changed_pixels += 1
            if (x, y) in visited:
                continue
            queue = deque([(x, y)])
            visited.add((x, y))
            min_x = max_x = x
            min_y = max_y = y
            area = 0
            while queue:
                cx, cy = queue.popleft()
                area += 1
                min_x = min(min_x, cx)
                min_y = min(min_y, cy)
                max_x = max(max_x, cx)
                max_y = max(max_y, cy)
                for nx, ny in ((cx - 1, cy), (cx + 1, cy), (cx, cy - 1), (cx, cy + 1)):
                    if nx < 0 or ny < 0 or nx >= width or ny >= height:
                        continue
                    if pixels[nx, ny] == 0 or (nx, ny) in visited:
                        continue
                    visited.add((nx, ny))
                    queue.append((nx, ny))
            if area >= min_pixels:
                boxes.append((min_x, min_y, max_x + 1, max_y + 1))
    return boxes, changed_pixels


@dataclass(frozen=True)
class ObserverConfig:
    check_interval: int = 5
    force_capture_interval: int = 300
    diff_threshold: float = 0.30
    screenshot_format: str = "jpeg"
    screenshot_quality: int = 30
    screenshot_keep_hours: int = 24
    screenshot_scale: float = 0.5
    screenshot_strategy: str = "app_switch"
    capture_all_displays: bool = True
    enable_visual_summary: bool = True
    vision_model: str = OBSERVER_VISION_MODEL

    @classmethod
    def from_env(cls) -> "ObserverConfig":
        load_runtime_env()
        return cls(
            check_interval=max(1, int(os.environ.get("FLOWLENS_OBSERVER_CHECK_INTERVAL", "5"))),
            force_capture_interval=max(
                30, int(os.environ.get("FLOWLENS_OBSERVER_FORCE_CAPTURE_INTERVAL", "300"))
            ),
            diff_threshold=min(
                0.95,
                max(0.01, float(os.environ.get("FLOWLENS_OBSERVER_DIFF_THRESHOLD", "0.30"))),
            ),
            screenshot_strategy=os.environ.get(
                "FLOWLENS_OBSERVER_SCREENSHOT_STRATEGY", "app_switch"
            ).strip() or "app_switch",
            enable_visual_summary=_env_flag("FLOWLENS_OBSERVER_VISION_ENABLED", True),
            vision_model=os.environ.get(
                "FLOWLENS_OBSERVER_VISION_MODEL", OBSERVER_VISION_MODEL
            ).strip()
            or OBSERVER_VISION_MODEL,
        )


def get_browser_url(app_name: str) -> str:
    browser_scripts = {
        "Google Chrome": 'tell application "Google Chrome" to get URL of active tab of front window',
        "Chrome": 'tell application "Google Chrome" to get URL of active tab of front window',
        "Safari": 'tell application "Safari" to get URL of front document',
        "Arc": 'tell application "Arc" to get URL of active tab of front window',
        "Microsoft Edge": 'tell application "Microsoft Edge" to get URL of active tab of front window',
        "Firefox": 'tell application "Firefox" to get URL of front window',
    }
    script = browser_scripts.get(app_name)
    if not script:
        return ""
    try:
        result = subprocess.run(
            ["/usr/bin/osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except Exception:
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


class ObserverCaptureService:
    """Continuously captures app/window context into Observer storage."""

    def __init__(
        self,
        paths: ObserverPaths,
        *,
        store: ObserverStore | None = None,
        config: ObserverConfig | None = None,
        controller: MacOSController | None = None,
        ocr: AppleOCR | None = None,
        visual_media: MediaProcessor | None = None,
    ):
        self.paths = paths
        self.store = store or ObserverStore(paths)
        self.config = config or ObserverConfig.from_env()
        self.controller = controller or MacOSController()
        self.ocr = ocr or AppleOCR()
        self._visual_media = visual_media
        self._visual_media_checked = visual_media is not None

    def current_context(self) -> tuple[str, str, str]:
        window = self.controller.frontmost_window_info()
        if window is None:
            app_name = self.controller.frontmost_app_name() or "Unknown"
            return app_name, "", get_browser_url(app_name)
        return window.owner or "Unknown", window.title or "", get_browser_url(window.owner or "")

    def _screenshot_should_save(self, *, reason: str, is_keyframe: bool) -> bool:
        strategy = (self.config.screenshot_strategy or "app_switch").strip().lower()
        if strategy == "all":
            return True
        if strategy == "none":
            return False
        if strategy == "force_capture":
            return reason == "scheduled_capture"
        if strategy == "app_switch":
            return is_keyframe
        return is_keyframe

    def _get_visual_media(self) -> MediaProcessor | None:
        if self._visual_media_checked:
            return self._visual_media
        self._visual_media_checked = True
        if not self.config.enable_visual_summary:
            return None
        if not LocalLLM.is_available(self.config.vision_model):
            return None
        self._visual_media = MediaProcessor(
            MediaConfig(
                model=self.config.vision_model,
                backend=BACKEND_QWEN_LOCAL,
                use_apple_ocr=False,
                use_vision=True,
                use_whisper=False,
            )
        )
        return self._visual_media

    @staticmethod
    def _image_to_png_bytes(image: Image.Image) -> bytes:
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    @staticmethod
    def _crop_image(image: Image.Image, region: dict[str, int]) -> Image.Image:
        return image.crop(
            (
                region["x"],
                region["y"],
                region["x"] + region["w"],
                region["y"] + region["h"],
            )
        )

    @staticmethod
    def _build_region_strip(image: Image.Image, regions: list[dict[str, int]]) -> Image.Image:
        ordered = sorted(
            sorted(regions, key=lambda item: item["w"] * item["h"], reverse=True)[:4],
            key=lambda item: (item["y"], item["x"]),
        )
        crops = [ObserverCaptureService._crop_image(image, region) for region in ordered]
        if len(crops) == 1:
            return crops[0]
        gap = 12
        width = max(crop.width for crop in crops)
        height = sum(crop.height for crop in crops) + gap * (len(crops) - 1)
        canvas = Image.new("RGB", (width, height), (255, 255, 255))
        y_offset = 0
        for crop in crops:
            canvas.paste(crop, (0, y_offset))
            y_offset += crop.height + gap
        return canvas

    def _compute_capture_diff(
        self,
        previous_image: Image.Image | None,
        current_image: Image.Image,
        *,
        same_context: bool,
    ) -> CaptureDiff:
        if not same_context or previous_image is None or previous_image.size != current_image.size:
            return CaptureDiff(regions=[], changed_area_ratio=1.0, source="full")

        max_side = 320
        width, height = current_image.size
        scale = min(1.0, max_side / max(width, height))
        sample_size = (
            max(1, int(width * scale)),
            max(1, int(height * scale)),
        )
        prev_small = previous_image.convert("L").resize(sample_size, Image.Resampling.BILINEAR)
        curr_small = current_image.convert("L").resize(sample_size, Image.Resampling.BILINEAR)
        diff = ImageChops.difference(prev_small, curr_small)
        mask = diff.point(lambda value: 255 if value >= 18 else 0)
        mask = mask.filter(ImageFilter.MaxFilter(3))

        min_pixels = max(6, int(sample_size[0] * sample_size[1] * 0.002))
        boxes, changed_pixels = _connected_mask_regions(mask, min_pixels=min_pixels)
        changed_ratio = changed_pixels / max(1, sample_size[0] * sample_size[1])
        if changed_pixels == 0 or not boxes:
            return CaptureDiff(regions=[], changed_area_ratio=0.0, source="diff")

        scale_x = width / sample_size[0]
        scale_y = height / sample_size[1]
        regions: list[dict[str, int]] = []
        for min_x, min_y, max_x, max_y in boxes:
            x1 = max(0, int(min_x * scale_x) - 16)
            y1 = max(0, int(min_y * scale_y) - 16)
            x2 = min(width, int(max_x * scale_x) + 16)
            y2 = min(height, int(max_y * scale_y) + 16)
            if x2 - x1 < 8 or y2 - y1 < 8:
                continue
            regions.append({"x": x1, "y": y1, "w": x2 - x1, "h": y2 - y1})
        if not regions:
            return CaptureDiff(regions=[], changed_area_ratio=changed_ratio, source="diff")
        return CaptureDiff(
            regions=_merge_regions(regions),
            changed_area_ratio=changed_ratio,
            source="diff",
        )

    def _read_previous_capture_image(self) -> Image.Image | None:
        if not self.paths.latest_capture_path.exists():
            return None
        try:
            with Image.open(self.paths.latest_capture_path) as image:
                return image.convert("RGB").copy()
        except Exception:
            return None

    def _update_previous_capture_image(self, image: Image.Image) -> None:
        image.save(self.paths.latest_capture_path, format="PNG")

    def _save_capture_image(
        self,
        image: Image.Image,
        *,
        reason: str,
        is_keyframe: bool,
    ) -> tuple[Path, Path | None]:
        should_save = self._screenshot_should_save(reason=reason, is_keyframe=is_keyframe)
        fd, temp_raw = tempfile.mkstemp(prefix="flowlens_observer_", suffix=".png")
        os.close(fd)
        temp_path = Path(temp_raw)
        final_path: Path | None = None

        image.save(temp_path, format="PNG")
        if should_save:
            dated_dir = self.paths.screenshots_dir / datetime.now().strftime("%Y/%m/%d")
            dated_dir.mkdir(parents=True, exist_ok=True)
            ext = "jpg" if self.config.screenshot_format == "jpeg" else "png"
            final_path = dated_dir / f"screen_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{ext}"

            save_image = image
            if self.config.screenshot_scale < 1.0:
                new_size = (
                    max(1, int(save_image.width * self.config.screenshot_scale)),
                    max(1, int(save_image.height * self.config.screenshot_scale)),
                )
                save_image = save_image.resize(new_size, Image.Resampling.LANCZOS)
            if self.config.screenshot_format == "jpeg":
                save_image.convert("RGB").save(
                    final_path,
                    "JPEG",
                    quality=self.config.screenshot_quality,
                )
            else:
                save_image.save(final_path)
        return temp_path, final_path

    def _extract_ocr_text(self, image: Image.Image, capture_diff: CaptureDiff) -> tuple[str, str]:
        if capture_diff.source == "diff":
            if capture_diff.changed_area_ratio == 0:
                return "", "diff"
            if (
                capture_diff.changed_area_ratio <= self.config.diff_threshold
                and capture_diff.regions
                and len(capture_diff.regions) <= 6
            ):
                parts: list[str] = []
                for region in sorted(capture_diff.regions, key=lambda item: (item["y"], item["x"])):
                    cropped = self._crop_image(image, region)
                    text = self.ocr.extract_text(self._image_to_png_bytes(cropped)).strip()
                    if text:
                        parts.append(text)
                return "\n\n".join(parts), "diff"
        return self.ocr.extract_text(self._image_to_png_bytes(image)), "full"

    def _describe_capture(
        self,
        image: Image.Image,
        *,
        capture_diff: CaptureDiff,
        ocr_text: str,
    ) -> tuple[str | None, str | None, str | None]:
        media = self._get_visual_media()
        if media is None:
            return None, None, None
        if capture_diff.source == "diff" and capture_diff.changed_area_ratio == 0:
            return "[no visible change]", "diff", self.config.vision_model

        scope = "full"
        prompt = (
            "Describe this desktop screenshot in 1-2 short sentences. Focus on what the user is doing, "
            "which app or page is visible, and any important UI state or visual content."
        )
        focus_image = image
        if (
            capture_diff.source == "diff"
            and capture_diff.changed_area_ratio <= self.config.diff_threshold
            and capture_diff.regions
            and len(capture_diff.regions) <= 6
        ):
            scope = "diff"
            focus_image = self._build_region_strip(image, capture_diff.regions)
            prompt = (
                "This image contains only the changed region(s) from the latest desktop capture. "
                "Describe what changed and what the user appears to be doing in 1-2 short sentences."
            )
        if ocr_text:
            prompt += f"\nOCR context:\n{ocr_text[:400]}"
        try:
            summary = media.describe_image(
                self._image_to_png_bytes(focus_image),
                prompt,
                max_tokens=120,
            ).strip()
        except Exception:
            return None, None, None
        return summary or None, scope, self.config.vision_model

    def _capture_combined_image(self) -> Image.Image:
        displays = self.controller.list_displays()
        if not displays:
            raise RuntimeError("No displays found for observer capture")

        if not self.config.capture_all_displays:
            main = next((item for item in displays if item.is_main), displays[0])
            return self._capture_display_image(main)

        # FlowLens stitched each display side-by-side instead of trying to
        # recreate the physical monitor layout. That is more robust here too,
        # because Quartz display frames are reported in points while the
        # captured images are Retina pixels.
        captures: list[Image.Image] = []
        for display in sorted(displays, key=lambda item: item.index):
            image = self._capture_display_image(display)
            captures.append(image)

        total_width = sum(image.width for image in captures)
        max_height = max(image.height for image in captures)
        canvas = Image.new("RGB", (total_width, max_height), (255, 255, 255))

        x_offset = 0
        for image in captures:
            canvas.paste(image, (x_offset, 0))
            x_offset += image.width
        return canvas

    def _capture_display_image(self, display) -> Image.Image:
        try:
            return self.controller.capture_display(display.display_id).convert("RGB")
        except Exception:
            fd, temp_raw = tempfile.mkstemp(prefix=f"flowlens_display_{display.index}_", suffix=".png")
            os.close(fd)
            temp_path = Path(temp_raw)
            try:
                result = subprocess.run(
                    [
                        "/usr/sbin/screencapture",
                        "-x",
                        "-D",
                        str(display.index + 1),
                        str(temp_path),
                    ],
                    capture_output=True,
                    check=False,
                )
                if result.returncode != 0 or not temp_path.exists():
                    raise RuntimeError("screencapture fallback failed")
                with Image.open(temp_path) as image:
                    return image.convert("RGB").copy()
            finally:
                temp_path.unlink(missing_ok=True)

    def cleanup_old_screenshots(self) -> int:
        cutoff = time.time() - (self.config.screenshot_keep_hours * 3600)
        deleted = 0
        for path in self.paths.screenshots_dir.rglob("screen_*.*"):
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
                deleted += 1
        return deleted

    def capture_once(
        self,
        *,
        app_name: str | None = None,
        window_title: str | None = None,
        browser_url: str | None = None,
        reason: str = "manual",
        is_keyframe: bool = True,
    ) -> dict:
        if app_name is None or window_title is None or browser_url is None:
            app_name, window_title, browser_url = self.current_context()

        timestamp = datetime.now().isoformat()
        total_start = time.perf_counter()
        timings_ms = {
            "capture_image_ms": 0.0,
            "diff_ms": 0.0,
            "save_ms": 0.0,
            "ocr_ms": 0.0,
            "visual_ms": 0.0,
            "total_ms": 0.0,
        }
        previous_capture = self.store.latest_capture() or {}
        same_context = (
            previous_capture.get("app_name") == (app_name or "Unknown")
            and previous_capture.get("window_title") == (window_title or "")
            and previous_capture.get("browser_url") == (browser_url or "")
        )

        try:
            stage_start = time.perf_counter()
            image = self._capture_combined_image()
            timings_ms["capture_image_ms"] = round((time.perf_counter() - stage_start) * 1000, 1)

            previous_image = self._read_previous_capture_image()
            stage_start = time.perf_counter()
            capture_diff = self._compute_capture_diff(previous_image, image, same_context=same_context)
            timings_ms["diff_ms"] = round((time.perf_counter() - stage_start) * 1000, 1)

            stage_start = time.perf_counter()
            temp_path, saved_path = self._save_capture_image(
                image,
                reason=reason,
                is_keyframe=is_keyframe,
            )
            timings_ms["save_ms"] = round((time.perf_counter() - stage_start) * 1000, 1)

            stage_start = time.perf_counter()
            ocr_text, ocr_scope = self._extract_ocr_text(image, capture_diff)
            timings_ms["ocr_ms"] = round((time.perf_counter() - stage_start) * 1000, 1)

            stage_start = time.perf_counter()
            visual_summary, visual_scope, visual_model = self._describe_capture(
                image,
                capture_diff=capture_diff,
                ocr_text=ocr_text,
            )
            timings_ms["visual_ms"] = round((time.perf_counter() - stage_start) * 1000, 1)
            self._update_previous_capture_image(image)
        except Exception:
            temp_path = None
            saved_path = None
            capture_diff = CaptureDiff(regions=[], changed_area_ratio=1.0, source="full")
            ocr_text = ""
            ocr_scope = "full"
            visual_summary = None
            visual_scope = None
            visual_model = None
        timings_ms["total_ms"] = round((time.perf_counter() - total_start) * 1000, 1)
        if temp_path:
            temp_path.unlink(missing_ok=True)

        capture_id = self.store.insert_capture(
            timestamp=timestamp,
            app_name=app_name or "Unknown",
            window_title=window_title or "",
            browser_url=browser_url or "",
            ocr_text=ocr_text,
            screenshot_path=str(saved_path) if saved_path else None,
            visual_summary=visual_summary,
            capture_reason=reason,
            is_keyframe=is_keyframe,
            diff_regions_json=json.dumps(capture_diff.regions, ensure_ascii=False) if capture_diff.regions else None,
            changed_area_ratio=round(capture_diff.changed_area_ratio, 4),
            ocr_scope=ocr_scope,
            visual_scope=visual_scope,
            visual_model=visual_model,
            capture_image_ms=timings_ms["capture_image_ms"],
            diff_ms=timings_ms["diff_ms"],
            save_ms=timings_ms["save_ms"],
            ocr_ms=timings_ms["ocr_ms"],
            visual_ms=timings_ms["visual_ms"],
            total_ms=timings_ms["total_ms"],
        )
        return {
            "id": capture_id,
            "timestamp": timestamp,
            "app_name": app_name or "Unknown",
            "window_title": window_title or "",
            "browser_url": browser_url or "",
            "ocr_length": len(ocr_text),
            "ocr_scope": ocr_scope,
            "screenshot_path": str(saved_path) if saved_path else "",
            "capture_reason": reason,
            "is_keyframe": is_keyframe,
            "changed_area_ratio": round(capture_diff.changed_area_ratio, 4),
            "diff_region_count": len(capture_diff.regions),
            "visual_summary": visual_summary or "",
            "visual_scope": visual_scope or "",
            "visual_model": visual_model or "",
            "timings_ms": timings_ms,
        }

    def run_loop(self) -> None:
        sys.stdout.reconfigure(line_buffering=True)
        sys.stderr.reconfigure(line_buffering=True)

        print("FlowLens Observer capture loop started")
        print(f"  data_root: {self.paths.root}")
        print(f"  check_interval: {self.config.check_interval}s")
        print(f"  force_capture_interval: {self.config.force_capture_interval}s")
        print(f"  diff_threshold: {self.config.diff_threshold:.2f}")
        print(f"  screenshot_strategy: {self.config.screenshot_strategy}")
        print(
            "  local_vision: "
            f"{'enabled' if self.config.enable_visual_summary else 'disabled'}"
            f" ({self.config.vision_model})"
        )

        last_app = ""
        last_window = ""
        last_url = ""
        last_capture_time = 0.0
        capture_count = 0
        skipped = 0

        try:
            while True:
                if self.controller.is_screen_locked():
                    skipped += 1
                    time.sleep(self.config.check_interval)
                    continue

                app_name, window_title, browser_url = self.current_context()
                now = time.time()
                changed = (
                    app_name != last_app
                    or window_title != last_window
                    or browser_url != last_url
                )
                force_capture = (now - last_capture_time) >= self.config.force_capture_interval

                if changed or force_capture:
                    app_switched = app_name != last_app
                    reason = (
                        "app_switch"
                        if app_switched
                        else "scheduled_capture"
                        if force_capture
                        else "window_or_url_change"
                    )
                    record = self.capture_once(
                        app_name=app_name,
                        window_title=window_title,
                        browser_url=browser_url,
                        reason=reason,
                        is_keyframe=app_switched or force_capture,
                    )
                    capture_count += 1
                    last_capture_time = now
                    print(
                        f"[{capture_count}] {record['timestamp'][:19]} {reason} "
                        f"{record['app_name']} | {record['window_title'][:60]} "
                        f"| ocr={record['ocr_length']} ({record['ocr_scope']}) "
                        f"| diff={record['changed_area_ratio']:.2f} "
                        f"| visual={record['visual_scope'] or '-'} "
                        f"| total={record['timings_ms']['total_ms']:.0f}ms "
                        f"(shot={record['timings_ms']['capture_image_ms']:.0f} "
                        f"diff={record['timings_ms']['diff_ms']:.0f} "
                        f"save={record['timings_ms']['save_ms']:.0f} "
                        f"ocr={record['timings_ms']['ocr_ms']:.0f} "
                        f"vision={record['timings_ms']['visual_ms']:.0f}) "
                        f"| screenshot={record['screenshot_path'] or '(none)'}"
                    )
                    if skipped:
                        print(f"  skipped_checks={skipped}")
                    skipped = 0
                    if capture_count % 20 == 0:
                        deleted = self.cleanup_old_screenshots()
                        if deleted:
                            print(f"  cleaned_old_screenshots={deleted}")
                else:
                    skipped += 1

                last_app = app_name
                last_window = window_title
                last_url = browser_url
                time.sleep(self.config.check_interval)
        except KeyboardInterrupt:
            print("Observer capture loop stopped")


def launch_agent_status(paths: ObserverPaths) -> dict:
    plist_exists = paths.launch_agent_path.exists()
    try:
        result = subprocess.run(
            ["launchctl", "list"],
            capture_output=True,
            text=True,
            check=False,
        )
        loaded = LAUNCH_AGENT_LABEL in result.stdout
    except Exception:
        loaded = False
    return {
        "label": LAUNCH_AGENT_LABEL,
        "plist_path": str(paths.launch_agent_path),
        "installed": plist_exists,
        "loaded": loaded,
    }


def install_launch_agent(paths: ObserverPaths) -> Path:
    plist_path = paths.launch_agent_path
    plist_path.parent.mkdir(parents=True, exist_ok=True)

    environment = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "PYTHONPATH": str(REPO_ROOT),
        "FLOWLENS_OBSERVER_ROOT": str(paths.root),
    }
    plist = {
        "Label": LAUNCH_AGENT_LABEL,
        "ProgramArguments": [
            sys.executable,
            "-u",
            "-m",
            "flowlens",
            "observer",
            "--root",
            str(paths.root),
            "capture-loop",
        ],
        "WorkingDirectory": str(REPO_ROOT),
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": str(paths.capture_log_path),
        "StandardErrorPath": str(paths.capture_error_log_path),
        "EnvironmentVariables": environment,
    }

    subprocess.run(["launchctl", "unload", str(plist_path)], check=False, capture_output=True)
    with plist_path.open("wb") as handle:
        plistlib.dump(plist, handle)
    subprocess.run(["launchctl", "load", str(plist_path)], check=True)
    return plist_path


def uninstall_launch_agent(paths: ObserverPaths) -> bool:
    plist_path = paths.launch_agent_path
    if not plist_path.exists():
        return False
    subprocess.run(["launchctl", "unload", str(plist_path)], check=False, capture_output=True)
    plist_path.unlink(missing_ok=True)
    return True
