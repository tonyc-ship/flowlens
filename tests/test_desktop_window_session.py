from __future__ import annotations

import unittest
from unittest.mock import patch

from flowlens.core.desktop import DesktopWindowSession
from flowlens.debug.macos import WindowInfo


class _FakeController:
    def __init__(self, *, visible_after: int = 1) -> None:
        self.visible_after = visible_after
        self.best_calls = 0
        self.open_calls = 0

    def best_window_for_app(self, _app_name: str, *, title_contains=None, visible_only: bool = False):
        del title_contains, visible_only
        self.best_calls += 1
        if self.best_calls < self.visible_after:
            return None
        return WindowInfo(
            window_id=1,
            owner="WeChat",
            title="Weixin",
            x=10,
            y=20,
            width=800,
            height=600,
            layer=0,
            on_screen=True,
            capture_backend="region",
        )

    def open_app(self, _app_name: str) -> None:
        self.open_calls += 1


class _FakeOCR:
    pass


class DesktopWindowSessionTests(unittest.TestCase):
    def test_resolve_window_reopens_app_once_when_visible_window_is_delayed(self) -> None:
        controller = _FakeController(visible_after=3)
        session = DesktopWindowSession("WeChat", controller=controller, ocr=_FakeOCR())

        with patch("flowlens.core.desktop.time.sleep", return_value=None):
            window = session.resolve_window(visible_only=True)

        self.assertEqual(window.title, "Weixin")
        self.assertEqual(controller.open_calls, 1)
        self.assertEqual(controller.best_calls, 3)

    def test_resolve_window_does_not_reopen_for_non_visible_lookup(self) -> None:
        controller = _FakeController(visible_after=1)
        session = DesktopWindowSession("WeChat", controller=controller, ocr=_FakeOCR())

        with patch("flowlens.core.desktop.time.sleep", return_value=None):
            window = session.resolve_window(visible_only=False)

        self.assertEqual(window.title, "Weixin")
        self.assertEqual(controller.open_calls, 0)


if __name__ == "__main__":
    unittest.main()
