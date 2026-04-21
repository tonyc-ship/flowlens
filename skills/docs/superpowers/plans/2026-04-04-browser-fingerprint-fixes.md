# Browser Fingerprint & CDP Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 Codex review 发现的浏览器指纹泄漏、CDP 抓包不完整、Cookie storageState 伪造三类问题，使所有脚本的浏览器画像统一且接近真实 Chrome。

**Architecture:** 新增 `scripts/browser_config.py` 作为所有 Playwright 入口的共享浏览器配置；用 CDPSession + `requestWillBeSentExtraInfo` 替换 `page.on("request")` 以捕获真实 Cookie；`cookie_string_to_storage_state` 加 httpOnly/sameSite 修正并标记为低保真兜底。

**Tech Stack:** Python 3.13, Playwright async/sync API, CDP (`requestWillBeSentExtraInfo`, `requestWillBeSent`), pytest

---

## 文件变更清单

| 操作 | 路径 | 职责 |
|------|------|------|
| 新建 | `scripts/browser_config.py` | 统一浏览器启动参数、init script、context 工厂 |
| 新建 | `tests/test_browser_config.py` | 验证 launch_options / init_script 格式 |
| 修改 | `scripts/account_manager.py:82-100` | `cookie_string_to_storage_state` 修正 cookie 属性；`_playwright_login` 使用 browser_config |
| 修改 | `scripts/xhs_keepalive.py:54-99` | 用 browser_config；等待真实 API 响应替换 sleep |
| 修改 | `tests/test_account_manager.py` | 补充 httpOnly/sameSite 属性断言 |
| 重写 | `scripts/xhs_network_spy.py` | 真正 CDP 抓包 + `--bot` 模式 |

---

## Task 1: 新建 `scripts/browser_config.py`

**Files:**
- Create: `scripts/browser_config.py`
- Create: `tests/test_browser_config.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_browser_config.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from browser_config import launch_options, NAVIGATOR_INIT_SCRIPT


def test_launch_options_contains_channel():
    opts = launch_options(headless=False)
    assert opts["channel"] == "chrome"
    assert opts["headless"] is False


def test_launch_options_headless():
    opts = launch_options(headless=True)
    assert opts["headless"] is True


def test_launch_options_has_automation_flag():
    opts = launch_options()
    args = opts.get("args", [])
    assert "--disable-blink-features=AutomationControlled" in args


def test_navigator_init_script_hides_webdriver():
    assert "navigator" in NAVIGATOR_INIT_SCRIPT
    assert "webdriver" in NAVIGATOR_INIT_SCRIPT
    assert "undefined" in NAVIGATOR_INIT_SCRIPT
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd /Users/chenqinghua/Desktop/Auto-Redbook-Skills
.venv/bin/pytest tests/test_browser_config.py -v
```

Expected: `ImportError: cannot import name 'launch_options' from 'browser_config'`

- [ ] **Step 3: 实现 `scripts/browser_config.py`**

```python
# scripts/browser_config.py
"""
统一浏览器启动配置：所有 Playwright 入口共用，保证画像一致。
"""

# 注入到每个 page，隐藏 navigator.webdriver
NAVIGATOR_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
"""


def launch_options(headless: bool = False) -> dict:
    """
    返回 playwright.chromium.launch(**launch_options()) 的参数字典。
    优先使用本机真实 Chrome（channel='chrome'），无则回退到 Playwright Chromium。
    调用方负责 try/except 回退：
        try:
            browser = await p.chromium.launch(**launch_options())
        except Exception:
            browser = await p.chromium.launch(**launch_options_fallback(headless))
    """
    return {
        "headless": headless,
        "channel": "chrome",
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-default-browser-check",
        ],
    }


def launch_options_fallback(headless: bool = False) -> dict:
    """channel='chrome' 不可用时的回退（Playwright 内置 Chromium）。"""
    return {
        "headless": headless,
        "args": [
            "--disable-blink-features=AutomationControlled",
        ],
    }


def context_options(storage_state=None, viewport: dict = None) -> dict:
    """
    返回 browser.new_context(**context_options()) 的参数字典。
    不硬编码 User-Agent，让浏览器用真实值（与 sec-ch-ua 版本保持一致）。
    """
    opts = {
        "viewport": viewport or {"width": 1280, "height": 800},
    }
    if storage_state:
        opts["storage_state"] = storage_state
    return opts
```

- [ ] **Step 4: 跑测试确认通过**

