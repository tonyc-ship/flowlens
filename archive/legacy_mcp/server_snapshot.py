"""Archived ClawVision MCP server snapshot.

Exposes screen-level visual perception tools that any MCP-compatible
AI agent can call. Focused on precise UI understanding and interaction.

This file is preserved for reference only. The active runtime path now
uses the Chrome Extension + `clawvision.agent.xhs` workflow instead.
"""

from __future__ import annotations

import base64
import io
import json

from mcp.server.fastmcp import FastMCP
from PIL import Image

from .screen import ScreenController
from .vision.llm import VisionLLM
from .vision.ocr import OCREngine
from .workflows.xiaohongshu import XiaohongshuWorkflow

mcp = FastMCP("ClawVision")

# Lazy-initialized singletons
_screen: ScreenController | None = None
_llm: VisionLLM | None = None
_ocr: OCREngine | None = None
_xhs: XiaohongshuWorkflow | None = None


def _get_screen() -> ScreenController:
    global _screen
    if _screen is None:
        _screen = ScreenController()
    return _screen


def _get_llm() -> VisionLLM:
    global _llm
    if _llm is None:
        _llm = VisionLLM()
    return _llm


def _get_ocr() -> OCREngine:
    global _ocr
    if _ocr is None:
        _ocr = OCREngine(_get_llm())
    return _ocr


def _get_xhs() -> XiaohongshuWorkflow:
    global _xhs
    if _xhs is None:
        _xhs = XiaohongshuWorkflow()
    return _xhs


def _image_to_base64(image: Image.Image) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.standard_b64encode(buffer.getvalue()).decode("utf-8")


# ── General vision tools ──


@mcp.tool()
def capture_screen(app_name: str | None = None) -> str:
    """Capture a screenshot of the full screen or a specific application window.

    Args:
        app_name: Optional application name to capture (e.g., "Google Chrome", "Safari").
                  If not provided, captures the full screen.

    Returns:
        JSON with base64-encoded screenshot and window info.
    """
    screen = _get_screen()

    if app_name:
        windows = screen.find_windows(app_name)
        if not windows:
            return json.dumps({"error": f"No windows found for '{app_name}'"})
        window = max(windows, key=lambda w: w.width * w.height)
        image = screen.capture_window(window)
        info = {"app": app_name, "window_title": window.title, "width": window.width, "height": window.height}
    else:
        image = screen.capture_full_screen()
        info = {"app": "full_screen", "width": image.width, "height": image.height}

    return json.dumps({
        "image_base64": _image_to_base64(image),
        "info": info,
    })


@mcp.tool()
def analyze_screen(question: str | None = None, app_name: str | None = None) -> str:
    """Capture and analyze the current screen or app window using AI vision.

    Args:
        question: Specific question about what's on screen. If not provided,
                  gives a general page analysis.
        app_name: Optional app to focus on. Defaults to full screen.

    Returns:
        Natural language analysis of the screen content.
    """
    screen = _get_screen()
    llm = _get_llm()

    if app_name:
        windows = screen.find_windows(app_name)
        if not windows:
            return f"No windows found for '{app_name}'"
        window = max(windows, key=lambda w: w.width * w.height)
        image = screen.capture_window(window)
    else:
        image = screen.capture_full_screen()

    return llm.analyze_page(image, question)


@mcp.tool()
def find_and_click(element_description: str, app_name: str = "Google Chrome") -> str:
    """Find a UI element on screen by description and click it.

    Args:
        element_description: Natural language description of what to click
                             (e.g., "the search button", "the first note card").
        app_name: Application to look in. Defaults to Chrome.

    Returns:
        JSON with success status and element info.
    """
    screen = _get_screen()
    llm = _get_llm()

    windows = screen.find_windows(app_name)
    if not windows:
        return json.dumps({"success": False, "error": f"No windows found for '{app_name}'"})

    window = max(windows, key=lambda w: w.width * w.height)
    screenshot = screen.capture_window(window)
    element = llm.locate_element(screenshot, element_description)

    if not element or not element.get("found"):
        return json.dumps({"success": False, "error": f"Element not found: {element_description}"})

    click_x = window.x + int(element["x"] / 100 * window.width)
    click_y = window.y + int(element["y"] / 100 * window.height)

    screen.click(click_x, click_y)

    return json.dumps({
        "success": True,
        "clicked_at": {"x": click_x, "y": click_y},
        "element": element,
    })


@mcp.tool()
def type_text(text: str) -> str:
    """Type text at the current cursor position.

    Args:
        text: The text to type. Supports CJK characters.
    """
    _get_screen().type_text(text)
    return json.dumps({"success": True, "typed": text})


@mcp.tool()
def extract_text(app_name: str = "Google Chrome", fields: list[str] | None = None) -> str:
    """Extract text from the current screen of an application.

    Args:
        app_name: Application to capture. Defaults to Chrome.
        fields: Optional list of specific fields to extract
                (e.g., ["title", "author", "likes"]).
                If not provided, extracts all visible text.

    Returns:
        Extracted text or structured JSON with requested fields.
    """
    screen = _get_screen()
    ocr = _get_ocr()

    windows = screen.find_windows(app_name)
    if not windows:
        return json.dumps({"error": f"No windows found for '{app_name}'"})

    window = max(windows, key=lambda w: w.width * w.height)
    image = screen.capture_window(window)

    if fields:
        result = ocr.extract_structured(image, fields)
        return json.dumps(result, ensure_ascii=False)
    else:
        return ocr.extract_all_text(image)


