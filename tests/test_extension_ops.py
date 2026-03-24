from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from clawvision.extension_ops import ExtensionOperationResult
from clawvision.extension_ops import _write_report


class ExtensionOpsReportTests(unittest.TestCase):
    def test_write_report_creates_json_and_html(self):
        result = ExtensionOperationResult(
            operation="reload",
            success=True,
            port=8765,
            started_at="2026-03-24T18:00:00",
            finished_at="2026-03-24T18:00:02",
            duration_s=2.0,
            logs=[
                {"ts": "2026-03-24T18:00:00", "action": "server_started", "detail": "Listening"},
                {"ts": "2026-03-24T18:00:01", "action": "reload", "detail": "Extension reconnected after reload"},
            ],
            output_dir="",
            error="",
        )
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            _write_report(result, out_dir)
            report_json = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
            report_html = (out_dir / "report.html").read_text(encoding="utf-8")

        self.assertEqual(report_json["operation"], "reload")
        self.assertTrue(report_json["success"])
        self.assertIn("ClawVision Extension Operation Report", report_html)
        self.assertIn("Bridge Logs", report_html)


if __name__ == "__main__":
    unittest.main()
