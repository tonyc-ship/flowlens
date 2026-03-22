"""Xiaohongshu (小红书) platform-specific agent modules."""

from .browser import XHSBrowser
from .entities import (
    NoteType, ImageInfo, VideoInfo, Comment,
    NoteEntity, NoteCard, AuthorEntity, SearchResult,
)
from .research import XHSResearchAgent, ResearchConfig, run_research
from .user_analysis import XHSUserAnalyzer, UserAnalysisConfig, run_user_analysis

__all__ = [
    "XHSBrowser",
    "NoteType", "ImageInfo", "VideoInfo", "Comment",
    "NoteEntity", "NoteCard", "AuthorEntity", "SearchResult",
    "XHSResearchAgent", "ResearchConfig", "run_research",
    "XHSUserAnalyzer", "UserAnalysisConfig", "run_user_analysis",
]
