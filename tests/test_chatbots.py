import unittest

from clawvision.chatbots.cleanup import parse_orphaned_chrome_processes
from clawvision.chatbots.sites import CHATBOT_SITES
from clawvision.chatbots.vision_profiles import (
    CHATBOT_COMPLEX_FALLBACK_CHECK,
    CHATBOT_INPUT_SIMPLE_CHECK,
    CHATBOT_PAGE_SIMPLE_CHECK,
)


class MultiChatHelpersTest(unittest.TestCase):
    def test_parse_orphaned_chrome_processes_filters_temp_profile_processes(self) -> None:
        output = """
123 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome --user-data-dir=/tmp/browser-use-user-data-dir-abc
456 /Applications/Google Chrome.app/Contents/Frameworks/Google Chrome Helper.app/Contents/MacOS/Google Chrome Helper --user-data-dir=/tmp/browser-use-user-data-dir-abc
789 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome --profile-directory=Default
999 pgrep -fal browser-use-user-data-dir-
""".strip()

        parsed = parse_orphaned_chrome_processes(output)

        self.assertEqual([item["pid"] for item in parsed], [123, 456])
        self.assertTrue(all("browser-use-user-data-dir-" in item["command"] for item in parsed))

    def test_default_chatbot_sites_cover_three_major_chatbots(self) -> None:
        self.assertEqual([site.name for site in CHATBOT_SITES], ["ChatGPT", "Gemini", "Claude"])
        self.assertEqual([site.submit_mode for site in CHATBOT_SITES], ["enter", "enter", "enter"])

    def test_chatbot_vision_profiles_route_simple_and_complex_checks_to_expected_models(self) -> None:
        self.assertEqual(CHATBOT_PAGE_SIMPLE_CHECK.local_model_name, "Qwen3.5-2B-6bit")
        self.assertEqual(CHATBOT_INPUT_SIMPLE_CHECK.local_model_name, "Qwen3.5-2B-6bit")
        self.assertEqual(CHATBOT_COMPLEX_FALLBACK_CHECK.local_model_name, "Qwen3.5-9B-MLX-4bit")
