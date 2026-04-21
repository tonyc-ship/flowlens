# Anti-Ban Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 通过人工 vs Bot 网络请求对比，找出 XHS 风控感知的参数差异，修复 explore_v3.js 和 browser_config.py，让自动化请求的参数链与真实浏览器完全一致。

**Architecture:** 
分三阶段：① 增强 xhs_network_spy.py 捕获更多参数（localStorage、Cookie 变更时序、完整 POST body、x-s-common 解码）；② 用户人工操作 + Bot 对比录制，生成 diff 报告；③ 根据 diff 修复 explore_v3.js（补指纹注入 + 预热序列）和 browser_config.py（强化 NAVIGATOR_INIT_SCRIPT）。

**Tech Stack:** Playwright (JS + Python), Node.js ESM, Python 3.9, xhs_network_spy.py

---

## 文件变更地图

| 文件 | 动作 | 职责 |
|---|---|---|
| `scripts/xhs_network_spy.py` | 修改 | 新增 localStorage 捕获、Cookie 变更监听、完整 POST body、x-s-common 解码、事件时序记录 |
| `mcp/explore/scripts/explore_v3.js` | 修改 | 补上 browser-fingerprint.js 三件套 + warmupSession |
| `scripts/browser_config.py` | 修改 | NAVIGATOR_INIT_SCRIPT 补全 WebGL / canvas / plugins / hardwareConcurrency |

---

## Task 1：增强 xhs_network_spy.py — 捕获完整参数链

> 目标：明天用户人工操作时，能录制到 XHS 风控所依赖的所有参数，包括 localStorage 里的 b1/b1b1、Cookie 变更时序、x-s-common 解码内容。

**Files:**
- Modify: `scripts/xhs_network_spy.py`

- [ ] **Step 1.1：在 `RequestRecord` 中补全 POST body 完整捕获**

在 `_safe_post_preview` 方法里，现在截断了 body（`[:300]`）。改为保留完整内容并单独存字段：

```python
# 在 RequestRecord.to_dict() 的 return dict 里加一个字段
def to_dict(self):
    parsed = urlparse(self.url)
    cookies = self._extract_cookies()
    anti_headers = self._extract_anti_headers()
    return {
        "timestamp": datetime.fromtimestamp(self.timestamp).isoformat(),
        "method": self.method,
        "url": self.url,
        "path": parsed.path,
        "domain": parsed.netloc,
        "resource_type": self.resource_type,
        "anti_crawler": {
            "headers": anti_headers,
            "cookies": cookies,
        },
        "all_headers": self.headers,
        "post_data_preview": self._safe_post_preview(),
        "post_data_full": self._full_post_data(),   # ← 新增
        "x_s_common_decoded": self._decode_xs_common(anti_headers),  # ← 新增
    }

def _full_post_data(self):
    """完整 POST body，用于分析搜索请求的所有字段"""
    if not self.post_data:
        return None
    try:
        return json.loads(self.post_data)
    except Exception:
        return self.post_data

def _decode_xs_common(self, anti_headers: dict):
    """解码 x-s-common（base64 → JSON），展示其中的设备/签名字段"""
    xs_common = anti_headers.get("x-s-common", "")
    if not xs_common:
        return None
    try:
        import base64
        decoded = base64.b64decode(xs_common + "==").decode("utf-8", errors="replace")
        return json.loads(decoded)
    except Exception:
        return {"raw": xs_common[:100]}
```

- [ ] **Step 1.2：注入 localStorage 监听脚本**

在 `run_human_mode` 和 `run_bot_mode` 里，`page` 创建后立即注入监听脚本，捕获 XHS 写入 localStorage 的 `b1` / `b1b1` 等关键值：

```python
# 在 page = await context.new_page() 之后、page.goto() 之前插入
await context.add_init_script("""
(function() {
    const _ls = {};
    const _orig_set = localStorage.setItem.bind(localStorage);
    localStorage.setItem = function(k, v) {
        _orig_set(k, v);
        if (!window.__xhs_ls_log) window.__xhs_ls_log = [];
        window.__xhs_ls_log.push({ op: 'set', key: k, value: v.slice(0, 200), ts: Date.now() });
    };
    const _orig_get = localStorage.getItem.bind(localStorage);
    localStorage.getItem = function(k) {
        const v = _orig_get(k);
        if (!window.__xhs_ls_log) window.__xhs_ls_log = [];
        window.__xhs_ls_log.push({ op: 'get', key: k, value: v ? v.slice(0, 200) : null, ts: Date.now() });
        return v;
    };
})();
""")
```

