"""Chrome DevTools Protocol backend for FlowLens.

This package contains generic CDP discovery, connection, target, and page
primitives. Platform-specific behavior such as Xiaohongshu login/security state
detection belongs under ``flowlens.platforms``.
"""

from .discovery import INSPECT_URL, discover_chrome_cdp, open_inspect_page

__all__ = ["INSPECT_URL", "discover_chrome_cdp", "open_inspect_page"]
