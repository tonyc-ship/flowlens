"""Vision request profiles for chatbot workflow checks.

These profiles keep simple chat-window state checks cheap while reserving
heavier models for ambiguous or structured tasks.
"""

from __future__ import annotations

from ..vision.llm import VisionRequestConfig

CHATBOT_PAGE_SIMPLE_CHECK = VisionRequestConfig(
    name="chatbot_page_simple_2b",
    local_model_name="Qwen3.5-2B-6bit",
    max_image_pixels=768,
    max_tokens=4,
)

CHATBOT_INPUT_SIMPLE_CHECK = VisionRequestConfig(
    name="chatbot_input_simple_2b",
    local_model_name="Qwen3.5-2B-6bit",
    max_image_pixels=768,
    max_tokens=4,
)

CHATBOT_COMPLEX_FALLBACK_CHECK = VisionRequestConfig(
    name="chatbot_complex_fallback_9b",
    local_model_name="Qwen3.5-9B-MLX-4bit",
    max_image_pixels=1024,
    max_tokens=256,
)
