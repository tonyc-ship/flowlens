"""Browser automation tools wrapping core/bridge.py primitives.

Each tool is a thin wrapper that translates the LLM's structured input
into a bridge call and formats the result as text for the LLM.
"""

from __future__ import annotations

import base64
import json

from ...core.bridge import ExtensionBridge, TabBridge
from ..tool import Tool, ToolContext


def _downscale_image(
    img_bytes: bytes, max_dim: int
) -> tuple[bytes, str, str]:
    """Downscale image bytes if larger than max_dim. Returns (bytes, b64, media_type)."""
    import io
    from PIL import Image

    img = Image.open(io.BytesIO(img_bytes))
    w, h = img.size
    if max(w, h) <= max_dim:
        b64 = base64.b64encode(img_bytes).decode()
        mt = "image/jpeg" if img_bytes[:2] == b'\xff\xd8' else "image/png"
        return img_bytes, b64, mt

    scale = max_dim / max(w, h)
    img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    if img.mode == "RGBA":
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[3])
        img = bg

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    new_bytes = buf.getvalue()
    b64 = base64.b64encode(new_bytes).decode()
    return new_bytes, b64, "image/jpeg"


class NavigateTool(Tool):
    def __init__(self, bridge: ExtensionBridge | TabBridge):
        self._bridge = bridge

    name = "navigate"
    description = "Navigate browser to a URL."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to navigate to"},
                "wait_ms": {"type": "integer", "description": "Wait ms after load (default 5000)", "default": 5000},
            },
            "required": ["url"],
        }

    async def execute(self, params: dict, ctx: ToolContext) -> str:
        url = params["url"]
        wait_ms = params.get("wait_ms", 5000)
        result = await self._bridge.navigate(url, wait_ms=wait_ms)
        info = await self._bridge.get_tab_info()
        return f"Navigated to: {info.get('url', url)}\nPage title: {info.get('title', '(unknown)')}"


class GoBackTool(Tool):
    def __init__(self, bridge: ExtensionBridge | TabBridge):
        self._bridge = bridge

    name = "go_back"
    description = "Go back to the previous page in browser history."

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, params: dict, ctx: ToolContext) -> str:
        await self._bridge.go_back()
        info = await self._bridge.get_tab_info()
        return f"Went back to: {info.get('url', '(unknown)')}\nTitle: {info.get('title', '')}"


class ScreenshotTool(Tool):
    def __init__(self, bridge: ExtensionBridge | TabBridge):
        self._bridge = bridge

    name = "screenshot"
    description = (
        "Take a screenshot of the current page and save it to disk. Returns the "
        "filename and page metadata only. If you need visual understanding, call "
        "`analyze_screenshot` or `ocr_screenshot` on the saved file."
    )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "label": {
                    "type": "string",
                    "description": "Short label for the file (e.g. 'search_results')",
                    "default": "screenshot",
                },
            },
        }

    async def execute(self, params: dict, ctx: ToolContext) -> list:
        import io
        from PIL import Image

        label = params.get("label", "screenshot")
        path = ctx.next_screenshot_path(label)
        data_url = await self._bridge.capture_screenshot()
        if not data_url:
            return "Failed to capture screenshot."

        # Parse data URL — detect media type from header or sniff bytes
        media_type = "image/png"
        if "," in data_url:
            header, b64_data = data_url.split(",", 1)
            if "image/jpeg" in header:
                media_type = "image/jpeg"
            elif "image/webp" in header:
                media_type = "image/webp"
        else:
            b64_data = data_url

        img_bytes = base64.b64decode(b64_data)

        # Sniff actual format from magic bytes
        if img_bytes[:2] == b'\xff\xd8':
            media_type = "image/jpeg"
        elif img_bytes[:4] == b'\x89PNG':
            media_type = "image/png"
        elif img_bytes[:4] == b'RIFF' and img_bytes[8:12] == b'WEBP':
            media_type = "image/webp"

        # Downscale if max_dim is set (e.g. for local model efficiency)
        if ctx.screenshot_max_dim > 0:
            img_bytes, _b64_data, media_type = _downscale_image(
                img_bytes, ctx.screenshot_max_dim
            )

        ext = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}.get(media_type, ".png")
        path = path.with_suffix(ext)
        path.write_bytes(img_bytes)

        info = await self._bridge.get_tab_info()
        with Image.open(io.BytesIO(img_bytes)) as img:
            width, height = img.size

        return [
            {
                "type": "text",
                "text": (
                    f"Screenshot saved to {path.name}\n"
                    f"URL: {info.get('url', '')}\n"
                    f"Title: {info.get('title', '')}\n"
                    f"Image: {width}x{height} {media_type}\n"
                    f"Use analyze_screenshot(question=..., screenshot_file='{path.name}') "
                    "for visual inspection."
                ),
            },
        ]


