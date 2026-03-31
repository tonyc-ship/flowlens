"""Chat platform adapters and cheap page-state verification helpers."""

from .sites import CHATBOT_SITES, ChatbotSite
from .visible_verifier import ChatbotVisibleVerifier, build_visible_submit_prompt, parse_status_label
from .vision_profiles import (
    CHATBOT_COMPLEX_FALLBACK_CHECK,
    CHATBOT_INPUT_SIMPLE_CHECK,
    CHATBOT_PAGE_SIMPLE_CHECK,
)

__all__ = [
    "CHATBOT_SITES", "ChatbotSite",
    "ChatbotVisibleVerifier", "build_visible_submit_prompt", "parse_status_label",
    "CHATBOT_PAGE_SIMPLE_CHECK", "CHATBOT_INPUT_SIMPLE_CHECK", "CHATBOT_COMPLEX_FALLBACK_CHECK",
]
