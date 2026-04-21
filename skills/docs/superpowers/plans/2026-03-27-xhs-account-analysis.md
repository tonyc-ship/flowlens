# XHS 账号分析报表 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 批量爬取 19 个小红书账号最近 10 篇笔记，用 Claude API 分析图文风格，输出 Excel 报表。

**Architecture:** 单文件脚本 `analyze_accounts.py`，拆分为纯函数层（可测试）+ XHS 爬取层 + Claude 分析层 + Excel 写入层，主函数串行编排。复用 `account_manager.py` 的 `get_next_account()` 获取 Cookie。

**Tech Stack:** Python 3.10+, xhs==0.2.13, anthropic, openpyxl, python-dotenv, pytest

---

## 文件结构

| 文件 | 操作 | 职责 |
|------|------|------|
| `requirements.txt` | 修改 | 修正 xhs 版本，新增 openpyxl、anthropic |
| `scripts/analyze_accounts.py` | 新建 | 完整脚本：账号常量 + 纯函数 + 爬取 + 分析 + Excel |
| `tests/test_analyze_accounts.py` | 新建 | 纯函数单元测试 |
| `output/` | 新建目录 | 存放生成的 Excel（gitignore） |
| `.gitignore` | 修改 | 追加 `output/` |

---

### Task 1: 环境准备 + 纯函数（TDD）

**Files:**
- Modify: `requirements.txt`
- Modify: `.gitignore`
- Create: `tests/test_analyze_accounts.py`
- Create: `scripts/analyze_accounts.py`（仅纯函数部分）

- [ ] **Step 1: 修改 requirements.txt**

将 `xhs>=0.4.0` 改为 `xhs==0.2.13`，新增两行：

```
# 小红书发布
xhs==0.2.13

# 账号分析
openpyxl>=3.1.0
anthropic>=0.20.0
```

- [ ] **Step 2: 追加 output/ 到 .gitignore**

在 `.gitignore` 末尾追加：
```
output/
```

- [ ] **Step 3: 安装新依赖**

```bash
pip install xhs==0.2.13 openpyxl anthropic -q
```

期望无报错。

- [ ] **Step 4: 写失败测试 `tests/test_analyze_accounts.py`**

```python
# tests/test_analyze_accounts.py
import json
import pytest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from analyze_accounts import (
    strip_json_fences,
    compute_stats,
    format_image_preference,
    compute_interaction_rate,
    compute_stickiness,
)

# ── strip_json_fences ──────────────────────────────────────────────────────────

def test_strip_json_fences_removes_markdown_block():
    text = '```json\n{"a": 1}\n```'
    assert strip_json_fences(text) == '{"a": 1}'

def test_strip_json_fences_plain_json_unchanged():
    text = '{"a": 1}'
    assert strip_json_fences(text) == '{"a": 1}'

def test_strip_json_fences_strips_whitespace():
    text = '  ```json\n{"a": 1}\n```  '
    assert json.loads(strip_json_fences(text)) == {"a": 1}

# ── compute_stats ──────────────────────────────────────────────────────────────

SAMPLE_NOTES = [
    {"image_count": 3, "word_count": 150, "liked_count": 200, "collected_count": 80, "comment_count": 12},
    {"image_count": 1, "word_count": 80,  "liked_count": 100, "collected_count": 20, "comment_count": 5},
    {"image_count": 5, "word_count": 200, "liked_count": 300, "collected_count": 120, "comment_count": 20},
]

def test_compute_stats_averages():
    stats = compute_stats(SAMPLE_NOTES)
    assert stats["avg_images"] == pytest.approx(3.0)
    assert stats["avg_words"] == 143       # (150+80+200)//3
    assert stats["avg_liked"] == 200       # (200+100+300)//3
    assert stats["avg_collected"] == 73    # (80+20+120)//3
    assert stats["avg_comments"] == 12     # (12+5+20)//3

def test_compute_stats_empty_notes():
    stats = compute_stats([])
    assert stats["avg_images"] == 0
    assert stats["avg_liked"] == 0

# ── format_image_preference ────────────────────────────────────────────────────

def test_format_image_preference_single():
    notes = [{"image_count": 1}] * 5
    result = format_image_preference(notes)
    assert "单图" in result

def test_format_image_preference_multi():
    notes = [{"image_count": 6}] * 5
    result = format_image_preference(notes)
    assert "多图" in result

def test_format_image_preference_mixed():
    notes = [{"image_count": 1}] * 3 + [{"image_count": 5}] * 3
    result = format_image_preference(notes)
    assert "混合" in result

# ── compute_interaction_rate ───────────────────────────────────────────────────

def test_compute_interaction_rate_normal():
    result = compute_interaction_rate(avg_liked=100, avg_collected=50, fans_count=1000)
    assert result == "15.0%"

def test_compute_interaction_rate_zero_fans():
    result = compute_interaction_rate(avg_liked=100, avg_collected=50, fans_count=0)
    assert result == "N/A"

# ── compute_stickiness ─────────────────────────────────────────────────────────

def test_compute_stickiness_normal():
    result = compute_stickiness(avg_collected=40, avg_liked=100)
    assert result == "40.0%"

def test_compute_stickiness_zero_liked():
    result = compute_stickiness(avg_collected=40, avg_liked=0)
    assert result == "N/A"
```

