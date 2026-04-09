import unittest
from unittest import mock

from flowlens.core.process_metrics import chrome_process_snapshot, parse_size_to_mb


class ProcessMetricsTest(unittest.TestCase):
    def test_parse_size_to_mb_handles_top_units(self) -> None:
        self.assertEqual(parse_size_to_mb("8557M"), 8557.0)
        self.assertEqual(parse_size_to_mb("8.37G"), 8570.88)
        self.assertEqual(parse_size_to_mb("1024K"), 1.0)

    def test_chrome_process_snapshot_classifies_top_helpers(self) -> None:
        ps_output = "\n".join(
            [
                "467 410544 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "742 134368 /Applications/Google Chrome.app/.../Google Chrome Helper --type=gpu-process",
                "782 104592 /Applications/Google Chrome.app/.../Google Chrome Helper (Renderer) --type=renderer --extension-process --renderer-client-id=7",
                "35280 932912 /Applications/Google Chrome.app/.../Google Chrome Helper (Renderer) --type=renderer --renderer-client-id=1667",
            ]
        )

        def fake_run_text(args: list[str], *, timeout: float = 2.0) -> str:
            if args[:3] == ["ps", "-axo", "pid=,rss=,command="]:
                return ps_output
            if args[:3] == ["/usr/bin/osascript", "-e", 'tell application "Google Chrome" to get count of windows']:
                return "1"
            if args[:2] == ["/usr/bin/osascript", "-e"]:
                return "5"
            return ""

        with mock.patch("flowlens.core.process_metrics._run_text", side_effect=fake_run_text):
            snapshot = chrome_process_snapshot()

        self.assertEqual(snapshot["window_count"], 1)
        self.assertEqual(snapshot["tab_count"], 5)
        self.assertEqual(snapshot["largest_renderer_rss_mb"], 911.05)
        self.assertEqual(snapshot["process_counts_by_kind"]["renderer"], 1)
        self.assertEqual(snapshot["process_counts_by_kind"]["renderer_extension"], 1)
        self.assertEqual(snapshot["process_counts_by_kind"]["gpu"], 1)
        self.assertEqual(snapshot["top_processes"][0]["kind"], "renderer")


if __name__ == "__main__":
    unittest.main()
