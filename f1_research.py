"""F1上海站品牌营销活动研究报告生成器.

搜索小红书上F1上海站相关的品牌营销内容，
生成一份图文并茂、易于人类阅读的研究报告。
"""

from __future__ import annotations

import base64
import io
import json
import os
import time
from pathlib import Path

from PIL import Image

# Load API key
with open(os.path.expanduser("~/.zshrc.pre-oh-my-zsh")) as f:
    for line in f:
        if "ANTHROPIC_API_KEY" in line and "export" in line:
            os.environ["ANTHROPIC_API_KEY"] = line.strip().split("=", 1)[1]
            break

import anthropic

from clawvision.screen import ScreenController
from clawvision.vision.llm import VisionLLM
from clawvision.vision.ocr import OCREngine

REPORT_DIR = Path("f1_report")
REPORT_DIR.mkdir(exist_ok=True)

screen = ScreenController()
llm = VisionLLM()
ocr = OCREngine(llm)
client = anthropic.Anthropic()


def get_xhs_window():
    windows = screen.find_windows("Google Chrome")
    for w in windows:
        if "小红书" in w.title:
            return w
    return None


def capture_xhs(filename: str) -> tuple[Image.Image, str]:
    w = get_xhs_window()
    if not w:
        raise RuntimeError("XHS window not found")
    img = screen.capture_window(w)
    path = str(REPORT_DIR / filename)
    img.save(path)
    return img, path


def navigate_search(query: str):
    import urllib.parse
    encoded = urllib.parse.quote(query)
    url = f"https://www.xiaohongshu.com/search_result?keyword={encoded}&source=web_search_result_note"
    screen.open_url(url, "Google Chrome")
    time.sleep(4)


def click_note_by_index(note_index: int) -> bool:
    """Click nth note in search results. Returns True if successful."""
    screen.activate_app("Google Chrome")
    time.sleep(0.5)

    img, _ = capture_xhs("_temp_click.png")
    result = llm.locate_element(
        img,
        f"note card number {note_index + 1} (counting from top-left, row by row) in the search results grid"
    )
    if not result or not result.get("found"):
        return False

    w = get_xhs_window()
    click_x = w.x + int(result["x"] / 100 * w.width)
    click_y = w.y + int(result["y"] / 100 * w.height)
    screen.click(click_x, click_y)
    time.sleep(3)
    return True