```bash
.venv/bin/pytest tests/test_browser_config.py -v
```

Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/browser_config.py tests/test_browser_config.py
git commit -m "feat: add browser_config.py — unified Playwright launch + context options"
```

---

## Task 2: 修复 `cookie_string_to_storage_state` + `_playwright_login`

**Files:**
- Modify: `scripts/account_manager.py:82-100` (cookie_string_to_storage_state)
- Modify: `scripts/account_manager.py:305-337` (_playwright_login)
- Modify: `tests/test_account_manager.py` (补充属性断言)

背景：当前所有 cookie 被设成 `httpOnly=False / sameSite=None / expires=-1`，与真实浏览器 jar 不符。已知：`web_session` 是 httpOnly；`a1` 由 JS 设置，httpOnly=False。

- [ ] **Step 1: 在 `tests/test_account_manager.py` 追加失败测试**

在文件末尾追加：

```python
# --- cookie_string_to_storage_state 属性修正 ---

def test_web_session_is_http_only():
    """web_session 是服务端 Set-Cookie，应标记 httpOnly=True"""
    result = cookie_string_to_storage_state("a1=abc; web_session=xyz")
    cookies = {c["name"]: c for c in result["cookies"]}
    assert cookies["web_session"]["httpOnly"] is True


def test_a1_is_not_http_only():
    """a1 由 JS 写入，httpOnly 应为 False"""
    result = cookie_string_to_storage_state("a1=abc; web_session=xyz")
    cookies = {c["name"]: c for c in result["cookies"]}
    assert cookies["a1"]["httpOnly"] is False


def test_web_session_same_site_lax():
    """web_session 的 sameSite 应为 Lax，而不是 None"""
    result = cookie_string_to_storage_state("a1=abc; web_session=xyz")
    cookies = {c["name"]: c for c in result["cookies"]}
    assert cookies["web_session"]["sameSite"] == "Lax"


def test_unknown_cookie_defaults_safe():
    """未知字段默认 httpOnly=False、sameSite=Lax"""
    result = cookie_string_to_storage_state("foo=bar")
    cookies = {c["name"]: c for c in result["cookies"]}
    assert cookies["foo"]["httpOnly"] is False
    assert cookies["foo"]["sameSite"] == "Lax"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/pytest tests/test_account_manager.py::test_web_session_is_http_only -v
```

Expected: FAIL (`AssertionError: assert False is True`)

- [ ] **Step 3: 修改 `scripts/account_manager.py` 中的 `cookie_string_to_storage_state`**

将 `cookie_string_to_storage_state` 函数（约第 82-100 行）替换为：

```python
# 已知各 cookie 的真实属性（来自浏览器 Network 面板观察）
_COOKIE_ATTRS: dict = {
    # name: (httpOnly, sameSite)
    "web_session":       (True,  "Lax"),
    "customer-sso-sid":  (True,  "Lax"),
    "websectiga":        (True,  "Lax"),
    "sec_poison_id":     (True,  "Lax"),
    "a1":                (False, "None"),
    "webId":             (False, "None"),
    "gid":               (False, "None"),
    "xsecappid":         (False, "None"),
}
_DEFAULT_ATTRS = (False, "Lax")


def cookie_string_to_storage_state(cookie_string: str) -> dict:
    """
    将明文 cookie 字符串（a1=xxx; web_session=yyy）转为 Playwright storageState 格式。

    注意：这是低保真兜底方案。httpOnly/sameSite/expires 根据已知规律设置，
    但无法还原浏览器真实 jar 的完整状态。优先使用扫码登录得到的 storageState。
    """
    cookies = []
    for item in cookie_string.split(";"):
        item = item.strip()
        if "=" not in item:
            continue
        name, value = item.split("=", 1)
        name = name.strip()
        value = value.strip()
        http_only, same_site = _COOKIE_ATTRS.get(name, _DEFAULT_ATTRS)
        # sameSite=None 要求 secure=True；其余也设 secure=True（XHS 全站 HTTPS）
        cookies.append({
            "name": name,
            "value": value,
            "domain": ".xiaohongshu.com",
            "path": "/",
            "expires": -1,
            "httpOnly": http_only,
            "secure": True,
            "sameSite": same_site,
        })
    return {"cookies": cookies, "origins": []}
```

- [ ] **Step 4: 跑测试确认新旧测试全部通过**

```bash
.venv/bin/pytest tests/test_account_manager.py -v
```

Expected: 全部 pass（原有测试 + 4 个新测试）

- [ ] **Step 5: 修改 `_playwright_login` 使用 browser_config**

在 `scripts/account_manager.py` 头部导入（已有 `from pathlib import Path` 等），追加：

```python
import sys as _sys
_sys.path.insert(0, str(Path(__file__).parent))
from browser_config import launch_options, launch_options_fallback, context_options, NAVIGATOR_INIT_SCRIPT
```

将 `_playwright_login` 函数中的浏览器启动部分（约第 320-325 行）替换为：

```python
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(**launch_options(headless=False))
        except Exception:
            browser = p.chromium.launch(**launch_options_fallback(headless=False))

        context = browser.new_context(**context_options(viewport={"width": 1280, "height": 800}))
        context.add_init_script(NAVIGATOR_INIT_SCRIPT)
        page = context.new_page()
        page.goto("https://www.xiaohongshu.com/login")
