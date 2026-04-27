"""Build the unified tool list.

`build_tools` returns a flat list of `Tool` instances. Both the internal
agent loop and the external MCP server enumerate this list to advertise
their tool surface — guaranteeing they always see the same set.

Callers can trim unused categories via flags (e.g. a site-only MCP that
doesn't want vision tools), but the default is "everything that has the
prerequisites available".
"""

from __future__ import annotations

from ..agent.tool import Tool
from ..agent.tools.capability_packs import make_capability_pack_tools
from ..agent.tools.browser import make_browser_tools
from ..agent.tools.desktop import make_desktop_tools
from ..agent.tools.state import make_state_tools
from ..agent.tools.vision import AnalyzeScreenshotTool, OcrScreenshotTool
from ..core.bridge import ExtensionBridge, TabBridge
from ..perception.media import MediaProcessor
from ..platforms.wechat.tools import make_wechat_tools
from ..platforms.xhs.tools import make_xhs_tools


def _assign_capability_pack(tools: list[Tool], pack_id: str) -> list[Tool]:
    for tool in tools:
        setattr(tool, "capability_pack", pack_id)
    return tools


def build_tools(
    bridge: ExtensionBridge | TabBridge | None,
    *,
    ext_bridge: ExtensionBridge | None = None,
    media: MediaProcessor | None = None,
    site_media: MediaProcessor | None = None,
    include_browser: bool = True,
    include_state: bool = True,
    include_vision: bool = True,
    include_desktop: bool = True,
    include_sites: bool = True,
) -> list[Tool]:
    """Assemble the flat Tool list.

    - include_browser: navigate / click / scroll / screenshot / type_text / read_page / extract_page_data / ...
    - include_state:   task-state helpers (record_decision, summarize_progress, ...)
    - include_vision:  analyze_screenshot, ocr_screenshot  (needs `media`)
    - include_sites:   site-specific tools (currently Xiaohongshu)  (needs `ext_bridge` + `site_media`)
    """
    tools: list[Tool] = []
    browser_available = include_browser and bridge is not None
    desktop_available = include_desktop

    if browser_available:
        tools.extend(_assign_capability_pack(make_browser_tools(bridge, ext_bridge=ext_bridge), "browser_generic"))
    if include_state:
        tools.extend(make_state_tools())
    if include_vision and media is not None:
        tools.append(AnalyzeScreenshotTool(media=media))
        tools.append(OcrScreenshotTool(media=media))
    if desktop_available:
        tools.extend(_assign_capability_pack(make_desktop_tools(), "desktop_generic"))
        tools.extend(
            _assign_capability_pack(
                make_wechat_tools(llm_backend=str(getattr(media, "backend", "") or "sonnet")),
                "wechat",
            )
        )
    if include_sites and ext_bridge is not None and site_media is not None:
        tools.extend(_assign_capability_pack(make_xhs_tools(bridge, ext_bridge=ext_bridge, media=site_media), "xiaohongshu"))

    capability_tools = make_capability_pack_tools(
        tools_provider=lambda: tools,
        browser_available=browser_available,
        desktop_available=desktop_available,
    )
    return [*capability_tools, *tools]