def image_to_base64_for_report(img_path: str) -> str:
    """Convert image to base64 for embedding in HTML report."""
    with open(img_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def generate_report():
    print("=" * 60)
    print("F1上海站品牌营销活动 — 小红书研究报告")
    print("=" * 60)

    all_data = {}
    screenshots = {}

    # ── Phase 1: Search multiple queries ──
    queries = [
        "F1上海站 品牌营销",
        "F1上海站 赞助商 活动",
        "F1 上海 品牌联名",
    ]

    for i, query in enumerate(queries):
        print(f"\n[Phase 1.{i+1}] Searching: {query}")
        navigate_search(query)

        img, path = capture_xhs(f"search_{i+1}.png")
        screenshots[f"search_{i+1}"] = path

        analysis = llm.analyze_page(img,
            f"This is Xiaohongshu search results for '{query}'.\n"
            "Extract ALL visible note cards with:\n"
            "- title (exact text)\n"
            "- author\n"
            "- like_count (number)\n"
            "- brief content description from the cover image\n"
            "- any brand names mentioned\n"
            "Return as JSON array.",
            max_tokens=2048,
        )
        all_data[query] = analysis
        print(f"  Extracted data for: {query}")

        # Scroll once to get more results
        screen.activate_app("Google Chrome")
        time.sleep(0.3)
        import pyautogui
        pyautogui.scroll(-5)
        time.sleep(2)

        img2, path2 = capture_xhs(f"search_{i+1}_scroll.png")
        screenshots[f"search_{i+1}_scroll"] = path2

        analysis2 = llm.analyze_page(img2,
            f"Continue extracting note cards from this scrolled view of '{query}' results.\n"
            "Same format: title, author, like_count, content description, brand names.\n"
            "Return as JSON array.",
            max_tokens=2048,
        )
        all_data[f"{query}_scroll"] = analysis2

    # ── Phase 2: Open top notes for detailed analysis ──
    print("\n[Phase 2] Opening top notes for detail...")

    # Go back to the first search
    navigate_search("F1上海站 品牌营销")
    time.sleep(2)

    note_details = []
    for note_idx in range(3):  # Top 3 notes
        print(f"  Opening note {note_idx + 1}...")
        if click_note_by_index(note_idx):
            time.sleep(2)
            img, path = capture_xhs(f"note_detail_{note_idx + 1}.png")

            # Apply dynamic layout to extract the exact scrollable content and panel
            from clawvision.vision.layout import JSONLayoutPrior
            layout = JSONLayoutPrior("clawvision/vision/layouts/xiaohongshu.json")
            regions = layout.extract_regions(img)
            
            # Use the "scrollable" content region (post text & comments) to avoid background and media noise
            analyze_img = regions.get("scrollable", regions.get("panel", img))
            analyze_img.save(path) # Overwrite with precise crop
            
            screenshots[f"note_detail_{note_idx + 1}"] = path

            detail = llm.analyze_page(analyze_img,
                "Extract full detail of this Xiaohongshu note:\n"
                "- title\n"
                "- author\n"
                "- full text content (every word visible)\n"
                "- brands/companies mentioned\n"
                "- marketing activities described\n"
                "- likes, favorites, comments count\n"
                "- key visual elements in the images\n"
                "- hashtags\n"
                "Return as structured JSON.",
                max_tokens=2048,
            )
            note_details.append(detail)

            # Close detail (press Escape)
            screen.press_key("escape")
            time.sleep(1)
        else:
            print(f"  Could not click note {note_idx + 1}")

    all_data["note_details"] = note_details

    # ── Phase 3: Generate the research report ──
    print("\n[Phase 3] Generating research report...")

    # Prepare image references for the report
    img_refs = []
    for key, path in screenshots.items():
        img_refs.append(f"- {key}: {path}")
    img_refs_text = "\n".join(img_refs)

    report_prompt = f"""你是一个品牌营销研究分析师。根据以下从小红书搜集到的数据，撰写一份关于"F1上海站品牌营销活动和方案"的研究报告。

## 搜集到的数据

### 搜索结果数据
{json.dumps(all_data, ensure_ascii=False, indent=2, default=str)[:8000]}

### 可用的截图文件
{img_refs_text}

## 报告要求

请用 Markdown 格式写一份专业的研究报告，包含以下章节：

1. **摘要** — 2-3句话概括发现
2. **F1上海站品牌营销概览** — 涉及哪些品牌、什么类型的营销活动
3. **热门品牌营销案例分析** — 从搜集到的笔记中挑选3-5个有代表性的案例，分析其营销策略
4. **用户反馈与互动数据** — 点赞、评论等数据反映了什么
5. **品牌营销趋势洞察** — 总结出的趋势和规律
6. **对品牌方的建议** — 可行的营销建议

格式要求：
- 使用中文撰写
- 每个章节之间用分隔线
- 在报告中引用具体的笔记标题和数据
- 用表格展示对比数据
- 语气专业但易读，像一个有经验的运营分析师写的
- 报告末尾注明数据来源和采集时间

在合适的位置插入图片引用，用这个格式：
![描述](screenshot_filename.png)

比如：![F1上海站品牌营销搜索结果](search_1.png)
"""

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=8192,
        messages=[{"role": "user", "content": report_prompt}],
    )
    report_md = response.content[0].text

    # Save markdown report
    report_path = REPORT_DIR / "F1上海站品牌营销研究报告.md"
    with open(report_path, "w") as f:
        f.write(report_md)
    print(f"  Report saved: {report_path}")

    # Generate HTML version with embedded images
    html = generate_html_report(report_md, screenshots)
    html_path = REPORT_DIR / "F1上海站品牌营销研究报告.html"
    with open(html_path, "w") as f:
        f.write(html)
    print(f"  HTML report saved: {html_path}")

    # Save raw data
    with open(REPORT_DIR / "raw_data.json", "w") as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2, default=str)

    print(f"\n{'=' * 60}")
    print(f"Report complete! Files in {REPORT_DIR}/")
    print(f"  - F1上海站品牌营销研究报告.md")
    print(f"  - F1上海站品牌营销研究报告.html (图文并茂版)")
    print(f"  - raw_data.json")
    print(f"  - {len(screenshots)} screenshots")
    print(f"{'=' * 60}")

    return report_md


