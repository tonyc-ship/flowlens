"""Xiaohongshu site capability layer for the generic agent."""

from .capabilities import (
    CapabilityCost,
    CapabilitySpec,
    ExtractionLevel,
    NoteExtractionPlan,
    capability_catalog,
    capability_catalog_markdown,
    card_note_plan,
    deep_note_plan,
    lite_note_plan,
    plan_for_level,
)
from .spec import EntityFieldSpec, EntitySchemaSpec, entity_schema_catalog, load_xhs_spec
from .entities import (
    AuthorEntity,
    Comment,
    ImageInfo,
    NoteCard,
    NoteEntity,
    NoteType,
    VideoInfo,
    parse_count_text,
)
from .processor import ProcessorConfig, TimingRecord, XHSSiteAdapter, rank_note_card
from .agent_tools import ExtractSiteEntityTool, RunSiteActionTool, XHSTopicScanTool

__all__ = [
    "AuthorEntity",
    "CapabilityCost",
    "CapabilitySpec",
    "Comment",
    "ExtractSiteEntityTool",
    "ExtractionLevel",
    "ImageInfo",
    "NoteCard",
    "NoteEntity",
    "NoteExtractionPlan",
    "NoteType",
    "ProcessorConfig",
    "RunSiteActionTool",
    "TimingRecord",
    "VideoInfo",
    "XHSSiteAdapter",
    "XHSTopicScanTool",
    "capability_catalog",
    "capability_catalog_markdown",
    "card_note_plan",
    "deep_note_plan",
    "entity_schema_catalog",
    "EntityFieldSpec",
    "EntitySchemaSpec",
    "lite_note_plan",
    "load_xhs_spec",
    "parse_count_text",
    "plan_for_level",
    "rank_note_card",
]
