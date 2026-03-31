from __future__ import annotations

import unittest

from clawvision.reasoning.task_agent import TaskAgent


class _FakeMedia:
    def __init__(self, response: str = ""):
        self.response = response

    def extract_json(self, text: str):
        import json
        import re

        match = re.search(r"[\[{][\s\S]*[\]}]", text)
        return json.loads(match.group()) if match else None

    def call_text(self, prompt: str, max_tokens: int = 0):
        return self.response


class TaskAgentParsingTest(unittest.TestCase):
    def test_parse_json_response_handles_fenced_json(self):
        agent = TaskAgent(_FakeMedia(), site_context="test")
        raw = """```json
{"complete": true, "confidence": 0.9, "strengths": ["ok"]}
```"""
        data = agent._parse_json_response(raw)
        self.assertEqual(data["complete"], True)
        self.assertEqual(data["confidence"], 0.9)
        self.assertEqual(data["strengths"], ["ok"])

    def test_plan_execution_parses_strategy(self):
        agent = TaskAgent(
            _FakeMedia(
                """```json
{"mode":"coverage_first","keyword_count":5,"cards_per_keyword":14,"lite_note_count":12,"deep_note_count":4,"reasoning":"Prefer breadth first."}
```"""
            ),
            site_context="test",
        )
        strategy = agent.plan_execution("task", "topic_research", "| cap |")
        self.assertEqual(strategy.mode, "coverage_first")
        self.assertEqual(strategy.keyword_count, 5)
        self.assertEqual(strategy.cards_per_keyword, 14)
        self.assertEqual(strategy.lite_note_count, 12)
        self.assertEqual(strategy.deep_note_count, 4)


if __name__ == "__main__":
    unittest.main()
