import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from PIL import Image

from flowlens.perception.llm import VisionLLM, VisionRequestConfig
from flowlens.perception.local_llm import LocalLLM


class VisionRequestConfigTest(unittest.TestCase):
    def test_prepare_image_applies_crop_then_resize(self) -> None:
        llm = VisionLLM(backend="sonnet")
        image = Image.new("RGB", (1280, 1920), "white")
        config = VisionRequestConfig(
            name="crop_resize_test",
            crop_bounds=(0.0, 0.65, 1.0, 1.0),
            max_image_pixels=768,
        )

        prepared = llm._prepare_image(image, config)

        self.assertEqual(prepared.size, (768, 403))

    def test_explicit_local_model_name_is_not_forced_back_to_default(self) -> None:
        llm = VisionLLM(backend="qwen-local")
        with mock.patch("flowlens.perception.local_llm.LocalLLM.is_available", return_value=False):
            local = llm._get_local_llm("mlx-community/Qwen3.5-4B-MLX-4bit")

        self.assertEqual(local.model_name, "mlx-community/Qwen3.5-4B-MLX-4bit")

    def test_local_model_is_available_rejects_partial_download(self) -> None:
        with TemporaryDirectory() as tmp:
            weights_dir = Path(tmp)
            model_dir = weights_dir / "Qwen3.5-4B-MLX-4bit"
            model_dir.mkdir()
            (model_dir / "model.safetensors").write_bytes(b"x" * 1024)
            (model_dir / "model.safetensors.index.json").write_text('{"metadata": {"total_size": 1000000}}')

            with mock.patch("flowlens.perception.local_llm.WEIGHTS_DIR", weights_dir):
                self.assertFalse(LocalLLM.is_available("Qwen3.5-4B-MLX-4bit"))
