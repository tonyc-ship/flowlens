"""Generic macOS desktop tools for cross-app computer use."""

from __future__ import annotations

import json

from ...core.desktop import DesktopWindowSession
from ...debug import MacOSController, WindowInfo
from ...perception.apple_ocr import AppleOCR
from ..tool import Tool, ToolContext


class _DesktopTool(Tool):
    capability_pack = "desktop_generic"

    def __init__(self) -> None:
        self._controller = MacOSController()
        self._ocr = AppleOCR()

    def _resolve_window(
        self,
        *,
        app_name: str = "",
        title_contains: str = "",
        frontmost: bool = False,
    ) -> WindowInfo:
        if frontmost or not app_name.strip():
            window = self._controller.frontmost_window_info()
            if window is not None:
                return window
            if not app_name.strip():
                raise RuntimeError("No visible frontmost window was found.")
        session = DesktopWindowSession(
            app_name.strip(),
            title_contains=title_contains.strip() or None,
            controller=self._controller,
            ocr=self._ocr,
        )
        return session.resolve_window(visible_only=True)

    def _session(self, app_name: str, *, title_contains: str = "") -> DesktopWindowSession:
        return DesktopWindowSession(
            app_name.strip(),
            title_contains=title_contains.strip() or None,
            controller=self._controller,
            ocr=self._ocr,
        )


class DesktopListWindowsTool(_DesktopTool):
    name = "desktop_list_windows"
    description = "List visible macOS windows so you can decide which app/window to control next."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "app_name": {"type": "string"},
                "title_contains": {"type": "string"},
                "on_screen_only": {"type": "boolean", "default": True},
            },
        }

    async def execute(self, params: dict, ctx: ToolContext) -> str:
        windows = self._controller.list_windows(
            app_name=str(params.get("app_name") or "").strip() or None,
            title_contains=str(params.get("title_contains") or "").strip() or None,
            on_screen_only=bool(params.get("on_screen_only", True)),
        )
        payload = [window.to_dict() for window in windows[:20]]
        return json.dumps(payload, ensure_ascii=False, indent=2)


class DesktopFocusAppTool(_DesktopTool):
    name = "desktop_focus_app"
    description = "Bring a macOS app to the foreground."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "app_name": {"type": "string"},
            },
            "required": ["app_name"],
        }

    async def execute(self, params: dict, ctx: ToolContext) -> str:
        app_name = str(params.get("app_name") or "").strip()
        if not app_name:
            return "app_name is required."
        self._controller.activate_app(app_name)
        return json.dumps({"ok": True, "focused_app": app_name}, ensure_ascii=False, indent=2)


class DesktopCaptureWindowTool(_DesktopTool):
    name = "desktop_capture_window"
    description = "Capture a screenshot of the frontmost macOS window or a specific app window and save it to disk."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "app_name": {"type": "string"},
                "title_contains": {"type": "string"},
                "frontmost": {"type": "boolean", "default": True},
                "label": {"type": "string", "default": "desktop_window"},
            },
        }

    async def execute(self, params: dict, ctx: ToolContext) -> str:
        label = str(params.get("label") or "desktop_window").strip() or "desktop_window"
        frontmost = bool(params.get("frontmost", True))
        app_name = str(params.get("app_name") or "").strip()
        title_contains = str(params.get("title_contains") or "").strip()
        path = ctx.next_screenshot_path(label)

        if frontmost or not app_name:
            window = self._resolve_window(app_name=app_name, title_contains=title_contains, frontmost=True)
            image = self._controller.capture_window_info(window).convert("RGB")
            image.save(path.with_suffix(".jpg"), quality=95)
            path = path.with_suffix(".jpg")
        else:
            session = self._session(app_name, title_contains=title_contains)
            capture = session.capture_to_path(path.with_suffix(".jpg"), visible_only=True)
            window = capture.window
            path = capture.path

        return json.dumps(
            {
                "ok": True,
                "screenshot_file": path.name,
                "window": window.to_dict(),
            },
            ensure_ascii=False,
            indent=2,
        )


class DesktopClickTool(_DesktopTool):
    name = "desktop_click_in_window"
    description = "Click inside a macOS window using normalized coordinates (0-1)."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "x": {"type": "number", "minimum": 0, "maximum": 1},
                "y": {"type": "number", "minimum": 0, "maximum": 1},
                "clicks": {"type": "integer", "minimum": 1, "maximum": 3, "default": 1},
                "app_name": {"type": "string"},
                "title_contains": {"type": "string"},
                "frontmost": {"type": "boolean", "default": True},
            },
            "required": ["x", "y"],
        }

    async def execute(self, params: dict, ctx: ToolContext) -> str:
        x = float(params.get("x"))
        y = float(params.get("y"))
        clicks = max(1, int(params.get("clicks", 1)))
        app_name = str(params.get("app_name") or "").strip()
        title_contains = str(params.get("title_contains") or "").strip()
        frontmost = bool(params.get("frontmost", True))
        window = self._resolve_window(app_name=app_name, title_contains=title_contains, frontmost=frontmost)
        screen_x, screen_y = DesktopWindowSession.normalized_point_to_screen(window, x=x, y=y)
        self._controller.click(screen_x, screen_y, clicks=clicks)
        return json.dumps(
            {
                "ok": True,
                "window_owner": window.owner,
                "window_title": window.title,
                "screen_point": {"x": screen_x, "y": screen_y},
            },
            ensure_ascii=False,
            indent=2,
        )


