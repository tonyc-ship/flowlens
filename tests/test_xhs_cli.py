import unittest

from flowlens.xhs_cli import _normalize_agent_request, build_parser


class XhsCliTests(unittest.TestCase):
    def test_parser_supports_search_command(self):
        args = build_parser().parse_args(["search", "研究露营装备"])
        self.assertEqual(args.command, "search")
        self.assertEqual(args.request, "研究露营装备")

    def test_parser_supports_author_command(self):
        args = build_parser().parse_args(["author", "https://www.xiaohongshu.com/user/profile/abc"])
        self.assertEqual(args.command, "author")
        self.assertEqual(args.url, "https://www.xiaohongshu.com/user/profile/abc")

    def test_agent_request_gets_xhs_prefix(self):
        self.assertEqual(
            _normalize_agent_request("找最近高互动的露营帖子"),
            "在小红书上找最近高互动的露营帖子",
        )

    def test_agent_request_keeps_existing_site_context(self):
        self.assertEqual(
            _normalize_agent_request("在小红书上找最近高互动的露营帖子"),
            "在小红书上找最近高互动的露营帖子",
        )


if __name__ == "__main__":
    unittest.main()