```

- [ ] **Step 6: 跑全量测试确认无回归**

```bash
.venv/bin/pytest tests/test_account_manager.py tests/test_browser_config.py -v
```

Expected: 全部 pass

- [ ] **Step 7: Commit**

```bash
git add scripts/account_manager.py tests/test_account_manager.py
git commit -m "fix: cookie_string_to_storage_state httpOnly/sameSite; _playwright_login uses browser_config"
```

---

## Task 3: 升级 `xhs_keepalive.py`——等待真实 API 响应

**Files:**
- Modify: `scripts/xhs_keepalive.py:54-99` (keepalive_account)

背景：当前只 goto("/") + sleep(2)，没有等待任何 API、没有模拟真实用户登录后的初始化序列。根据 network spy 录制结果，真实登录后首先会命中：`/api/sns/web/v2/user/me`、`/api/sns/web/v1/system/config`、`/api/sns/web/unread_count`。

- [ ] **Step 1: 写失败测试（mock 级别，验证 keepalive 至少访问了必要路径）**

```python
# tests/test_keepalive.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))


def test_keepalive_target_paths_defined():
    """WARMUP_PATHS 常量应包含已知初始化 API"""
    from xhs_keepalive import WARMUP_PATHS
    assert "/api/sns/web/v2/user/me" in WARMUP_PATHS
    assert "/api/sns/web/unread_count" in WARMUP_PATHS
    assert "/api/sns/web/v1/system/config" in WARMUP_PATHS


def test_warmup_paths_are_relative():
    """路径应是相对路径（以 / 开头），不含域名"""
    from xhs_keepalive import WARMUP_PATHS
    for p in WARMUP_PATHS:
        assert p.startswith("/"), f"路径应以 / 开头: {p}"
        assert "xiaohongshu" not in p, f"路径不应含域名: {p}"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/pytest tests/test_keepalive.py -v
```

Expected: `ImportError: cannot import name 'WARMUP_PATHS'`

- [ ] **Step 3: 修改 `scripts/xhs_keepalive.py`**

在文件顶部（`BASE_DIR` 常量之后）新增常量，并重写 `keepalive_account`：

```python
# 真实用户登录后必经的初始化 API（来自 xhs_network_spy 录制结果）
WARMUP_PATHS = [
    "/api/sns/web/v2/user/me",
    "/api/sns/web/v1/system/config",
    "/api/sns/web/v1/zones",
    "/api/sns/web/unread_count",
    "/api/sns/web/v1/search/querytrending",
]
```

将 `keepalive_account` 函数中的浏览器操作部分（第 54-99 行）替换为：

```python
def keepalive_account(account: dict, headless: bool = False) -> dict:
    from playwright.sync_api import sync_playwright

    # 延迟导入，避免循环
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).parent))
    from browser_config import launch_options, launch_options_fallback, context_options, NAVIGATOR_INIT_SCRIPT

    name = account["name"]
    storage_path = Path(account.get("storage_path", "")).expanduser()

    if not storage_path.exists():
        return {"ok": False, "name": name, "message": f"storageState 不存在: {storage_path}"}

    print(f"  [keepalive] 账号: {name}")
    api_results = {}

    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(**launch_options(headless=headless))
        except Exception:
            browser = pw.chromium.launch(**launch_options_fallback(headless=headless))

        context = browser.new_context(
            **context_options(storage_state=str(storage_path))
        )
        context.add_init_script(NAVIGATOR_INIT_SCRIPT)

        # 监听 API 响应，记录保活命中率
        def _on_response(response):
            for path in WARMUP_PATHS:
                if path in response.url:
                    api_results[path] = response.status

        page = context.new_page()
        page.on("response", _on_response)

        try:
            print(f"  [keepalive] 访问主页...")
            page.goto("https://www.xiaohongshu.com/", wait_until="domcontentloaded", timeout=30000)

            # 检查是否被踢出
            if "login" in page.url or "signin" in page.url:
                browser.close()
                return {"ok": False, "name": name, "message": "session 已失效，需要重新扫码登录"}

            # 等待 user/me 响应，比 sleep 更可靠
            try:
                page.wait_for_response(
                    lambda r: "/api/sns/web/v2/user/me" in r.url,
                    timeout=10000,
                )
            except Exception:
                print(f"  [keepalive] 警告: /user/me 未在 10s 内响应")

            print(f"  [keepalive] 访问 explore...")
            page.goto("https://www.xiaohongshu.com/explore", wait_until="domcontentloaded", timeout=20000)

            # 等待 unread_count（常见刷新信号）
            try:
                page.wait_for_response(
                    lambda r: "/api/sns/web/unread_count" in r.url,
                    timeout=8000,
                )
            except Exception:
                pass

            context.storage_state(path=str(storage_path))
            print(f"  [keepalive] storageState 已更新: {storage_path}")

            hit = [p for p in WARMUP_PATHS if p in api_results]
            print(f"  [keepalive] API 预热命中: {len(hit)}/{len(WARMUP_PATHS)} — {hit}")

        except Exception as e:
            browser.close()
            return {"ok": False, "name": name, "message": f"浏览器异常: {e}"}

        browser.close()

    return {"ok": True, "name": name, "message": "保活成功", "api_hits": api_results}
