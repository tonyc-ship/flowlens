"""Video audio transcription using local whisper.cpp.

Extracts audio from video files, transcribes with whisper.cpp,
and optionally summarizes with LLM.
"""

from __future__ import annotations

import asyncio
import math
import tempfile
from pathlib import Path

from ..runtime import find_whisper_cli, find_whisper_models_dir


class WhisperTranscriber:
    """Audio transcription using local whisper.cpp."""

    def __init__(
        self,
        model: str = "large-v3-turbo",
        whisper_cli: str | None = None,
        models_dir: str | None = None,
    ):
        self.whisper_cli = find_whisper_cli(whisper_cli)
        self.models_dir = find_whisper_models_dir(models_dir)
        self.model_path = self.models_dir / f"ggml-{model}.bin"

        if self.whisper_cli is None:
            raise FileNotFoundError(
                "whisper-cli not found. Set CLAWVISION_WHISPER_CLI or install whisper.cpp."
            )
        if not self.model_path.exists():
            raise FileNotFoundError(f"Whisper model not found: {self.model_path}")

    @staticmethod
    def build_ffmpeg_input_args(
        source: str,
        referer: str = "",
        user_agent: str = "Mozilla/5.0",
    ) -> list[str]:
        """Build ffmpeg input args for local files or remote media URLs."""
        args: list[str] = []
        if source.startswith(("https://", "http://")):
            headers: list[str] = []
            if referer:
                headers.append(f"Referer: {referer}")
            if user_agent:
                headers.append(f"User-Agent: {user_agent}")
            if headers:
                args.extend(["-headers", "\r\n".join(headers) + "\r\n"])
        args.extend(["-i", source])
        return args

    @staticmethod
    def compute_frame_interval(max_seconds: int, num_frames: int) -> int:
        safe_seconds = max(1, int(max_seconds or 1))
        safe_frames = max(1, int(num_frames or 1))
        return max(1, math.ceil(safe_seconds / safe_frames))

    @staticmethod
    async def extract_video_frames(
        source: str,
        output_dir: str | None = None,
        *,
        referer: str = "",
        max_seconds: int = 60,
        num_frames: int = 4,
        timeout_s: float = 180,
    ) -> list[str]:
        """Sample representative JPEG frames from a local or remote video."""
        if output_dir is None:
            output_dir = tempfile.mkdtemp(prefix="clawvision_frames_")

        frame_dir = Path(output_dir)
        frame_dir.mkdir(parents=True, exist_ok=True)
        pattern = str(frame_dir / "frame_%02d.jpg")
        interval = WhisperTranscriber.compute_frame_interval(max_seconds, num_frames)
        input_args = WhisperTranscriber.build_ffmpeg_input_args(source, referer=referer)

        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            *input_args,
            "-t", str(max(1, int(max_seconds))),
            "-vf", f"fps=1/{interval}",
            "-frames:v", str(max(1, int(num_frames))),
            "-q:v", "2",
            pattern,
            "-y",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            raise TimeoutError(f"ffmpeg frame extraction timed out after {timeout_s}s")

        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg frame extraction failed: {stderr.decode()[:200]}")

        return [str(path) for path in sorted(frame_dir.glob("frame_*.jpg"))]

    async def extract_audio(
        self,
        source: str,
        output_path: str | None = None,
        *,
        referer: str = "",
        max_seconds: int | None = 90,
        timeout_s: float = 180,
    ) -> str:
        """Extract audio from a local video file or remote video URL."""
        if output_path is None:
            output_path = tempfile.mktemp(suffix=".wav")

        input_args = self.build_ffmpeg_input_args(source, referer=referer)
        ffmpeg_args = [
            "ffmpeg",
            *input_args,
        ]
        if max_seconds is not None and max_seconds > 0:
            ffmpeg_args.extend(["-t", str(max_seconds)])
        ffmpeg_args.extend([
            "-ar", "16000", "-ac", "1", "-f", "wav",
            output_path, "-y",
        ])
        proc = await asyncio.create_subprocess_exec(
            *ffmpeg_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            raise TimeoutError(f"ffmpeg timed out after {timeout_s}s")
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg failed: {stderr.decode()[:200]}")
        return output_path

    async def transcribe_audio(
        self,
        audio_path: str,
        language: str = "zh",
        *,
        timeout_s: float = 300,
    ) -> str:
        """Transcribe audio file with whisper.cpp. Returns text."""
        proc = await asyncio.create_subprocess_exec(
            str(self.whisper_cli),
            "-m", self.model_path,
            "-f", audio_path,
            "-l", language,
            "--no-timestamps",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            raise TimeoutError(f"whisper-cli timed out after {timeout_s}s")
        if proc.returncode != 0:
            raise RuntimeError(f"whisper-cli failed: {stderr.decode()[:200]}")

        # whisper-cli outputs text to stdout, filter out log lines
        lines = stdout.decode().strip().split("\n")
        text_lines = [l for l in lines if not l.startswith("[") and l.strip()]
        return "\n".join(text_lines).strip()

    async def transcribe_video(
        self,
        source: str,
        language: str = "zh",
        *,
        referer: str = "",
        max_audio_seconds: int | None = 90,
        timeout_s: float = 300,
    ) -> str:
        """Extract audio from local or remote video and transcribe."""
        audio_path = await self.extract_audio(
            source,
            referer=referer,
            max_seconds=max_audio_seconds,
            timeout_s=timeout_s,
        )
        try:
            return await self.transcribe_audio(audio_path, language, timeout_s=timeout_s)
        finally:
            Path(audio_path).unlink(missing_ok=True)

    async def download_and_transcribe(
        self,
        video_url: str,
        language: str = "zh",
        *,
        referer: str = "",
        max_audio_seconds: int | None = 90,
        timeout_s: float = 300,
    ) -> str:
        """Transcribe a remote video URL via ffmpeg + whisper.cpp."""
        return await self.transcribe_video(
            video_url,
            language,
            referer=referer,
            max_audio_seconds=max_audio_seconds,
            timeout_s=timeout_s,
        )