- [ ] **Step 1.3：在 save_records 保存前，从页面读取 localStorage 日志**

在 `save_records` 方法里调用 `page.evaluate` 获取 `__xhs_ls_log`。为此 `NetworkSpy` 需要持有 `page` 引用：

```python
# NetworkSpy.__init__ 加
self.page = None  # 在 run_*_mode 里赋值

# run_human_mode 里 page 创建后
self.page = page

# save_records 里，在 data dict 里加
"localStorage_log": await self._get_ls_log(),

# 新增方法
async def _get_ls_log(self):
    if not self.page:
        return []
    try:
        return await self.page.evaluate("() => window.__xhs_ls_log || []")
    except Exception:
        return []
```

- [ ] **Step 1.4：记录 Cookie 变更时序**

注入 `document.cookie` setter 拦截，捕获 XHS JS 在页面里写入的 Cookie（acw_tc、loadts、ets 等是由 JS 动态写入的）：

```python
# 在 Step 1.2 的 add_init_script 内容里追加
"""
(function() {
    const _desc = Object.getOwnPropertyDescriptor(Document.prototype, 'cookie');
    if (!window.__xhs_cookie_log) window.__xhs_cookie_log = [];
    Object.defineProperty(document, 'cookie', {
        get: function() { return _desc.get.call(this); },
        set: function(val) {
            window.__xhs_cookie_log.push({ value: val.slice(0, 300), ts: Date.now() });
            return _desc.set.call(this, val);
        },
        configurable: true,
    });
})();
"""

# save_records 里加
"cookie_mutation_log": await self._get_cookie_log(),

async def _get_cookie_log(self):
    if not self.page:
        return []
    try:
        return await self.page.evaluate("() => window.__xhs_cookie_log || []")
    except Exception:
        return []
```

- [ ] **Step 1.5：在 compare_recordings 里新增 x-s-common 字段对比**

```python
# 在 compare_recordings 函数末尾加
print("\n【x-s-common 解码对比（前2条）】")
for label, data in [("人工", human), ("Bot", bot)]:
    for req in data.get("requests", [])[:20]:
        dec = req.get("x_s_common_decoded")
        if dec and isinstance(dec, dict):
            print(f"  {label}: s0={dec.get('s0')} x1={dec.get('x1')} x2={dec.get('x2')} x3={dec.get('x3')} x10={dec.get('x10')}")
            break

print("\n【localStorage b1/b1b1 对比】")
for label, data in [("人工", human), ("Bot", bot)]:
    ls = data.get("localStorage_log", [])
    b1_writes = [e for e in ls if e.get("key") in ("b1", "b1b1") and e.get("op") == "set"]
    if b1_writes:
        print(f"  {label} b1 writes: {b1_writes[:3]}")
    else:
        print(f"  {label}: 未捕获到 b1/b1b1 写入")

print("\n【Cookie 动态写入（JS 触发）对比】")
for label, data in [("人工", human), ("Bot", bot)]:
    clog = data.get("cookie_mutation_log", [])
    print(f"  {label} cookie mutations: {len(clog)} 次")
    for entry in clog[:5]:
        print(f"    {entry.get('value', '')[:80]}")
```

- [ ] **Step 1.6：验证脚本能正常启动**

```bash
cd /Users/chenqinghua/Desktop/Auto-Redbook-Skills
python scripts/xhs_network_spy.py --help
```

期望输出：显示 usage，无报错。

- [ ] **Step 1.7：commit**

```bash
cd /Users/chenqinghua/Desktop/Auto-Redbook-Skills
git add scripts/xhs_network_spy.py
git commit -m "feat: enhance network spy with localStorage, cookie mutations, full POST body, x-s-common decode"
```

---

## Task 2：【用户操作】人工录制 + Bot 对比

> 这一步由用户手动执行，Claude 不参与。完成后输出两个 JSON 文件给 Task 3 分析。

**Files:**
- Read: `output/network_spy/spy_human_*.json`
- Read: `output/network_spy/spy_bot_*.json`

- [ ] **Step 2.1：启动人工录制模式**

```bash
# 打开有头浏览器，监控人工操作
python scripts/xhs_network_spy.py --account <你的账号名>
```

