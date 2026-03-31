"""Compatibility exports for the legacy `clawvision.agent.xhs` namespace."""

from ...platforms.xhs import (
    AuthorEntity,
    CapabilityCost,
    CapabilitySpec,
    Comment,
    ImageInfo,
    NoteCard,
    NoteEntity,
    NoteExtractionLevel,
    NoteExtractionPlan,
    NoteProcessor,
    NoteType,
    ProcessorConfig,
    SearchResult,
    TimingRecord,
    VideoInfo,
    XHSBrowser,
    capability_catalog,
    capabilities_for_task,
    deep_note_plan,
    lite_note_plan,
)
from ...workflows.xhs import (
    ResearchConfig,
    UserAnalysisConfig,
    XHSResearchAgent,
    XHSTaskRunner,
    XHSUserAnalyzer,
    run_research,
    run_user_analysis,
)

__all__ = [
    "XHSBrowser",
    "CapabilityCost", "CapabilitySpec", "NoteExtractionLevel", "NoteExtractionPlan",
    "capability_catalog", "capabilities_for_task", "lite_note_plan", "deep_note_plan",
    "NoteType", "ImageInfo", "VideoInfo", "Comment",
    "NoteEntity", "NoteCard", "AuthorEntity", "SearchResult",
    "NoteProcessor", "ProcessorConfig", "TimingRecord",
    "XHSResearchAgent", "ResearchConfig", "run_research",
    "XHSTaskRunner",
    "XHSUserAnalyzer", "UserAnalysisConfig", "run_user_analysis",
]
