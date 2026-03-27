"""Visual debugging and macOS automation helpers."""

from .macos import DisplayInfo, MacOSController, WindowInfo
from .visual_debug import VisualDebugger
from .workflows import WorkflowResult, run_sidepanel_demo_sync

__all__ = [
    "DisplayInfo",
    "MacOSController",
    "VisualDebugger",
    "WorkflowResult",
    "WindowInfo",
    "run_sidepanel_demo_sync",
]
