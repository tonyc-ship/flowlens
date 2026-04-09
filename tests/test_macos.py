from __future__ import annotations

import contextlib
import unittest
from unittest import mock

from flowlens.debug import macos


class MacOSTest(unittest.TestCase):
    def test_cgimage_to_pil_uses_frombytes(self) -> None:
        cg_image = object()
        copied_data = b"\x00" * 16
        sentinel = object()

        with (
            mock.patch.object(macos.Quartz, "CGImageGetWidth", return_value=2),
            mock.patch.object(macos.Quartz, "CGImageGetHeight", return_value=2),
            mock.patch.object(macos.Quartz, "CGImageGetBytesPerRow", return_value=8),
            mock.patch.object(macos.Quartz, "CGImageGetDataProvider", return_value=object()),
            mock.patch.object(macos.Quartz, "CGDataProviderCopyData", return_value=copied_data),
            mock.patch.object(macos.Image, "frombytes", return_value=sentinel) as frombytes,
        ):
            result = macos._cgimage_to_pil(cg_image)

        self.assertIs(result, sentinel)
        frombytes.assert_called_once()

    def test_capture_display_uses_autorelease_pool(self) -> None:
        controller = macos.MacOSController()
        entered: list[str] = []

        @contextlib.contextmanager
        def fake_pool():
            entered.append("enter")
            try:
                yield
            finally:
                entered.append("exit")

        with (
            mock.patch("flowlens.debug.macos._autorelease_pool", fake_pool),
            mock.patch.object(macos.Quartz, "CGDisplayCreateImage", return_value="cg-image") as create_image,
            mock.patch("flowlens.debug.macos._cgimage_to_pil", return_value="pil-image") as convert,
        ):
            result = controller.capture_display(7)

        self.assertEqual(result, "pil-image")
        self.assertEqual(entered, ["enter", "exit"])
        create_image.assert_called_once_with(7)
        convert.assert_called_once_with("cg-image")

    def test_capture_window_uses_autorelease_pool(self) -> None:
        controller = macos.MacOSController()
        entered: list[str] = []

        @contextlib.contextmanager
        def fake_pool():
            entered.append("enter")
            try:
                yield
            finally:
                entered.append("exit")

        with (
            mock.patch("flowlens.debug.macos._autorelease_pool", fake_pool),
            mock.patch.object(macos.Quartz, "CGWindowListCreateImage", return_value="cg-window") as create_image,
            mock.patch("flowlens.debug.macos._cgimage_to_pil", return_value="pil-window") as convert,
        ):
            result = controller.capture_window(42)

        self.assertEqual(result, "pil-window")
        self.assertEqual(entered, ["enter", "exit"])
        create_image.assert_called_once()
        convert.assert_called_once_with("cg-window")


if __name__ == "__main__":
    unittest.main()
