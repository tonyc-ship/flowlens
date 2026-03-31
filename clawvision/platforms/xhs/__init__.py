"""Xiaohongshu platform knowledge and browser adapters."""

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
    AuthorEntity,
    Comment,
    ImageInfo,
    NoteCard,
    NoteEntity,
    NoteType,
    SearchResult,
    VideoInfo,
)
from .processor import NoteProcessor, ProcessorConfig, TimingRecord

__all__ = [
    "XHSBrowser",
    "CapabilityCost", "CapabilitySpec", "NoteExtractionLevel", "NoteExtractionPlan",
    "capability_catalog", "capabilities_for_task", "lite_note_plan", "deep_note_plan",
    "AuthorEntity", "Comment", "ImageInfo", "NoteCard", "NoteEntity", "NoteType", "SearchResult", "VideoInfo",
    "NoteProcessor", "ProcessorConfig", "TimingRecord",
]
