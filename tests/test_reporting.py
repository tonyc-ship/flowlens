from __future__ import annotations

import unittest

from clawvision.reporting import render_markdown, render_markdown_block


class ReportingMarkdownTests(unittest.TestCase):
    def test_render_markdown_supports_core_blocks(self) -> None:
        markdown = """# Title

- alpha
- beta

`inline`

```python
print("ok")
```
"""
        html = render_markdown(markdown)
        self.assertIn("<h1>Title</h1>", html)
        self.assertIn("<ul><li>alpha</li><li>beta</li></ul>", html)
        self.assertIn("<code>inline</code>", html)
        self.assertIn("<pre><code>print(&quot;ok&quot;)</code></pre>", html)

    def test_render_markdown_block_wraps_md_content(self) -> None:
        html = render_markdown_block("**hello**", "vision")
        self.assertIn("class='md-content vision'", html)
        self.assertIn("<strong>hello</strong>", html)


if __name__ == "__main__":
    unittest.main()