- [ ] **Step 5: 确认测试全部失败**

```bash
cd /Users/chenqinghua/Auto-Redbook-Skills
python -m pytest tests/test_analyze_accounts.py -v 2>&1 | head -20
```

期望：`ImportError: No module named 'analyze_accounts'`

- [ ] **Step 6: 创建 `scripts/analyze_accounts.py`（仅纯函数 + 常量）**

```python
#!/usr/bin/env python3
"""
XHS 账号分析报表生成脚本

运行方式：
    python scripts/analyze_accounts.py

依赖：
    pip install xhs==0.2.13 openpyxl anthropic python-dotenv

输出：
    output/xhs_account_analysis_YYYYMMDD.xlsx
"""

import json
import os
import re
import sys
import time
import random
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    from dotenv import load_dotenv
except ImportError:
    pass

# ── 账号清单（昵称 + 状态）────────────────────────────────────────────────────
# 注意：昵称须与小红书主页完全一致；截断的名称请在运行前核实

ACCOUNTS = [
    {"name": "好运绵绵冰",     "status": "闲置中"},
    {"name": "发呆糕手",       "status": "闲置中"},
    {"name": "Lily学姐（归国求职版）",       "status": "闲置中"},   # 原表截断，请核实全名
    {"name": "offer收割机学长（留子归国求职版）", "status": "闲置中"},
    {"name": "Giselle in uk",  "status": "闲置中"},
    {"name": "知识分子（无小号助理）",        "status": "闲置中"},   # 原表截断，请核实全名
    {"name": "英国求职辛普森",   "status": "闲置中"},
    {"name": "辛普森英国咨询", "status": "闲置中"},
    {"name": "小鹅留英咨询",   "status": "闲置中"},
    {"name": "辛普森学长咨询", "status": "闲置中"},
    {"name": "叹鸭求职咨询",   "status": "闲置中"},
    {"name": "蘑菇蘑菇（无小号助理）",        "status": "正常使用"}, # 原表截断，请核实全名
    {"name": "ada的留英求职",  "status": "正常使用"}, # 原表截断，请核实全名
    {"name": "wikk",           "status": "闲置中"},
    {"name": "offer羊羊",      "status": "未登录"},
    {"name": "小泡芙在英国",   "status": "正常使用"},
    {"name": "小鹅Consulting", "status": "正常使用"},
    {"name": "利娅在英国",     "status": "正常使用"},
    {"name": "Jojo的英国生活", "status": "正常使用"},
]

NOTES_LIMIT = 10  # 每账号分析笔记数


# ── 纯函数层（可单元测试）────────────────────────────────────────────────────

def strip_json_fences(text: str) -> str:
    """去除 Claude 返回的 Markdown 代码块包装（```json ... ```）。"""
    text = text.strip()
    # 匹配 ```json ... ``` 或 ``` ... ```
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if match:
        return match.group(1).strip()
    return text


def compute_stats(notes: list) -> dict:
    """从笔记列表计算统计指标（纯函数）。"""
    if not notes:
        return {
            "avg_images": 0, "avg_words": 0,
            "avg_liked": 0, "avg_collected": 0, "avg_comments": 0,
        }
    n = len(notes)
    return {
        "avg_images":    round(sum(x.get("image_count", 0) for x in notes) / n, 1),
        "avg_words":     sum(x.get("word_count", 0) for x in notes) // n,
        "avg_liked":     sum(x.get("liked_count", 0) for x in notes) // n,
        "avg_collected": sum(x.get("collected_count", 0) for x in notes) // n,
        "avg_comments":  sum(x.get("comment_count", 0) for x in notes) // n,
    }


def format_image_preference(notes: list) -> str:
    """根据图片数量分布生成偏好描述（纯函数）。"""
    if not notes:
        return "无数据"
    counts = [x.get("image_count", 0) for x in notes]
    single = sum(1 for c in counts if c <= 1)
    multi  = len(counts) - single
    avg    = round(sum(counts) / len(counts), 1)
    if single == 0:
        return f"多图为主（均{avg}张）"
    if multi == 0:
        return "单图为主"
    return f"单图/多图混合（均{avg}张）"


def compute_interaction_rate(avg_liked: int, avg_collected: int, fans_count: int) -> str:
    """计算互动率 = (均点赞 + 均收藏) / 粉丝数（纯函数）。"""
    if fans_count == 0:
        return "N/A"
    rate = (avg_liked + avg_collected) / fans_count * 100
    return f"{rate:.1f}%"


def compute_stickiness(avg_collected: int, avg_liked: int) -> str:
    """计算用户粘度 = 均收藏 / 均点赞（纯函数）。"""
    if avg_liked == 0:
        return "N/A"
    rate = avg_collected / avg_liked * 100
    return f"{rate:.1f}%"
```

