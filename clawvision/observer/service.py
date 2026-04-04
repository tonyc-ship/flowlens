"""Background desktop capture loop for Observer."""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PIL import Image

from ..core.runtime import PROJECT_ROOT, load_runtime_env
from ..debug import MacOSController
from ..perception.apple_ocr import AppleOCR
from .paths import LAUNCH_AGENT_LABEL, ObserverPaths, REPO_ROOT
from .store import ObserverStore


@dataclass(frozen=True)
class ObserverConfig:
    check_interval: int = 5
    force_capture_interval: int = 300
    screenshot_format: str = "jpeg"
    screenshot_quality: int = 30
    screenshot_keep_hours: int = 24
    screenshot_scale: float = 0.5
    screenshot_strategy: str = "app_switch"
    capture_all_displays: bool = True

    @classmethod
    def from_env(cls) -> "ObserverConfig":
        load_runtime_env()
        return cls(
            check_interval=max(1, int(os.environ.get("CLAWVISION_OBSERVER_CHECK_INTERVAL", "5"))),
            force_capture_interval=max(
                30, int(os.environ.get("CLAWVISION_OBSERVER_FORCE_CAPTURE_INTERVAL", "300"))
            ),
            screenshot_strategy=os.environ.get(
                "CLAWVISION_OBSERVER_SCREENSHOT_STRATEGY", "app_switch"
            ).strip() or "app_switch",
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
    ):
        self.paths = paths
        self.store = store or ObserverStore(paths)
        self.config = config or ObserverConfig.from_env()
        self.controller = controller or MacOSController()
        self.ocr = ocr or AppleOCR()

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

    def take_screenshot(self, *, reason: str, is_keyframe: bool) -> tuple[Path | None, Path | None]:
        should_save = self._screenshot_should_save(reason=reason, is_keyframe=is_keyframe)
        fd, temp_raw = tempfile.mkstemp(prefix="clawvision_observer_", suffix=".png")
        os.close(fd)
        temp_path = Path(temp_raw)
        final_path: Path | None = None

        try:
            image = self._capture_combined_image()
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
        except Exception:
            temp_path.unlink(missing_ok=True)
            return None, None

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
            fd, temp_raw = tempfile.mkstemp(prefix=f"clawvision_display_{display.index}_", suffix=".png")
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
        temp_path, saved_path = self.take_screenshot(reason=reason, is_keyframe=is_keyframe)
        ocr_text = self.ocr.extract_text(temp_path) if temp_path and temp_path.exists() else ""
        if temp_path:
            temp_path.unlink(missing_ok=True)

        capture_id = self.store.insert_capture(
            timestamp=timestamp,
            app_name=app_name or "Unknown",
            window_title=window_title or "",
            browser_url=browser_url or "",
            ocr_text=ocr_text,
            screenshot_path=str(saved_path) if saved_path else None,
            capture_reason=reason,
            is_keyframe=is_keyframe,
        )
        return {
            "id": capture_id,
            "timestamp": timestamp,
            "app_name": app_name or "Unknown",
            "window_title": window_title or "",
            "browser_url": browser_url or "",
            "ocr_length": len(ocr_text),
            "screenshot_path": str(saved_path) if saved_path else "",
            "capture_reason": reason,
            "is_keyframe": is_keyframe,
        }

    def run_loop(self) -> None:
        sys.stdout.reconfigure(line_buffering=True)
        sys.stderr.reconfigure(line_buffering=True)

        print("ClawVision Observer capture loop started")
        print(f"  data_root: {self.paths.root}")
        print(f"  check_interval: {self.config.check_interval}s")
        print(f"  force_capture_interval: {self.config.force_capture_interval}s")
        print(f"  screenshot_strategy: {self.config.screenshot_strategy}")

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
                        f"| ocr={record['ocr_length']} | screenshot={record['screenshot_path'] or '(none)'}"
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
        "CLAWVISION_OBSERVER_ROOT": str(paths.root),
    }
    plist = {
        "Label": LAUNCH_AGENT_LABEL,
        "ProgramArguments": [
            sys.executable,
            "-u",
            "-m",
            "clawvision",
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
