"""Chrome Extension + External Agent architecture for XHS research."""

from .bridge import ExtensionBridge
from .xhs_agent import XHSAgent

__all__ = ["ExtensionBridge", "XHSAgent"]
