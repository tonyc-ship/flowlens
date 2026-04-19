"""Site knowledge loader test — page_state gates which YAML sections load."""

import unittest

from flowlens.knowledge.loader import get_knowledge_for_url


class KnowledgeLoaderTest(unittest.TestCase):
    def test_page_state_gates_knowledge_sections(self) -> None:
        homepage = get_knowledge_for_url(
            "https://www.xiaohongshu.com/explore", page_state="homepage",
        )
        self.assertIn("Homepage (首页)", homepage)
        self.assertIn("Search Results (搜索结果)", homepage)
        self.assertNotIn("Note Detail (笔记详情)", homepage)

        note_detail = get_knowledge_for_url(
            "https://www.xiaohongshu.com/search_result?keyword=test",
            page_state="note_detail",
        )
        self.assertIn("Note Detail (笔记详情)", note_detail)
        self.assertIn("Reporting Guidelines", note_detail)
        self.assertNotIn("Homepage (首页)", note_detail)


if __name__ == "__main__":
    unittest.main()
