# 发布管理功能 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在运营页（`http://localhost:8888`）新增「立即发布」和「定时发布」按钮，支持账号选择（全部/指定）、内容选择、简介自动提炼，定时任务持久化并由后台线程自动执行。

**Architecture:** 新增 `scripts/publish_manager.py` 承载全部发布业务逻辑（账号读取、卡片目录扫描、简介提炼、发布执行、定时调度），`visual_discovery.py` 只做路由转发和前端 HTML/JS。发布调用直接 import `LocalPublisher` from `publish_xhs`，不使用 subprocess。

**Tech Stack:** Python 3.x, `http.server`（已有）, `threading`（已有）, `uuid`, `publish_xhs.LocalPublisher`, `account_manager.extract_cookie_string`

---

## 文件结构

| 文件 | 操作 | 职责 |
|------|------|------|
| `scripts/publish_manager.py` | 新建 | 账号读取、卡片扫描、简介提炼、发布执行、定时调度 |
| `tests/test_publish_manager.py` | 新建 | publish_manager 单元测试 |
| `scripts/visual_discovery.py` | 修改 | 新增 7 个路由 + 发布管理 HTML/JS 区块 |
| `output/publish_schedule.json` | 运行时自动创建 | 定时任务持久化 |

---

## Task 1: publish_manager — 账号列表

**Files:**
- Create: `scripts/publish_manager.py`
- Create: `tests/test_publish_manager.py`

- [ ] **Step 1: 新建测试文件，写失败测试**

```python
# tests/test_publish_manager.py
import json
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import publish_manager


def test_list_accounts_returns_valid_account(tmp_path):
    future = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    storage = tmp_path / "storage.json"
    storage.write_text(json.dumps({"cookies": [{"name": "a1", "value": "x"}]}))
    accounts_json = tmp_path / "accounts.json"
    accounts_json.write_text(json.dumps({
        "accounts": [{
            "name": "acc1",
            "storage_path": str(storage),
            "session_expires_at": future,
            "active": True,
            "last_used_at": None,
        }]
    }))
    result = publish_manager.list_accounts(str(accounts_json))
    assert len(result) == 1
    assert result[0]["name"] == "acc1"
    assert result[0]["valid"] is True


def test_list_accounts_marks_expired(tmp_path):
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    accounts_json = tmp_path / "accounts.json"
    accounts_json.write_text(json.dumps({
        "accounts": [{
            "name": "old",
            "storage_path": str(tmp_path / "missing.json"),
            "session_expires_at": past,
            "active": True,
            "last_used_at": None,
        }]
    }))
    result = publish_manager.list_accounts(str(accounts_json))
    assert result[0]["valid"] is False


def test_list_accounts_skips_inactive(tmp_path):
    future = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    accounts_json = tmp_path / "accounts.json"
    accounts_json.write_text(json.dumps({
        "accounts": [{
            "name": "inactive",
            "storage_path": "",
            "session_expires_at": future,
            "active": False,
            "last_used_at": None,
        }]
    }))
    result = publish_manager.list_accounts(str(accounts_json))
    assert result == []
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd /Users/chenqinghua/Desktop/Auto-Redbook-Skills
python -m pytest tests/test_publish_manager.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'publish_manager'`

- [ ] **Step 3: 新建 publish_manager.py，实现 list_accounts**

```python
# scripts/publish_manager.py
"""
发布管理模块 — 账号读取、卡片目录扫描、简介提炼、发布执行、定时调度
"""
import json
import os
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# 路径常量
BASE_DIR = Path(__file__).parent.parent
ACCOUNTS_PATH = BASE_DIR / "accounts.json"
DRAFT_DIR = BASE_DIR / "output" / "小红书备选"
SCHEDULE_FILE = BASE_DIR / "output" / "publish_schedule.json"

sys.path.insert(0, str(Path(__file__).parent))
from account_manager import is_session_valid


def list_accounts(accounts_path: str = None) -> List[Dict[str, Any]]:
    """返回所有账号及有效性状态，跳过 active=False 的账号。"""
    path = Path(accounts_path or ACCOUNTS_PATH)
    if not path.exists():
        return []
    with open(path) as f:
        data = json.load(f)
    result = []
    for acc in data.get("accounts", []):
        if not acc.get("active"):
            continue
        storage = os.path.expanduser(acc.get("storage_path", ""))
        valid = is_session_valid(acc.get("session_expires_at")) and Path(storage).exists()
        result.append({
            "name": acc["name"],
            "valid": valid,
            "session_expires_at": acc.get("session_expires_at"),
            "storage_path": storage,
        })
    return result
```

- [ ] **Step 4: 运行测试确认通过**

```bash
python -m pytest tests/test_publish_manager.py::test_list_accounts_returns_valid_account tests/test_publish_manager.py::test_list_accounts_marks_expired tests/test_publish_manager.py::test_list_accounts_skips_inactive -v
```

Expected: 3 PASSED

- [ ] **Step 5: Commit**

```bash
git add scripts/publish_manager.py tests/test_publish_manager.py
git commit -m "feat: add publish_manager with list_accounts"
```

