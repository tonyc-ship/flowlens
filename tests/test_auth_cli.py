"""Authentication CLI interaction tests."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from flowlens.auth_cli import _prompt_api_key, _select_default_model, main as auth_main
from flowlens.core.auth import (
    PROVIDERS,
    PROVIDER_KIMI,
    PROVIDER_QWEN,
    default_cloud_model,
    default_model_for_provider,
    preferred_provider,
    provider_status,
)


class AuthCliPromptTest(unittest.TestCase):
    def test_api_key_prompt_uses_visible_input(self) -> None:
        stdin = io.StringIO("sk-test-key\r")
        stderr = io.StringIO()

        with mock.patch("sys.stdin", stdin), mock.patch("sys.stderr", stderr):
            key = _prompt_api_key("Qwen")

        output = stderr.getvalue()
        self.assertEqual(key, "sk-test-key")
        self.assertIn("Qwen API Key:", output)
        self.assertNotIn("Your input will be visible.", output)
        self.assertNotIn("input is hidden", output)

    def test_qwen_display_name_is_english(self) -> None:
        label = PROVIDERS[PROVIDER_QWEN].display_name

        self.assertEqual(label, "Qwen")
        self.assertNotIn("通义", label)
        self.assertNotIn("千问", label)
        with (
            mock.patch("flowlens.core.auth.load_runtime_env", lambda: None),
            mock.patch.dict("os.environ", {}, clear=True),
        ):
            self.assertEqual(default_model_for_provider(PROVIDER_QWEN), "qwen3.6-plus")

    def test_kimi_display_name_is_short(self) -> None:
        self.assertEqual(PROVIDERS[PROVIDER_KIMI].display_name, "Kimi")

    def test_model_subcommand_sets_default_model_and_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            auth_file = Path(tmp) / "auth.json"
            stdout = io.StringIO()
            with (
                mock.patch("flowlens.core.auth.FLOWLENS_DIR", Path(tmp)),
                mock.patch("flowlens.core.auth.FLOWLENS_AUTH_FILE", auth_file),
                mock.patch("flowlens.core.auth.load_runtime_env", lambda: None),
                mock.patch.dict("os.environ", {}, clear=True),
                mock.patch("sys.stdout", stdout),
            ):
                code = auth_main(["model", "qwen", "qwen-vl-test"])
                data = json.loads(auth_file.read_text(encoding="utf-8"))

                self.assertEqual(code, 0)
                self.assertEqual(data["defaults"]["provider"], "qwen")
                self.assertEqual(data["defaults"]["qwen_model"], "qwen-vl-test")
                self.assertEqual(default_cloud_model(), "qwen-vl-test")
                self.assertEqual(preferred_provider(), "qwen")

        output = stdout.getvalue()
        self.assertIn("provider=qwen", output)
        self.assertIn("model=qwen-vl-test", output)

    def test_auth_json_default_is_not_overridden_by_env_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            auth_file = Path(tmp) / "auth.json"
            with (
                mock.patch("flowlens.core.auth.FLOWLENS_DIR", Path(tmp)),
                mock.patch("flowlens.core.auth.FLOWLENS_AUTH_FILE", auth_file),
                mock.patch("flowlens.core.auth.load_runtime_env", lambda: None),
                mock.patch.dict(
                    "os.environ",
                    {
                        "FLOWLENS_MODEL_PROVIDER": "kimi",
                        "FLOWLENS_QWEN_MODEL": "qwen-env-should-not-win",
                    },
                    clear=True,
                ),
                mock.patch("sys.stdout", io.StringIO()),
            ):
                code = auth_main(["model", "qwen", "qwen-vl-test"])

                self.assertEqual(code, 0)
                self.assertEqual(preferred_provider(), "qwen")
                self.assertEqual(default_cloud_model(), "qwen-vl-test")
                self.assertEqual(default_model_for_provider(PROVIDER_QWEN), "qwen-vl-test")

    def test_model_menu_marks_only_global_default_as_current(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            auth_file = Path(tmp) / "auth.json"
            captured_options: list[list[str]] = []

            def fake_pick(prompt: str, options: list[str], *, show_status: str = "") -> int:
                captured_options.append(options)
                return len(options) - 1

            with (
                mock.patch("flowlens.core.auth.FLOWLENS_DIR", Path(tmp)),
                mock.patch("flowlens.core.auth.FLOWLENS_AUTH_FILE", auth_file),
                mock.patch("flowlens.core.auth.load_runtime_env", lambda: None),
                mock.patch.dict("os.environ", {}, clear=True),
                mock.patch("sys.stdout", io.StringIO()),
            ):
                self.assertEqual(auth_main(["model", "kimi", "kimi-k2.5"]), 0)

                with mock.patch("flowlens.auth_cli._pick", fake_pick):
                    self.assertIsNone(_select_default_model("Qwen", PROVIDER_QWEN))
                    qwen_options = captured_options[-1]

                    self.assertIsNone(_select_default_model("Kimi", PROVIDER_KIMI))
                    kimi_options = captured_options[-1]

                self.assertTrue(all("(current)" not in option for option in qwen_options))
                self.assertIn("kimi-k2.5 (current)", kimi_options)

    def test_auth_file_source_uses_actual_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            auth_file = Path(tmp) / "auth.json"
            stdout = io.StringIO()
            with (
                mock.patch("flowlens.core.auth.FLOWLENS_DIR", Path(tmp)),
                mock.patch("flowlens.core.auth.FLOWLENS_AUTH_FILE", auth_file),
                mock.patch("flowlens.core.auth.load_runtime_env", lambda: None),
                mock.patch.dict("os.environ", {}, clear=True),
                mock.patch("sys.stdout", stdout),
            ):
                code = auth_main(["set", "qwen", "api_key", "--value", "sk-test"])
                status = provider_status(PROVIDER_QWEN)

                self.assertEqual(code, 0)
                self.assertTrue(status.api_key_available)
                self.assertEqual(status.api_key_source, str(auth_file))
                self.assertNotEqual(status.api_key_source, "flowlens auth file")


if __name__ == "__main__":
    unittest.main()
