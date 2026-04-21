"""Runtime environment loading tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from flowlens.core import runtime


class RuntimeEnvLoadingTest(unittest.TestCase):
    def test_shell_api_key_exports_are_loaded_but_model_defaults_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env.local"
            shell_file = Path(tmp) / ".zshrc"
            env_file.write_text(
                "\n".join(
                    [
                        "export DASHSCOPE_API_KEY=from-project-env",
                        "export FLOWLENS_WHISPER_CLI=/tmp/project-whisper",
                    ]
                ),
                encoding="utf-8",
            )
            shell_file.write_text(
                "\n".join(
                    [
                        "export DASHSCOPE_API_KEY=from-shell",
                        "export FLOWLENS_MODEL_PROVIDER=kimi",
                        "export FLOWLENS_QWEN_MODEL=qwen-env-model",
                    ]
                ),
                encoding="utf-8",
            )

            with (
                mock.patch("flowlens.core.runtime.LOCAL_ENV_FILES", (env_file,)),
                mock.patch("flowlens.core.runtime.SHELL_EXPORT_FILES", (shell_file,)),
                mock.patch("flowlens.core.runtime._LOADED", False),
                mock.patch.dict("os.environ", {}, clear=True),
            ):
                runtime.load_runtime_env()

                self.assertEqual(runtime.os.environ.get("FLOWLENS_WHISPER_CLI"), "/tmp/project-whisper")
                self.assertEqual(runtime.os.environ.get("DASHSCOPE_API_KEY"), "from-shell")
                self.assertNotIn("FLOWLENS_MODEL_PROVIDER", runtime.os.environ)
                self.assertNotIn("FLOWLENS_QWEN_MODEL", runtime.os.environ)

    def test_task_runs_root_defaults_to_repo_root(self) -> None:
        expected = (runtime.PROJECT_ROOT / "task_runs").resolve(strict=False)
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(runtime.task_runs_root(), expected)

    def test_task_runs_root_relative_override_is_repo_anchored(self) -> None:
        expected = (runtime.PROJECT_ROOT / "custom_runs").resolve(strict=False)
        with mock.patch.dict("os.environ", {"FLOWLENS_TASK_RUNS_DIR": "custom_runs"}, clear=True):
            self.assertEqual(runtime.task_runs_root(), expected)


if __name__ == "__main__":
    unittest.main()