---

## Task 2: publish_manager — 卡片目录扫描

**Files:**
- Modify: `scripts/publish_manager.py`
- Modify: `tests/test_publish_manager.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_publish_manager.py` 末尾追加：

```python
def test_list_card_folders_finds_png_dirs(tmp_path):
    folder = tmp_path / "draft_stem"
    folder.mkdir()
    (folder / "01-cover.png").write_bytes(b"")
    (folder / "02-content.png").write_bytes(b"")
    result = publish_manager.list_card_folders(str(tmp_path))
    assert len(result) == 1
    assert result[0]["name"] == "draft_stem"
    assert result[0]["png_count"] == 2


def test_list_card_folders_skips_empty_dirs(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    result = publish_manager.list_card_folders(str(tmp_path))
    assert result == []


def test_list_card_folders_returns_sorted_by_mtime(tmp_path):
    import time
    f1 = tmp_path / "older"
    f1.mkdir()
    (f1 / "a.png").write_bytes(b"")
    time.sleep(0.05)
    f2 = tmp_path / "newer"
    f2.mkdir()
    (f2 / "b.png").write_bytes(b"")
    result = publish_manager.list_card_folders(str(tmp_path))
    assert result[0]["name"] == "newer"
```

- [ ] **Step 2: 运行测试确认失败**

```bash
python -m pytest tests/test_publish_manager.py::test_list_card_folders_finds_png_dirs -v
```

Expected: `AttributeError: module 'publish_manager' has no attribute 'list_card_folders'`

- [ ] **Step 3: 实现 list_card_folders**

在 `scripts/publish_manager.py` 的 `list_accounts` 函数后追加：

```python
def list_card_folders(draft_dir: str = None) -> List[Dict[str, Any]]:
    """扫描 output/小红书备选/ 下含 PNG 的子目录，按修改时间降序返回。"""
    base = Path(draft_dir or DRAFT_DIR)
    if not base.exists():
        return []
    result = []
    for d in base.iterdir():
        if not d.is_dir():
            continue
        pngs = sorted(d.glob("*.png"))
        if not pngs:
            continue
        result.append({
            "name": d.name,
            "path": str(d),
            "png_count": len(pngs),
            "mtime": d.stat().st_mtime,
            "mtime_str": datetime.fromtimestamp(d.stat().st_mtime).strftime("%m-%d %H:%M"),
        })
    result.sort(key=lambda x: x["mtime"], reverse=True)
    return result
```

- [ ] **Step 4: 运行测试确认通过**

```bash
python -m pytest tests/test_publish_manager.py::test_list_card_folders_finds_png_dirs tests/test_publish_manager.py::test_list_card_folders_skips_empty_dirs tests/test_publish_manager.py::test_list_card_folders_returns_sorted_by_mtime -v
```

Expected: 3 PASSED

- [ ] **Step 5: Commit**

```bash
git add scripts/publish_manager.py tests/test_publish_manager.py
git commit -m "feat: add list_card_folders to publish_manager"
```

---

## Task 3: publish_manager — 简介提炼

**Files:**
- Modify: `scripts/publish_manager.py`
- Modify: `tests/test_publish_manager.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_publish_manager.py` 末尾追加：

```python
def test_extract_summary_from_md(tmp_path):
    folder = tmp_path / "我的文案_文案1_20260401_120000"
    folder.mkdir()
    md = tmp_path / "我的文案_文案1_20260401_120000.md"
    md.write_text(
        "# 留英求职3个月我学到了什么\n\n"
        "**账号参考**：test\n"
        "**Hook**：刷到就是缘分！\n\n"
        "**痛点引入**\n关于痛点的描述，很长很长很长很长很长很长很长很长很长很长。\n",
        encoding="utf-8"
    )
    result = publish_manager.extract_summary(str(folder), draft_dir=str(tmp_path))
    assert result["title"] == "留英求职3个月我学到了什么"
    assert len(result["desc"]) <= 100
    assert "刷到就是缘分" in result["desc"]


def test_extract_summary_no_md_returns_defaults(tmp_path):
    folder = tmp_path / "no_md_folder"
    folder.mkdir()
    result = publish_manager.extract_summary(str(folder), draft_dir=str(tmp_path))
    assert result["title"] == "no_md_folder"
    assert result["desc"] == ""
```

- [ ] **Step 2: 运行测试确认失败**

```bash
python -m pytest tests/test_publish_manager.py::test_extract_summary_from_md -v
```

Expected: `AttributeError: module 'publish_manager' has no attribute 'extract_summary'`

- [ ] **Step 3: 实现 extract_summary**

在 `scripts/publish_manager.py` 的 `list_card_folders` 后追加：

