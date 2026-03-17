"""Chrome Extension + External Agent architecture for browser automation."""

from .bridge import ExtensionBridge
from .media import MediaProcessor, MediaConfig
from .xhs import (
    XHSBrowser,
    XHSResearchAgent, ResearchConfig, run_research,
    XHSUserAnalyzer, UserAnalysisConfig, run_user_analysis,
)

__all__ = [
    "ExtensionBridge",
    "MediaProcessor", "MediaConfig",
    "XHSBrowser",
    "XHSResearchAgent", "ResearchConfig", "run_research",
    "XHSUserAnalyzer", "UserAnalysisConfig", "run_user_analysis",
]
