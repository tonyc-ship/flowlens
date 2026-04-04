from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta

from clawvision.observer import ObserverPaths, ObserverStore, generate_work_journal


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
                        "next_steps": ["Wire the CLI into clawvision observer"],
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
            self.assertIn("ClawVision Observer Review", report)
            self.assertIn("Captures: 2", report)
            self.assertIn("Cursor", report)


if __name__ == "__main__":
    unittest.main()
