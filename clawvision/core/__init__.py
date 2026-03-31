"""Core browser/runtime primitives used across all platforms and workflows."""

from .bridge import ExtensionBridge, TabBridge
from .composer import (
    ComposerEntryResult,
    ComposerSpec,
    ComposerSubmitResult,
    SubmitAttemptResult,
    enter_text,
    focus_chat_input,
    submit_attempt_order,
    submit_with_dom_verification,
    wait_for_input_ready,
)
from .recorder import SessionRecorder
from .reporting import markdown_styles, render_markdown, render_markdown_block
from .runtime import find_whisper_cli, find_whisper_models_dir, load_runtime_env
from .verification import DomTextAssessment, assess_expected_text_state, compact_text

__all__ = [
    "ExtensionBridge", "TabBridge",
    "ComposerSpec", "ComposerEntryResult", "SubmitAttemptResult", "ComposerSubmitResult",
    "wait_for_input_ready", "focus_chat_input", "enter_text", "submit_attempt_order", "submit_with_dom_verification",
    "SessionRecorder",
    "markdown_styles", "render_markdown", "render_markdown_block",
    "load_runtime_env", "find_whisper_cli", "find_whisper_models_dir",
    "DomTextAssessment", "compact_text", "assess_expected_text_state",
]
