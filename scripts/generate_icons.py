#!/usr/bin/env python3
"""Generate all app icons from a single source-of-truth PNG.

Usage:
    python scripts/generate_icons.py
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "branding" / "icon_source.png"
CHROME_ICONS = ROOT / "chrome_extension" / "icons"
APP_SRC = ROOT / "app" / "src"
APP_TAURI = ROOT / "app" / "src-tauri"
APP_ICONS = APP_TAURI / "icons"


def render_png(img: Image.Image, size: int, path: Path) -> None:
    resized = img.resize((size, size), Image.Resampling.LANCZOS)
    resized.save(path)
    print(f"wrote {path.relative_to(ROOT)}")


def generate_chrome_icons(img: Image.Image) -> None:
    CHROME_ICONS.mkdir(parents=True, exist_ok=True)
    for size in (16, 32, 48, 128, 512):
        render_png(img, size, CHROME_ICONS / f"icon{size}.png")


def generate_desktop_frontend_icon(img: Image.Image) -> None:
    APP_SRC.mkdir(parents=True, exist_ok=True)
    render_png(img, 128, APP_SRC / "app-icon.png")


def generate_tauri_icons() -> None:
    subprocess.run(
        ["npm", "run", "tauri", "icon", str(SOURCE)],
        cwd=ROOT / "app",
        check=True,
    )

    # Keep only the desktop-relevant derived assets that tauri.conf actually uses.
    for pattern in ("Square*.png", "StoreLogo.png", "64x64.png"):
        for path in APP_ICONS.glob(pattern):
            path.unlink(missing_ok=True)
            print(f"removed {path.relative_to(ROOT)}")

    ios_dir = APP_ICONS / "ios"
    if ios_dir.exists():
        shutil.rmtree(ios_dir)
        print(f"removed {ios_dir.relative_to(ROOT)}")

    android_dir = APP_ICONS / "android"
    if android_dir.exists():
        shutil.rmtree(android_dir)
        print(f"removed {android_dir.relative_to(ROOT)}")


def main() -> None:
    if not SOURCE.exists():
        raise SystemExit(f"Missing source icon: {SOURCE}")

    img = Image.open(SOURCE).convert("RGBA")
    generate_chrome_icons(img)
    generate_desktop_frontend_icon(img)
    generate_tauri_icons()


if __name__ == "__main__":
    main()
