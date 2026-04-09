"""Observer storage path helpers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ..core.runtime import PROJECT_ROOT, load_runtime_env

LAUNCH_AGENT_LABEL = "com.flowlens.observer"
REPO_ROOT = PROJECT_ROOT.parent if (PROJECT_ROOT / "__init__.py").exists() else PROJECT_ROOT


@dataclass(frozen=True)
class ObserverPaths:
    """Resolved filesystem layout for the Observer subsystem."""

    root: Path
    db_path: Path
    screenshots_dir: Path
    logs_dir: Path
    state_dir: Path

    @classmethod
    def resolve(cls, root: str | Path | None = None) -> "ObserverPaths":
        load_runtime_env()
        if root:
            base = Path(root).expanduser()
        elif os.environ.get("FLOWLENS_OBSERVER_ROOT"):
            base = Path(os.environ["FLOWLENS_OBSERVER_ROOT"]).expanduser()
        elif os.environ.get("FLOWLENS_APP_DATA_DIR"):
            base = Path(os.environ["FLOWLENS_APP_DATA_DIR"]).expanduser() / "observer"
        else:
            base = REPO_ROOT / "observer_data"

        screenshots_dir = base / "screenshots"
        logs_dir = base / "logs"
        state_dir = base / "state"
        screenshots_dir.mkdir(parents=True, exist_ok=True)
        logs_dir.mkdir(parents=True, exist_ok=True)
        state_dir.mkdir(parents=True, exist_ok=True)
        return cls(
            root=base,
            db_path=base / "observer.db",
            screenshots_dir=screenshots_dir,
            logs_dir=logs_dir,
            state_dir=state_dir,
        )

    @property
    def capture_log_path(self) -> Path:
        return self.logs_dir / "capture.log"

    @property
    def capture_error_log_path(self) -> Path:
        return self.logs_dir / "capture.error.log"

    @property
    def resource_monitor_log_path(self) -> Path:
        return self.logs_dir / "resource_monitor.jsonl"

    @property
    def launch_agent_path(self) -> Path:
        return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCH_AGENT_LABEL}.plist"

    @property
    def latest_capture_path(self) -> Path:
        return self.state_dir / "latest_capture.png"