# ── Local CV detection tools (no API calls, fast) ──


@mcp.tool()
def detect_ui_elements(app_name: str = "Google Chrome", confidence: float = 0.25) -> str:
    """Detect all interactive UI elements on screen using local YOLO model.

    Fast (~100ms), no API calls. Detects buttons, icons, text fields, cards, etc.
    Requires: pip install clawvision[detect] and OmniParser weights downloaded.

    Args:
        app_name: Application to analyze. Defaults to Chrome.
        confidence: Detection confidence threshold (0.0-1.0).

    Returns:
        JSON list of detected elements with labels and bounding boxes.
    """
    from .vision.detector import YOLOUIDetector

    screen = _get_screen()
    windows = screen.find_windows(app_name)
    if not windows:
        return json.dumps({"error": f"No windows found for '{app_name}'"})

    window = max(windows, key=lambda w: w.width * w.height)
    image = screen.capture_window(window)

    detector = YOLOUIDetector(confidence=confidence)
    elements = detector.detect(image)

    return json.dumps({
        "count": len(elements),
        "elements": [
            {
                "label": e.label,
                "confidence": round(e.confidence, 3),
                "bbox": {"x": e.x, "y": e.y, "width": e.width, "height": e.height},
                "center": {"x": e.center[0], "y": e.center[1]},
            }
            for e in elements
        ],
    })


@mcp.tool()
def find_elements_by_query(
    queries: list[str],
    app_name: str = "Google Chrome",
    confidence: float = 0.1,
) -> str:
    """Find specific UI elements by text description using open-vocabulary detection (OWLv2).

    Slower than detect_ui_elements (~1s) but can find any element by description.
    No predefined classes needed — describe what you want in natural language.

    Requires: pip install clawvision[detect]

    Args:
        queries: List of element descriptions to search for,
                 e.g., ["search box", "note card", "like button", "搜索框"].
        app_name: Application to analyze. Defaults to Chrome.
        confidence: Detection confidence threshold (0.0-1.0).

    Returns:
        JSON list of matched elements with labels and bounding boxes.
    """
    from .vision.detector import OWLv2Detector

    screen = _get_screen()
    windows = screen.find_windows(app_name)
    if not windows:
        return json.dumps({"error": f"No windows found for '{app_name}'"})

    window = max(windows, key=lambda w: w.width * w.height)
    image = screen.capture_window(window)

    detector = OWLv2Detector()
    elements = detector.detect(image, queries, confidence)

    return json.dumps({
        "count": len(elements),
        "queries": queries,
        "elements": [
            {
                "label": e.label,
                "confidence": round(e.confidence, 3),
                "bbox": {"x": e.x, "y": e.y, "width": e.width, "height": e.height},
                "center": {"x": e.center[0], "y": e.center[1]},
            }
            for e in elements
        ],
    })


# ── Xiaohongshu-specific tools ──


@mcp.tool()
def xhs_search(query: str, max_notes: int = 10) -> str:
    """Search Xiaohongshu (Little Red Book) for a topic and extract results.

    Prerequisites: Chrome must be open with xiaohongshu.com loaded and user logged in.

    Args:
        query: Search query in Chinese (e.g., "露营装备推荐").
        max_notes: Maximum number of notes to extract (default 10).

    Returns:
        JSON with extracted note cards (title, author, likes) and page analysis.
    """
    xhs = _get_xhs()
    result = xhs.search(query, max_notes)

    return json.dumps(
        {
            "query": result.query,
            "page_description": result.page_description,
            "notes": [
                {"title": n.title, "author": n.author, "likes": n.likes}
                for n in result.notes
            ],
            "screenshot_base64": _image_to_base64(result.screenshot) if result.screenshot else None,
        },
        ensure_ascii=False,
    )


@mcp.tool()
def xhs_note_detail(note_index: int) -> str:
    """Click on a note in Xiaohongshu search results and extract its details.

    Call xhs_search first to get results, then use this to drill into a specific note.

    Args:
        note_index: Zero-based index of the note to open (0 = first note).

    Returns:
        JSON with note details (title, content, author, engagement metrics).
    """
    xhs = _get_xhs()
    detail = xhs.capture_note_detail(note_index)

    screenshot = detail.pop("screenshot", None)
    result = {**detail}
    if screenshot:
        result["screenshot_base64"] = _image_to_base64(screenshot)

    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
def xhs_scroll_collect(rounds: int = 3) -> str:
    """Scroll through Xiaohongshu results and capture screenshots at each position.

    Useful for collecting more notes beyond the initial viewport.

    Args:
        rounds: Number of scroll-and-capture rounds (default 3).

    Returns:
        JSON with base64 screenshots from each scroll position.
    """
    xhs = _get_xhs()
    screenshots = xhs.scroll_and_collect(rounds)

    return json.dumps({
        "rounds": len(screenshots),
        "screenshots": [_image_to_base64(s) for s in screenshots],
    })


def main():
    mcp.run()


if __name__ == "__main__":
    main()
