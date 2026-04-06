import unittest
from unittest import mock

from flowlens.perception.local_llm import DEFAULT_LOCAL_MODEL
from flowlens.perception.media import MediaConfig, MediaProcessor


class MediaProcessorTest(unittest.TestCase):
    def test_local_backend_defaults_to_default_local_model(self) -> None:
        processor = MediaProcessor(MediaConfig(backend="qwen-local"))

        with mock.patch("flowlens.perception.local_llm.LocalLLM") as local_llm_cls:
            processor.local_llm

        local_llm_cls.assert_called_once_with(DEFAULT_LOCAL_MODEL)

    def test_local_backend_preserves_explicit_local_model(self) -> None:
        processor = MediaProcessor(MediaConfig(backend="qwen-local", model="Qwen3.5-2B-6bit"))

        with mock.patch("flowlens.perception.local_llm.LocalLLM") as local_llm_cls:
            processor.local_llm

        local_llm_cls.assert_called_once_with("Qwen3.5-2B-6bit")


if __name__ == "__main__":
    unittest.main()
