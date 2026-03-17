"""Video audio transcription using local whisper.cpp.

Extracts audio from video files, transcribes with whisper.cpp,
and optionally summarizes with LLM.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path


class WhisperTranscriber:
    """Audio transcription using local whisper.cpp."""

    WHISPER_CLI = "/Users/tonychong/whisper.cpp/build/bin/whisper-cli"
    MODELS_DIR = "/Users/tonychong/whisper.cpp/models"

    def __init__(self, model: str = "large-v3-turbo"):
        self.model_path = f"{self.MODELS_DIR}/ggml-{model}.bin"
        if not Path(self.model_path).exists():
            raise FileNotFoundError(f"Whisper model not found: {self.model_path}")

    async def extract_audio(self, video_path: str, output_path: str | None = None) -> str:
        """Extract audio from video to WAV (16kHz mono, required by whisper)."""
        if output_path is None:
            output_path = tempfile.mktemp(suffix=".wav")

        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-i", video_path,
            "-ar", "16000", "-ac", "1", "-f", "wav",
            output_path, "-y",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg failed: {stderr.decode()[:200]}")
        return output_path

    async def transcribe_audio(self, audio_path: str, language: str = "zh") -> str:
        """Transcribe audio file with whisper.cpp. Returns text."""
        proc = await asyncio.create_subprocess_exec(
            self.WHISPER_CLI,
            "-m", self.model_path,
            "-f", audio_path,
            "-l", language,
            "--no-timestamps",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"whisper-cli failed: {stderr.decode()[:200]}")

        # whisper-cli outputs text to stdout, filter out log lines
        lines = stdout.decode().strip().split("\n")
        text_lines = [l for l in lines if not l.startswith("[") and l.strip()]
        return "\n".join(text_lines).strip()

    async def transcribe_video(self, video_path: str, language: str = "zh") -> str:
        """Extract audio from video and transcribe. Returns text."""
        audio_path = await self.extract_audio(video_path)
        try:
            return await self.transcribe_audio(audio_path, language)
        finally:
            Path(audio_path).unlink(missing_ok=True)

    async def download_and_transcribe(self, video_url: str, language: str = "zh") -> str:
        """Download video from URL, extract audio, transcribe."""
        import urllib.request

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            video_path = f.name

        try:
            req = urllib.request.Request(video_url, headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://www.xiaohongshu.com/",
            })
            with urllib.request.urlopen(req, timeout=60) as resp:
                with open(video_path, "wb") as f:
                    f.write(resp.read())

            return await self.transcribe_video(video_path, language)
        finally:
            Path(video_path).unlink(missing_ok=True)