```

- [ ] **Step 4: 跑测试**

```bash
.venv/bin/pytest tests/test_keepalive.py -v
```

Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/xhs_keepalive.py tests/test_keepalive.py
git commit -m "fix: keepalive uses browser_config + waits for real API responses instead of sleep"
```

---

## Task 4: 重写 `xhs_network_spy.py`——真正 CDP 抓包 + `--bot` 模式

**Files:**
- Rewrite: `scripts/xhs_network_spy.py`

背景：当前用 `page.on("request")` 只能看到 JS 层可见的请求头，httpOnly Cookie 不在其中。需要用 `CDPSession` + `Network.requestWillBeSentExtraInfo`（浏览器网络栈层），才能拿到完整 Cookie 和真实头。

`--bot` 模式：加载指定账号的 cookie，用 `requests` 库直接发一次 `/api/sns/web/v2/user/me`，捕获 xhs 库（`local_sign`）生成的请求头，与浏览器头做结构对比。

- [ ] **Step 1: 写失败测试（结构验证，不依赖真实网络）**

```python
# tests/test_network_spy.py
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from xhs_network_spy import is_xhs_api, extract_anti_headers, extract_cookies_from_extra_info


def test_is_xhs_api_matches_edith():
    assert is_xhs_api("https://edith.xiaohongshu.com/api/sns/web/v2/user/me") is True


def test_is_xhs_api_rejects_cdn():
    assert is_xhs_api("https://ci.xiaohongshu.com/image.jpg") is False


def test_extract_anti_headers_picks_xs():
    headers = [
        {"name": "x-s", "value": "XYS_abc"},
        {"name": "x-t", "value": "1234567890"},
        {"name": "content-type", "value": "application/json"},
    ]
    result = extract_anti_headers(headers)
    assert result["x-s"] == "XYS_abc"
    assert result["x-t"] == "1234567890"
    assert "content-type" not in result


def test_extract_cookies_from_extra_info():
    # requestWillBeSentExtraInfo 里 associatedCookies 格式
    extra_info = {
        "associatedCookies": [
            {"cookie": {"name": "a1", "value": "abc123", "httpOnly": False}},
            {"cookie": {"name": "web_session", "value": "sess456", "httpOnly": True}},
            {"cookie": {"name": "foo", "value": "bar", "httpOnly": False}},
        ]
    }
    result = extract_cookies_from_extra_info(extra_info)
    # 只返回反爬虫关键 cookie
    assert result["a1"] == "abc123"
    assert result["web_session"] == "sess456"
    assert "foo" not in result
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/pytest tests/test_network_spy.py -v
```

Expected: `ImportError: cannot import name 'is_xhs_api'`

- [ ] **Step 3: 重写 `scripts/xhs_network_spy.py`**

