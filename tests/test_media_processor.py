import unittest
from unittest import mock

from flowlens.perception.local_llm import (
    DEFAULT_LOCAL_IMAGE_MAX_DIM,
    DEFAULT_LOCAL_MODEL,
    DEFAULT_UI_TARS_MODEL,
)
from flowlens.perception.media import MediaConfig, MediaProcessor


class MediaProcessorTest(unittest.TestCase):
    def test_local_backend_defaults_to_default_local_model(self) -> None:
        processor = MediaProcessor(MediaConfig(backend="qwen-local"))

        with mock.patch("flowlens.perception.local_llm.LocalLLM") as local_llm_cls:
            processor.local_llm

        local_llm_cls.assert_called_once_with(
            DEFAULT_LOCAL_MODEL,
            max_image_dim=DEFAULT_LOCAL_IMAGE_MAX_DIM,
        )

    def test_local_backend_preserves_explicit_local_model(self) -> None:
        processor = MediaProcessor(MediaConfig(backend="qwen-local", model="Qwen3.5-2B-6bit"))

        with mock.patch("flowlens.perception.local_llm.LocalLLM") as local_llm_cls:
            processor.local_llm

        local_llm_cls.assert_called_once_with(
            "Qwen3.5-2B-6bit",
            max_image_dim=DEFAULT_LOCAL_IMAGE_MAX_DIM,
        )

    def test_ui_tars_backend_defaults_to_ui_tars_model(self) -> None:
        processor = MediaProcessor(MediaConfig(backend="ui-tars-local"))

        with mock.patch("flowlens.perception.local_llm.LocalLLM") as local_llm_cls:
            processor.local_llm

        local_llm_cls.assert_called_once_with(
            DEFAULT_UI_TARS_MODEL,
            max_image_dim=DEFAULT_LOCAL_IMAGE_MAX_DIM,
        )

    def test_local_backend_passes_explicit_image_max_dim(self) -> None:
        processor = MediaProcessor(
            MediaConfig(
                backend="qwen-local",
                model="Qwen3.5-0.8B-8bit",
                local_image_max_dim=896,
            )
        )

        with mock.patch("flowlens.perception.local_llm.LocalLLM") as local_llm_cls:
            processor.local_llm

        local_llm_cls.assert_called_once_with(
            "Qwen3.5-0.8B-8bit",
            max_image_dim=896,
        )


if __name__ == "__main__":
    unittest.main()
