"""Site-aware tool registry.

Xiaohongshu-specific tool implementations live under `flowlens.platforms.xhs`
so they stay with the site capability layer instead of the generic agent tool
package.
"""

from ...core.bridge import ExtensionBridge, TabBridge
from ...perception.media import MediaProcessor
from ...platforms.xhs.agent_tools import (
    ExtractSiteEntityTool,
    RunSiteActionTool,
    XHSTopicScanTool,
)
from ..tool import Tool


def make_site_tools(
    bridge: ExtensionBridge | TabBridge,
    *,
    ext_bridge: ExtensionBridge | None,
    media: MediaProcessor | None,
) -> list[Tool]:
    if ext_bridge is None or media is None:
        return []
    return [
        RunSiteActionTool(bridge, ext_bridge=ext_bridge, media=media),
        ExtractSiteEntityTool(bridge, ext_bridge=ext_bridge, media=media),
        XHSTopicScanTool(bridge, ext_bridge=ext_bridge, media=media),
    ]

__all__ = [
    "ExtractSiteEntityTool",
    "make_site_tools",
    "RunSiteActionTool",
    "XHSTopicScanTool",
]