- [ ] **Step 7: 运行测试，确认全部通过**

```bash
python -m pytest tests/test_analyze_accounts.py -v
```

期望：所有测试 PASS。

- [ ] **Step 8: Commit**

```bash
git add requirements.txt .gitignore scripts/analyze_accounts.py tests/test_analyze_accounts.py
git commit -m "feat: add analyze_accounts.py pure functions with tests"
```

---

### Task 2: XHS 爬取层

**Files:**
- Modify: `scripts/analyze_accounts.py`（追加爬取函数）

- [ ] **Step 1: 在 `scripts/analyze_accounts.py` 末尾追加以下函数**

（在纯函数之后添加）

```python
# ── XHS 爬取层 ────────────────────────────────────────────────────────────────

def _load_cookie() -> str:
    """获取有效 Cookie（优先 account_manager，回退 .env）。"""
    sys.path.insert(0, str(Path(__file__).parent))
    try:
        from account_manager import get_next_account
        cookie, _ = get_next_account(fallback_to_env=True)
        return cookie
    except Exception:
        pass
    # 回退：直接读 .env
    for env_path in [Path.cwd() / ".env", Path(__file__).parent.parent / ".env"]:
        if env_path.exists():
            load_dotenv(env_path)
            break
    cookie = os.getenv("XHS_COOKIE")
    if not cookie:
        print("❌ 未找到有效 Cookie，请确保 accounts.json 中有登录账号或 .env 中有 XHS_COOKIE")
        sys.exit(1)
    return cookie


def _init_client(cookie: str):
    """初始化 XhsClient（含 sign 函数，参考 publish_xhs.py 模式）。"""
    from xhs import XhsClient
    from xhs.help import sign as local_sign

    cookies = {}
    for item in cookie.split(";"):
        item = item.strip()
        if "=" in item:
            k, v = item.split("=", 1)
            cookies[k.strip()] = v.strip()
    a1 = cookies.get("a1", "")

    def sign_func(uri, data=None, a1_param="", web_session=""):
        return local_sign(uri, data, a1=a1 or a1_param)

    return XhsClient(cookie=cookie, sign=sign_func)


def search_user_id(client, nickname: str) -> Optional[str]:
    """搜索昵称，返回第一个匹配用户的 user_id；未找到返回 None。"""
    try:
        result = client.get_user_by_keyword(nickname)
        users = result.get("users", [])
        if not users:
            return None
        return users[0].get("user_id") or users[0].get("id")
    except Exception as e:
        print(f"  ⚠️  搜索 [{nickname}] 失败: {e}")
        return None


def fetch_user_profile(client, user_id: str) -> dict:
    """获取用户主页信息（粉丝数、笔记总数）。"""
    try:
        result = client.get_user_info(user_id)
        basic = result.get("basic_info", result)
        interact = result.get("interact_info", {})
        fans_count = int(interact.get("fans_count", basic.get("fans_count", 0)) or 0)
        # notes_count 在不同版本字段名有差异，尝试多个键
        notes_count = (
            interact.get("note_count")
            or basic.get("notes_count")
            or basic.get("note_count")
            or 0
        )
        return {"fans_count": fans_count, "notes_count": int(notes_count or 0)}
    except Exception as e:
        print(f"  ⚠️  获取用户信息失败: {e}")
        return {"fans_count": 0, "notes_count": 0}


def fetch_notes(client, user_id: str, limit: int = NOTES_LIMIT) -> list:
    """获取用户最新 N 篇笔记的基础信息（不含正文）。"""
    try:
        result = client.get_user_notes(user_id, cursor="")
        raw_notes = result.get("notes", result.get("data", []))
        notes = []
        for note in raw_notes[:limit]:
            interact = note.get("interact_info", {})
            notes.append({
                "note_id":        note.get("note_id") or note.get("id", ""),
                "title":          note.get("title", ""),
                "image_count":    len(note.get("image_list", [])) or note.get("image_count", 0),
                "liked_count":    int(interact.get("liked_count", 0) or 0),
                "collected_count":int(interact.get("collected_count", 0) or 0),
                "comment_count":  int(interact.get("comment_count", 0) or 0),
                "desc":           "",  # 详情页再填充
            })
        return notes
    except Exception as e:
        print(f"  ⚠️  获取笔记列表失败: {e}")
        return []


def fetch_note_detail(client, note: dict) -> dict:
    """补充单篇笔记正文（desc）和图片数，返回更新后的 note dict。"""
    note_id = note.get("note_id", "")
    if not note_id:
        return note
    try:
        result = client.get_note_by_id(note_id)
        note_data = result.get("note", result)
        note["desc"] = note_data.get("desc", "")
        img_list = note_data.get("image_list", [])
        if img_list:
            note["image_count"] = len(img_list)
        note["word_count"] = len(note["title"] + note["desc"])
    except Exception as e:
        print(f"    ⚠️  获取笔记详情 [{note_id}] 失败: {e}")
        note["word_count"] = len(note.get("title", "") + note.get("desc", ""))
    return note
```

