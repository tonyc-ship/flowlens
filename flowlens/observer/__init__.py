"""Observer subsystem for continuous desktop capture and workflow recall."""

from .analysis import ask_question, extract_summaries, format_project_memories, generate_work_journal
from .paths import ObserverPaths
from .service import ObserverCaptureService, ObserverConfig
from .store import ObserverStore

__all__ = [
    "ObserverCaptureService",
    "ObserverConfig",
    "ObserverPaths",
    "ObserverStore",
    "ask_question",
    "extract_summaries",
    "format_project_memories",
    "generate_work_journal",
]