def generate_html_report(markdown_text: str, screenshots: dict[str, str]) -> str:
    """Convert markdown report to HTML with embedded images."""

    html_body = markdown_text

    # Replace markdown image syntax with HTML img tags using relative paths
    import re
    for match in re.finditer(r'!\[([^\]]*)\]\(([^)]+)\)', html_body):
        alt, fname = match.group(1), match.group(2)
        # Use just the filename (images are in the same directory as the HTML)
        src = Path(fname).name
        img_tag = (
            f'<div class="screenshot">'
            f'<img src="{src}" alt="{alt}" />'
            f'<p class="caption">{alt}</p></div>'
        )
        html_body = html_body.replace(match.group(0), img_tag)

    # Convert remaining markdown to basic HTML
    lines = html_body.split("\n")
    html_lines = []
    in_table = False
    in_list = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("# "):
            html_lines.append(f"<h1>{stripped[2:]}</h1>")
        elif stripped.startswith("## "):
            html_lines.append(f"<h2>{stripped[3:]}</h2>")
        elif stripped.startswith("### "):
            html_lines.append(f"<h3>{stripped[4:]}</h3>")
        elif stripped.startswith("#### "):
            html_lines.append(f"<h4>{stripped[5:]}</h4>")
        elif stripped.startswith("---"):
            html_lines.append("<hr/>")
        elif stripped.startswith("|"):
            if not in_table:
                html_lines.append("<table>")
                in_table = True
            if all(c in "|-: " for c in stripped):
                continue  # Skip separator rows
            cells = [c.strip() for c in stripped.split("|")[1:-1]]
            row = "".join(f"<td>{c}</td>" for c in cells)
            html_lines.append(f"<tr>{row}</tr>")
        elif in_table and not stripped.startswith("|"):
            html_lines.append("</table>")
            in_table = False
            html_lines.append(f"<p>{stripped}</p>" if stripped else "")
        elif stripped.startswith("- ") or stripped.startswith("* "):
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            html_lines.append(f"<li>{stripped[2:]}</li>")
        elif in_list and not (stripped.startswith("- ") or stripped.startswith("* ")):
            html_lines.append("</ul>")
            in_list = False
            html_lines.append(f"<p>{stripped}</p>" if stripped else "")
        elif stripped.startswith("> "):
            html_lines.append(f"<blockquote>{stripped[2:]}</blockquote>")
        elif stripped:
            # Bold and italic
            import re
            stripped = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', stripped)
            stripped = re.sub(r'\*(.+?)\*', r'<em>\1</em>', stripped)
            stripped = re.sub(r'`(.+?)`', r'<code>\1</code>', stripped)
            html_lines.append(f"<p>{stripped}</p>")
        else:
            html_lines.append("")

    if in_table:
        html_lines.append("</table>")
    if in_list:
        html_lines.append("</ul>")

    body = "\n".join(html_lines)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>F1上海站品牌营销研究报告</title>
<style>
body {{
    font-family: -apple-system, "PingFang SC", "Helvetica Neue", Arial, sans-serif;
    max-width: 900px;
    margin: 0 auto;
    padding: 40px 20px;
    color: #333;
    line-height: 1.8;
    background: #fafafa;
}}
h1 {{
    color: #d32f2f;
    border-bottom: 3px solid #d32f2f;
    padding-bottom: 10px;
    font-size: 28px;
}}
h2 {{
    color: #1a1a1a;
    border-left: 4px solid #d32f2f;
    padding-left: 12px;
    margin-top: 40px;
    font-size: 22px;
}}
h3 {{
    color: #555;
    font-size: 18px;
}}
table {{
    width: 100%;
    border-collapse: collapse;
    margin: 20px 0;
    background: white;
    box-shadow: 0 1px 3px rgba(0,0,0,0.1);
}}
td, th {{
    padding: 12px 16px;
    border: 1px solid #e0e0e0;
    text-align: left;
}}
tr:nth-child(odd) {{ background: #f9f9f9; }}
tr:first-child {{ background: #d32f2f; color: white; font-weight: bold; }}
.screenshot {{
    margin: 24px 0;
    text-align: center;
}}
.screenshot img {{
    max-width: 100%;
    border-radius: 8px;
    box-shadow: 0 4px 12px rgba(0,0,0,0.15);
}}
.caption {{
    font-size: 13px;
    color: #888;
    margin-top: 8px;
    font-style: italic;
}}
blockquote {{
    border-left: 4px solid #d32f2f;
    margin: 16px 0;
    padding: 12px 20px;
    background: #fff5f5;
    color: #555;
}}
hr {{
    border: none;
    border-top: 1px solid #ddd;
    margin: 40px 0;
}}
code {{
    background: #f0f0f0;
    padding: 2px 6px;
    border-radius: 3px;
    font-size: 14px;
}}
ul {{ padding-left: 24px; }}
li {{ margin: 6px 0; }}
strong {{ color: #d32f2f; }}
</style>
</head>
<body>
{body}
</body>
</html>"""


if __name__ == "__main__":
    generate_report()
