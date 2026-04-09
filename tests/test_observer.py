from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest import mock

from PIL import Image, ImageDraw

from flowlens.observer import ObserverCaptureService, ObserverConfig, ObserverPaths, ObserverStore, generate_work_journal


class FakeController:
    def __init__(self, images: list[Image.Image]):
        self.images = [image.copy() for image in images]
        self._index = 0

    def frontmost_window_info(self):
        return SimpleNamespace(owner="Cursor", title="observer test")

    def frontmost_app_name(self):
        return "Cursor"

    def list_displays(self):
        image = self.images[min(self._index, len(self.images) - 1)]
        return [
            SimpleNamespace(
                index=0,
                display_id=1,
                x=0,
                y=0,
                width=image.width,
                height=image.height,
                is_main=True,
                scale=1.0,
            )
        ]

    def capture_display(self, _display_id: int):
        image = self.images[min(self._index, len(self.images) - 1)].copy()
        self._index += 1
        return image

    def is_screen_locked(self):
        return False


class FakeOCR:
    def extract_text(self, source):
        if isinstance(source, (bytes, bytearray)):
            image = Image.open(io.BytesIO(source))
        else:
            image = Image.open(source)
        return f"ocr:{image.width}x{image.height}"


class FakeVisualMedia:
    def describe_image(self, img_bytes: bytes, _prompt: str, max_tokens: int = 120):
        image = Image.open(io.BytesIO(img_bytes))
        return f"vision:{image.width}x{image.height}:{max_tokens}"


