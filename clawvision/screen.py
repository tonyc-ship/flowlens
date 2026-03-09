"""macOS screen capture and input control.

Requires two system permissions:
- Screen Recording (for screenshots)
- Accessibility (for mouse/keyboard control)

Grant via: System Settings → Privacy & Security
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass

import numpy as np
import pyautogui
from PIL import Image

# pyautogui safety settings
pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.1


@dataclass
class WindowInfo:
    title: str
    owner: str
    x: int
    y: int
    width: int
    height: int
    window_id: int


class ScreenController:
    """Capture screen content and simulate user input on macOS."""

    # -- Capture --

    def capture_full_screen(self) -> Image.Image:
        """Capture the entire main display."""
        return pyautogui.screenshot()

    def capture_region(self, x: int, y: int, width: int, height: int) -> Image.Image:
        """Capture a specific screen region."""
        return pyautogui.screenshot(region=(x, y, width, height))

    def capture_window(self, window: WindowInfo) -> Image.Image:
        """Capture a specific window by its window ID.

        Uses CGWindowListCreateImage which works across Spaces/desktops —
        the window does NOT need to be on the current screen.
        """
        import Quartz

        cg_image = Quartz.CGWindowListCreateImage(
            Quartz.CGRectNull,
            Quartz.kCGWindowListOptionIncludingWindow,
            window.window_id,
            Quartz.kCGWindowImageBoundsIgnoreFraming,
        )
        if cg_image is None:
            # Fallback to region capture if CGWindowListCreateImage fails
            return self.capture_region(window.x, window.y, window.width, window.height)

        w = Quartz.CGImageGetWidth(cg_image)
        h = Quartz.CGImageGetHeight(cg_image)
        bpr = Quartz.CGImageGetBytesPerRow(cg_image)

        data_provider = Quartz.CGImageGetDataProvider(cg_image)
        raw_data = Quartz.CGDataProviderCopyData(data_provider)

        # Handle bytes-per-row padding (rows may be padded beyond width*4)
        arr = np.frombuffer(bytes(raw_data), dtype=np.uint8).reshape(h, bpr)
        arr = arr[:, :w * 4].reshape(h, w, 4)

        # BGRA -> RGBA
        return Image.fromarray(arr[:, :, [2, 1, 0, 3]])

    # -- Application control --

    def open_url(self, url: str, browser: str = "Google Chrome") -> None:
        """Open a URL in the specified browser."""
        subprocess.run(["open", "-a", browser, url], check=True)
        time.sleep(2)  # Wait for page to start loading

    def activate_app(self, app_name: str) -> None:
        """Bring an application to the foreground."""
        subprocess.run(
            ["osascript", "-e", f'tell application "{app_name}" to activate'],
            check=True,
        )
        time.sleep(0.5)

    # -- Window discovery --

    def find_windows(self, app_name: str | None = None, on_screen_only: bool = False) -> list[WindowInfo]:
        """Find windows, optionally filtered by application name.

        Args:
            app_name: Filter by application name (case-insensitive substring match).
            on_screen_only: If True, only return windows on the current Space/desktop.
                            Defaults to False to find windows across all Spaces.
        """
        import Quartz

        option = (
            Quartz.kCGWindowListOptionOnScreenOnly if on_screen_only
            else Quartz.kCGWindowListOptionAll
        ) | Quartz.kCGWindowListExcludeDesktopElements

        window_list = Quartz.CGWindowListCopyWindowInfo(option, Quartz.kCGNullWindowID)

        results = []
        for win in window_list:
            owner = win.get(Quartz.kCGWindowOwnerName, "")
            title = win.get(Quartz.kCGWindowName, "")
            bounds = win.get(Quartz.kCGWindowBounds, {})
            layer = win.get(Quartz.kCGWindowLayer, -1)

            if not bounds or bounds.get("Width", 0) < 100 or bounds.get("Height", 0) < 100:
                continue
            # Layer 0 = normal windows; skip menu bars, tooltips, overlays
            if layer != 0:
                continue
            if app_name and app_name.lower() not in owner.lower():
                continue

            results.append(
                WindowInfo(
                    title=title,
                    owner=owner,
                    x=int(bounds.get("X", 0)),
                    y=int(bounds.get("Y", 0)),
                    width=int(bounds.get("Width", 0)),
                    height=int(bounds.get("Height", 0)),
                    window_id=int(win.get(Quartz.kCGWindowNumber, 0)),
                )
            )
        return results

    def find_chrome_window(self, title_contains: str | None = None) -> WindowInfo | None:
        """Find a Chrome window, optionally matching title substring."""
        windows = self.find_windows("Google Chrome")
        if not windows:
            return None
        if title_contains:
            for w in windows:
                if title_contains.lower() in w.title.lower():
                    return w
        # Return the largest Chrome window (likely the main one)
        return max(windows, key=lambda w: w.width * w.height)

    # -- Input simulation --

    def click(self, x: int, y: int, clicks: int = 1) -> None:
        """Click at screen coordinates."""
        pyautogui.click(x, y, clicks=clicks)

    def double_click(self, x: int, y: int) -> None:
        """Double-click at screen coordinates."""
        pyautogui.doubleClick(x, y)

    def type_text(self, text: str, interval: float = 0.02) -> None:
        """Type text character by character."""
        pyautogui.typewrite(text, interval=interval) if text.isascii() else self._type_cjk(text)

    def _type_cjk(self, text: str) -> None:
        """Type CJK text using clipboard (pyautogui can't type non-ASCII directly)."""
        import subprocess

        process = subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)
        pyautogui.hotkey("command", "v")
        time.sleep(0.1)

    def press_key(self, key: str) -> None:
        """Press a single key (e.g., 'enter', 'tab', 'escape')."""
        pyautogui.press(key)

    def hotkey(self, *keys: str) -> None:
        """Press a key combination (e.g., hotkey('command', 'c'))."""
        pyautogui.hotkey(*keys)

    def scroll(self, clicks: int, x: int | None = None, y: int | None = None) -> None:
        """Scroll at position. Positive = up, negative = down."""
        if x is not None and y is not None:
            pyautogui.scroll(clicks, x=x, y=y)
        else:
            pyautogui.scroll(clicks)

    def move_to(self, x: int, y: int, duration: float = 0.2) -> None:
        """Move mouse to position."""
        pyautogui.moveTo(x, y, duration=duration)
