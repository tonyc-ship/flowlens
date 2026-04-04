from __future__ import annotations

import io
import tempfile
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

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

            service = ObserverCaptureService(
                paths,
                config=ObserverConfig(
                    capture_all_displays=False,
                    diff_threshold=0.30,
                    enable_visual_summary=True,
                    vision_model="Qwen3.5-2B-6bit",
                ),
                controller=FakeController([base, changed]),
                ocr=FakeOCR(),
                visual_media=FakeVisualMedia(),
            )

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


if __name__ == "__main__":
    unittest.main()
