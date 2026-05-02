"""Video audio transcription using local whisper.cpp or MLX Whisper.

Extracts audio from video files, transcribes with whisper.cpp or mlx-whisper,
and optionally summarizes with LLM.
"""

from __future__ import annotations

import asyncio
import math
import tempfile
from pathlib import Path

from ..core.runtime import find_whisper_cli, find_whisper_models_dir

_MLX_WHISPER_MODEL_ALIASES = {
    "mlx-community/whisper-base-asr-fp16": "mlx-community/whisper-base-mlx",
}


class WhisperTranscriber:
    """Audio transcription using local whisper.cpp or mlx-whisper."""

    def __init__(
        self,
        model: str = "large-v3-turbo",
        whisper_cli: str | None = None,
        models_dir: str | None = None,
    ):
        self.requested_model = model
        self.model_name = self._resolve_model_name(model)
        self.backend = "mlx-whisper" if self._is_mlx_model(self.model_name) else "whisper.cpp"
        self.whisper_cli = None
        self.models_dir = None
        self.model_path = None

        if self.backend == "mlx-whisper":
            try:
                import mlx_whisper  # noqa: F401
            except ImportError as exc:
                raise FileNotFoundError(
                    "mlx-whisper not installed. Run `python -m pip install mlx-whisper`."
                ) from exc
            return

        self.whisper_cli = find_whisper_cli(whisper_cli)
        self.models_dir = find_whisper_models_dir(models_dir)
        self.model_path = self.models_dir / f"ggml-{model}.bin"

        if self.whisper_cli is None:
            raise FileNotFoundError(
                "whisper-cli not found. Set FLOWLENS_WHISPER_CLI or install whisper.cpp."
            )
        if not self.model_path.exists():
            raise FileNotFoundError(f"Whisper model not found: {self.model_path}")

    @staticmethod
    def _resolve_model_name(model: str) -> str:
        name = str(model or "").strip()
        return _MLX_WHISPER_MODEL_ALIASES.get(name, name)

    @staticmethod
    def _is_mlx_model(model: str) -> bool:
        name = str(model or "").strip()
        if not name:
            return False
        local = Path(name).expanduser()
        if local.exists() and local.is_dir():
            return (local / "config.json").exists() and (
                (local / "weights.safetensors").exists()
                or (local / "weights.npz").exists()
                or (local / "model.safetensors").exists()
            )
        return "/" in name

    @staticmethod
    def build_ffmpeg_input_args(
        source: str,
        referer: str = "",
        user_agent: str = "Mozilla/5.0",
        stall_timeout_s: float = 30.0,
    ) -> list[str]:
        """Build ffmpeg input args for local files or remote media URLs.

        ``stall_timeout_s`` bounds how long ffmpeg will wait on a stalled
        HTTP(S) socket before aborting. It guards against CDN connections
        that stay ESTABLISHED but stop delivering bytes.
        """
        args: list[str] = []
        if source.startswith(("https://", "http://")):
            if stall_timeout_s and stall_timeout_s > 0:
                us = str(int(stall_timeout_s * 1_000_000))
                # rw_timeout is the generic libav socket read/write timeout;
                # HTTP also honours `-timeout` for initial connect/read.
                args.extend(["-rw_timeout", us, "-timeout", us])
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
            output_dir = tempfile.mkdtemp(prefix="flowlens_frames_")

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
        """Transcribe audio file with whisper.cpp or mlx-whisper. Returns text."""
        if self.backend == "mlx-whisper":
            return await asyncio.wait_for(
                asyncio.to_thread(self._transcribe_audio_mlx, audio_path, language),
                timeout=timeout_s,
            )

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

    def _transcribe_audio_mlx(self, audio_path: str, language: str) -> str:
        import mlx_whisper

        result = mlx_whisper.transcribe(
            audio_path,
            path_or_hf_repo=self.model_name,
            verbose=False,
            language=language,
            task="transcribe",
            condition_on_previous_text=False,
            word_timestamps=False,
        )
        if isinstance(result, dict):
            return str(result.get("text", "")).strip()
        return str(result).strip()

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
