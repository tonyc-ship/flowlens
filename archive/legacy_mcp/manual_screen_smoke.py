"""Archived smoke script for the legacy screen-level automation helpers."""

from screen import ScreenController


def test_capture_full_screen():
    sc = ScreenController()
    img = sc.capture_full_screen()
    assert img.width > 0 and img.height > 0
    print(f"Captured full screen: {img.width}x{img.height}")


def test_find_chrome_window():
    sc = ScreenController()
    windows = sc.find_windows("Google Chrome")
    print(f"Found {len(windows)} Chrome windows")
    for w in windows:
        print(f"  - {w.title} ({w.width}x{w.height})")


if __name__ == "__main__":
    test_capture_full_screen()
    test_find_chrome_window()