```python
def extract_summary(folder: str, draft_dir: str = None) -> Dict[str, str]:
    """
    从与 folder 同名的 .md 文件提炼标题和 ≤100 字简介。
    简介 = Hook + 各节正文片段拼接，截至 100 字。
    """
    import re
    folder_path = Path(folder)
    base = Path(draft_dir or DRAFT_DIR)
    md_path = base / f"{folder_path.name}.md"

    if not md_path.exists():
        return {"title": folder_path.name, "desc": ""}

    text = md_path.read_text(encoding="utf-8")

    # 提取标题
    title_m = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
    title = (title_m.group(1).strip()[:20]) if title_m else folder_path.name

    # 提取 Hook
    hook_m = re.search(r"\*\*Hook\*\*：(.+)", text)
    hook = hook_m.group(1).strip() if hook_m else ""

    # 提取各节正文（跳过元数据行）
    meta_keys = {"账号参考", "内容结构", "Hook", "情绪", "图片风格"}
    sections = re.findall(r"\*\*([^*\n]+)\*\*\n([^\n*#]+)", text)
    body_parts = [b.strip() for h, b in sections if h.strip() not in meta_keys]

    # 拼接并截取 100 字
    raw = " ".join([hook] + body_parts).strip()
    desc = raw[:100]

    return {"title": title, "desc": desc}
```

- [ ] **Step 4: 运行测试确认通过**

```bash
python -m pytest tests/test_publish_manager.py::test_extract_summary_from_md tests/test_publish_manager.py::test_extract_summary_no_md_returns_defaults -v
```

Expected: 2 PASSED

- [ ] **Step 5: Commit**

```bash
git add scripts/publish_manager.py tests/test_publish_manager.py
git commit -m "feat: add extract_summary to publish_manager"
```

---

## Task 4: publish_manager — 定时任务 CRUD

**Files:**
- Modify: `scripts/publish_manager.py`
- Modify: `tests/test_publish_manager.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_publish_manager.py` 末尾追加：

```python
def test_save_and_get_scheduled_task(tmp_path):
    schedule_file = str(tmp_path / "schedule.json")
    task = publish_manager.save_scheduled_task(
        folder="/some/folder",
        title="测试标题",
        desc="测试简介",
        accounts=["acc1"],
        scheduled_at="2026-04-05T09:30:00",
        schedule_file=schedule_file,
    )
    assert "id" in task
    assert task["status"] == "pending"

    tasks = publish_manager.get_scheduled_tasks(schedule_file)
    assert len(tasks) == 1
    assert tasks[0]["id"] == task["id"]


def test_cancel_task(tmp_path):
    schedule_file = str(tmp_path / "schedule.json")
    task = publish_manager.save_scheduled_task(
        folder="/f", title="t", desc="d",
        accounts=[], scheduled_at="2026-04-05T09:30:00",
        schedule_file=schedule_file,
    )
    result = publish_manager.cancel_task(task["id"], schedule_file)
    assert result is True
    tasks = publish_manager.get_scheduled_tasks(schedule_file)
    assert tasks[0]["status"] == "cancelled"


def test_cancel_nonexistent_task(tmp_path):
    schedule_file = str(tmp_path / "schedule.json")
    result = publish_manager.cancel_task("nonexistent-id", schedule_file)
    assert result is False
```

- [ ] **Step 2: 运行测试确认失败**

```bash
python -m pytest tests/test_publish_manager.py::test_save_and_get_scheduled_task -v
```

Expected: `AttributeError: module 'publish_manager' has no attribute 'save_scheduled_task'`

- [ ] **Step 3: 实现 save_scheduled_task / get_scheduled_tasks / cancel_task**

在 `scripts/publish_manager.py` 的 `extract_summary` 后追加：

```python
def _load_schedule(schedule_file: str) -> Dict:
    path = Path(schedule_file)
    if not path.exists():
        return {"tasks": []}
    with open(path) as f:
        return json.load(f)


def _save_schedule(data: Dict, schedule_file: str) -> None:
    path = Path(schedule_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)


def save_scheduled_task(
    folder: str,
    title: str,
    desc: str,
    accounts: List[str],
    scheduled_at: str,
    schedule_file: str = None,
) -> Dict[str, Any]:
    """保存一条定时发布任务，返回任务 dict。"""
    sf = schedule_file or str(SCHEDULE_FILE)
    data = _load_schedule(sf)
    task = {
        "id": str(uuid.uuid4()),
        "created_at": datetime.now().isoformat(),
        "scheduled_at": scheduled_at,
        "folder": folder,
        "title": title,
        "desc": desc,
        "accounts": accounts,
        "status": "pending",
        "result": {},
    }
    data["tasks"].append(task)
    _save_schedule(data, sf)
    return task


def get_scheduled_tasks(schedule_file: str = None) -> List[Dict[str, Any]]:
    """返回全部定时任务列表（按 scheduled_at 升序）。"""
    sf = schedule_file or str(SCHEDULE_FILE)
    data = _load_schedule(sf)
    tasks = data.get("tasks", [])
    tasks.sort(key=lambda t: t.get("scheduled_at", ""))
    return tasks


def cancel_task(task_id: str, schedule_file: str = None) -> bool:
    """将指定 pending 任务标记为 cancelled，返回是否成功。"""
    sf = schedule_file or str(SCHEDULE_FILE)
    data = _load_schedule(sf)
    for task in data["tasks"]:
        if task["id"] == task_id and task["status"] == "pending":
            task["status"] = "cancelled"
            _save_schedule(data, sf)
            return True
    return False
```

