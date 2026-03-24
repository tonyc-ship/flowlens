"""Session recorder — captures periodic CDP screenshots into an animated GIF.

Generic infrastructure: works with any bridge that has capture_screenshot().
"""

from __future__ import annotations

import asyncio
import base64
import io
import time
from pathlib import Path

from PIL import Image


class SessionRecorder:
    """Records browser session as periodic screenshots → animated GIF.

    Usage:
        recorder = SessionRecorder(bridge, interval=2.0)
        await recorder.start()
        # ... do work ...
        await recorder.stop()
        recorder.save_gif("session.gif")
    """

    def __init__(self, bridge, interval: float = 2.0, max_frames: int = 600):
        self.bridge = bridge
        self.interval = interval
        self.max_frames = max_frames
        self._frames: list[tuple[float, bytes]] = []  # (timestamp, png_bytes)
        self._task: asyncio.Task | None = None
        self._t0 = 0.0

    @property
    def frame_count(self) -> int:
        return len(self._frames)

    async def start(self) -> None:
        """Start recording in background."""
        self._t0 = time.time()
        self._task = asyncio.create_task(self._record_loop())

    async def stop(self) -> None:
        """Stop recording."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _record_loop(self) -> None:
        while len(self._frames) < self.max_frames:
            try:
                data_url = await self.bridge.capture_screenshot()
                if data_url and "," in data_url:
                    b64 = data_url.split(",", 1)[1]
                    img_bytes = base64.b64decode(b64)
                    # Sanity check: valid image should be at least 1KB
                    if len(img_bytes) > 1024:
                        self._frames.append((time.time() - self._t0, img_bytes))
            except asyncio.CancelledError:
                raise  # Let cancellation propagate
            except Exception:
                pass  # Bridge disconnected, page loading, etc.
            await asyncio.sleep(self.interval)

    def save_gif(self, path: str | Path, fps: float = 1.0, max_width: int = 800) -> Path:
        """Save recorded frames as animated GIF.

        Args:
            path: Output file path.
            fps: Frames per second in output GIF.
            max_width: Resize frames to this width (keeps aspect ratio).

        Returns:
            Path to the saved GIF.
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        if not self._frames:
            return path

        images = []
        failed = 0
        for i, (ts, img_bytes) in enumerate(self._frames):
            try:
                img = Image.open(io.BytesIO(img_bytes))
                # Resize for reasonable GIF size
                if img.width > max_width:
                    ratio = max_width / img.width
                    img = img.resize(
                        (max_width, int(img.height * ratio)),
                        Image.LANCZOS,
                    )
                # Convert to RGB (GIF doesn't support RGBA well)
                if img.mode != "RGB":
                    img = img.convert("RGB")
                images.append(img)
            except Exception as e:
                failed += 1
                if failed <= 3:  # Log first few failures
                    print(f"  [recorder] Frame {i} ({ts:.1f}s) decode failed: {e}, {len(img_bytes)} bytes")
                continue
        if failed:
            print(f"  [recorder] {failed}/{len(self._frames)} frames failed to decode")

        if not images:
            return path

        duration_ms = int(1000 / fps)
        images[0].save(
            path,
            save_all=True,
            append_images=images[1:],
            duration=duration_ms,
            loop=0,
            optimize=True,
        )
        return path

    def summary(self) -> dict:
        """Return recording stats."""
        if not self._frames:
            return {"frames": 0, "duration_s": 0}
        return {
            "frames": len(self._frames),
            "duration_s": round(self._frames[-1][0], 1),
            "interval_s": self.interval,
        }
