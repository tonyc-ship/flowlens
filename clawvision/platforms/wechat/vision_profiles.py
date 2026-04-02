"""Vision request profiles for WeChat desktop automation."""

from __future__ import annotations

from ...perception.llm import VisionRequestConfig


WECHAT_UI_SIMPLE_CHECK = VisionRequestConfig(
    name="wechat_ui_simple_2b",
    local_model_name="Qwen3.5-2B-6bit",
    max_image_pixels=768,
    max_tokens=12,
)

WECHAT_LAYOUT_PARSE_2B = VisionRequestConfig(
    name="wechat_layout_parse_2b",
    local_model_name="Qwen3.5-2B-6bit",
    max_image_pixels=1280,
    max_tokens=512,
)

WECHAT_PARSE_FALLBACK = VisionRequestConfig(
    name="wechat_parse_fallback_9b",
    local_model_name="Qwen3.5-9B-MLX-4bit",
    max_image_pixels=1280,
    max_tokens=768,
)