- [ ] **Step 4: 运行测试确认通过**

```bash
python -m pytest tests/test_publish_manager.py::test_save_and_get_scheduled_task tests/test_publish_manager.py::test_cancel_task tests/test_publish_manager.py::test_cancel_nonexistent_task -v
```

Expected: 3 PASSED

- [ ] **Step 5: Commit**

```bash
git add scripts/publish_manager.py tests/test_publish_manager.py
git commit -m "feat: add scheduled task CRUD to publish_manager"
```

---

## Task 5: publish_manager — 发布执行 & 定时调度线程

**Files:**
- Modify: `scripts/publish_manager.py`
- Modify: `tests/test_publish_manager.py`

- [ ] **Step 1: 写失败测试（mock LocalPublisher）**

在 `tests/test_publish_manager.py` 末尾追加：

```python
from unittest.mock import patch, MagicMock


def test_do_publish_calls_publisher_for_each_account(tmp_path):
    future = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    storage = tmp_path / "storage.json"
    storage.write_text(json.dumps({
        "cookies": [{"name": "a1", "value": "x"}, {"name": "web_session", "value": "y"}]
    }))
    accounts_json = tmp_path / "accounts.json"
    accounts_json.write_text(json.dumps({
        "accounts": [{
            "name": "acc1",
            "storage_path": str(storage),
            "session_expires_at": future,
            "active": True,
            "last_used_at": None,
        }]
    }))
    folder = tmp_path / "cards"
    folder.mkdir()
    (folder / "01.png").write_bytes(b"")

    mock_publisher = MagicMock()
    mock_publisher.publish.return_value = {"note_id": "abc"}

    with patch("publish_manager.LocalPublisher", return_value=mock_publisher):
        results = publish_manager.do_publish(
            folder=str(folder),
            title="测试",
            desc="简介",
            account_names=["acc1"],
            accounts_path=str(accounts_json),
        )

    assert results["acc1"]["status"] == "ok"
    mock_publisher.init_client.assert_called_once()
    mock_publisher.publish.assert_called_once()


def test_do_publish_reports_error_on_failure(tmp_path):
    future = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    storage = tmp_path / "storage.json"
    storage.write_text(json.dumps({
        "cookies": [{"name": "a1", "value": "x"}, {"name": "web_session", "value": "y"}]
    }))
    accounts_json = tmp_path / "accounts.json"
    accounts_json.write_text(json.dumps({
        "accounts": [{
            "name": "acc1",
            "storage_path": str(storage),
            "session_expires_at": future,
            "active": True,
            "last_used_at": None,
        }]
    }))
    folder = tmp_path / "cards"
    folder.mkdir()
    (folder / "01.png").write_bytes(b"")

    mock_publisher = MagicMock()
    mock_publisher.publish.side_effect = Exception("网络错误")

    with patch("publish_manager.LocalPublisher", return_value=mock_publisher):
        results = publish_manager.do_publish(
            folder=str(folder),
            title="测试",
            desc="简介",
            account_names=["acc1"],
            accounts_path=str(accounts_json),
        )

    assert results["acc1"]["status"] == "error"
    assert "网络错误" in results["acc1"]["error"]
```

- [ ] **Step 2: 运行测试确认失败**

```bash
python -m pytest tests/test_publish_manager.py::test_do_publish_calls_publisher_for_each_account -v
```

Expected: `AttributeError: module 'publish_manager' has no attribute 'do_publish'`

- [ ] **Step 3: 实现 do_publish 和定时调度线程**

在 `scripts/publish_manager.py` 的 `cancel_task` 后追加：

```python
from publish_xhs import LocalPublisher
from account_manager import extract_cookie_string


def do_publish(
    folder: str,
    title: str,
    desc: str,
    account_names: List[str],
    accounts_path: str = None,
) -> Dict[str, Any]:
    """
    对指定账号列表逐一发布。account_names 为空表示全部 active 有效账号。
    返回 {account_name: {"status": "ok"|"error", "result"|"error": ...}}
    """
    accounts = list_accounts(accounts_path)
    if account_names:
        accounts = [a for a in accounts if a["name"] in account_names]

    pngs = sorted(Path(folder).glob("*.png"))
    image_paths = [str(p) for p in pngs]

    results = {}
    for acc in accounts:
        if not acc["valid"]:
            results[acc["name"]] = {"status": "error", "error": "账号 session 已过期"}
            continue
        try:
            cookie = extract_cookie_string(acc["storage_path"])
            publisher = LocalPublisher(cookie)
            publisher.init_client()
            result = publisher.publish(
                title=title[:20],
                desc=desc,
                images=image_paths,
                is_private=False,
            )
            results[acc["name"]] = {"status": "ok", "result": result}
        except Exception as e:
            results[acc["name"]] = {"status": "error", "error": str(e)}

    return results


# ── 定时调度 ──────────────────────────────────────────────────────────────────

_scheduler_started = False
_scheduler_lock = threading.Lock()


def _run_due_tasks(schedule_file: str = None, accounts_path: str = None) -> None:
    """检查并执行到期的 pending 任务（供调度线程调用）。"""
    sf = schedule_file or str(SCHEDULE_FILE)
    data = _load_schedule(sf)
    now = datetime.now().isoformat()
    changed = False
    for task in data["tasks"]:
        if task["status"] != "pending":
            continue
        if task["scheduled_at"] > now:
            continue
        try:
            result = do_publish(
                folder=task["folder"],
                title=task["title"],
                desc=task["desc"],
                account_names=task.get("accounts") or [],
                accounts_path=accounts_path,
            )
            task["status"] = "done"
            task["result"] = result
        except Exception as e:
            task["status"] = "failed"
            task["result"] = {"error": str(e)}
        changed = True
    if changed:
        _save_schedule(data, sf)


def start_scheduler(schedule_file: str = None, accounts_path: str = None) -> None:
    """启动定时调度后台线程（每 30 秒检查一次，重复调用安全）。"""
    global _scheduler_started
    with _scheduler_lock:
        if _scheduler_started:
            return
        _scheduler_started = True

    def _loop():
        import time
        while True:
            try:
                _run_due_tasks(schedule_file, accounts_path)
            except Exception:
                pass
            time.sleep(30)

    t = threading.Thread(target=_loop, daemon=True)
    t.start()
```

