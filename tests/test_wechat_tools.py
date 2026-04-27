"""WeChat tool surface tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from flowlens.agent.tool import ToolContext
from flowlens.platforms.wechat.tools import WeChatReadHistoryArtifactTool


class WeChatHistoryArtifactToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_read_history_artifact_returns_compact_message_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            site_results = run_dir / "site_results"
            site_results.mkdir(parents=True, exist_ok=True)
            artifact = site_results / "001_wechat_history.json"
            artifact.write_text(
                json.dumps(
                    {
                        "conversation": "x-mcp（三群）",
                        "capture_count": 3,
                        "date_markers": ["2026-04-22", "2026-04-23", "2026-04-24"],
                        "messages": [
                            {
                                "speaker": "Alice",
                                "side": "left",
                                "text": "大家想把 Claude Code 接到更多桌面程序。",
                                "timestamp": "2026-04-22 10:00",
                                "source_capture": 2,
                            },
                            {
                                "speaker": "self",
                                "side": "right",
                                "text": "需要先把工具层拆干净。",
                                "timestamp": "2026-04-23 09:30",
                                "source_capture": 1,
                            },
                            {
                                "speaker": "Bob",
                                "side": "left",
                                "text": "微信这边最好有直接读历史和局部 OCR 的工具。",
                                "timestamp": "2026-04-24 08:15",
                                "source_capture": 0,
                            },
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            tool = WeChatReadHistoryArtifactTool(llm_backend="sonnet")
            ctx = ToolContext(run_dir=run_dir)
            raw = await tool.execute({"order": "newest_first", "limit": 2}, ctx)
            payload = json.loads(raw)

            self.assertTrue(payload["ok"])
            self.assertEqual(payload["conversation"], "x-mcp（三群）")
            self.assertEqual(payload["artifact_path"], "site_results/001_wechat_history.json")
            self.assertEqual(payload["capture_count"], 3)
            self.assertEqual(len(payload["messages"]), 2)
            self.assertEqual(payload["messages"][0]["speaker"], "Bob")
            self.assertEqual(payload["top_speakers"][0]["message_count"], 1)


if __name__ == "__main__":
    unittest.main()