class ClickTool(Tool):
    def __init__(self, bridge: ExtensionBridge | TabBridge):
        self._bridge = bridge

    name = "click"
    description = "Click at viewport coordinates (x, y). Use this for manual fallback actions; prefer site-specific tools and commands first."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "X coordinate in viewport pixels"},
                "y": {"type": "integer", "description": "Y coordinate in viewport pixels"},
            },
            "required": ["x", "y"],
        }

    async def execute(self, params: dict, ctx: ToolContext) -> str:
        await self._bridge.click_at(params["x"], params["y"])
        return f"Clicked at ({params['x']}, {params['y']})"


class ScrollTool(Tool):
    def __init__(self, bridge: ExtensionBridge | TabBridge):
        self._bridge = bridge

    name = "scroll"
    description = "Scroll the page. Positive=down, negative=up."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "pixels": {
                    "type": "integer",
                    "description": "Pixels to scroll. Default 600.",
                    "default": 600,
                },
            },
        }

    async def execute(self, params: dict, ctx: ToolContext) -> str:
        pixels = params.get("pixels", 600)
        await self._bridge.scroll_page(pixels)
        direction = "down" if pixels > 0 else "up"
        return f"Scrolled {direction} by {abs(pixels)} pixels."


class TypeTextTool(Tool):
    def __init__(self, bridge: ExtensionBridge | TabBridge):
        self._bridge = bridge

    name = "type_text"
    description = "Type text at the current cursor position. Handles Unicode/CJK."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The text to type"},
            },
            "required": ["text"],
        }

    async def execute(self, params: dict, ctx: ToolContext) -> str:
        await self._bridge.type_text(params["text"])
        return f"Typed: {params['text']}"


class PressKeyTool(Tool):
    def __init__(self, bridge: ExtensionBridge | TabBridge):
        self._bridge = bridge

    name = "press_key"
    description = "Press a keyboard key."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Key name (Enter, Escape, Tab, ArrowDown, etc)"},
            },
            "required": ["key"],
        }

    async def execute(self, params: dict, ctx: ToolContext) -> str:
        await self._bridge.press_key(params["key"])
        return f"Pressed key: {params['key']}"


class ReadPageTool(Tool):
    """Extract structured page content via JS injection."""

    def __init__(self, bridge: ExtensionBridge | TabBridge):
        self._bridge = bridge

    name = "read_page"
    description = "Extract visible text, links, and interactive elements from the page with their (x,y) coordinates. Use this on generic pages or when a site-specific command is unavailable."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "CSS selector to scope extraction. Omit for full page.",
                },
                "max_length": {
                    "type": "integer",
                    "description": "Max chars to return (default 8000)",
                    "default": 8000,
                },
            },
        }

    _JS_TEMPLATE = """
return (function() {
  const root = SELECTOR ? document.querySelector(SELECTOR) : document.body;
  if (!root) return JSON.stringify({error: "Selector not found"});

  // Page info
  const info = {
    url: location.href,
    title: document.title,
    elements: []
  };

  // Collect visible text, links, buttons, inputs
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
  let count = 0;
  while (walker.nextNode() && count < 200) {
    const el = walker.currentNode;
    const tag = el.tagName.toLowerCase();
    const rect = el.getBoundingClientRect();

    // Skip invisible elements
    if (rect.width === 0 || rect.height === 0) continue;
    if (rect.bottom < 0 || rect.top > window.innerHeight) continue;

    const text = (el.innerText || '').trim().substring(0, 200);
    if (!text && !['a', 'button', 'input', 'textarea', 'img'].includes(tag)) continue;

    const entry = {tag, x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height)};
    if (text) entry.text = text;
    if (tag === 'a' && el.href) entry.href = el.href;
    if (tag === 'img' && el.alt) entry.alt = el.alt;
    if (tag === 'input' || tag === 'textarea') {
      entry.type = el.type || 'text';
      entry.placeholder = el.placeholder || '';
      entry.value = (el.value || '').substring(0, 100);
    }
    if (tag === 'button' || el.getAttribute('role') === 'button') entry.role = 'button';

    info.elements.push(entry);
    count++;
  }

  return JSON.stringify(info);
})()
"""

    async def execute(self, params: dict, ctx: ToolContext) -> str:
        selector = params.get("selector", "")
        max_length = params.get("max_length", 8000)

        selector_js = f"'{selector}'" if selector else "null"
        code = self._JS_TEMPLATE.replace("SELECTOR", selector_js)

        result = await self._bridge.run_js(code)
        raw = result.get("value", result.get("result", ""))

        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            return f"Page content (raw):\n{str(raw)[:max_length]}"

        if isinstance(data, dict) and "error" in data:
            return f"Error: {data['error']}"

        lines = [f"URL: {data.get('url', '')}", f"Title: {data.get('title', '')}", ""]
        for el in data.get("elements", []):
            tag = el.get("tag", "")
            text = el.get("text", "")
            pos = f"({el.get('x', 0)},{el.get('y', 0)})"

            if tag == "a":
                lines.append(f"[link {pos}] {text} -> {el.get('href', '')}")
            elif el.get("role") == "button" or tag == "button":
                lines.append(f"[button {pos}] {text}")
            elif tag in ("input", "textarea"):
                lines.append(f"[{el.get('type', 'text')} input {pos}] placeholder={el.get('placeholder', '')} value={el.get('value', '')}")
            elif tag == "img":
                lines.append(f"[image {pos}] alt={el.get('alt', '')}")
            elif text:
                lines.append(f"[{tag} {pos}] {text}")

        output = "\n".join(lines)
        return output[:max_length]


