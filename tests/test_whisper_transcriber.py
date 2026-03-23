import unittest

from clawvision.vision.transcriber import WhisperTranscriber


class WhisperTranscriberTests(unittest.TestCase):
    def test_build_ffmpeg_input_args_for_remote_url_with_headers(self):
        args = WhisperTranscriber.build_ffmpeg_input_args(
            "https://sns-video.xiaohongshu.com/example/video.mp4",
            referer="https://www.xiaohongshu.com/",
        )

        self.assertEqual(args[0], "-headers")
        self.assertIn("Referer: https://www.xiaohongshu.com/", args[1])
        self.assertIn("User-Agent: Mozilla/5.0", args[1])
        self.assertEqual(args[-2:], ["-i", "https://sns-video.xiaohongshu.com/example/video.mp4"])

    def test_build_ffmpeg_input_args_for_local_file(self):
        args = WhisperTranscriber.build_ffmpeg_input_args("/tmp/sample.mp4")
        self.assertEqual(args, ["-i", "/tmp/sample.mp4"])

    def test_compute_frame_interval(self):
        self.assertEqual(WhisperTranscriber.compute_frame_interval(60, 4), 15)
        self.assertEqual(WhisperTranscriber.compute_frame_interval(10, 20), 1)


if __name__ == "__main__":
    unittest.main()
