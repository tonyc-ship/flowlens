"""macOS screen capture and UI automation helpers.

This module intentionally avoids pyautogui so it can run on a minimal
SocAI install as long as Quartz/AppKit are available.
"""

from __future__ import annotations

import contextlib
import json
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass

import objc
import Quartz
from AppKit import NSScreen, NSWorkspace
from PIL import Image


@dataclass
class DisplayInfo:
    """A physical macOS display."""

    index: int
    display_id: int
    x: int
    y: int
    width: int
    height: int
    is_main: bool
    scale: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class WindowInfo:
    """A visible app window."""

    window_id: int
    owner: str
    title: str
    x: int
    y: int
    width: int
    height: int
    layer: int
    on_screen: bool
    capture_backend: str = "quartz"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AccessibilityElementInfo:
    """A matched accessibility element within an app window."""

    app_name: str
    role: str
    name: str
    description: str
    x: int
    y: int
    width: int
    height: int

    def to_dict(self) -> dict:
        return asdict(self)


def _cgimage_to_pil(cg_image) -> Image.Image:
    if cg_image is None:
        raise RuntimeError("Quartz returned no image")
    width = Quartz.CGImageGetWidth(cg_image)
    height = Quartz.CGImageGetHeight(cg_image)
    bytes_per_row = Quartz.CGImageGetBytesPerRow(cg_image)
    data_provider = Quartz.CGImageGetDataProvider(cg_image)
    copied_data = Quartz.CGDataProviderCopyData(data_provider)
    raw_data = bytes(copied_data)
    image = Image.frombytes(
        "RGBA",
        (width, height),
        raw_data,
        "raw",
        "BGRA",
        bytes_per_row,
        1,
    )
    del copied_data
    del raw_data
    return image


@contextlib.contextmanager
def _autorelease_pool():
    with objc.autorelease_pool():
        yield


