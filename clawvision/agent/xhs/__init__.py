"""Xiaohongshu (小红书) platform-specific agent modules."""

from .browser import XHSBrowser
from .capabilities import (
    CapabilityCost,
    CapabilitySpec,
    NoteExtractionLevel,
    NoteExtractionPlan,
    capability_catalog,
    capabilities_for_task,
    deep_note_plan,
    lite_note_plan,
)
from .entities import (
    NoteType, ImageInfo, VideoInfo, Comment,
    NoteEntity, NoteCard, AuthorEntity, SearchResult,
)
from .processor import NoteProcessor, ProcessorConfig, TimingRecord
from .research import XHSResearchAgent, ResearchConfig, run_research
from .task_runner import XHSTaskRunner
from .user_analysis import XHSUserAnalyzer, UserAnalysisConfig, run_user_analysis

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