- [ ] **Step 2: 验证语法**

```bash
python -c "import ast; ast.parse(open('scripts/analyze_accounts.py').read()); print('语法OK')"
```

- [ ] **Step 3: 运行测试确认不受影响**

```bash
python -m pytest tests/test_analyze_accounts.py -v
```

期望：所有测试仍 PASS。

- [ ] **Step 4: Commit**

```bash
git add scripts/analyze_accounts.py
git commit -m "feat: add XHS fetch layer in analyze_accounts.py"
```

---

### Task 3: Claude 分析层 + Excel 写入层

**Files:**
- Modify: `scripts/analyze_accounts.py`（追加分析 + Excel 函数）

- [ ] **Step 1: 在 `scripts/analyze_accounts.py` 末尾追加以下函数**

```python
# ── Claude 分析层 ─────────────────────────────────────────────────────────────

CLAUDE_PROMPT_TEMPLATE = """你是一名小红书运营分析师。以下是账号「{nickname}」最近{n}篇笔记内容：

{content}

请分析该账号，严格用JSON格式回答（不要加任何额外说明）：
{{
  "图文风格描述": "不超过100字，描述视觉风格、配色偏好、排版习惯、文案语气",
  "内容偏好": "不超过30字，高频话题方向",
  "受众分析": "不超过30字，主要受众群体特征",
  "目标对象关联点": "不超过30字，内容与目标用户的痛点/需求连接",
  "账号权重评级": "A/B/C + 不超过20字理由"
}}"""

DEFAULT_ANALYSIS = {
    "图文风格描述": "分析失败",
    "内容偏好":     "分析失败",
    "受众分析":     "分析失败",
    "目标对象关联点": "分析失败",
    "账号权重评级": "分析失败",
}


def analyze_with_claude(notes: list, nickname: str) -> dict:
    """调用 Claude API 分析笔记风格，返回结构化 dict。"""
    try:
        import anthropic
    except ImportError:
        print("  ⚠️  缺少 anthropic 库，跳过 Claude 分析")
        return DEFAULT_ANALYSIS.copy()

    # 加载 API Key
    for env_path in [Path.cwd() / ".env", Path(__file__).parent.parent / ".env"]:
        if env_path.exists():
            load_dotenv(env_path)
            break
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("  ⚠️  未找到 ANTHROPIC_API_KEY，跳过 Claude 分析")
        return DEFAULT_ANALYSIS.copy()

    # 拼接笔记内容
    parts = []
    for i, note in enumerate(notes, 1):
        title = note.get("title", "")
        desc  = note.get("desc", "")
        parts.append(f"[{i}] 标题：{title}\n正文：{desc[:500]}")  # 每篇正文最多500字
    content = "\n\n".join(parts)

    prompt = CLAUDE_PROMPT_TEMPLATE.format(
        nickname=nickname, n=len(notes), content=content
    )

    try:
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text
        clean = strip_json_fences(raw)
        return json.loads(clean)
    except Exception as e:
        print(f"  ⚠️  Claude 分析失败: {e}")
        return DEFAULT_ANALYSIS.copy()


# ── Excel 写入层 ──────────────────────────────────────────────────────────────

HEADERS = [
    "账号昵称", "状态", "粉丝数", "笔记总数", "分析笔记篇数",
    "平均图片张数", "图片偏好", "平均文字字数",
    "图文风格描述", "内容偏好",
    "平均点赞数", "平均收藏数", "平均评论数",
    "互动率", "用户粘度",
    "受众分析", "目标对象关联点", "账号权重评级",
]


def write_excel(rows: list, output_path: str) -> None:
    """将分析结果写入 Excel 文件。rows 为 dict 列表，key 对应 HEADERS。"""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment

    wb = Workbook()
    ws = wb.active
    ws.title = "账号分析"

    # 表头样式
    header_fill = PatternFill("solid", fgColor="4472C4")
    header_font = Font(bold=True, color="FFFFFF")
    for col, header in enumerate(HEADERS, 1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", wrap_text=True)

    # 数据行
    for row_idx, row in enumerate(rows, 2):
        for col_idx, key in enumerate(HEADERS, 1):
            val = row.get(key, "")
            cell = ws.cell(row=row_idx, column=col_idx, value=val)
            cell.alignment = Alignment(wrap_text=True, vertical="top")

    # 列宽
    col_widths = {
        1: 18, 2: 10, 3: 10, 4: 10, 5: 12,
        6: 12, 7: 20, 8: 12,
        9: 45, 10: 25,     # 图文风格描述宽一些
        11: 12, 12: 12, 13: 12,
        14: 10, 15: 10,
        16: 25, 17: 25, 18: 25,
    }
    for col, width in col_widths.items():
        ws.column_dimensions[ws.cell(row=1, column=col).column_letter].width = width

    # 冻结表头
    ws.freeze_panes = "A2"

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_path)
    print(f"✅ Excel 已保存：{output_path}")
```