在浏览器里：
1. 等页面完全加载（约 3 秒）
2. 在搜索框输入一个关键词（如"护肤"）并回车
3. 滚动浏览搜索结果 2-3 屏
4. 点击一条笔记打开
5. 返回搜索结果
6. 关闭浏览器窗口

期望：`output/network_spy/spy_human_YYYYMMDD_HHMMSS.json`

- [ ] **Step 2.2：启动 Bot 录制模式**

```bash
python scripts/xhs_network_spy.py --bot --account <你的账号名>
```

期望：`output/network_spy/spy_bot_YYYYMMDD_HHMMSS.json`

- [ ] **Step 2.3：生成对比报告**

```bash
python scripts/xhs_network_spy.py --compare \
  output/network_spy/spy_human_<时间戳>.json \
  output/network_spy/spy_bot_<时间戳>.json
```

**重点观察：**
- `x-s-common` 里 `x1`（版本号）、`x2`（平台）、`x3`（appId）字段是否匹配
- `b1` / `b1b1` localStorage 写入是否存在于 Bot 侧
- Cookie 动态写入（`acw_tc`、`loadts`、`ets`）时序是否一致
- Bot 缺少哪些初始化 API 路径（`/api/sns/web/v1/system/config` 等）

---

## Task 3：修复 explore_v3.js — 补上指纹注入 + 预热序列

> 已知 explore_v3.js 完全没有使用 browser-fingerprint.js，导致浏览器以 Chromium 默认状态运行，XHS 的 JS 生成的 x-s-common 包含暴露自动化的特征值。

**Files:**
- Modify: `mcp/explore/scripts/explore_v3.js:1-5`（imports）
- Modify: `mcp/explore/scripts/explore_v3.js:374-377`（browser launch block）

- [ ] **Step 3.1：在文件顶部补上 browser-fingerprint.js 的 import**

当前 `explore_v3.js` 第 1-4 行：
```js
import { chromium } from 'playwright'
import { getValidSession, getSessionError, markAccountUsed } from './auth.js'

const SEARCH_URL = 'https://www.xiaohongshu.com/search_result?keyword='
```

替换为：
```js
import { chromium } from 'playwright'
import { getValidSession, getSessionError, markAccountUsed, cookieStringToStorageState } from './auth.js'
import {
  NAVIGATOR_INIT_SCRIPT,
  launchOptions,
  launchOptionsFallback,
  contextOptions,
  warmupSession,
} from './browser-fingerprint.js'

const SEARCH_URL = 'https://www.xiaohongshu.com/search_result?keyword='
```

- [ ] **Step 3.2：替换 explore() 函数中的浏览器启动块**

当前第 374-377 行：
```js
  const browser = await chromium.launch({ headless: process.env.HEADLESS !== 'false' })
  const context = await browser.newContext({ storageState: session.storagePath })
  const page = await context.newPage()
```

替换为：
```js
  const headless = process.env.HEADLESS !== 'false'
  let browser
  try {
    browser = await chromium.launch(launchOptions(headless))
  } catch {
    browser = await chromium.launch(launchOptionsFallback(headless))
  }

  const storageStateOpt = session.storagePath
    ? session.storagePath
    : cookieStringToStorageState(session.cookieStr)

  const context = await browser.newContext(contextOptions(storageStateOpt))
  await context.addInitScript(NAVIGATOR_INIT_SCRIPT)
  const page = await context.newPage()
```

- [ ] **Step 3.3：在阶段 1（验证登录）之前加 warmupSession**

当前第 383-391 行（阶段一）：
```js
    // 阶段 1: 验证登录状态
    console.error(`[INFO] 阶段一: 验证登录状态...`)
    await page.goto('https://www.xiaohongshu.com/explore', { waitUntil: 'domcontentloaded', timeout: 30000 })
    
    if (await needsLogin(page)) {
```

在 `await page.goto(...)` 之前插入 warmup：
```js
    // 阶段 1: 验证登录状态 + 预热初始化 API
    console.error(`[INFO] 阶段一: 会话预热 + 验证登录状态...`)
    const warmHits = await warmupSession(page)
    console.error(`[INFO] 预热命中 ${warmHits.length} 个初始化 API: ${warmHits.join(', ')}`)

    if (await needsLogin(page)) {
```

同时删除原来的 `await page.goto('https://www.xiaohongshu.com/explore', ...)` 这行（warmupSession 内部已经访问了首页和 explore 页）。

- [ ] **Step 3.4：验证修改后语法正确**