class DesktopClickTextTool(_DesktopTool):
    name = "desktop_click_text"
    description = "Find visible text in a macOS app window via OCR and click it."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "app_name": {"type": "string"},
                "title_contains": {"type": "string"},
                "exact": {"type": "boolean", "default": False},
                "clicks": {"type": "integer", "minimum": 1, "maximum": 3, "default": 1},
            },
            "required": ["text"],
        }

    async def execute(self, params: dict, ctx: ToolContext) -> str:
        text = str(params.get("text") or "").strip()
        if not text:
            return "text is required."
        app_name = str(params.get("app_name") or "").strip()
        if not app_name:
            front = self._resolve_window(frontmost=True)
            app_name = front.owner
        session = self._session(app_name, title_contains=str(params.get("title_contains") or "").strip())
        line = session.click_text(
            text,
            exact=bool(params.get("exact", False)),
            clicks=max(1, int(params.get("clicks", 1))),
        )
        if line is None:
            return json.dumps({"ok": False, "error": f"Could not find visible text: {text}"}, ensure_ascii=False, indent=2)
        return json.dumps(
            {
                "ok": True,
                "matched_text": line.text,
                "normalized_point": {"x": line.center_x, "y": line.center_y},
            },
            ensure_ascii=False,
            indent=2,
        )


class DesktopTypeTextTool(_DesktopTool):
    name = "desktop_type_text"
    description = "Type or paste text into the frontmost macOS app, optionally focusing a specific app first."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "app_name": {"type": "string"},
            },
            "required": ["text"],
        }

    async def execute(self, params: dict, ctx: ToolContext) -> str:
        app_name = str(params.get("app_name") or "").strip()
        if app_name:
            self._controller.activate_app(app_name)
        self._controller.paste_text(str(params.get("text") or ""))
        return json.dumps({"ok": True, "app_name": app_name or "frontmost"}, ensure_ascii=False, indent=2)


class DesktopPressKeyTool(_DesktopTool):
    name = "desktop_press_key"
    description = "Press a keyboard key in the frontmost macOS app, optionally focusing a specific app first."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "key": {"type": "string"},
                "app_name": {"type": "string"},
            },
            "required": ["key"],
        }

    async def execute(self, params: dict, ctx: ToolContext) -> str:
        app_name = str(params.get("app_name") or "").strip()
        if app_name:
            self._controller.activate_app(app_name)
        self._controller.press_key(str(params.get("key") or ""))
        return json.dumps({"ok": True, "app_name": app_name or "frontmost"}, ensure_ascii=False, indent=2)


class DesktopScrollTool(_DesktopTool):
    name = "desktop_scroll_in_window"
    description = "Scroll inside a macOS window. Positive line_delta scrolls upward to reveal older content."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "line_delta": {"type": "integer", "default": 8},
                "repeats": {"type": "integer", "minimum": 1, "maximum": 40, "default": 6},
                "x": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.5},
                "y": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.5},
                "app_name": {"type": "string"},
                "title_contains": {"type": "string"},
                "frontmost": {"type": "boolean", "default": True},
            },
        }

    async def execute(self, params: dict, ctx: ToolContext) -> str:
        line_delta = int(params.get("line_delta", 8))
        repeats = max(1, int(params.get("repeats", 6)))
        x = float(params.get("x", 0.5))
        y = float(params.get("y", 0.5))
        app_name = str(params.get("app_name") or "").strip()
        title_contains = str(params.get("title_contains") or "").strip()
        frontmost = bool(params.get("frontmost", True))
        window = self._resolve_window(app_name=app_name, title_contains=title_contains, frontmost=frontmost)
        screen_x, screen_y = DesktopWindowSession.normalized_point_to_screen(window, x=x, y=y)
        self._controller.scroll(line_delta, x=screen_x, y=screen_y, repeats=repeats)
        return json.dumps(
            {
                "ok": True,
                "window_owner": window.owner,
                "window_title": window.title,
                "repeats": repeats,
                "line_delta": line_delta,
            },
            ensure_ascii=False,
            indent=2,
        )


def make_desktop_tools() -> list[Tool]:
    return [
        DesktopListWindowsTool(),
        DesktopFocusAppTool(),
        DesktopCaptureWindowTool(),
        DesktopClickTool(),
        DesktopClickTextTool(),
        DesktopTypeTextTool(),
        DesktopPressKeyTool(),
        DesktopScrollTool(),
    ]
