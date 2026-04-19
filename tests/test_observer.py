"""Observer integration tests — SQLite store + diff-aware capture pipeline."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest import mock

from PIL import Image, ImageDraw

from flowlens.observer import (
    ObserverCaptureService,
    ObserverConfig,
    ObserverPaths,
    ObserverStore,
)


class _FakeController:
    def __init__(self, images: list[Image.Image]):
        self.images = [image.copy() for image in images]
        self._index = 0

    def frontmost_window_info(self):
        return SimpleNamespace(owner="Cursor", title="observer test")

    def frontmost_app_name(self):
        return "Cursor"

    def list_displays(self):
        image = self.images[min(self._index, len(self.images) - 1)]
        return [SimpleNamespace(
            index=0, display_id=1, x=0, y=0,
            width=image.width, height=image.height, is_main=True, scale=1.0,
        )]

    def capture_display(self, _display_id: int):
        image = self.images[min(self._index, len(self.images) - 1)].copy()
        self._index += 1
        return image

    def is_screen_locked(self):
        return False


class _FakeOCR:
    def extract_text(self, source):
        image = (Image.open(io.BytesIO(source))
                 if isinstance(source, (bytes, bytearray))
                 else Image.open(source))
        return f"ocr:{image.width}x{image.height}"


class _FakeVisualMedia:
    def describe_image(self, img_bytes: bytes, _prompt: str, max_tokens: int = 120):
        image = Image.open(io.BytesIO(img_bytes))
        return f"vision:{image.width}x{image.height}:{max_tokens}"


class ObserverStoreTest(unittest.TestCase):
    def test_insert_search_and_memory_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = ObserverPaths.resolve(tmp)
            store = ObserverStore(paths)
            now = datetime.now()

            store.insert_capture(
                timestamp=(now - timedelta(minutes=4)).isoformat(),
                app_name="Cursor", window_title="observer migration plan",
                browser_url="",
                ocr_text="Implement observer sqlite storage and launchd integration",
                screenshot_path=str(paths.screenshots_dir / "one.jpg"),
                capture_reason="manual", is_keyframe=True,
            )
            store.insert_capture(
                timestamp=(now - timedelta(minutes=1)).isoformat(),
                app_name="Google Chrome", window_title="Observer docs",
                browser_url="https://example.com/observer",
                ocr_text="Read observer design notes and review screenshots",
                screenshot_path=None, capture_reason="manual", is_keyframe=False,
            )

            matches = store.search_captures("launchd")
            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0]["app_name"], "Cursor")

            store.upsert_project_memories({"Observer": {
                "status": "Implementing capture",
                "key_decisions": ["Use SQLite for durable local storage"],
                "next_steps": [], "blockers": [],
            }})
            self.assertIn("status", store.get_project_memories("Observer")["Observer"]["current"])


class ObserverCaptureDiffTest(unittest.TestCase):
    def test_capture_uses_diff_scope_when_change_is_below_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = ObserverPaths.resolve(tmp)
            base = Image.new("RGB", (240, 120), "white")
            changed = base.copy()
            ImageDraw.Draw(changed).rectangle((12, 14, 42, 44), fill="black")

            def snapshot(ws: float, chrome: float) -> dict:
                return {
                    "timestamp": "2026-04-08T00:00:00",
                    "current_process": {"rss_mb": 120.0},
                    "windowserver": {"pid": 161, "rss_mb": 100.0,
                                     "footprint_mb": ws, "ports": 4000},
                    "chrome": {
                        "window_count": 1, "tab_count": 4,
                        "total_rss_mb": chrome, "largest_renderer_rss_mb": 320.0,
                        "top_processes": [{"pid": 1001, "rss_mb": 320.0, "kind": "renderer"}],
                    },
                    "observer": {"pid": 911, "rss_mb": 220.0},
                }

            service = ObserverCaptureService(
                paths,
                config=ObserverConfig(
                    capture_backend="quartz", capture_all_displays=False,
                    diff_threshold=0.30, enable_visual_summary=True,
                    vision_model="Qwen3.5-2B-6bit",
                ),
                controller=_FakeController([base, changed]),
                ocr=_FakeOCR(), visual_media=_FakeVisualMedia(),
            )

            with mock.patch(
                "flowlens.observer.service.system_resource_snapshot",
                side_effect=[snapshot(1024.0, 1500.0), snapshot(1025.0, 1505.0),
                             snapshot(1025.0, 1505.0), snapshot(1027.0, 1512.0)],
            ):
                first = service.capture_once(reason="manual", is_keyframe=True)
                self.assertEqual(first["ocr_scope"], "full")

                second = service.capture_once(reason="manual", is_keyframe=True)
                self.assertEqual(second["ocr_scope"], "diff")
                self.assertEqual(second["visual_scope"], "diff")
                self.assertLess(second["changed_area_ratio"], 0.30)
                self.assertIn("vision:", second["visual_summary"])

            store = ObserverStore(paths)
            latest = store.latest_capture()
            self.assertEqual(latest["visual_model"], "Qwen3.5-2B-6bit")
            self.assertEqual(latest["ocr_scope"], "diff")

            entries = [
                json.loads(line)
                for line in paths.resource_monitor_log_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(len(entries), 2)


if __name__ == "__main__":
    unittest.main()