- [ ] **Step 4: 运行测试确认通过**

```bash
python -m pytest tests/test_publish_manager.py::test_do_publish_calls_publisher_for_each_account tests/test_publish_manager.py::test_do_publish_reports_error_on_failure -v
```

Expected: 2 PASSED

- [ ] **Step 5: 运行全部 publish_manager 测试**

```bash
python -m pytest tests/test_publish_manager.py -v
```

Expected: 全部 PASSED

- [ ] **Step 6: Commit**

```bash
git add scripts/publish_manager.py tests/test_publish_manager.py
git commit -m "feat: add do_publish and scheduler to publish_manager"
```

---

## Task 6: visual_discovery.py — API 路由

**Files:**
- Modify: `scripts/visual_discovery.py`

- [ ] **Step 1: 在文件顶部 import publish_manager**

在 `visual_discovery.py` 的 import 块末尾（`from threading import Thread` 之后）追加：

```python
sys.path.insert(0, str(Path(__file__).parent))
import publish_manager
publish_manager.start_scheduler()
```

- [ ] **Step 2: 在 Handler.do_GET 中追加 4 个读取路由**

在 `do_GET` 方法的 `elif path == "/api/drafts":` 分支之后、`else` 之前插入：

```python
        elif path == "/api/card_folders":
            self._json(publish_manager.list_card_folders())
        elif path == "/api/card_summary":
            folder = qs.get("folder", [""])[0]
            if not folder:
                self._json({"error": "folder 参数缺失"})
            else:
                self._json(publish_manager.extract_summary(folder))
        elif path == "/api/accounts":
            self._json(publish_manager.list_accounts())
        elif path == "/api/scheduled_tasks":
            self._json(publish_manager.get_scheduled_tasks())
```

- [ ] **Step 3: 在 Handler.do_POST 中追加发布路由**

在 `do_POST` 方法的 `elif path == "/api/save_draft":` 分支之后、`else` 之前插入：

```python
        elif path == "/api/publish":
            folder = body.get("folder", "")
            title = body.get("title", "")
            desc = body.get("desc", "")
            accounts = body.get("accounts", [])
            if not folder:
                self._json({"error": "folder 不能为空"})
                return
            result = publish_manager.do_publish(folder, title, desc, accounts)
            self._json({"ok": True, "result": result})
        elif path == "/api/schedule_publish":
            folder = body.get("folder", "")
            title = body.get("title", "")
            desc = body.get("desc", "")
            accounts = body.get("accounts", [])
            scheduled_at = body.get("scheduled_at", "")
            if not folder or not scheduled_at:
                self._json({"error": "folder 和 scheduled_at 不能为空"})
                return
            task = publish_manager.save_scheduled_task(folder, title, desc, accounts, scheduled_at)
            self._json({"ok": True, "task": task})
```

- [ ] **Step 4: 在 Handler 类中添加 do_DELETE 方法**

在 `do_POST` 方法之后追加：

```python
    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/api/scheduled_tasks/"):
            task_id = path.split("/api/scheduled_tasks/", 1)[1]
            ok = publish_manager.cancel_task(task_id)
            self._json({"ok": ok})
        else:
            self.send_response(404); self.end_headers()
```

- [ ] **Step 5: 手动验证（启动服务器后测试端点）**

```bash
python scripts/visual_discovery.py &
sleep 2
curl -s http://localhost:8888/api/accounts | python -m json.tool
curl -s http://localhost:8888/api/card_folders | python -m json.tool
curl -s http://localhost:8888/api/scheduled_tasks | python -m json.tool
kill %1
```

Expected: 三个请求均返回合法 JSON

- [ ] **Step 6: Commit**

```bash
git add scripts/visual_discovery.py
git commit -m "feat: add publish API routes to visual_discovery"
```

---

