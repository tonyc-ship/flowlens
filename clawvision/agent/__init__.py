"""Chrome Extension + External Agent architecture for browser automation."""

from .bridge import ExtensionBridge
from .media import MediaProcessor, MediaConfig
from .tasks import (
    StructuredTask,
    TaskKind,
    make_creator_growth_breakdown_task,
    make_topic_research_task,
)
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
