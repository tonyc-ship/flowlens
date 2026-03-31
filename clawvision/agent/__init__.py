"""Compatibility exports for the legacy `clawvision.agent` namespace."""

from ..core import (
    ComposerEntryResult,
    ComposerSpec,
    ComposerSubmitResult,
    DomTextAssessment,
    ExtensionBridge,
    SubmitAttemptResult,
    assess_expected_text_state,
    compact_text,
    enter_text,
    focus_chat_input,
    submit_attempt_order,
    submit_with_dom_verification,
    wait_for_input_ready,
)
from ..perception import MediaConfig, MediaProcessor
from ..platforms.xhs import (
    CapabilityCost,
    CapabilitySpec,
    NoteExtractionLevel,
    NoteExtractionPlan,
    XHSBrowser,
    capability_catalog,
    capabilities_for_task,
    deep_note_plan,
    lite_note_plan,
)
from ..reasoning import StructuredTask, TaskKind, make_creator_growth_breakdown_task, make_topic_research_task
from ..workflows.xhs import (
    ResearchConfig,
    UserAnalysisConfig,
    XHSResearchAgent,
    XHSTaskRunner,
    XHSUserAnalyzer,
    run_research,
    run_user_analysis,
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