class RunJavaScriptTool(Tool):
    def __init__(self, bridge: ExtensionBridge | TabBridge):
        self._bridge = bridge

    name = "run_javascript"
    description = "Execute JavaScript in the page. Must use `return` to get a value back."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "JavaScript code. Use JSON.stringify for objects."},
            },
            "required": ["code"],
        }

    async def execute(self, params: dict, ctx: ToolContext) -> str:
        result = await self._bridge.run_js(params["code"])
        if "error" in result:
            return f"JavaScript error: {result['error']}"
        raw = result.get("value", result.get("result", ""))
        return str(raw)[:8000]


class ExtractPageDataTool(Tool):
    """Delegates to the Chrome extension's site-specific extractors.

    The extension has optimized extraction commands for known sites (e.g.
    XHS search cards, note content, comments, profile info). This tool
    lets the agent leverage those when available.
    """

    def __init__(self, bridge: ExtensionBridge):
        self._bridge = bridge

    name = "extract_page_data"
    description = (
        "Site-specific extraction on XHS. This is ONE tool — always call it as "
        "extract_page_data with a `command` string and a `params` object. "
        "`submit_search_query`, `click_card`, `close_note` etc. are commands, "
        "NOT separate top-level tools.\n"
        "Commands (format: command — description — params):\n"
        "submit_search_query — Low-level search helper; prefer run_site_action(search_notes) at planner level — {keyword}\n"
        "extract_search_cards — Get note cards from search results — {}\n"
        "click_card — Open a note card by position index — {index}\n"
        "click_note_by_id / click_note_link — Open a visible card by id or URL — {note_id} / {url}\n"
        "extract_note_content — Get title/author/text/engagement of the OPEN note modal — {}\n"
        "collect_carousel_images — Collect all image URLs from the OPEN note carousel — {max_images}\n"
        "extract_comments — Get comments from the open note — {max_comments}\n"
        "scroll_note — Scroll inside the open note modal — {pixels}\n"
        "close_note — Close the note modal (ALWAYS use this to close; press_key Escape does NOT work for the XHS modal) — {}\n"
        "extract_profile_info / extract_profile_notes / extract_search_tabs / get_search_page_state / click_search_tab — {}\n"
        "Closing + opening the next note must be: extract_page_data close_note → extract_page_data click_card {index: N} → extract_page_data extract_note_content."
    )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Command name (see description)",
                    "enum": [
                        "submit_search_query",
                        "extract_search_cards",
                        "extract_search_tabs",
                        "get_search_page_state",
                        "click_search_tab",
                        "click_card",
                        "click_note_by_id",
                        "click_note_link",
                        "extract_note_content",
                        "collect_carousel_images",
                        "extract_comments",
                        "scroll_note",
                        "close_note",
                        "extract_profile_info",
                        "extract_profile_notes",
                    ],
                },
                "params": {
                    "type": "object",
                    "description": "Command params, e.g. {keyword: '...'} or {index: 0}",
                },
            },
            "required": ["command"],
        }

    async def execute(self, params: dict, ctx: ToolContext) -> str:
        command = params["command"]
        cmd_params = params.get("params", {})
        result = await self._bridge.send_command(command, cmd_params)
        output = json.dumps(result, ensure_ascii=False, indent=2)
        return output[:12000]


class WaitTool(Tool):
    """Wait for a specified duration — important for anti-bot compliance."""

    name = "wait"
    description = "Wait for a delay (anti-bot, page load)."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "seconds": {
                    "type": "number",
                    "description": "Seconds (1-30, default 3)",
                    "default": 3,
                },
            },
        }

    async def execute(self, params: dict, ctx: ToolContext) -> str:
        import asyncio
        seconds = min(max(params.get("seconds", 3), 0.5), 30)
        await asyncio.sleep(seconds)
        return f"Waited {seconds} seconds."


def make_browser_tools(
    bridge: ExtensionBridge | TabBridge,
    *,
    ext_bridge: ExtensionBridge | None = None,
) -> list[Tool]:
    """Create all browser tools bound to a bridge instance.

    Args:
        bridge: The bridge (or TabBridge) for navigation/interaction tools.
        ext_bridge: The parent ExtensionBridge for extension-level commands
                    like extract_page_data. If None and bridge is an
                    ExtensionBridge, bridge is used directly.
    """
    tools: list[Tool] = [
        NavigateTool(bridge),
        GoBackTool(bridge),
        ScreenshotTool(bridge),
        ClickTool(bridge),
        ScrollTool(bridge),
        TypeTextTool(bridge),
        PressKeyTool(bridge),
        ReadPageTool(bridge),
        RunJavaScriptTool(bridge),
        WaitTool(),
    ]
    # ExtractPageDataTool needs send_command on the ExtensionBridge
    eb = ext_bridge or (bridge if isinstance(bridge, ExtensionBridge) else None)
    if eb is not None:
        tools.append(ExtractPageDataTool(eb))
    return tools
