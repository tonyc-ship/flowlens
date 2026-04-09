"""Compatibility re-exports for site-aware tools.

Xiaohongshu-specific tool implementations live under `flowlens.platforms.xhs`
so they stay with the site capability layer instead of the generic agent tool
package.
"""

from ...platforms.xhs.agent_tools import (
    ExtractSiteEntityTool,
    RunSiteActionTool,
    XHSTopicScanTool,
)

__all__ = [
    "ExtractSiteEntityTool",
    "RunSiteActionTool",
    "XHSTopicScanTool",
]
