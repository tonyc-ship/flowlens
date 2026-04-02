"""Task-layer reasoning for WeChat conversation summarization."""

from __future__ import annotations

import json
from dataclasses import dataclass

from ...perception.media import MediaProcessor
from ...platforms.wechat.models import WeChatMessage, WeChatParsedCapture
from ...reasoning.tasks import StructuredTask


def _parse_json(raw: str) -> dict:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    if not cleaned.startswith("{") and "{" in cleaned and "}" in cleaned:
        cleaned = cleaned[cleaned.find("{"): cleaned.rfind("}") + 1]
    return json.loads(cleaned)


@dataclass(frozen=True)
class CollectionDecision:
    continue_collection: bool
    scroll_batches: int
    reasoning: str


class WeChatSummaryAgent:
    """9B task controller and summarizer."""

    def __init__(self, media: MediaProcessor):
        self.media = media

    def decide_collection(
        self,
        task: StructuredTask,
        captures: list[WeChatParsedCapture],
        *,
        consecutive_stale: int,
    ) -> CollectionDecision:
        summaries = []
        for item in captures[-4:]:
            summaries.append({
                "capture_index": item.capture_index,
                "conversation_title": item.conversation_title,
                "parser_mode": item.parser_mode,
                "date_markers": item.date_markers[-3:],
                "message_count": len(item.messages),
                "messages_preview": [msg.to_dict() for msg in item.messages[:6]],
            })
        prompt = f"""You are controlling a local desktop task that reads WeChat chat history.

Task:
{task.to_prompt()}

Recent capture summaries:
{json.dumps(summaries, ensure_ascii=False, indent=2)}

Consecutive stale captures with no meaningful new content: {consecutive_stale}

Decide whether to continue scrolling upward. Prefer collecting enough context from multiple visible date separators, but stop when captures are repeating or already sufficient.

Return JSON only:
{{
  "continue_collection": true,
  "scroll_batches": 1,
  "reasoning": "short explanation"
}}
"""
        raw = self.media.call_text(prompt, max_tokens=512)
        try:
            data = _parse_json(raw)
        except Exception:
            data = {}
        return CollectionDecision(
            continue_collection=bool(data.get("continue_collection", consecutive_stale < 2)),
            scroll_batches=max(1, int(data.get("scroll_batches", 1))),
            reasoning=str(data.get("reasoning") or raw[:200]),
        )

    def summarize(
        self,
        task: StructuredTask,
        *,
        conversation_title: str,
        messages: list[WeChatMessage],
        date_markers: list[str],
    ) -> str:
        transcript = [
            {
                "speaker": item.speaker,
                "side": item.side,
                "timestamp": item.timestamp,
                "kind": item.kind,
                "text": item.text,
            }
            for item in messages[:120]
        ]
        prompt = f"""You are summarizing a WeChat conversation that was extracted from the macOS desktop client.

Task:
{task.to_prompt()}

Conversation title: {conversation_title}
Visible date markers: {json.dumps(date_markers[:20], ensure_ascii=False)}

Visible transcript excerpts:
{json.dumps(transcript, ensure_ascii=False, indent=2)}

Write a concise Markdown report in Chinese with these sections:
1. 总览
2. 参与者
3. 关键时间线
4. 主要话题与结论
5. 待办 / 风险 / 未决问题

Requirements:
- Only summarize what is supported by the visible transcript.
- Never invent dates, exact times, participant names, or decisions that are not explicitly visible in the transcript payload.
- If date markers mix relative expressions like Yesterday/Today with weekday names like Tuesday, do not infer their absolute ordering unless the visible text states it directly.
- If the visible date markers are sparse or fragmented, say so directly instead of filling gaps.
- Only list participants that appear in the transcript payload above.
- Mention uncertainty when coverage is partial.
- Keep it crisp and evidence-driven.
"""
        return self.media.call_text(prompt, max_tokens=1400)

    def repair_summary(
        self,
        task: StructuredTask,
        *,
        conversation_title: str,
        messages: list[WeChatMessage],
        date_markers: list[str],
        summary_markdown: str,
        verification_feedback: str,
    ) -> str:
        transcript = [
            {
                "speaker": item.speaker,
                "side": item.side,
                "timestamp": item.timestamp,
                "kind": item.kind,
                "text": item.text,
            }
            for item in messages[:120]
        ]
        prompt = f"""You wrote a WeChat conversation summary, but the verification step found hallucinations or unsupported claims.

Task:
{task.to_prompt()}

Conversation title: {conversation_title}
Visible date markers: {json.dumps(date_markers[:20], ensure_ascii=False)}

Original transcript excerpts:
{json.dumps(transcript, ensure_ascii=False, indent=2)}

Previous summary:
{summary_markdown}

Verification feedback:
{verification_feedback}

Rewrite the summary in Chinese Markdown.

Requirements:
- Remove any unsupported dates, names, or conclusions.
- If a time cannot be confirmed directly, say "未见明确时间" or equivalent.
- If relative time markers and weekday markers cannot be aligned safely, keep them as separate visible markers and explicitly state that their chronological order is uncertain.
- Stay conservative and evidence-driven.
- Keep the same section structure as before.
"""
        return self.media.call_text(prompt, max_tokens=1400)

    def verify_summary(
        self,
        *,
        summary_markdown: str,
        captures: list[WeChatParsedCapture],
    ) -> str:
        if not captures:
            return ""
        sample_indices = sorted({0, len(captures) // 2, len(captures) - 1})
        evidence = []
        for index in sample_indices:
            capture = captures[index]
            evidence.append({
                "capture_index": capture.capture_index,
                "conversation_title": capture.conversation_title,
                "date_markers": capture.date_markers[:4],
                "parser_mode": capture.parser_mode,
                "messages_preview": [item.to_dict() for item in capture.messages[:8]],
            })
        prompt = f"""You are checking whether a WeChat chat summary is grounded in visible evidence.

Representative visible captures:
{json.dumps(evidence, ensure_ascii=False, indent=2)}

Summary:
{summary_markdown}

Return a short Chinese verdict covering:
- 是否基本符合可见内容
- 主要不确定点
- 是否还有明显遗漏风险

        Important:
- The summary may legitimately combine information from different captures.
- Do not call it a hallucination just because a detail appears in capture 0 but not capture {captures[-1].capture_index}.
"""
        return self.media.call_text(prompt, max_tokens=500)