def _escape_applescript(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _normalize_title(title: str, owner: str) -> str:
    text = (title or "").strip()
    suffix = f" - {owner}"
    if owner and text.endswith(suffix):
        text = text[: -len(suffix)].strip()
    return text


class MacOSController:
    """Capture displays/windows and trigger basic UI actions on macOS."""

    def _pyautogui(self):
        try:
            import pyautogui
        except ImportError:
            return None
        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = 0.05
        return pyautogui

    def list_displays(self) -> list[DisplayInfo]:
        with _autorelease_pool():
            screens = list(NSScreen.screens())
            main_screen = NSScreen.mainScreen()
            main_id = None
            if main_screen is not None:
                main_id = int(main_screen.deviceDescription()["NSScreenNumber"])

            displays: list[DisplayInfo] = []
            for index, screen in enumerate(screens):
                frame = screen.frame()
                display_id = int(screen.deviceDescription()["NSScreenNumber"])
                displays.append(
                    DisplayInfo(
                        index=index,
                        display_id=display_id,
                        x=int(frame.origin.x),
                        y=int(frame.origin.y),
                        width=int(frame.size.width),
                        height=int(frame.size.height),
                        is_main=display_id == main_id,
                        scale=float(screen.backingScaleFactor()),
                    )
                )
            return displays

    def list_windows(
        self,
        app_name: str | None = None,
        *,
        on_screen_only: bool = False,
        title_contains: str | None = None,
    ) -> list[WindowInfo]:
        with _autorelease_pool():
            window_list = self._quartz_window_list(on_screen_only=on_screen_only)
        if not window_list:
            return self._list_windows_via_accessibility(
                app_name=app_name,
                title_contains=title_contains,
            )

        windows: list[WindowInfo] = []
        for win in window_list:
            owner = str(win.get("kCGWindowOwnerName") or "")
            title = str(win.get("kCGWindowName") or "")
            bounds = dict(win.get("kCGWindowBounds") or {})
            width = int(bounds.get("Width", 0) or 0)
            height = int(bounds.get("Height", 0) or 0)
            layer = int(win.get("kCGWindowLayer") or -1)
            if width < 80 or height < 80:
                continue
            if layer != 0:
                continue
            if app_name and app_name.lower() not in owner.lower():
                continue
            if title_contains and title_contains.lower() not in title.lower():
                continue
            sharing_state = int(win.get("kCGWindowSharingState") or 0)
            backend = "region" if sharing_state == 0 else "quartz"
            windows.append(
                WindowInfo(
                    window_id=int(win.get("kCGWindowNumber") or 0),
                    owner=owner,
                    title=title,
                    x=int(bounds.get("X", 0) or 0),
                    y=int(bounds.get("Y", 0) or 0),
                    width=width,
                    height=height,
                    layer=layer,
                    on_screen=bool(win.get("kCGWindowIsOnscreen") or False),
                    capture_backend=backend,
                )
            )

        windows.sort(key=lambda item: item.width * item.height, reverse=True)
        if windows:
            return windows
        return self._list_windows_via_accessibility(
            app_name=app_name,
            title_contains=title_contains,
        )

    def frontmost_app_name(self) -> str | None:
        with _autorelease_pool():
            app = NSWorkspace.sharedWorkspace().frontmostApplication()
            if app is None:
                return None
            return str(app.localizedName())

    def frontmost_window_info(self) -> WindowInfo | None:
        """Return the actual frontmost user-visible window.

        Quartz window lists are ordered front-to-back, which is more reliable
        than ``frontmostApplication()`` when fullscreen windows or separate
        Spaces are involved.
        """
        with _autorelease_pool():
            window_list = self._quartz_window_list(on_screen_only=True) or []
        system_owners = {
            "Window Server",
            "Dock",
            "SystemUIServer",
            "Control Center",
            "Spotlight",
            "NotificationCenter",
        }
        for win in window_list:
            owner = str(win.get("kCGWindowOwnerName") or "")
            bounds = dict(win.get("kCGWindowBounds") or {})
            width = int(bounds.get("Width", 0) or 0)
            height = int(bounds.get("Height", 0) or 0)
            layer = int(win.get("kCGWindowLayer") or -1)
            if not owner or owner in system_owners:
                continue
            if layer != 0 or width < 80 or height < 80:
                continue
            return WindowInfo(
                window_id=int(win.get("kCGWindowNumber") or 0),
                owner=owner,
                title=str(win.get("kCGWindowName") or ""),
                x=int(bounds.get("X", 0) or 0),
                y=int(bounds.get("Y", 0) or 0),
                width=width,
                height=height,
                layer=layer,
                on_screen=bool(win.get("kCGWindowIsOnscreen") or False),
                capture_backend="quartz",
            )

        app_name = self.frontmost_app_name()
        if not app_name:
            return None
        front = self.front_window(app_name)
        if front is None:
            return None
        matched = self._match_quartz_window(front, app_name=app_name)
        return matched or front

    def is_screen_locked(self) -> bool:
        session = Quartz.CGSessionCopyCurrentDictionary()
        if not session:
            return False
        return bool(session.get("CGSSessionScreenIsLocked", False))

    def front_window(self, app_name: str) -> WindowInfo | None:
        script = """
function run(argv) {
  const targetApp = argv[0];
  const se = Application("System Events");
  const proc = se.applicationProcesses.byName(targetApp);
  try {
    const wins = proc.windows();
    if (!wins.length) return "";
    const w = wins[0];
    return JSON.stringify({
      window_id: -1,
      owner: targetApp,
      title: String(w.name() || ""),
      x: Number(w.position()[0]),
      y: Number(w.position()[1]),
      width: Number(w.size()[0]),
      height: Number(w.size()[1]),
      layer: 0,
      on_screen: true,
      capture_backend: "region"
    });
  } catch (e) {
    return "";
  }
}
"""
        try:
            result = subprocess.run(
                ["osascript", "-l", "JavaScript", "-e", script, app_name],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError:
            return None
        payload = result.stdout.strip()
        if not payload:
            return None
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return None
        return WindowInfo(**data)

    def best_window_for_app(
        self,
        app_name: str,
        *,
        title_contains: str | None = None,
        visible_only: bool = False,
    ) -> WindowInfo | None:
        if visible_only:
            front = self.front_window(app_name)
            matched = self._match_quartz_window(front, app_name=app_name) if front is not None else None
            preferred = matched or front
            if preferred is not None and preferred.width >= 500 and preferred.height >= 400:
                return preferred

            windows = self.list_windows(app_name=app_name, title_contains=title_contains)
            if windows:
                return windows[0]
            return preferred

        windows = self.list_windows(app_name=app_name, title_contains=title_contains)
        if not windows:
            return None
        return windows[0]

    def find_accessibility_element(
        self,
        app_name: str,
        text: str,
        *,
        window_index: int = 0,
    ) -> AccessibilityElementInfo | None:
        script = """
function run(argv){
  const appName = argv[0];
  const target = argv[1];
  const windowIndex = Number(argv[2] || "0");
  const se = Application("System Events");
  const proc = se.processes.byName(appName);
  function matches(el){
    try{
      const name = String(el.name() || "");
      const desc = String(el.description() || "");
      return name.includes(target) || desc.includes(target);
    }catch(e){
      return false;
    }
  }
  function walk(elements){
    for (let i = 0; i < elements.length; i += 1){
      const el = elements[i];
      try{
        if (matches(el)){
          let pos = [0, 0];
          let size = [0, 0];
          let role = "";
          let name = "";
          let desc = "";
          try { pos = el.position(); } catch (e) {}
          try { size = el.size(); } catch (e) {}
          try { role = String(el.role() || ""); } catch (e) {}
          try { name = String(el.name() || ""); } catch (e) {}
          try { desc = String(el.description() || ""); } catch (e) {}
          return JSON.stringify({
            app_name: appName,
            role,
            name,
            description: desc,
            x: Number(pos[0] || 0),
            y: Number(pos[1] || 0),
            width: Number(size[0] || 0),
            height: Number(size[1] || 0),
          });
        }
      }catch(e){}
      try{
        const nested = el.uiElements();
        const out = walk(nested);
        if (out) return out;
      }catch(e){}
    }
    return "";
  }
  try{
    const wins = proc.windows();
    if (!wins.length || windowIndex >= wins.length) return "";
    return walk(wins[windowIndex].uiElements()) || "";
  }catch(e){
    return "";
  }
}
"""
        try:
            result = subprocess.run(
                ["osascript", "-l", "JavaScript", "-e", script, app_name, text, str(window_index)],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError:
            return None
        payload = result.stdout.strip()
        if not payload:
            return None
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return None
        return AccessibilityElementInfo(**data)

    def click_accessibility_element(
        self,
        app_name: str,
        text: str,
        *,
        window_index: int = 0,
    ) -> AccessibilityElementInfo | None:
        info = self.find_accessibility_element(app_name, text, window_index=window_index)
        if info is None:
            return None
        script = """
function run(argv){
  const appName = argv[0];
  const target = argv[1];
  const windowIndex = Number(argv[2] || "0");
  const se = Application("System Events");
  const proc = se.processes.byName(appName);
  function matches(el){
    try{
      const name = String(el.name() || "");
      const desc = String(el.description() || "");
      return name.includes(target) || desc.includes(target);
    }catch(e){
      return false;
    }
  }
  function walk(elements){
    for (let i = 0; i < elements.length; i += 1){
      const el = elements[i];
      try{
        if (matches(el)){
          try{
            el.actions.byName("AXPress").perform();
            return "clicked";
          }catch(e){}
          try{
            el.click();
            return "clicked";
          }catch(e){}
        }
      }catch(e){}
      try{
        const nested = el.uiElements();
        const out = walk(nested);
        if (out) return out;
      }catch(e){}
    }
    return "";
  }
  try{
    const wins = proc.windows();
    if (!wins.length || windowIndex >= wins.length) return "";
    return walk(wins[windowIndex].uiElements()) || "";
  }catch(e){
    return "";
  }
}
"""
        try:
            result = subprocess.run(
                ["osascript", "-l", "JavaScript", "-e", script, app_name, text, str(window_index)],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError:
            return None
        if result.stdout.strip() != "clicked":
            return None
        time.sleep(0.3)
        return info

    def _quartz_window_list(self, *, on_screen_only: bool = False):
        option = (
            Quartz.kCGWindowListOptionOnScreenOnly
            if on_screen_only
            else Quartz.kCGWindowListOptionAll
        ) | Quartz.kCGWindowListExcludeDesktopElements
        return Quartz.CGWindowListCopyWindowInfo(option, Quartz.kCGNullWindowID)

    def _match_quartz_window(
        self,
        window: WindowInfo,
        *,
        app_name: str,
    ) -> WindowInfo | None:
        window_list = self._quartz_window_list(on_screen_only=True) or self._quartz_window_list(on_screen_only=False)
        if not window_list:
            return None

        target_title = _normalize_title(window.title, app_name)
        candidates: list[tuple[int, WindowInfo]] = []
        for win in window_list:
            owner = str(win.get("kCGWindowOwnerName") or "")
            if owner != app_name:
                continue
            bounds = dict(win.get("kCGWindowBounds") or {})
            width = int(bounds.get("Width", 0) or 0)
            height = int(bounds.get("Height", 0) or 0)
            layer = int(win.get("kCGWindowLayer") or -1)
            if width < 80 or height < 80 or layer != 0:
                continue
            title = str(win.get("kCGWindowName") or "")
            normalized_title = _normalize_title(title, app_name)
            score = abs(width - window.width) + abs(height - window.height)
            score += abs(int(bounds.get("X", 0) or 0) - window.x)
            score += abs(int(bounds.get("Y", 0) or 0) - window.y)
            if target_title and normalized_title == target_title:
                score -= 200
            elif target_title and target_title in normalized_title:
                score -= 100
            sharing_state = int(win.get("kCGWindowSharingState") or 0)
            backend = "region" if sharing_state == 0 else "quartz"
            candidates.append(
                (
                    score,
                    WindowInfo(
                        window_id=int(win.get("kCGWindowNumber") or 0),
                        owner=owner,
                        title=title,
                        x=int(bounds.get("X", 0) or 0),
                        y=int(bounds.get("Y", 0) or 0),
                        width=width,
                        height=height,
                        layer=layer,
                        on_screen=bool(win.get("kCGWindowIsOnscreen") or False),
                        capture_backend=backend,
                    ),
                )
            )

        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0])
        return candidates[0][1]

    def _list_windows_via_accessibility(
        self,
        *,
        app_name: str | None,
        title_contains: str | None,
    ) -> list[WindowInfo]:
        script = """
function run(argv) {
  const targetApp = argv.length ? argv[0] : "";
  const titleFilter = argv.length > 1 ? argv[1].toLowerCase() : "";
  const se = Application("System Events");
  const out = [];
  const processes = targetApp
    ? se.applicationProcesses.whose({name: targetApp})()
    : se.applicationProcesses().filter(p => {
        try { return p.windows().length > 0; } catch (e) { return false; }
      });
  for (const proc of processes) {
    const procName = proc.name();
    const wins = proc.windows();
    for (let i = 0; i < wins.length; i += 1) {
      const w = wins[i];
      try {
        const title = String(w.name() || "");
        if (titleFilter && !title.toLowerCase().includes(titleFilter)) {
          continue;
        }
        const pos = w.position();
        const size = w.size();
        out.push({
          window_id: -1 * (out.length + 1),
          owner: procName,
          title: title,
          x: Number(pos[0]),
          y: Number(pos[1]),
          width: Number(size[0]),
          height: Number(size[1]),
          layer: 0,
          on_screen: true,
          capture_backend: "region"
        });
      } catch (e) {}
    }
  }
  return JSON.stringify(out);
}
"""
        try:
            result = subprocess.run(
                ["osascript", "-l", "JavaScript", "-e", script, app_name or "", title_contains or ""],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError:
            return []
        payload = result.stdout.strip()
        if not payload:
            return []
        try:
            items = json.loads(payload)
        except json.JSONDecodeError:
            return []
        windows = [WindowInfo(**item) for item in items]
        unique: dict[tuple, WindowInfo] = {}
        for window in windows:
            key = (
                window.owner,
                window.title,
                window.x,
                window.y,
                window.width,
                window.height,
                window.capture_backend,
            )
            unique.setdefault(key, window)
        windows = list(unique.values())
        windows.sort(key=lambda item: item.width * item.height, reverse=True)
        return windows

    def capture_display(self, display_id: int) -> Image.Image:
        with _autorelease_pool():
            return _cgimage_to_pil(Quartz.CGDisplayCreateImage(display_id))

    def capture_region(self, x: int, y: int, width: int, height: int) -> Image.Image:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            path = tmp.name
        try:
            subprocess.run(
                ["screencapture", "-x", "-R", f"{x},{y},{width},{height}", path],
                check=True,
            )
            return Image.open(path).convert("RGBA")
        finally:
            subprocess.run(["rm", "-f", path], check=False)

    def capture_window(self, window_id: int) -> Image.Image:
        with _autorelease_pool():
            cg_image = Quartz.CGWindowListCreateImage(
                Quartz.CGRectNull,
                Quartz.kCGWindowListOptionIncludingWindow,
                window_id,
                Quartz.kCGWindowImageBoundsIgnoreFraming,
            )
            return _cgimage_to_pil(cg_image)

    def capture_window_info(self, window: WindowInfo) -> Image.Image:
        if window.capture_backend == "region":
            return self.capture_region(window.x, window.y, window.width, window.height)
        try:
            return self.capture_window(window.window_id)
        except RuntimeError:
            return self.capture_region(window.x, window.y, window.width, window.height)

    def open_app(self, app_name: str) -> None:
        subprocess.run(["open", "-a", app_name], check=True)
        time.sleep(0.4)

    def activate_app(self, app_name: str) -> None:
        self.open_app(app_name)
        subprocess.run(
            ["osascript", "-e", f'tell application "{_escape_applescript(app_name)}" to activate'],
            check=True,
        )
        time.sleep(0.3)

    def open_url(self, url: str, *, browser: str = "Google Chrome") -> None:
        subprocess.run(["open", "-a", browser, url], check=True)
        time.sleep(0.5)

    def display_notification(self, title: str, message: str, *, subtitle: str = "") -> None:
        script = (
            f'display notification "{_escape_applescript(message)}" '
            f'with title "{_escape_applescript(title)}"'
        )
        if subtitle:
            script += f' subtitle "{_escape_applescript(subtitle)}"'
        subprocess.run(["osascript", "-e", script], check=False)

    def keystroke(self, key: str, *, modifiers: list[str] | None = None) -> None:
        mods = modifiers or []
        using_clause = ""
        if mods:
            using_clause = " using {" + ", ".join(f"{mod} down" for mod in mods) + "}"
        script = (
            'tell application "System Events" to keystroke '
            f'"{_escape_applescript(key)}"{using_clause}'
        )
        subprocess.run(["osascript", "-e", script], check=True)
        time.sleep(0.15)

    def press_key(self, key_name: str) -> None:
        key_codes = {
            "enter": 36,
            "return": 36,
            "tab": 48,
            "space": 49,
            "escape": 53,
            "esc": 53,
            "home": 115,
            "pageup": 116,
            "page_up": 116,
            "end": 119,
            "pagedown": 121,
            "page_down": 121,
            "left": 123,
            "right": 124,
            "down": 125,
            "up": 126,
        }
        if key_name.lower() not in key_codes:
            raise ValueError(f"Unsupported special key: {key_name}")
        script = (
            'tell application "System Events" to key code '
            f"{key_codes[key_name.lower()]}"
        )
        subprocess.run(["osascript", "-e", script], check=True)
        time.sleep(0.15)

    def hotkey(self, *keys: str) -> None:
        if len(keys) < 2:
            raise ValueError("hotkey requires modifiers followed by the key")
        modifiers = list(keys[:-1])
        key = keys[-1]
        self.keystroke(key, modifiers=modifiers)

    def _to_quartz_point(self, x: int, y: int) -> tuple[float, float]:
        displays = self.list_displays()
        main = next((item for item in displays if item.is_main), None)
        if main is None:
            return float(x), float(y)
        return float(x), float(main.height - y)

    def click(self, x: int, y: int, *, button: str = "left", clicks: int = 1) -> None:
        pyautogui = self._pyautogui()
        if pyautogui is not None:
            pyautogui.click(x=x, y=y, clicks=max(clicks, 1), button=button)
            time.sleep(0.1)
            return

        button_map = {
            "left": Quartz.kCGMouseButtonLeft,
            "right": Quartz.kCGMouseButtonRight,
        }
        down_map = {
            "left": Quartz.kCGEventLeftMouseDown,
            "right": Quartz.kCGEventRightMouseDown,
        }
        up_map = {
            "left": Quartz.kCGEventLeftMouseUp,
            "right": Quartz.kCGEventRightMouseUp,
        }
        if button not in button_map:
            raise ValueError(f"Unsupported button: {button}")
        point = self._to_quartz_point(x, y)
        for _ in range(max(clicks, 1)):
            down = Quartz.CGEventCreateMouseEvent(None, down_map[button], point, button_map[button])
            up = Quartz.CGEventCreateMouseEvent(None, up_map[button], point, button_map[button])
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)
            time.sleep(0.05)
        time.sleep(0.1)

    def move_mouse(self, x: int, y: int) -> None:
        pyautogui = self._pyautogui()
        if pyautogui is not None:
            pyautogui.moveTo(x=x, y=y, duration=0.05)
            return

        point = self._to_quartz_point(x, y)
        move = Quartz.CGEventCreateMouseEvent(
            None,
            Quartz.kCGEventMouseMoved,
            point,
            Quartz.kCGMouseButtonLeft,
        )
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, move)
        time.sleep(0.05)

    def scroll(self, line_delta: int, *, x: int | None = None, y: int | None = None, repeats: int = 1) -> None:
        """Send a mouse-wheel event.

        Positive ``line_delta`` scrolls upward to reveal older content.
        If ``x``/``y`` are provided, move the pointer there first so the
        target scroll view captures the wheel event.
        """
        if x is not None and y is not None:
            self.move_mouse(x, y)

        pyautogui = self._pyautogui()
        if pyautogui is not None:
            for _ in range(max(repeats, 1)):
                pyautogui.scroll(line_delta, x=x, y=y)
                time.sleep(0.05)
            return

        for _ in range(max(repeats, 1)):
            event = Quartz.CGEventCreateScrollWheelEvent(
                None,
                Quartz.kCGScrollEventUnitLine,
                1,
                int(line_delta),
            )
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
            time.sleep(0.05)

    def paste_text(self, text: str) -> None:
        subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)
        self.hotkey("command", "v")