```python
#!/usr/bin/env python3
"""
XHS 网络请求监控工具 — 真正 CDP 级抓包

用途：
  1. 打开 Chrome（有头模式），通过 CDPSession + requestWillBeSentExtraInfo
     拦截完整请求头（含 httpOnly Cookie），解决 page.on('request') 看不到 Cookie 的问题
  2. --bot 模式：用 requests 库模拟 xhs 库发一次真实 API 请求，与浏览器头做结构对比
  3. --compare 模式：对比两次录制结果

用法：
    python scripts/xhs_network_spy.py                       # 监控人工操作
    python scripts/xhs_network_spy.py --account 账号A       # 加载账号 storageState
    python scripts/xhs_network_spy.py --bot --account 账号A # bot 模式（需安装 xhs 包）
    python scripts/xhs_network_spy.py --compare human.json bot.json
"""

import asyncio
import json
import sys
import time
import argparse
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

BASE_DIR = Path(__file__).parent.parent
OUTPUT_DIR = BASE_DIR / "output" / "network_spy"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# 只关注这些 XHS API 域
XHS_DOMAINS = {
    "edith.xiaohongshu.com",
    "www.xiaohongshu.com",
    "fe-api.xiaohongshu.com",
}

# 反爬虫关键请求头（小写）
ANTI_CRAWLER_HEADERS = {
    "x-s", "x-t", "x-s-common", "x-b3-traceid", "x-e-traceid",
    "x-platform", "x-mini-app-id", "x-xray-traceid",
    "referer", "origin", "user-agent",
    "sec-ch-ua", "sec-ch-ua-mobile", "sec-ch-ua-platform",
    "sec-fetch-site", "sec-fetch-mode",
    "cookie",
}

# 反爬虫关键 Cookie 字段
ANTI_CRAWLER_COOKIES = {
    "a1", "web_session", "webId", "gid",
    "customer-sso-sid", "xsecappid", "websectiga", "sec_poison_id",
}


# ── 纯函数（可单元测试）────────────────────────────────────────────────────────

def is_xhs_api(url: str) -> bool:
    """判断 URL 是否属于 XHS API 域（排除 CDN 图片等）。"""
    return urlparse(url).netloc in XHS_DOMAINS


def extract_anti_headers(headers: list) -> dict:
    """
    从 CDP headers 列表（[{name, value}, ...]）提取反爬虫关键头。
    CDP requestWillBeSent / requestWillBeSentExtraInfo 都用这个格式。
    """
    return {
        h["name"].lower(): h["value"]
        for h in headers
        if h["name"].lower() in ANTI_CRAWLER_HEADERS
    }


def extract_cookies_from_extra_info(extra_info: dict) -> dict:
    """
    从 CDP requestWillBeSentExtraInfo.associatedCookies 提取关键 Cookie。
    这是唯一能看到 httpOnly Cookie 的层级。
    返回 {name: value} 仅含 ANTI_CRAWLER_COOKIES 中的字段。
    """
    result = {}
    for item in extra_info.get("associatedCookies", []):
        cookie = item.get("cookie", {})
        name = cookie.get("name", "")
        if name in ANTI_CRAWLER_COOKIES:
            result[name] = cookie.get("value", "")[:80]
    return result


# ── CDP 录制核心 ──────────────────────────────────────────────────────────────

class CdpRecord:
    """单次请求的完整 CDP 级记录（requestWillBeSent + requestWillBeSentExtraInfo 合并）。"""

    def __init__(self):
        self.request_id: str = ""
        self.timestamp: float = 0.0
        self.url: str = ""
        self.method: str = ""
        self.path: str = ""
        self.domain: str = ""
        # requestWillBeSent 提供
        self.headers_basic: dict = {}
        self.post_data: str = ""
        # requestWillBeSentExtraInfo 提供（含 httpOnly cookie）
        self.headers_extra: dict = {}
        self.cookies_from_extra: dict = {}

    def to_dict(self) -> dict:
        # 合并两层头（extra 优先，因为它更完整）
        merged_anti = {**self.headers_basic, **self.headers_extra}
        xs = merged_anti.get("x-s", "")
        return {
            "timestamp": datetime.fromtimestamp(self.timestamp).isoformat(),
            "request_id": self.request_id,
            "method": self.method,
            "url": self.url,
            "path": self.path,
            "domain": self.domain,
            "anti_crawler": {
                "headers": merged_anti,
                "cookies": self.cookies_from_extra,
            },
            "x_s_prefix_84": xs[:84] if xs else "",
            "x_s_tail": xs[-24:] if xs else "",
            "post_data_preview": self.post_data[:300] if self.post_data else None,
        }


class NetworkSpy:
    def __init__(self, mode: str = "human", account_name: str = None):
        self.mode = mode
        self.account_name = account_name
        self.records: list[CdpRecord] = []
        self._pending: dict[str, CdpRecord] = {}  # requestId → record
        self.start_time: float = 0.0

    def _on_request_will_be_sent(self, params: dict):
        req = params.get("request", {})
        url = req.get("url", "")
        if not is_xhs_api(url):
            return
        parsed = urlparse(url)
        record = CdpRecord()
        record.request_id = params["requestId"]
        record.timestamp = time.time() - self.start_time
        record.url = url
        record.method = req.get("method", "GET")
        record.path = parsed.path
        record.domain = parsed.netloc
        record.headers_basic = extract_anti_headers(
            [{"name": k, "value": v} for k, v in req.get("headers", {}).items()]
        )
        record.post_data = req.get("postData", "")
        self._pending[record.request_id] = record

        has_sig = "x-s" in record.headers_basic
        print(f"  {'🔐' if has_sig else '  '} [{record.method}] {record.path[:60]}")

    def _on_request_extra_info(self, params: dict):
        request_id = params.get("requestId", "")
        record = self._pending.get(request_id)
        if record is None:
            return
        # extraInfo headers 是列表格式
        raw_headers = params.get("headers", {})
        # CDP extra headers 可能是 dict（name→value）格式
        if isinstance(raw_headers, dict):
            header_list = [{"name": k, "value": v} for k, v in raw_headers.items()]
        else:
            header_list = raw_headers
        record.headers_extra = extract_anti_headers(header_list)
        record.cookies_from_extra = extract_cookies_from_extra_info(params)

        if record.cookies_from_extra:
            print(f"       🍪 cookies: {list(record.cookies_from_extra.keys())}")

        # 合并完成，移入 records
        self.records.append(record)
        del self._pending[request_id]

    async def run_human_mode(self):
        from playwright.async_api import async_playwright
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).parent))
        from browser_config import launch_options, launch_options_fallback, context_options, NAVIGATOR_INIT_SCRIPT

        print("=" * 60)
        print("🕵️  XHS CDP 网络监控 — 人工操作模式")
        print("=" * 60)
        print("浏览器已启动，完成操作后关闭窗口停止录制")
        print("=" * 60)

        async with async_playwright() as p:
            try:
                browser = await p.chromium.launch(**launch_options(headless=False))
            except Exception:
                browser = await p.chromium.launch(**launch_options_fallback(headless=False))

            storage_path = self._get_storage_path()
            context = await browser.new_context(**context_options(storage_state=storage_path))
            await context.add_init_script(NAVIGATOR_INIT_SCRIPT)
            page = await context.new_page()

            # 开启 CDP Session
            cdp = await context.new_cdp_session(page)
            await cdp.send("Network.enable")
            cdp.on("Network.requestWillBeSent", self._on_request_will_be_sent)
            cdp.on("Network.requestWillBeSentExtraInfo", self._on_request_extra_info)

            self.start_time = time.time()
            url = "https://www.xiaohongshu.com/login" if not storage_path else "https://www.xiaohongshu.com/"
            await page.goto(url)
            print("\n📡 CDP 监控已启动...\n")

            try:
                await page.wait_for_event("close", timeout=300_000)
            except Exception:
                pass
            finally:
                try:
                    await browser.close()
                except Exception:
                    pass

    async def run_bot_mode(self):
        """
        用现有账号 cookie 通过 requests 直接调用 /api/sns/web/v2/user/me，
        模拟 xhs 库发出的请求，捕获其请求头结构。
        """
        storage_path = self._get_storage_path()
        if not storage_path:
            print("❌ --bot 模式需要 --account 指定账号")
            return

        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).parent))
        from account_manager import extract_cookie_string

        try:
            cookie = extract_cookie_string(storage_path)
        except Exception as e:
            print(f"❌ 无法读取 cookie: {e}")
            return

        # 尝试用 xhs 库签名
        try:
            from xhs import XhsClient
            from xhs.help import sign as local_sign
            cookies_dict = {}
            for part in cookie.split(";"):
                part = part.strip()
                if "=" in part:
                    k, v = part.split("=", 1)
                    cookies_dict[k.strip()] = v.strip()
            a1 = cookies_dict.get("a1", "")

            def sign_func(uri, data=None, a1_param="", web_session=""):
                return local_sign(uri, data, a1=a1 or a1_param)

            client = XhsClient(cookie=cookie, sign=sign_func)
        except ImportError:
            print("⚠️  xhs 包未安装，bot 模式将只发原始 requests 请求（无签名）")
            client = None

        import requests as _req

        uri = "/api/sns/web/v2/user/me"
        url = f"https://edith.xiaohongshu.com{uri}"

        if client:
            # 拦截 xhs 库请求头
            try:
                sign_result = local_sign(uri)
                xs = sign_result.get("x-s", "")
                xt = sign_result.get("x-t", "")
            except Exception as e:
                print(f"⚠️  签名失败: {e}")
                xs, xt = "", ""

            bot_headers = {
                "cookie": cookie,
                "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                "x-s": xs,
                "x-t": xt,
                "referer": "https://www.xiaohongshu.com/",
                "origin": "https://www.xiaohongshu.com",
            }
        else:
            bot_headers = {
                "cookie": cookie,
                "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                "referer": "https://www.xiaohongshu.com/",
            }

        print(f"\n🤖 Bot 模式：发送请求到 {url}")
        print(f"   签名 x-s: {bot_headers.get('x-s', '(无)')[:40]}")

        self.start_time = time.time()
        record = CdpRecord()
        record.request_id = "bot-0"
        record.timestamp = 0.0
        record.url = url
        record.method = "GET"
        record.path = uri
        record.domain = "edith.xiaohongshu.com"
        record.headers_basic = extract_anti_headers(
            [{"name": k, "value": v} for k, v in bot_headers.items()]
        )
        # Cookie 从 cookie header 解析
        for part in cookie.split(";"):
            part = part.strip()
            if "=" in part:
                name, val = part.split("=", 1)
                name = name.strip()
                if name in ANTI_CRAWLER_COOKIES:
                    record.cookies_from_extra[name] = val.strip()[:80]
        self.records.append(record)

        try:
            resp = _req.get(url, headers=bot_headers, timeout=15)
            print(f"   响应状态: {resp.status_code}")
        except Exception as e:
            print(f"   请求异常: {e}")

    def _get_storage_path(self):
        if not self.account_name:
            return None
        p = Path.home() / ".xhs-accounts" / self.account_name / "storage.json"
        if p.exists():
            print(f"📂 加载账号 '{self.account_name}' storageState")
            return str(p)
        print(f"⚠️  账号 '{self.account_name}' storageState 不存在，以空 session 启动")
        return None

    def save_records(self, label: str = "human") -> Path:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = OUTPUT_DIR / f"spy_{label}_{ts}.json"
        summary = self._build_summary()
        data = {
            "meta": {
                "mode": self.mode,
                "label": label,
                "cdp_mode": True,
                "recorded_at": datetime.now().isoformat(),
                "total_requests": len(self.records),
                "duration_seconds": time.time() - (self.start_time or time.time()),
            },
            "summary": summary,
            "requests": [r.to_dict() for r in self.records],
        }
        out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n✅ 已保存 {len(self.records)} 条请求记录 → {out_path}")
        return out_path

    def _build_summary(self) -> dict:
        signed = [r for r in self.records if r.headers_basic.get("x-s") or r.headers_extra.get("x-s")]
        xs_prefixes = {(r.headers_basic.get("x-s", "") or r.headers_extra.get("x-s", ""))[:84] for r in signed}
        xs_tails = {(r.headers_basic.get("x-s", "") or r.headers_extra.get("x-s", ""))[-24:] for r in signed}
        uas = {(r.headers_basic.get("user-agent") or r.headers_extra.get("user-agent", "")) for r in self.records if r.headers_basic.get("user-agent") or r.headers_extra.get("user-agent")}
        sec_uas = {(r.headers_basic.get("sec-ch-ua") or r.headers_extra.get("sec-ch-ua", "")) for r in self.records if r.headers_basic.get("sec-ch-ua") or r.headers_extra.get("sec-ch-ua")}
        cookie_keys = set()
        for r in self.records:
            cookie_keys.update(r.cookies_from_extra.keys())
        first_cookies = {}
        for r in self.records:
            if r.cookies_from_extra:
                first_cookies = r.cookies_from_extra
                break

        return {
            "signed_requests": len(signed),
            "total_requests": len(self.records),
            "unique_user_agents": list(uas),
            "unique_sec_ch_ua": list(sec_uas),
            "x_s_prefixes_84char": list(xs_prefixes),
            "x_s_tails_24char": list(xs_tails),
            "cookie_fields_observed": list(cookie_keys),
            "first_cookies": first_cookies,
            "api_paths_visited": list(dict.fromkeys(r.path for r in self.records))[:20],
        }


# ── 对比函数 ──────────────────────────────────────────────────────────────────

def compare_recordings(human_file: Path, bot_file: Path):
    human = json.loads(human_file.read_text(encoding="utf-8"))
    bot = json.loads(bot_file.read_text(encoding="utf-8"))
    h, b = human["summary"], bot["summary"]

    print("\n" + "=" * 60)
    print("📊 人工 vs Bot 请求对比")
    print("=" * 60)

    print("\n【User-Agent】")
    for ua in h.get("unique_user_agents", []):
        print(f"  人工: {ua}")
    for ua in b.get("unique_user_agents", []):
        print(f"  Bot:  {ua}")

    print("\n【sec-ch-ua vs UA 版本一致性】")
    import re
    for label, summary in [("人工", h), ("Bot", b)]:
        for ua in summary.get("unique_user_agents", []):
            for sec in summary.get("unique_sec_ch_ua", []):
                ua_ver = re.search(r"Chrome/(\d+)", ua)
                sec_ver = re.search(r'Google Chrome.*?v="(\d+)"', sec)
                if ua_ver and sec_ver:
                    match = ua_ver.group(1) == sec_ver.group(1)
                    flag = "✅" if match else "❌"
                    print(f"  {flag} {label}: UA=Chrome/{ua_ver.group(1)}, sec-ch-ua=v{sec_ver.group(1)}")

    print("\n【Cookie（CDPSession 真实捕获）】")
    h_c = h.get("first_cookies", {})
    b_c = b.get("first_cookies", {})
    all_keys = sorted(set(h_c) | set(b_c))
    for k in all_keys:
        hv = h_c.get(k, "(无)")
        bv = b_c.get(k, "(无)")
        match = "✅" if hv == bv else "❌"
        print(f"  {match} {k}:")
        print(f"       人工: {hv[:50]}")
        print(f"       Bot:  {bv[:50]}")

    print("\n【x-s 签名前缀（设备指纹段）】")
    print(f"  人工: {list(h.get('x_s_prefixes_84char', []))[:2]}")
    print(f"  Bot:  {list(b.get('x_s_prefixes_84char', []))[:2]}")

    print("\n【x-s 签名尾段（session 固定段）】")
    print(f"  人工唯一尾段: {list(h.get('x_s_tails_24char', []))[:3]}")
    print(f"  Bot  唯一尾段: {list(b.get('x_s_tails_24char', []))[:3]}")
    print("=" * 60)


# ── CLI ───────────────────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(
        description="XHS CDP 网络监控：对比人工和自动化的反爬虫参数",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--account", "-a", default=None, help="账号名称（加载其 storageState）")
    parser.add_argument("--bot", action="store_true", help="Bot 模式：用 xhs 库发请求并录制头信息")
    parser.add_argument("--label", default=None, help="录制文件标签")
    parser.add_argument("--compare", nargs=2, metavar=("HUMAN_JSON", "BOT_JSON"),
                        help="对比两个录制文件")
    args = parser.parse_args()

    if args.compare:
        h, b = Path(args.compare[0]), Path(args.compare[1])
        for f in (h, b):
            if not f.exists():
                print(f"❌ 文件不存在: {f}")
                sys.exit(1)
        compare_recordings(h, b)
        return

    if args.bot:
        mode = "bot"
        label = args.label or f"bot_{args.account or 'env'}"
    else:
        mode = "human"
        label = args.label or ("human" if not args.account else f"human_{args.account}")

    spy = NetworkSpy(mode=mode, account_name=args.account)

    try:
        if args.bot:
            await spy.run_bot_mode()
        else:
            await spy.run_human_mode()
    except KeyboardInterrupt:
        print("\n⏹  监控已停止")
    finally:
        if spy.records:
            out = spy.save_records(label=label)
            summary = spy._build_summary()
            print(f"\n🔍 摘要: {summary['signed_requests']}/{summary['total_requests']} 请求含签名")
            print(f"   Cookie 字段: {summary['cookie_fields_observed']}")
            if summary.get("x_s_prefixes_84char"):
                print(f"   x-s 前缀(84): {list(summary['x_s_prefixes_84char'])[0][:50]}...")
        else:
            print("⚠️  未录制到 XHS API 请求")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 4: 跑单元测试**

```bash
.venv/bin/pytest tests/test_network_spy.py -v
```

Expected: 4 passed

- [ ] **Step 5: 跑全量测试确认无回归**

```bash
.venv/bin/pytest tests/ -v --ignore=tests/__pycache__
```

Expected: 全部 pass

- [ ] **Step 6: Commit**

```bash
git add scripts/xhs_network_spy.py tests/test_network_spy.py
git commit -m "feat: rewrite xhs_network_spy with real CDPSession + --bot mode; extract testable helpers"
```

---

## 最终验证

- [ ] **手动冒烟测试**：`python scripts/xhs_network_spy.py --account 账号A`，打开浏览器，浏览几个页面，确认终端打印出 `🍪 cookies: ['a1', 'web_session', ...]`

- [ ] **Bot 模式冒烟测试**：`python scripts/xhs_network_spy.py --bot --account 账号A`，确认输出 bot 请求头摘要

- [ ] **对比冒烟测试**：`python scripts/xhs_network_spy.py --compare output/network_spy/spy_human_*.json output/network_spy/spy_bot_*.json`

- [ ] **全量测试**：

```bash
.venv/bin/pytest tests/ -v
```

Expected: 全部 pass

---

## 自检：规格覆盖

| Codex 问题 | 对应 Task |
|-----------|-----------|
| sec-ch-ua vs UA 不匹配 | Task 1 (browser_config 去掉 hardcode UA) |
| _playwright_login 无 channel/webdriver 隐藏 | Task 2 Step 5 |
| cookie_string_to_storage_state httpOnly 错误 | Task 2 Steps 1-4 |
| keepalive 只访问页面不等 API | Task 3 |
| xhs_network_spy 非真 CDP | Task 4 |
| --bot 模式未实现 | Task 4 |
| 各脚本指纹策略不统一 | Task 1 (browser_config 统一入口) |
| publish_xhs.py sign 无法证明一致 | 超出代码修复范围，需 xhs 包装层签名 diff 测试（手动验证步骤已含） |