- [ ] **Step 2: 在测试文件追加 Excel 写入测试**

在 `tests/test_analyze_accounts.py` 末尾追加：

```python
# ── write_excel ───────────────────────────────────────────────────────────────

def test_write_excel_creates_file(tmp_path):
    from analyze_accounts import write_excel, HEADERS
    rows = [
        {h: f"test_{i}" for i, h in enumerate(HEADERS)}
    ]
    out = str(tmp_path / "test.xlsx")
    write_excel(rows, out)
    assert Path(out).exists()

def test_write_excel_correct_headers(tmp_path):
    from analyze_accounts import write_excel, HEADERS
    from openpyxl import load_workbook
    rows = [{h: "x" for h in HEADERS}]
    out = str(tmp_path / "test.xlsx")
    write_excel(rows, out)
    wb = load_workbook(out)
    ws = wb.active
    actual_headers = [ws.cell(row=1, column=c).value for c in range(1, len(HEADERS)+1)]
    assert actual_headers == HEADERS

def test_write_excel_data_row(tmp_path):
    from analyze_accounts import write_excel, HEADERS
    from openpyxl import load_workbook
    row = {h: f"val_{i}" for i, h in enumerate(HEADERS)}
    out = str(tmp_path / "test.xlsx")
    write_excel([row], out)
    wb = load_workbook(out)
    ws = wb.active
    assert ws.cell(row=2, column=1).value == "val_0"
```

