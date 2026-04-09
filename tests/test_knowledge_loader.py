import unittest

from flowlens.knowledge.loader import get_knowledge_for_url


class KnowledgeLoaderTest(unittest.TestCase):
    def test_xhs_search_stage_loads_only_search_relevant_sections(self) -> None:
        text = get_knowledge_for_url(
            "https://www.xiaohongshu.com/explore",
            page_state="homepage",
        )

        self.assertIn("Homepage (首页)", text)
        self.assertIn("Search Results (搜索结果)", text)
        self.assertNotIn("Note Detail (笔记详情)", text)
        self.assertNotIn("Reporting Guidelines", text)

    def test_xhs_note_stage_includes_note_and_reporting_sections(self) -> None:
        text = get_knowledge_for_url(
            "https://www.xiaohongshu.com/search_result?keyword=test",
            page_state="note_detail",
        )

        self.assertIn("Note Detail (笔记详情)", text)
        self.assertIn("Reporting Guidelines", text)
        self.assertNotIn("Homepage (首页)", text)


if __name__ == "__main__":
    unittest.main()
