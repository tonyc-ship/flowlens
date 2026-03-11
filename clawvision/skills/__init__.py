"""Site-specific Skills — state machine architecture.

Skills encode site knowledge as page states, transitions, and extraction rules.
No pixel heuristics — all understanding delegated to LLMs and grounding models.
"""

from .base import ExtractionRule, PageState, SiteSkill, Transition
from .xiaohongshu_skill import XiaohongshuSkill

__all__ = [
    "SiteSkill",
    "PageState",
    "Transition",
    "ExtractionRule",
    "XiaohongshuSkill",
]
