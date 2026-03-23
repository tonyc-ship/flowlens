"""Runtime environment helpers for local development machines.

Loads optional local env files and a small set of shell exports so the
agent can run without hard-coding machine-specific paths into modules.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOCAL_ENV_FILES = (
    PROJECT_ROOT / ".env.local",
    PROJECT_ROOT / ".env",
)
SHELL_EXPORT_FILES = (
    Path.home() / ".zshrc.pre-oh-my-zsh",
    Path.home() / ".zshrc",
)
RUNTIME_KEYS = (
    "ANTHROPIC_API_KEY",
    "CLAWVISION_WHISPER_CLI",
    "CLAWVISION_WHISPER_MODELS_DIR",
)

_LOADED = False


def _parse_assignment(raw: str) -> tuple[str, str] | None:
    line = raw.strip()
    if not line or line.startswith("#"):
        return None
    if line.startswith("export "):
        line = line[len("export "):].strip()
    if "=" not in line:
        return None
    key, value = line.split("=", 1)
    key = key.strip()
    value = value.split("#", 1)[0].strip()
    if not key:
        return None
    if value[:1] == value[-1:] and value[:1] in {"'", '"'}:
        value = value[1:-1]
    return key, value


def load_runtime_env() -> None:
    """Load local runtime env once.

    Precedence:
    1. Existing process env
    2. `.env.local`
    3. `.env`
    4. Selected `export KEY=...` lines from shell config files
    """

    global _LOADED
    if _LOADED:
        return

    for env_file in LOCAL_ENV_FILES:
        if not env_file.exists():
            continue
        for raw in env_file.read_text(errors="ignore").splitlines():
            parsed = _parse_assignment(raw)
            if not parsed:
                continue
            key, value = parsed
            if key in RUNTIME_KEYS and value and key not in os.environ:
                os.environ[key] = value

    for key in RUNTIME_KEYS:
        if os.environ.get(key):
            continue
        for shell_file in SHELL_EXPORT_FILES:
            if not shell_file.exists():
                continue
            for raw in shell_file.read_text(errors="ignore").splitlines():
                parsed = _parse_assignment(raw)
                if not parsed:
                    continue
                parsed_key, value = parsed
                if parsed_key == key and value:
                    os.environ[key] = value
                    break
            if os.environ.get(key):
                break

    _LOADED = True


def find_whisper_cli(explicit: str | None = None) -> Path | None:
    """Resolve the local whisper-cli binary."""

    load_runtime_env()

    candidates = [
        explicit,
        os.environ.get("CLAWVISION_WHISPER_CLI"),
        shutil.which("whisper-cli"),
        str(Path.home() / "whisper.cpp" / "build" / "bin" / "whisper-cli"),
        "/opt/homebrew/bin/whisper-cli",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        if path.exists():
            return path
    return None


def find_whisper_models_dir(explicit: str | None = None) -> Path:
    """Resolve the local whisper model directory."""

    load_runtime_env()

    candidates = [
        explicit,
        os.environ.get("CLAWVISION_WHISPER_MODELS_DIR"),
        str(Path.home() / "whisper.cpp" / "models"),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        if path.exists():
            return path
    return Path.home() / "whisper.cpp" / "models"