class ObserverTests(unittest.TestCase):
    def test_config_from_env_defaults_to_screencapture(self) -> None:
        original_backend = os.environ.get("FLOWLENS_OBSERVER_CAPTURE_BACKEND")
        try:
            os.environ.pop("FLOWLENS_OBSERVER_CAPTURE_BACKEND", None)
            config = ObserverConfig.from_env()
            self.assertEqual(config.capture_backend, "screencapture")
        finally:
            if original_backend is None:
                os.environ.pop("FLOWLENS_OBSERVER_CAPTURE_BACKEND", None)
            else:
                os.environ["FLOWLENS_OBSERVER_CAPTURE_BACKEND"] = original_backend

    def test_config_from_env_respects_capture_all_displays_flag(self) -> None:
        original = os.environ.get("FLOWLENS_OBSERVER_CAPTURE_ALL_DISPLAYS")
        original_backend = os.environ.get("FLOWLENS_OBSERVER_CAPTURE_BACKEND")
        try:
            os.environ["FLOWLENS_OBSERVER_CAPTURE_ALL_DISPLAYS"] = "0"
            os.environ["FLOWLENS_OBSERVER_CAPTURE_BACKEND"] = "screencapture"
            config = ObserverConfig.from_env()
            self.assertFalse(config.capture_all_displays)
            self.assertEqual(config.capture_backend, "screencapture")

            os.environ["FLOWLENS_OBSERVER_CAPTURE_ALL_DISPLAYS"] = "1"
            os.environ["FLOWLENS_OBSERVER_CAPTURE_BACKEND"] = "quartz"
            config = ObserverConfig.from_env()
            self.assertTrue(config.capture_all_displays)
            self.assertEqual(config.capture_backend, "quartz")
        finally:
            if original is None:
                os.environ.pop("FLOWLENS_OBSERVER_CAPTURE_ALL_DISPLAYS", None)
            else:
                os.environ["FLOWLENS_OBSERVER_CAPTURE_ALL_DISPLAYS"] = original
            if original_backend is None:
                os.environ.pop("FLOWLENS_OBSERVER_CAPTURE_BACKEND", None)
            else:
                os.environ["FLOWLENS_OBSERVER_CAPTURE_BACKEND"] = original_backend

    def test_auto_backend_prefers_screencapture_for_observer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            controller = FakeController([Image.new("RGB", (32, 24), "white")])
            paths = ObserverPaths.resolve(tmp)
            service = ObserverCaptureService(
                paths,
                config=ObserverConfig(
                    capture_backend="auto",
                    capture_all_displays=False,
                    screenshot_strategy="none",
                    enable_visual_summary=False,
                ),
                controller=controller,
                ocr=FakeOCR(),
                visual_media=None,
            )
            display = controller.list_displays()[0]

            def fake_run(args, capture_output, text, timeout, check):
                self.assertEqual(args[:4], ["/usr/sbin/screencapture", "-x", "-D", "1"])
                path = args[-1]
                Image.new("RGB", (32, 24), "white").save(path, format="PNG")
                return SimpleNamespace(returncode=0, stderr="")

            with (
                mock.patch.object(
                    controller,
                    "capture_display",
                    side_effect=AssertionError("quartz should not run first in auto mode"),
                ),
                mock.patch("flowlens.observer.service.subprocess.run", side_effect=fake_run),
            ):
                image, stats = service._capture_display_image(display)

            self.assertEqual(image.size, (32, 24))
            self.assertEqual(stats["backend"], "screencapture")
            self.assertEqual(stats["backend_error"], "")

    def test_store_insert_search_and_memory_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = ObserverPaths.resolve(tmp)
            store = ObserverStore(paths)

            now = datetime.now()
            first = store.insert_capture(
                timestamp=(now - timedelta(minutes=4)).isoformat(),
                app_name="Cursor",
                window_title="observer migration plan",
                browser_url="",
                ocr_text="Implement observer sqlite storage and launchd integration",
                screenshot_path=str(paths.screenshots_dir / "one.jpg"),
                capture_reason="manual",
                is_keyframe=True,
            )
            self.assertGreater(first, 0)

            store.insert_capture(
                timestamp=(now - timedelta(minutes=1)).isoformat(),
                app_name="Google Chrome",
                window_title="Observer docs",
                browser_url="https://example.com/observer",
                ocr_text="Read observer design notes and review screenshots",
                screenshot_path=None,
                capture_reason="manual",
                is_keyframe=False,
            )

            matches = store.search_captures("launchd")
            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0]["app_name"], "Cursor")

            store.upsert_project_memories(
                {
                    "Observer": {
                        "status": "Implementing capture and journal features",
                        "key_decisions": ["Use SQLite for durable local storage"],
                        "next_steps": ["Wire the CLI into flowlens observer"],
                        "blockers": [],
                    }
                }
            )
            memories = store.get_project_memories("Observer")
            self.assertIn("Observer", memories)
            self.assertIn("status", memories["Observer"]["current"])

    def test_generate_work_journal_without_llm_uses_existing_captures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = ObserverPaths.resolve(tmp)
            store = ObserverStore(paths)
            now = datetime.now()
            store.insert_capture(
                timestamp=(now - timedelta(minutes=10)).isoformat(),
                app_name="Cursor",
                window_title="observer/service.py",
                browser_url="",
                ocr_text="Observer capture loop and screenshot retention",
                screenshot_path=None,
                capture_reason="manual",
                is_keyframe=True,
            )
            store.insert_capture(
                timestamp=(now - timedelta(minutes=5)).isoformat(),
                app_name="Google Chrome",
                window_title="launchd docs",
                browser_url="https://developer.apple.com",
                ocr_text="LaunchAgent plist ProgramArguments KeepAlive RunAtLoad",
                screenshot_path=None,
                capture_reason="manual",
                is_keyframe=False,
            )
            report = generate_work_journal(paths, use_llm=False)
            self.assertIn("FlowLens Observer Review", report)
            self.assertIn("Captures: 2", report)
            self.assertIn("Cursor", report)

    def test_capture_uses_diff_ocr_and_diff_visual_summary_for_small_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = ObserverPaths.resolve(tmp)
            base = Image.new("RGB", (240, 120), "white")
            changed = base.copy()
            draw = ImageDraw.Draw(changed)
            draw.rectangle((12, 14, 42, 44), fill="black")

            def snapshot(windowserver_footprint: float, chrome_total: float) -> dict:
                return {
                    "timestamp": "2026-04-08T00:00:00",
                    "current_process": {"rss_mb": 120.0},
                    "windowserver": {
                        "pid": 161,
                        "rss_mb": 100.0,
                        "footprint_mb": windowserver_footprint,
                        "ports": 4000,
                    },
                    "chrome": {
                        "window_count": 1,
                        "tab_count": 4,
                        "total_rss_mb": chrome_total,
                        "largest_renderer_rss_mb": 320.0,
                        "top_processes": [
                            {"pid": 1001, "rss_mb": 320.0, "kind": "renderer"},
                        ],
                    },
                    "observer": {"pid": 911, "rss_mb": 220.0},
                }

            service = ObserverCaptureService(
                paths,
                config=ObserverConfig(
                    capture_backend="quartz",
                    capture_all_displays=False,
                    diff_threshold=0.30,
                    enable_visual_summary=True,
                    vision_model="Qwen3.5-2B-6bit",
                ),
                controller=FakeController([base, changed]),
                ocr=FakeOCR(),
                visual_media=FakeVisualMedia(),
            )

            with mock.patch(
                "flowlens.observer.service.system_resource_snapshot",
                side_effect=[
                    snapshot(1024.0, 1500.0),
                    snapshot(1025.0, 1505.0),
                    snapshot(1025.0, 1505.0),
                    snapshot(1027.0, 1512.0),
                ],
            ):
                first = service.capture_once(reason="manual", is_keyframe=True)
                self.assertEqual(first["ocr_scope"], "full")
                self.assertEqual(first["visual_scope"], "full")

                second = service.capture_once(reason="manual", is_keyframe=True)
                self.assertEqual(second["ocr_scope"], "diff")
                self.assertEqual(second["visual_scope"], "diff")
                self.assertGreater(second["diff_region_count"], 0)
                self.assertLess(second["changed_area_ratio"], 0.30)
                self.assertIn("vision:", second["visual_summary"])
                self.assertGreaterEqual(second["timings_ms"]["total_ms"], 0.0)
                self.assertGreaterEqual(second["timings_ms"]["ocr_ms"], 0.0)
                self.assertEqual(second["display_count"], 1)
                self.assertEqual(second["combined_image"]["width"], 240)

            store = ObserverStore(paths)
            latest = store.latest_capture()
            self.assertEqual(latest["visual_model"], "Qwen3.5-2B-6bit")
            self.assertEqual(latest["ocr_scope"], "diff")
            self.assertIn('"x"', latest["diff_regions_json"])
            self.assertIsNotNone(latest["total_ms"])
            self.assertIsNotNone(latest["ocr_ms"])
            stats = store.stats()
            self.assertIn("avg_total_ms", stats)
            self.assertGreaterEqual(float(stats["avg_total_ms"]), 0.0)

            entries = [
                json.loads(line)
                for line in paths.resource_monitor_log_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(len(entries), 2)
            self.assertEqual(entries[-1]["capture"]["display_count"], 1)
            self.assertEqual(entries[-1]["capture"]["combined_image"]["pixels"], 240 * 120)
            self.assertEqual(entries[-1]["resources_after"]["chrome"]["window_count"], 1)
            self.assertEqual(entries[-1]["resource_delta"]["windowserver_footprint_mb"], 2)
            self.assertEqual(entries[-1]["resource_delta"]["chrome_total_rss_mb"], 7)


if __name__ == "__main__":
    unittest.main()