## Task 7: visual_discovery.py — 发布管理 HTML/JS 区块

**Files:**
- Modify: `scripts/visual_discovery.py`

- [ ] **Step 1: 在 HTML 的 `<style>` 块末尾追加发布区样式**

在 `HTML` 字符串中，`@media(max-width:768px)` 规则之后、`</style>` 之前插入：

```css
/* Publish section */
.pub-btn{padding:10px 24px;border:none;border-radius:10px;font-size:.95rem;font-weight:700;
  cursor:pointer;transition:opacity .2s}
.pub-btn-now{background:linear-gradient(135deg,#ff6b6b,#ff8e53);color:#fff}
.pub-btn-sched{background:linear-gradient(135deg,#667eea,#764ba2);color:#fff}
.pub-btn:disabled{opacity:.5;cursor:not-allowed}
.modal-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:100;
  align-items:center;justify-content:center}
.modal-overlay.open{display:flex}
.modal{background:#fff;border-radius:20px;padding:28px 32px;width:480px;max-width:95vw;
  max-height:90vh;overflow-y:auto;box-shadow:0 20px 60px rgba(0,0,0,.2)}
.modal h2{font-size:1.1rem;font-weight:900;color:#333;margin-bottom:20px}
.modal label{display:block;font-size:.85rem;color:#666;margin-bottom:4px;margin-top:14px}
.modal input,.modal select,.modal textarea{width:100%;padding:10px 12px;border:1.5px solid #eee;
  border-radius:8px;font-size:.9rem;outline:none;font-family:inherit}
.modal input:focus,.modal select:focus,.modal textarea:focus{border-color:#ff6b6b}
.modal textarea{resize:vertical;min-height:72px}
.char-count{font-size:.75rem;color:#aaa;text-align:right;margin-top:2px}
.char-count.over{color:#e53935}
.account-checkboxes{border:1.5px solid #eee;border-radius:8px;padding:10px 14px;
  max-height:140px;overflow-y:auto;display:flex;flex-direction:column;gap:6px}
.account-checkboxes label{font-size:.88rem;color:#333;display:flex;align-items:center;
  gap:8px;cursor:pointer;margin:0}
.acc-expired{color:#aaa}
.modal-footer{display:flex;justify-content:flex-end;gap:10px;margin-top:20px}
.btn-cancel-modal{padding:10px 22px;background:#f5f5f5;color:#666;border:none;
  border-radius:8px;font-size:.9rem;cursor:pointer}
.btn-confirm{padding:10px 22px;background:linear-gradient(135deg,#ff6b6b,#ff8e53);
  color:#fff;border:none;border-radius:8px;font-size:.9rem;font-weight:700;cursor:pointer}
.btn-confirm:disabled{opacity:.5;cursor:not-allowed}
.toast{position:fixed;top:20px;left:50%;transform:translateX(-50%);padding:12px 28px;
  border-radius:10px;font-weight:700;font-size:.95rem;z-index:200;
  box-shadow:0 4px 20px rgba(0,0,0,.15);display:none}
.toast.show{display:block}
.toast-ok{background:#e8f5e9;color:#2e7d32}
.toast-err{background:#ffebee;color:#c62828}
.sched-tbl{width:100%;border-collapse:collapse;font-size:.85rem;margin-top:12px}
.sched-tbl th{background:#f9f0ff;padding:9px 12px;text-align:left;
  border-bottom:2px solid #e0d0f0;color:#764ba2;font-weight:700;white-space:nowrap}
.sched-tbl td{padding:9px 12px;border-bottom:1px solid #f5f5f5;color:#444}
.sched-tbl tr:hover td{background:#faf5ff}
.badge-pending{background:#fff3e0;color:#e65100;padding:2px 8px;border-radius:20px;
  font-size:.75rem;font-weight:600}
.badge-done{background:#e8f5e9;color:#2e7d32;padding:2px 8px;border-radius:20px;
  font-size:.75rem;font-weight:600}
.badge-failed{background:#ffebee;color:#c62828;padding:2px 8px;border-radius:20px;
  font-size:.75rem;font-weight:600}
.badge-cancelled{background:#f5f5f5;color:#999;padding:2px 8px;border-radius:20px;
  font-size:.75rem;font-weight:600}
```

- [ ] **Step 2: 在 HTML body 末尾（`</div>` 闭合 `.wrap` 之前）追加发布区块**

找到 `</div>\n\n<script>` 这段，在其前插入：