- [ ] **Step 3: 运行全量测试**

```bash
python -m pytest tests/test_analyze_accounts.py -v
```

期望：所有测试 PASS（含新增 3 个 Excel 测试）。

- [ ] **Step 4: 验证语法**

```bash
python -c "import ast; ast.parse(open('scripts/analyze_accounts.py').read()); print('语法OK')"
```

- [ ] **Step 5: Commit**

```bash
git add scripts/analyze_accounts.py tests/test_analyze_accounts.py
git commit -m "feat: add Claude analyzer and Excel writer to analyze_accounts.py"
```

---

### Task 4: 主函数（main）+ 端到端冒烟测试

**Files:**
- Modify: `scripts/analyze_accounts.py`（追加 main 函数）

- [ ] **Step 1: 在 `scripts/analyze_accounts.py` 末尾追加 main 函数**

```python
# ── 主编排函数 ─────────────────────────────────────────────────────────────────

def process_account(client, account: dict) -> dict:
    """处理单个账号，返回 Excel 行 dict。"""
    nickname = account["name"]
    status   = account["status"]
    print(f"\n[{nickname}] 开始处理...")

    row = {h: "N/A" for h in HEADERS}
    row["账号昵称"] = nickname
    row["状态"]     = status

    # 1. 搜索用户
    user_id = search_user_id(client, nickname)
    if not user_id:
        row["账号权重评级"] = "未找到账号"
        print(f"  ⚠️  未找到账号，跳过")
        return row

    # 2. 用户主页信息
    profile = fetch_user_profile(client, user_id)
    row["粉丝数"]   = profile["fans_count"]
    row["笔记总数"] = profile["notes_count"]

    # 3. 笔记列表
    notes = fetch_notes(client, user_id, limit=NOTES_LIMIT)
    if not notes:
        row["分析笔记篇数"] = 0
        row["账号权重评级"] = "无笔记数据"
        print(f"  ⚠️  无笔记数据，跳过")
        return row

    # 4. 逐篇获取详情
    detailed = []
    for note in notes:
        note = fetch_note_detail(client, note)
        detailed.append(note)
        time.sleep(random.uniform(1, 2))
    print(f"  ✅ 获取了 {len(detailed)} 篇笔记详情")

    # 5. 计算统计
    stats = compute_stats(detailed)
    row["分析笔记篇数"] = len(detailed)
    row["平均图片张数"] = stats["avg_images"]
    row["图片偏好"]     = format_image_preference(detailed)
    row["平均文字字数"] = stats["avg_words"]
    row["平均点赞数"]   = stats["avg_liked"]
    row["平均收藏数"]   = stats["avg_collected"]
    row["平均评论数"]   = stats["avg_comments"]
    row["互动率"]       = compute_interaction_rate(
        stats["avg_liked"], stats["avg_collected"], profile["fans_count"]
    )
    row["用户粘度"]     = compute_stickiness(stats["avg_collected"], stats["avg_liked"])

    # 6. Claude 分析
    analysis = analyze_with_claude(detailed, nickname)
    row["图文风格描述"]    = analysis.get("图文风格描述", "分析失败")
    row["内容偏好"]        = analysis.get("内容偏好", "分析失败")
    row["受众分析"]        = analysis.get("受众分析", "分析失败")
    row["目标对象关联点"]  = analysis.get("目标对象关联点", "分析失败")
    row["账号权重评级"]    = analysis.get("账号权重评级", "分析失败")

    return row


def main():
    print("=" * 60)
    print("XHS 账号分析报表生成")
    print(f"账号数：{len(ACCOUNTS)}，每账号分析 {NOTES_LIMIT} 篇")
    print("=" * 60)

    cookie = _load_cookie()
    client = _init_client(cookie)

    rows = []
    for i, account in enumerate(ACCOUNTS, 1):
        print(f"\n[{i}/{len(ACCOUNTS)}] 处理账号：{account['name']}")
        row = process_account(client, account)
        rows.append(row)
        # 账号间延迟
        if i < len(ACCOUNTS):
            delay = random.uniform(3, 5)
            print(f"  ⏳ 等待 {delay:.1f}s...")
            time.sleep(delay)

    # 输出 Excel
    date_str = datetime.now().strftime("%Y%m%d")
    output_path = f"output/xhs_account_analysis_{date_str}.xlsx"
    write_excel(rows, output_path)
    print(f"\n✅ 全部完成！共处理 {len(rows)} 个账号")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 运行全量测试确认不受影响**

```bash
python -m pytest tests/test_analyze_accounts.py -v
```

期望：所有测试 PASS。

- [ ] **Step 3: 验证语法**

```bash
python -c "import ast; ast.parse(open('scripts/analyze_accounts.py').read()); print('语法OK')"
```

- [ ] **Step 4: 冒烟测试 — 仅测试第一个账号（需有效 Cookie 和 ANTHROPIC_API_KEY）**

如果 accounts.json 中有有效登录账号，运行：

```bash
python -c "
import sys
sys.path.insert(0, 'scripts')
from analyze_accounts import _load_cookie, _init_client, search_user_id, ACCOUNTS
cookie = _load_cookie()
client = _init_client(cookie)
name = ACCOUNTS[0]['name']
uid = search_user_id(client, name)
print(f'账号[{name}] user_id={uid}')
"
```

期望：打印出 `user_id=XXXXXXXX`（非 None）。

- [ ] **Step 5: Commit**

```bash
git add scripts/analyze_accounts.py
git commit -m "feat: add main orchestrator to analyze_accounts.py"
```

---

### Task 5: 更新 requirements.txt 并验证完整运行

**Files:**
- Modify: `requirements.txt`（确认 xhs==0.2.13 已写入）

- [ ] **Step 1: 确认 requirements.txt 包含所有新依赖**

```bash
cat requirements.txt
```

期望包含：`xhs==0.2.13`、`openpyxl>=3.1.0`、`anthropic>=0.20.0`

- [ ] **Step 2: 运行全量测试**

```bash
python -m pytest tests/test_analyze_accounts.py -v
```

期望：所有测试（纯函数 + Excel）PASS。

- [ ] **Step 3: Final commit**

```bash
git add requirements.txt
git commit -m "chore: finalize requirements.txt for account analysis feature"
```

- [ ] **Step 4: 实际运行说明**

运行前确认：
1. `.env` 中有 `ANTHROPIC_API_KEY=sk-ant-...`
2. `accounts.json` 中有至少一个有效登录账号（或 `.env` 中有 `XHS_COOKIE`）
3. 部分账号昵称在 ACCOUNTS 列表中标注了「请核实全名」，运行前对照小红书主页确认

```bash
python scripts/analyze_accounts.py
# 输出：output/xhs_account_analysis_20260327.xlsx
```