```bash
cd /Users/chenqinghua/Desktop/Auto-Redbook-Skills
node --input-type=module <<'EOF'
import { explore } from './mcp/explore/scripts/explore_v3.js'
console.log('import OK, explore type:', typeof explore)
EOF
```

期望输出：`import OK, explore type: function`（无报错）

- [ ] **Step 3.5：commit**

```bash
git add mcp/explore/scripts/explore_v3.js
git commit -m "fix: add fingerprint injection and warmup session to explore_v3.js"
```

---

## Task 4：修复 browser_config.py — 强化 NAVIGATOR_INIT_SCRIPT

> Python 侧（keepalive / publish_manager）的 NAVIGATOR_INIT_SCRIPT 只遮了 webdriver 和 languages，缺少 WebGL、canvas 噪声、plugins，与 JS 版 browser-fingerprint.js 不一致，仍会暴露自动化特征。

**Files:**
- Modify: `scripts/browser_config.py`

- [ ] **Step 4.1：替换 NAVIGATOR_INIT_SCRIPT 为完整版**

当前内容：
```python
NAVIGATOR_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
"""
```

替换为与 JS 版 `browser-fingerprint.js` 完全一致的内容：
```python
NAVIGATOR_INIT_SCRIPT = """
(function() {
  // webdriver
  Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

  // language / platform
  Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
  Object.defineProperty(navigator, 'platform',  { get: () => 'MacIntel' });

  // hardware concurrency & device memory
  Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
  Object.defineProperty(navigator, 'deviceMemory',        { get: () => 8 });

  // plugins — 非空列表阻止 headless 检测
  const _pluginData = [
    { name: 'Chrome PDF Plugin',  filename: 'internal-pdf-viewer',             description: 'Portable Document Format' },
    { name: 'Chrome PDF Viewer',  filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
    { name: 'Native Client',      filename: 'internal-nacl-plugin',             description: '' },
  ];
  const _plugins = Object.create(PluginArray.prototype);
  _pluginData.forEach((d, i) => {
    const p = Object.create(Plugin.prototype);
    Object.defineProperty(p, 'name',        { get: () => d.name });
    Object.defineProperty(p, 'filename',    { get: () => d.filename });
    Object.defineProperty(p, 'description', { get: () => d.description });
    Object.defineProperty(p, 'length',      { get: () => 0 });
    Object.defineProperty(_plugins, i,      { get: () => p });
    Object.defineProperty(_plugins, d.name, { get: () => p });
  });
  Object.defineProperty(_plugins, 'length',   { get: () => _pluginData.length });
  Object.defineProperty(_plugins, 'item',     { value: i => _plugins[i] });
  Object.defineProperty(_plugins, 'namedItem',{ value: name => _plugins[name] });
  Object.defineProperty(_plugins, Symbol.iterator, {
    value: function* () { for (let i = 0; i < _pluginData.length; i++) yield _plugins[i]; }
  });
  Object.defineProperty(navigator, 'plugins', { get: () => _plugins });

  // WebGL vendor / renderer
  try {
    const _getParam = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function(param) {
      if (param === 37445) return 'Intel Inc.';
      if (param === 37446) return 'Intel Iris OpenGL Engine';
      return _getParam.call(this, param);
    };
    const _getParam2 = WebGL2RenderingContext.prototype.getParameter;
    WebGL2RenderingContext.prototype.getParameter = function(param) {
      if (param === 37445) return 'Intel Inc.';
      if (param === 37446) return 'Intel Iris OpenGL Engine';
      return _getParam2.call(this, param);
    };
  } catch(e) {}

  // Canvas — 每个 session 固定噪声，防止指纹与 headless 基准一致
  try {
    const _noise = parseFloat((Math.random() * 0.04 - 0.02).toFixed(6));
    const _toDataURL = HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL = function(type, quality) {
      const ctx = this.getContext('2d');
      if (!ctx || this.width === 0 || this.height === 0) {
        return _toDataURL.call(this, type, quality);
      }
      const id = ctx.getImageData(0, 0, this.width, this.height);
      const delta = Math.round(_noise * 255);
      for (let i = 0; i < id.data.length; i += 4) {
        id.data[i]   = Math.min(255, Math.max(0, id.data[i]   + delta));
        id.data[i+1] = Math.min(255, Math.max(0, id.data[i+1] + delta));
        id.data[i+2] = Math.min(255, Math.max(0, id.data[i+2] + delta));
      }
      const scratch = document.createElement('canvas');
      scratch.width  = this.width;
      scratch.height = this.height;
      scratch.getContext('2d').putImageData(id, 0, 0);
      return _toDataURL.call(scratch, type, quality);
    };
  } catch(e) {}
})();
"""
```

