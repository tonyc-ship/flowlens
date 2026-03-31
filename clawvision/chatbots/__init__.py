"""Compatibility exports for the legacy `clawvision.chatbots` namespace."""

from ..platforms.chat import (
    CHATBOT_COMPLEX_FALLBACK_CHECK,
    CHATBOT_INPUT_SIMPLE_CHECK,
    CHATBOT_PAGE_SIMPLE_CHECK,
    CHATBOT_SITES,
    ChatbotSite,
    ChatbotVisibleVerifier,
)
from ..workflows.chat import (
    ChatbotWindow,
    ChatbotsCompanion,
    MultiChatRunner,
    cleanup_orphaned_chrome_processes,
    list_orphaned_chrome_processes,
    parse_orphaned_chrome_processes,
    run_multi_chat_sync,
)

__all__ = [
    "CHATBOT_SITES",
    "ChatbotSite",
    "CHATBOT_PAGE_SIMPLE_CHECK",
    "CHATBOT_INPUT_SIMPLE_CHECK",
    "CHATBOT_COMPLEX_FALLBACK_CHECK",
    "ChatbotVisibleVerifier",
    "ChatbotWindow",
    "ChatbotsCompanion",
    "MultiChatRunner",
    "cleanup_orphaned_chrome_processes",
    "list_orphaned_chrome_processes",
    "parse_orphaned_chrome_processes",
    "run_multi_chat_sync",
]
