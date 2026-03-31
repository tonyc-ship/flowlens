"""Static chatbot site configuration."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChatbotSite:
    """Configuration for a chatbot website."""

    name: str
    url: str
    input_selectors: list[str]
    submit_selectors: list[str]
    submit_mode: str = "auto"


CHATBOT_SITES = [
    ChatbotSite(
        name="ChatGPT",
        url="https://chatgpt.com",
        input_selectors=[
            "#prompt-textarea",
            'textarea[placeholder*="Message"]',
            'textarea[placeholder*="message"]',
            "textarea",
            '[contenteditable="true"]',
        ],
        submit_selectors=[
            'button[data-testid="send-button"]',
            'button[aria-label*="Send"]',
            'button[aria-label*="send"]',
        ],
        submit_mode="enter",
    ),
    ChatbotSite(
        name="Gemini",
        url="https://gemini.google.com/app",
        input_selectors=[
            ".ql-editor",
            'rich-textarea [contenteditable="true"]',
            '[contenteditable="true"]',
            "textarea",
        ],
        submit_selectors=[
            'button[aria-label*="Send"]',
            'button[aria-label*="send"]',
            'button.send-button',
            "button.send",
        ],
        submit_mode="enter",
    ),
    ChatbotSite(
        name="Claude",
        url="https://claude.ai/new",
        input_selectors=[
            'fieldset [contenteditable="true"]',
            '[contenteditable="true"].ProseMirror',
            '[contenteditable="true"]',
            "textarea",
        ],
        submit_selectors=[
            'button[aria-label*="Send"]',
            'button[aria-label*="send"]',
            'fieldset button:not([disabled])',
        ],
        submit_mode="enter",
    ),
]