- [ ] **Step 4.2：强化 context_options — 加 locale / timezone / viewport 随机化**

当前：
```python
def context_options(storage_state=None, viewport: dict = None) -> dict:
    opts = {
        "viewport": viewport or {"width": 1280, "height": 800},
    }
    if storage_state:
        opts["storage_state"] = storage_state
    return opts
```

替换为：
```python
import random as _random

def context_options(storage_state=None, viewport: dict = None) -> dict:
    w = 1280 + _random.randint(-40, 40)
    h = 800  + _random.randint(-30, 30)
    opts = {
        "viewport": viewport or {"width": w, "height": h},
        "locale": "zh-CN",
        "timezone_id": "Asia/Shanghai",
        "color_scheme": "light",
        "extra_http_headers": {"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
    }
    if storage_state:
        opts["storage_state"] = storage_state
    return opts
```

- [ ] **Step 4.3：验证 keepalive 还能正常导入**

```bash
cd /Users/chenqinghua/Desktop/Auto-Redbook-Skills
python3 -c "
from scripts.browser_config import NAVIGATOR_INIT_SCRIPT, launch_options, context_options
print('OK, init_script length:', len(NAVIGATOR_INIT_SCRIPT))
print('context_options sample:', context_options())
"
```

期望：`OK, init_script length: > 2000`，context_options 包含 locale 和 timezone_id

- [ ] **Step 4.4：commit**

```bash
git add scripts/browser_config.py
git commit -m "fix: strengthen NAVIGATOR_INIT_SCRIPT with WebGL/canvas/plugins, add locale and viewport randomization"
```

---

## Task 5：根据 Task 2 diff 补漏（Task 2 完成后执行）

> Task 2 的对比报告可能会发现 Task 3/4 没覆盖到的差异。这一步根据实际 diff 结果打补丁。

**Files:** 视 diff 结果而定

- [ ] **Step 5.1：读取对比报告，列出 Bot 独有的缺口**

重点看三个区块：
- `【仅人工有的 Cookie 字段】` → 检查是否能通过 warmup 自动填充
- `【x-s-common 解码对比】` → 对比 `x1` 版本号（小红书 webBuild 版本），如不一致需在 NAVIGATOR_INIT_SCRIPT 里补
- `【localStorage b1/b1b1】` → 如果 Bot 侧没有 b1 写入，说明指纹注入时机有问题（注入脚本要在 goto 之前）

- [ ] **Step 5.2：根据发现逐一修复**

常见补丁模式：

**如果 x-s-common.x1 版本号不匹配**（Bot 侧是旧版本）：
在 warmupSession 里，页面加载后用 evaluate 读取页面 webBuild：
```js
// 在 mcp/explore/scripts/browser-fingerprint.js 的 warmupSession 末尾加
const webBuild = await page.evaluate(() => window.__xhs_webBuild || '')
if (webBuild) console.error(`[INFO] webBuild: ${webBuild}`)
```

**如果 Bot 缺少某个 Cookie 字段（如 customer-sso-sid）**：
在 `auth.js` 的 `cookieStringToStorageState` 里补上对应 cookie attrs。

**如果 Bot 的请求序列缺少某个初始化 API**：
在 `browser-fingerprint.js` 的 `WARMUP_PATHS` 数组里补上该路径，warmupSession 会自动等待它。

- [ ] **Step 5.3：commit**

```bash
git add <修改的文件>
git commit -m "fix: patch anti-ban gaps discovered from human vs bot network diff"
```

---

## 验收标准

Task 3/4 完成后，再次录制 Bot 模式：

```bash
python scripts/xhs_network_spy.py --bot --account <账号名>
python scripts/xhs_network_spy.py --compare \
  output/network_spy/spy_human_<时间戳>.json \
  output/network_spy/spy_bot_<新时间戳>.json
```

**通过条件：**
- `x-s-common` 解码后 `s0=5, x2=Windows（或MacIntel）, x3=xhs-pc-web` 与人工一致
- Bot 侧 Cookie 字段不再缺失 `acw_tc` / `loadts` / `ets`
- `【仅人工有的 Cookie 字段】` 列表为空
- Bot 侧 API 路径包含 `user/me`、`system/config`、`unread_count` 等预热路径
