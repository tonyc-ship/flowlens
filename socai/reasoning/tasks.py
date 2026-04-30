"""Structured task definitions shared by task runners.

These are intentionally site-agnostic. Site runners can map them onto
site-specific workflows and browser skills.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import re


class TaskKind(StrEnum):
    TOPIC_RESEARCH = "topic_research"
    CREATOR_GROWTH_BREAKDOWN = "creator_growth_breakdown"
    WECHAT_CHAT_SUMMARY = "wechat_chat_summary"


@dataclass
class StructuredTask:
    """Declarative task request passed into a site-specific task runner."""

    kind: TaskKind
    title: str
    objective: str
    payload: dict = field(default_factory=dict)
    questions: list[str] = field(default_factory=list)
    success_criteria: list[str] = field(default_factory=list)
    site: str = "xiaohongshu"

    def slug(self) -> str:
        base = f"{self.kind}_{self.title}".lower()
        return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "_", base).strip("_")[:80]

    def to_prompt(self) -> str:
        lines = [
            f"Task type: {self.kind}",
            f"Site: {self.site}",
            f"Title: {self.title}",
            f"Objective: {self.objective}",
        ]
        if self.payload:
            lines.append(f"Payload: {self.payload}")
        if self.questions:
            lines.append("Questions to answer:")
            lines.extend(f"- {question}" for question in self.questions)
        if self.success_criteria:
            lines.append("Success criteria:")
            lines.extend(f"- {criterion}" for criterion in self.success_criteria)
        return "\n".join(lines)


def make_topic_research_task(
    topic: str,
    *,
    questions: list[str] | None = None,
    preset_keywords: list[str] | None = None,
    max_keywords: int = 4,
    max_cards_per_keyword: int = 12,
    max_lite_notes: int = 10,
    max_deep_notes: int = 4,
    lite_comment_count: int = 4,
    deep_comment_count: int = 12,
) -> StructuredTask:
    task_questions = questions or [
        "这个话题在小红书上最常见的内容切入点是什么？",
        "什么样的帖子更容易拿到互动和讨论？",
        "评论区暴露了哪些真实需求、疑问和反对点？",
    ]
    return StructuredTask(
        kind=TaskKind.TOPIC_RESEARCH,
        title=f"话题研究：{topic}",
        objective=f"在小红书上研究“{topic}”这个话题，输出内容模式、用户关注点和可执行洞察。",
        payload={
            "topic": topic,
            "preset_keywords": preset_keywords or [],
            "max_keywords": max_keywords,
            "max_cards_per_keyword": max_cards_per_keyword,
            "max_lite_notes": max_lite_notes,
            "max_deep_notes": max_deep_notes,
            "lite_comment_count": lite_comment_count,
            "deep_comment_count": deep_comment_count,
        },
        questions=task_questions,
        success_criteria=[
            "覆盖至少多个相关帖子，而不是只看单条内容",
            "总结出稳定的内容模式、互动模式和评论语义",
            "给出可以直接被运营或研究使用的结论",
        ],
    )


def make_creator_growth_breakdown_task(
    profile_url: str,
    *,
    creator_name: str = "",
    questions: list[str] | None = None,
    max_scroll_rounds: int = 18,
    max_timeline_samples: int = 12,
    max_deep_notes: int = 4,
    lite_comment_count: int = 4,
    deep_comment_count: int = 12,
) -> StructuredTask:
    display_name = creator_name or profile_url.rsplit("/", 1)[-1]
    task_questions = questions or [
        "这个账号的赛道定位和人设是什么？",
        "它的内容结构、标题结构和媒体形式有什么规律？",
        "它可能是通过哪些内容和节奏完成起号或持续涨粉的？",
        "哪些做法值得模仿，哪些风险和短板需要注意？",
    ]
    return StructuredTask(
        kind=TaskKind.CREATOR_GROWTH_BREAKDOWN,
        title=f"作者起号拆解：{display_name}",
        objective=f"拆解小红书作者“{display_name}”的起号路径、内容策略和增长方法。",
        payload={
            "profile_url": profile_url,
            "creator_name": creator_name,
            "max_scroll_rounds": max_scroll_rounds,
            "max_timeline_samples": max_timeline_samples,
            "max_deep_notes": max_deep_notes,
            "lite_comment_count": lite_comment_count,
            "deep_comment_count": deep_comment_count,
        },
        questions=task_questions,
        success_criteria=[
            "输出作者画像、内容策略和爆款规律",
            "结合详细帖子和评论，不只停留在主页浅层信息",
            "对可能的增长路径给出有证据的推断",
        ],
    )


def make_wechat_chat_summary_task(
    conversation: str = "",
    *,
    max_scroll_rounds: int = 12,
    min_capture_rounds: int = 3,
) -> StructuredTask:
    target = conversation or "未识别会话"
    return StructuredTask(
        kind=TaskKind.WECHAT_CHAT_SUMMARY,
        title=f"微信会话总结：{target}",
        objective=f"在微信 macOS 客户端里打开“{target}”，向上滚动读取聊天记录，并输出结构化总结报告。",
        payload={
            "conversation": conversation,
            "max_scroll_rounds": max_scroll_rounds,
            "min_capture_rounds": min_capture_rounds,
        },
        questions=[
            "这个会话最近主要在讨论什么主题？",
            "有哪些明确结论、待办、分歧或风险点？",
            "可以按时间顺序还原出哪些关键节点？",
        ],
        success_criteria=[
            "读取多个可见屏的历史，而不是只总结当前一屏",
            "尽量标注说话人、日期分隔和关键消息",
            "最终报告能让未读过聊天的人快速理解上下文",
        ],
        site="wechat_desktop",
    )
