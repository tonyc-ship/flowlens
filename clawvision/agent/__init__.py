"""Chrome Extension + External Agent architecture for browser automation."""

from .bridge import ExtensionBridge
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
from .media import MediaProcessor, MediaConfig
from .tasks import (
    StructuredTask,
    TaskKind,
    make_creator_growth_breakdown_task,
    make_topic_research_task,
)
from .verification import DomTextAssessment, assess_expected_text_state, compact_text
from .xhs import (
    XHSBrowser,
    CapabilityCost,
    CapabilitySpec,
    NoteExtractionLevel,
    NoteExtractionPlan,
    capability_catalog,
    capabilities_for_task,
    deep_note_plan,
    lite_note_plan,
    XHSTaskRunner,
    XHSResearchAgent, ResearchConfig, run_research,
    XHSUserAnalyzer, UserAnalysisConfig, run_user_analysis,
)

__all__ = [
    "ExtensionBridge",
    "ComposerSpec", "ComposerEntryResult", "SubmitAttemptResult", "ComposerSubmitResult",
    "wait_for_input_ready", "focus_chat_input", "enter_text", "submit_attempt_order", "submit_with_dom_verification",
    "DomTextAssessment", "compact_text", "assess_expected_text_state",
    "MediaProcessor", "MediaConfig",
    "StructuredTask", "TaskKind",
    "make_topic_research_task", "make_creator_growth_breakdown_task",
    "XHSBrowser",
    "CapabilityCost", "CapabilitySpec", "NoteExtractionLevel", "NoteExtractionPlan",
    "capability_catalog", "capabilities_for_task", "lite_note_plan", "deep_note_plan",
    "XHSTaskRunner",
    "XHSResearchAgent", "ResearchConfig", "run_research",
    "XHSUserAnalyzer", "UserAnalysisConfig", "run_user_analysis",
]
