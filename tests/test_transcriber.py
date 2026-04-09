import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from flowlens.perception.transcriber import WhisperTranscriber


class WhisperTranscriberTest(unittest.TestCase):
    def test_mlx_repo_id_uses_mlx_backend(self) -> None:
        transcriber = WhisperTranscriber(model="mlx-community/whisper-base-asr-fp16")
        self.assertEqual(transcriber.backend, "mlx-whisper")
        self.assertEqual(transcriber.model_name, "mlx-community/whisper-base-mlx")
        self.assertIsNone(transcriber.whisper_cli)
        self.assertIsNone(transcriber.model_path)

    def test_local_mlx_dir_uses_mlx_backend(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config.json").write_text("{}")
            (root / "model.safetensors").write_bytes(b"test")
            transcriber = WhisperTranscriber(model=str(root))
            self.assertEqual(transcriber.backend, "mlx-whisper")

    def test_whisper_cpp_backend_uses_cli_and_models_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cli = root / "whisper-cli"
            cli.write_text("")
            cli.chmod(0o755)
            models = root / "models"
            models.mkdir()
            (models / "ggml-base.bin").write_bytes(b"test")

            with mock.patch("flowlens.perception.transcriber.find_whisper_cli", return_value=cli):
                with mock.patch("flowlens.perception.transcriber.find_whisper_models_dir", return_value=models):
                    transcriber = WhisperTranscriber(model="base")

            self.assertEqual(transcriber.backend, "whisper.cpp")
            self.assertEqual(transcriber.whisper_cli, cli)
            self.assertEqual(transcriber.model_path, models / "ggml-base.bin")

    def test_mlx_transcribe_audio_returns_text_field(self) -> None:
        transcriber = WhisperTranscriber(model="mlx-community/whisper-base-asr-fp16")
        with mock.patch("mlx_whisper.transcribe", return_value={"text": " hello "}):
            text = asyncio.run(transcriber.transcribe_audio("/tmp/fake.wav", "zh"))
        self.assertEqual(text, "hello")


if __name__ == "__main__":
    unittest.main()