```html
  <!-- 发布管理 -->
  <div class="card" style="margin-bottom:16px">
    <div class="card-title">📤 发布管理</div>
    <div style="display:flex;gap:12px;margin-bottom:16px">
      <button class="pub-btn pub-btn-now" onclick="openPublishModal('now')">立即发布</button>
      <button class="pub-btn pub-btn-sched" onclick="openPublishModal('sched')">定时发布</button>
    </div>
    <div id="schedTasksWrap" style="overflow-x:auto">
      <table class="sched-tbl" id="schedTbl">
        <thead><tr>
          <th>发布时间</th><th>内容</th><th>账号</th><th>状态</th><th>操作</th>
        </tr></thead>
        <tbody id="schedTbody"><tr><td colspan="5" style="color:#bbb;text-align:center;padding:16px">暂无定时任务</td></tr></tbody>
      </table>
    </div>
  </div>

  <!-- 发布 Modal -->
  <div class="modal-overlay" id="pubModal">
    <div class="modal">
      <h2 id="modalTitle">立即发布</h2>

      <label>发布内容</label>
      <select id="folderSel" onchange="loadFolderSummary()">
        <option value="">— 选择图片文件夹 —</option>
      </select>
      <div id="folderMeta" style="font-size:.78rem;color:#aaa;margin-top:4px"></div>

      <label>标题（≤20字）</label>
      <input id="pubTitle" type="text" maxlength="20" placeholder="笔记标题">

      <label>简介（≤100字）</label>
      <textarea id="pubDesc" maxlength="120" rows="3" placeholder="自动提炼，可编辑..." oninput="updateCharCount()"></textarea>
      <div class="char-count" id="descCount">0 / 100</div>

      <label>发布账号</label>
      <select id="accMode" onchange="toggleAccounts()">
        <option value="all">全部发布</option>
        <option value="pick">指定发布</option>
      </select>
      <div id="accCheckboxes" class="account-checkboxes" style="display:none;margin-top:8px"></div>

      <div id="schedTimeRow" style="display:none">
        <label>发布时间</label>
        <div style="display:flex;gap:8px">
          <input id="schedDate" type="date" style="flex:1">
          <input id="schedTime" type="time" style="width:110px">
        </div>
      </div>

      <div class="modal-footer">
        <button class="btn-cancel-modal" onclick="closePublishModal()">取消</button>
        <button class="btn-confirm" id="confirmBtn" onclick="confirmPublish()">确认发布</button>
      </div>
    </div>
  </div>

  <!-- Toast -->
  <div class="toast" id="toast"></div>
```

- [ ] **Step 3: 在 `<script>` 块末尾追加发布 JS**

在 HTML 字符串的 `</script>` 之前追加：

```javascript
// ── 发布管理 ────────────────────────────────────────────────────────────────

let _pubMode = 'now';
let _allAccounts = [];

function showToast(msg, type) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast show toast-' + type;
  setTimeout(() => t.className = 'toast', 3000);
}

function openPublishModal(mode) {
  _pubMode = mode;
  document.getElementById('modalTitle').textContent = mode === 'now' ? '立即发布' : '定时发布';
  document.getElementById('schedTimeRow').style.display = mode === 'sched' ? 'block' : 'none';
  document.getElementById('confirmBtn').disabled = true;

  // 设置默认时间（明天 09:00）
  if (mode === 'sched') {
    const tomorrow = new Date(); tomorrow.setDate(tomorrow.getDate() + 1);
    document.getElementById('schedDate').value = tomorrow.toISOString().slice(0, 10);
    document.getElementById('schedTime').value = '09:00';
  }

  // 加载文件夹列表
  fetch('/api/card_folders').then(r => r.json()).then(folders => {
    const sel = document.getElementById('folderSel');
    sel.innerHTML = '<option value="">— 选择图片文件夹 —</option>';
    folders.forEach(f => {
      const opt = document.createElement('option');
      opt.value = f.path;
      opt.textContent = f.name + ' (' + f.png_count + '张 · ' + f.mtime_str + ')';
      sel.appendChild(opt);
    });
  });

  // 加载账号列表
  fetch('/api/accounts').then(r => r.json()).then(accounts => {
    _allAccounts = accounts;
    const box = document.getElementById('accCheckboxes');
    box.innerHTML = '';
    accounts.forEach(acc => {
      const exp = acc.session_expires_at ? acc.session_expires_at.slice(0, 10) : '未知';
      const disabled = !acc.valid;
      const label = document.createElement('label');
      label.className = disabled ? 'acc-expired' : '';
      label.innerHTML =
        '<input type="checkbox" value="' + acc.name + '"' + (disabled ? ' disabled' : '') + '>' +
        acc.name + (disabled ? ' ⚠️ 已过期' : ' （有效至 ' + exp + '）');
      box.appendChild(label);
    });
  });

  document.getElementById('pubModal').classList.add('open');
}

function closePublishModal() {
  document.getElementById('pubModal').classList.remove('open');
}

function loadFolderSummary() {
  const folder = document.getElementById('folderSel').value;
  document.getElementById('folderMeta').textContent = '';
  if (!folder) { document.getElementById('confirmBtn').disabled = true; return; }

  fetch('/api/card_summary?folder=' + encodeURIComponent(folder))
    .then(r => r.json()).then(s => {
      document.getElementById('pubTitle').value = s.title || '';
      document.getElementById('pubDesc').value = s.desc || '';
      updateCharCount();
      document.getElementById('confirmBtn').disabled = false;
      // 显示图片数
      const sel = document.getElementById('folderSel');
      const text = sel.options[sel.selectedIndex].text;
      document.getElementById('folderMeta').textContent = text;
    });
}

function updateCharCount() {
  const desc = document.getElementById('pubDesc').value;
  const cnt = document.getElementById('descCount');
  cnt.textContent = desc.length + ' / 100';
  cnt.className = 'char-count' + (desc.length > 100 ? ' over' : '');
  document.getElementById('confirmBtn').disabled = desc.length > 100 ||
    !document.getElementById('folderSel').value;
}

function toggleAccounts() {
  const mode = document.getElementById('accMode').value;
  document.getElementById('accCheckboxes').style.display = mode === 'pick' ? 'flex' : 'none';
}

function confirmPublish() {
  const folder = document.getElementById('folderSel').value;
  const title = document.getElementById('pubTitle').value.trim();
  const desc = document.getElementById('pubDesc').value.trim();
  const accMode = document.getElementById('accMode').value;
  let accounts = [];
  if (accMode === 'pick') {
    document.querySelectorAll('#accCheckboxes input:checked').forEach(cb => accounts.push(cb.value));
  }

  const btn = document.getElementById('confirmBtn');
  btn.disabled = true;
  btn.textContent = '发布中...';

  if (_pubMode === 'now') {
    fetch('/api/publish', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({folder, title, desc, accounts})
    }).then(r => r.json()).then(data => {
      closePublishModal();
      btn.textContent = '确认发布';
      if (data.ok) showToast('发布成功！', 'ok');
      else showToast('发布失败：' + (data.error || ''), 'err');
    }).catch(() => { btn.disabled = false; btn.textContent = '确认发布'; showToast('请求失败', 'err'); });
  } else {
    const date = document.getElementById('schedDate').value;
    const time = document.getElementById('schedTime').value;
    const scheduled_at = date + 'T' + time + ':00';
    fetch('/api/schedule_publish', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({folder, title, desc, accounts, scheduled_at})
    }).then(r => r.json()).then(data => {
      closePublishModal();
      btn.textContent = '确认发布';
      if (data.ok) { showToast('已加入定时队列！', 'ok'); loadScheduledTasks(); }
      else showToast('提交失败：' + (data.error || ''), 'err');
    }).catch(() => { btn.disabled = false; btn.textContent = '确认发布'; showToast('请求失败', 'err'); });
  }
}

function cancelScheduledTask(id) {
  if (!confirm('确认取消该定时任务？')) return;
  fetch('/api/scheduled_tasks/' + id, {method: 'DELETE'})
    .then(r => r.json()).then(() => loadScheduledTasks());
}

function loadScheduledTasks() {
  fetch('/api/scheduled_tasks').then(r => r.json()).then(tasks => {
    const tbody = document.getElementById('schedTbody');
    if (!tasks.length) {
      tbody.innerHTML = '<tr><td colspan="5" style="color:#bbb;text-align:center;padding:16px">暂无定时任务</td></tr>';
      return;
    }
    const statusLabel = {pending:'<span class="badge-pending">待发</span>',
      done:'<span class="badge-done">已发</span>',
      failed:'<span class="badge-failed">失败</span>',
      cancelled:'<span class="badge-cancelled">已取消</span>'};
    tbody.innerHTML = tasks.map(t => {
      const accText = t.accounts && t.accounts.length ? t.accounts.join(', ') : '全部';
      const cancelBtn = t.status === 'pending'
        ? '<button class="btn btn-outline" style="padding:4px 12px;font-size:.78rem" onclick="cancelScheduledTask(\'' + t.id + '\')">取消</button>'
        : '—';
      return '<tr><td>' + t.scheduled_at.replace('T', ' ').slice(0, 16) + '</td>' +
        '<td>' + (t.title || '').slice(0, 20) + '</td>' +
        '<td>' + accText + '</td>' +
        '<td>' + (statusLabel[t.status] || t.status) + '</td>' +
        '<td>' + cancelBtn + '</td></tr>';
    }).join('');
  });
}

// 页面加载时拉一次定时任务
window.addEventListener('load', loadScheduledTasks);
```

- [ ] **Step 4: 手动验证 UI**

```bash
python scripts/visual_discovery.py
# 浏览器打开 http://localhost:8888
# 验证：
# 1. 页面底部显示「发布管理」区块
# 2. 点击「立即发布」弹出 Modal，内容选择下拉有卡片目录
# 3. 选择目录后自动填入标题/简介
# 4. 字数超 100 显示红色计数
# 5. 点击「定时发布」显示时间选择器
# 6. 「指定发布」展开账号 checkbox
# Ctrl+C 停止
```

- [ ] **Step 5: Commit**

```bash
git add scripts/visual_discovery.py
git commit -m "feat: add publish management UI to visual_discovery dashboard"
```

---

## Task 8: 运行全量测试 & 最终验证

- [ ] **Step 1: 运行全部测试**

```bash
python -m pytest tests/ -v
```

Expected: 全部 PASSED（包含原有 test_account_manager.py、test_analyze_accounts.py 和新增 test_publish_manager.py）

- [ ] **Step 2: Commit（如有遗漏的修改）**

```bash
git status
# 若有未提交文件：
git add -p
git commit -m "chore: finalize publish dashboard feature"
```
