#!/usr/bin/env python3
"""
XHS 网络请求监控工具 — 人工 vs 自动化对比

用途：
  1. 打开 Chrome（有头模式）让人工操作小红书登录流程
  2. 通过 Playwright CDP 拦截并记录所有 XHS API 请求
  3. 重点提取反爬虫关键参数：签名头、Cookie、UA、指纹信息
  4. 将结果保存为 JSON，方便与自动化代码请求做 diff 对比

用法：
    python scripts/xhs_network_spy.py                    # 监控人工操作
    python scripts/xhs_network_spy.py --bot              # 用现有 cookie 模拟 bot 请求并录制
    python scripts/xhs_network_spy.py --compare          # 对比两次录制结果
    python scripts/xhs_network_spy.py --account 账号A    # 用指定账号 storageState 启动
"""

import asyncio
import json
import sys
import time
import argparse
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, parse_qs

# ── 配置 ─────────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent.parent
OUTPUT_DIR = BASE_DIR / "output" / "network_spy"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# 只关注这些 XHS API 域
XHS_DOMAINS = {"edith.xiaohongshu.com", "www.xiaohongshu.com", "fe-api.xiaohongshu.com"}

# 反爬虫关键请求头（大小写不敏感）
ANTI_CRAWLER_HEADERS = {
    "x-s",           # XHS 签名
    "x-t",           # 时间戳（签名用）
    "x-s-common",    # 公共签名
    "x-b3-traceid",  # 链路追踪
    "x-e-traceid",   # 另一个追踪 ID
    "x-platform",    # 平台标识
    "x-mini-app-id", # 小程序 ID
    "x-xray-traceid",
    "referer",
    "origin",
    "user-agent",
    "sec-ch-ua",
    "sec-ch-ua-mobile",
    "sec-ch-ua-platform",
    "sec-fetch-site",
    "sec-fetch-mode",
}

# 关键 Cookie 字段
ANTI_CRAWLER_COOKIES = {
    "a1",           # 设备指纹
    "web_session",  # 登录态
    "webId",        # 设备 ID
    "gid",          # GA 用户 ID
    "customer-sso-sid",
    "xsecappid",
    "websectiga",
    "sec_poison_id",
}

# ── 核心录制函数 ──────────────────────────────────────────────────────────────

class RequestRecord:
    """单次请求的完整记录"""
    def __init__(self, request, timestamp: float):
        self.timestamp = timestamp
        self.url = request.url
        self.method = request.method
        self.headers = dict(request.headers)
        self.post_data = request.post_data
        self.resource_type = request.resource_type

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
            "post_data_full": self._full_post_data(),
            "x_s_common_decoded": self._decode_xs_common(anti_headers),
        }

    def _extract_anti_headers(self):
        result = {}
        for k, v in self.headers.items():
            if k.lower() in ANTI_CRAWLER_HEADERS:
                result[k.lower()] = v
        return result

    def _extract_cookies(self):
        cookie_str = self.headers.get("cookie", "")
        if not cookie_str:
            return {}
        result = {}
        for part in cookie_str.split(";"):
            part = part.strip()
            if "=" in part:
                name, val = part.split("=", 1)
                name = name.strip()
                result[name] = val.strip()[:80]  # 保留全部字段，不再过滤
        return result

    def _full_post_data(self):
        """完整 POST body，分析搜索/评论请求的所有参数"""
        if not self.post_data:
            return None
        try:
            return json.loads(self.post_data)
        except Exception:
            return self.post_data

    def _decode_xs_common(self, anti_headers: dict):
        """解码 x-s-common（base64 → JSON），暴露设备/签名字段"""
        import base64
        xs_common = anti_headers.get("x-s-common", "")
        if not xs_common:
            return None
        try:
            # 补齐 padding
            padded = xs_common + "=" * (-len(xs_common) % 4)
            decoded = base64.b64decode(padded).decode("utf-8", errors="replace")
            return json.loads(decoded)
        except Exception:
            return {"raw_prefix": xs_common[:100]}


# 注入到页面的监控脚本：捕获 localStorage 读写、Cookie 变更、fetch/XHR 拦截
_PAGE_MONITOR_SCRIPT = """
(function() {
    if (window.__xhs_spy_injected) return;
    window.__xhs_spy_injected = true;

    window.__xhs_ls_log = [];
    window.__xhs_cookie_log = [];
    window.__xhs_fetch_log = [];
    window.__xhs_event_log = [];

    // ── localStorage 监听 ────────────────────────────────────────────────────
    const _lsSet = Storage.prototype.setItem;
    const _lsGet = Storage.prototype.getItem;
    const _lsRemove = Storage.prototype.removeItem;

    Storage.prototype.setItem = function(k, v) {
        _lsSet.call(this, k, v);
        window.__xhs_ls_log.push({ op: 'set', key: k, value: String(v).slice(0, 300), ts: Date.now() });
    };
    Storage.prototype.getItem = function(k) {
        const v = _lsGet.call(this, k);
        window.__xhs_ls_log.push({ op: 'get', key: k, value: v ? String(v).slice(0, 100) : null, ts: Date.now() });
        return v;
    };
    Storage.prototype.removeItem = function(k) {
        _lsRemove.call(this, k);
        window.__xhs_ls_log.push({ op: 'remove', key: k, ts: Date.now() });
    };

    // ── document.cookie 变更监听 ─────────────────────────────────────────────
    const _cookieDesc = Object.getOwnPropertyDescriptor(Document.prototype, 'cookie');
    if (_cookieDesc && _cookieDesc.set) {
        Object.defineProperty(document, 'cookie', {
            get: function() { return _cookieDesc.get.call(this); },
            set: function(val) {
                window.__xhs_cookie_log.push({ value: String(val).slice(0, 300), ts: Date.now() });
                return _cookieDesc.set.call(this, val);
            },
            configurable: true,
        });
    }

    // ── fetch 拦截：记录 XHS API 请求 URL + body ─────────────────────────────
    const _origFetch = window.fetch;
    window.fetch = function(url, opts) {
        const urlStr = String(url);
        if (urlStr.includes('xiaohongshu.com') || urlStr.includes('xhscdn.com')) {
            const entry = {
                url: urlStr.slice(0, 200),
                method: (opts && opts.method) || 'GET',
                ts: Date.now(),
            };
            try {
                if (opts && opts.body) {
                    entry.body = JSON.parse(opts.body);
                }
            } catch(e) {
                entry.body_raw = String(opts && opts.body || '').slice(0, 200);
            }
            try {
                const headers = {};
                if (opts && opts.headers) {
                    const h = opts.headers;
                    if (h instanceof Headers) {
                        h.forEach((v, k) => { headers[k] = v.slice(0, 100); });
                    } else {
                        Object.entries(h).forEach(([k, v]) => { headers[k] = String(v).slice(0, 100); });
                    }
                }
                entry.headers = headers;
            } catch(e) {}
            window.__xhs_fetch_log.push(entry);
        }
        return _origFetch.apply(this, arguments);
    };

    // ── 关键 DOM 事件监听 ────────────────────────────────────────────────────
    ['visibilitychange', 'focus', 'blur', 'scroll'].forEach(evt => {
        document.addEventListener(evt, () => {
            window.__xhs_event_log.push({ event: evt, ts: Date.now() });
        }, { passive: true });
    });

    // ── navigator 指纹快照（供对比）────────────────────────────────────────
    window.__xhs_nav_snapshot = {
        webdriver: navigator.webdriver,
        platform: navigator.platform,
        hardwareConcurrency: navigator.hardwareConcurrency,
        deviceMemory: navigator.deviceMemory,
        plugins_count: navigator.plugins ? navigator.plugins.length : 0,
        languages: Array.from(navigator.languages || []),
        userAgent: navigator.userAgent,
    };

    // ── WebGL 指纹快照 ───────────────────────────────────────────────────────
    try {
        const canvas = document.createElement('canvas');
        const gl = canvas.getContext('webgl') || canvas.getContext('experimental-webgl');
        if (gl) {
            const ext = gl.getExtension('WEBGL_debug_renderer_info');
            window.__xhs_webgl_snapshot = {
                vendor: ext ? gl.getParameter(ext.UNMASKED_VENDOR_WEBGL) : gl.getParameter(gl.VENDOR),
                renderer: ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER),
            };
        }
    } catch(e) {
        window.__xhs_webgl_snapshot = { error: String(e) };
    }
})();
"""


class NetworkSpy:
    def __init__(self, mode: str = "human", account_name: str = None):
        self.mode = mode
        self.account_name = account_name
        self.records = []
        self.start_time = None
        self.page = None       # 持有引用，供 save_records 读取 JS 数据
        self._page_data = {}   # 浏览器关闭前读取的 JS 监控数据缓存

    def _is_xhs_api(self, url: str) -> bool:
        parsed = urlparse(url)
        return parsed.netloc in XHS_DOMAINS

    async def _record_request(self, request):
        if not self._is_xhs_api(request.url):
            return
        record = RequestRecord(request, time.time() - self.start_time)
        self.records.append(record)
        # 实时打印关键信息
        anti = record._extract_anti_headers()
        has_sig = "x-s" in anti or "x-s-common" in anti
        sig_flag = "🔐" if has_sig else "  "
        path = urlparse(request.url).path[:60]
        print(f"  {sig_flag} [{request.method}] {path}")
        if "x-s" in anti:
            print(f"       x-s: {anti['x-s'][:40]}...")
        if "x-s-common" in anti:
            decoded = record._decode_xs_common(anti)
            if decoded and isinstance(decoded, dict):
                print(f"       x-s-common: s0={decoded.get('s0')} x1={decoded.get('x1')} x3={decoded.get('x3')}")

    async def run_human_mode(self):
        """有头模式，纯被动监控 — 不需要任何特定操作，正常浏览即可"""
        from playwright.async_api import async_playwright

        print("=" * 60)
        print("🕵️  XHS 网络监控 — 纯被动录制模式")
        print("=" * 60)
        print("浏览器已启动，正常使用小红书即可：")
        print("  · 搜索、浏览、点笔记……任意操作")
        print("  · 关闭浏览器窗口后自动保存录制结果")
        print("  · 录制内容：请求头 / Cookie / localStorage / WebGL 指纹 / x-s-common 解码")
        print("=" * 60)

        async with async_playwright() as p:
            try:
                browser = await p.chromium.launch(
                    headless=False,
                    channel="chrome",
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--no-first-run",
                        "--no-default-browser-check",
                    ],
                )
            except Exception:
                browser = await p.chromium.launch(
                    headless=False,
                    args=["--disable-blink-features=AutomationControlled"],
                )

            storage_path = self._get_storage_path()
            import random
            w = 1280 + random.randint(-40, 40)
            h = 800  + random.randint(-30, 30)

            context = await browser.new_context(
                storage_state=storage_path,
                viewport={"width": w, "height": h},
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
                color_scheme="light",
                extra_http_headers={"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
            )

            # 注入全量监控脚本（localStorage / Cookie / fetch / navigator 快照）
            await context.add_init_script(_PAGE_MONITOR_SCRIPT)

            page = await context.new_page()
            self.page = page
            page.on("request", self._record_request)

            self.start_time = time.time()

            url = "https://www.xiaohongshu.com/login" if not storage_path else "https://www.xiaohongshu.com/"
            await page.goto(url)

            print("\n📡 开始监控，正常浏览即可...\n")

            try:
                await page.wait_for_event("close", timeout=600_000)
            except Exception:
                pass
            finally:
                try:
                    # 浏览器关闭前读取 JS 监控数据
                    try:
                        self._page_data = await self._get_page_data()
                        print(f"\n📊 JS 监控数据已收集: "
                              f"localStorage变更={len(self._page_data.get('ls_log', []))} "
                              f"cookie变更={len(self._page_data.get('cookie_log', []))} "
                              f"fetch拦截={len(self._page_data.get('fetch_log', []))}")
                    except Exception as e:
                        print(f"\n⚠️  收集 JS 数据时出错: {e}")
                    if self.account_name:
                        sp = self._get_storage_save_path()
                        if sp:
                            await context.storage_state(path=sp)
                            print(f"💾 storageState 已更新: {sp}")
                    await browser.close()
                except Exception:
                    pass

    def _get_storage_path(self):
        """获取指定账号的 storageState 路径"""
        if not self.account_name:
            return None
        storage_path = Path.home() / ".xhs-accounts" / self.account_name / "storage.json"
        if storage_path.exists():
            print(f"📂 加载账号 '{self.account_name}' 的 storageState")
            return str(storage_path)
        print(f"⚠️  账号 '{self.account_name}' 的 storageState 不存在，以空 session 启动")
        return None

    def _get_storage_save_path(self):
        if not self.account_name:
            return None
        storage_dir = Path.home() / ".xhs-accounts" / self.account_name
        storage_dir.mkdir(parents=True, exist_ok=True)
        return str(storage_dir / "storage.json")

    async def _get_page_data(self) -> dict:
        """从页面读取 JS 监控数据（localStorage / Cookie 变更 / fetch / 指纹快照）

        优先返回已缓存的 _page_data（浏览器关闭前已读取），
        如果缓存为空则尝试从当前页面实时读取。
        """
        if self._page_data:
            return self._page_data
        if not self.page:
            return {}
        try:
            return await self.page.evaluate("""() => ({
                ls_log: window.__xhs_ls_log || [],
                cookie_log: window.__xhs_cookie_log || [],
                fetch_log: window.__xhs_fetch_log || [],
                event_log: window.__xhs_event_log || [],
                nav_snapshot: window.__xhs_nav_snapshot || {},
                webgl_snapshot: window.__xhs_webgl_snapshot || {},
            })""")
        except Exception:
            return {}

    def save_records(self, label: str = "human", page_data: dict = None) -> Path:
        """将录制结果保存为 JSON"""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = OUTPUT_DIR / f"spy_{label}_{ts}.json"

        summary = self._build_summary()
        data = {
            "meta": {
                "mode": self.mode,
                "label": label,
                "recorded_at": datetime.now().isoformat(),
                "total_requests": len(self.records),
                "duration_seconds": time.time() - (self.start_time or time.time()),
            },
            "summary": summary,
            "browser_fingerprint": {
                "navigator": (page_data or {}).get("nav_snapshot", {}),
                "webgl": (page_data or {}).get("webgl_snapshot", {}),
            },
            "localStorage_log": (page_data or {}).get("ls_log", []),
            "cookie_mutation_log": (page_data or {}).get("cookie_log", []),
            "fetch_intercepted": (page_data or {}).get("fetch_log", []),
            "dom_events": (page_data or {}).get("event_log", []),
            "requests": [r.to_dict() for r in self.records],
        }

        out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n✅ 已保存 {len(self.records)} 条请求记录 → {out_path}")
        return out_path

    def _build_summary(self):
        """生成关键信息摘要"""
        signed_count = 0
        sig_values = set()
        user_agents = set()
        cookie_keys_seen = set()
        paths_seen = []
        x_s_headers = []

        for r in self.records:
            d = r.to_dict()
            anti = d["anti_crawler"]
            h = anti["headers"]
            if "x-s" in h:
                signed_count += 1
                sig_values.add(h["x-s"][:20])
                x_s_headers.append({
                    "path": d["path"],
                    "x-s": h["x-s"][:40],
                    "x-t": h.get("x-t", ""),
                    "x-s-common": h.get("x-s-common", "")[:40],
                })
            if "user-agent" in h:
                user_agents.add(h["user-agent"])
            cookie_keys_seen.update(anti["cookies"].keys())
            paths_seen.append(d["path"])

        # 提取第一条请求的 cookie 详情（最完整）
        first_cookies = {}
        for r in self.records:
            c = r._extract_cookies()
            if c:
                first_cookies = c
                break

        return {
            "signed_requests": signed_count,
            "total_requests": len(self.records),
            "unique_user_agents": list(user_agents),
            "cookie_fields_observed": list(cookie_keys_seen),
            "first_cookies": first_cookies,
            "x_s_sample": x_s_headers[:5],  # 前5条带签名的请求
            "api_paths_visited": list(dict.fromkeys(paths_seen))[:20],  # 去重保序
        }


# ── 对比分析函数 ──────────────────────────────────────────────────────────────

def compare_recordings(human_file: Path, bot_file: Path):
    """对比人工和 bot 两次录制的关键差异"""
    human = json.loads(human_file.read_text(encoding="utf-8"))
    bot = json.loads(bot_file.read_text(encoding="utf-8"))

    h_sum = human["summary"]
    b_sum = bot["summary"]

    print("\n" + "=" * 60)
    print("📊 人工 vs Bot 请求对比分析")
    print("=" * 60)

    # User-Agent 对比
    print("\n【User-Agent】")
    print(f"  人工: {h_sum.get('unique_user_agents', ['无'])}")
    print(f"  Bot:  {b_sum.get('unique_user_agents', ['无'])}")

    # Cookie 字段对比
    print("\n【Cookie 关键字段】")
    h_cookies = set(h_sum.get("cookie_fields_observed", []))
    b_cookies = set(b_sum.get("cookie_fields_observed", []))
    only_human = h_cookies - b_cookies
    only_bot = b_cookies - h_cookies
    common = h_cookies & b_cookies
    print(f"  共有字段: {sorted(common)}")
    print(f"  仅人工有: {sorted(only_human)} {'⚠️ Bot 缺少' if only_human else ''}")
    print(f"  仅Bot 有: {sorted(only_bot)}")

    # Cookie 值对比
    print("\n【Cookie 值对比】")
    h_vals = h_sum.get("first_cookies", {})
    b_vals = b_sum.get("first_cookies", {})
    all_keys = sorted(set(h_vals) | set(b_vals))
    for k in all_keys:
        hv = h_vals.get(k, "(无)")[:40]
        bv = b_vals.get(k, "(无)")[:40]
        match = "✅" if hv == bv else "❌"
        print(f"  {match} {k}:")
        print(f"       人工: {hv}")
        print(f"       Bot:  {bv}")

    # 签名头对比
    print("\n【x-s 签名样本（前3条）】")
    print("  人工:")
    for item in h_sum.get("x_s_sample", [])[:3]:
        print(f"    [{item['path'][:40]}] x-s={item['x-s']} x-t={item.get('x-t','')}")
    print("  Bot:")
    for item in b_sum.get("x_s_sample", [])[:3]:
        print(f"    [{item['path'][:40]}] x-s={item['x-s']} x-t={item.get('x-t','')}")

    # 请求路径对比
    print("\n【API 路径差异】")
    h_paths = set(h_sum.get("api_paths_visited", []))
    b_paths = set(b_sum.get("api_paths_visited", []))
    only_h = h_paths - b_paths
    only_b = b_paths - h_paths
    if only_h:
        print(f"  ⚠️  仅人工调用的路径:")
        for p in sorted(only_h)[:10]:
            print(f"       {p}")
    if only_b:
        print(f"  ⚠️  仅 Bot 调用的路径:")
        for p in sorted(only_b)[:10]:
            print(f"       {p}")

    # ── x-s-common 解码对比 ────────────────────────────────────────────────────
    print("\n【x-s-common 解码对比】")
    for label, data in [("人工", human), ("Bot", bot)]:
        for req in data.get("requests", []):
            dec = req.get("x_s_common_decoded")
            if dec and isinstance(dec, dict):
                print(f"  {label}: s0={dec.get('s0')} x1={dec.get('x1')} x2={dec.get('x2')} "
                      f"x3={dec.get('x3')} x10={dec.get('x10')}")
                break
        else:
            print(f"  {label}: 无 x-s-common")

    # ── navigator / WebGL 指纹对比 ─────────────────────────────────────────────
    print("\n【浏览器指纹对比】")
    for label, data in [("人工", human), ("Bot", bot)]:
        nav = data.get("browser_fingerprint", {}).get("navigator", {})
        webgl = data.get("browser_fingerprint", {}).get("webgl", {})
        print(f"  {label}:")
        print(f"    webdriver={nav.get('webdriver')}  plugins={nav.get('plugins_count')}  "
              f"hardwareConcurrency={nav.get('hardwareConcurrency')}  deviceMemory={nav.get('deviceMemory')}")
        print(f"    WebGL vendor={webgl.get('vendor')}  renderer={webgl.get('renderer')}")

    # ── localStorage 关键字段对比 ──────────────────────────────────────────────
    print("\n【localStorage 写入（b1 / b1b1 / 其他关键值）】")
    for label, data in [("人工", human), ("Bot", bot)]:
        ls = data.get("localStorage_log", [])
        writes = [e for e in ls if e.get("op") == "set"]
        key_writes = [e for e in writes if e.get("key") in ("b1", "b1b1", "xhs_session", "logid")]
        print(f"  {label}: 共 {len(writes)} 次写入，关键字段: "
              f"{[e.get('key') for e in key_writes] or '无'}")

    # ── Cookie 动态写入对比 ────────────────────────────────────────────────────
    print("\n【JS 动态写入 Cookie（acw_tc / loadts / ets 等）】")
    for label, data in [("人工", human), ("Bot", bot)]:
        clog = data.get("cookie_mutation_log", [])
        print(f"  {label}: {len(clog)} 次 JS cookie 写入")
        for entry in clog[:4]:
            val = entry.get("value", "")
            print(f"    {val[:80]}")

    # ── fetch 拦截参数对比 ─────────────────────────────────────────────────────
    print("\n【fetch 拦截 — 搜索请求参数对比】")
    for label, data in [("人工", human), ("Bot", bot)]:
        fetches = data.get("fetch_intercepted", [])
        search_fetches = [f for f in fetches if "search" in f.get("url", "")]
        print(f"  {label}: 共 {len(fetches)} 次 fetch，其中搜索 {len(search_fetches)} 次")
        for f in search_fetches[:2]:
            body = f.get("body", {})
            if body:
                print(f"    body keys: {list(body.keys())}")

    print("\n" + "=" * 60)
    print("💡 反爬虫差异建议：")
    if only_human:
        print(f"  - Bot 缺少 Cookie 字段: {sorted(only_human)}")
        print("    → 考虑在 storageState 中手动补充这些字段")
    if only_h:
        print(f"  - Bot 未访问某些初始化 API（{len(only_h)} 个路径）")
        print("    → 考虑在自动化代码中预热这些接口")
    print("  - 检查 x-s-common 字段是否与人工一致（重点：x1 版本号、x2 平台）")
    print("  - 检查 navigator.plugins 数量（Bot 侧为 0 则指纹注入未生效）")
    print("  - 检查 WebGL vendor/renderer（Bot 侧为 SwiftShader 则暴露 headless）")
    print("=" * 60)


# ── CLI 入口 ─────────────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(
        description="XHS 网络请求监控：对比人工和自动化的反爬虫参数差异",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  # 步骤1：录制人工操作
  python scripts/xhs_network_spy.py

  # 步骤1b：用已有账号 storageState 启动，录制人工浏览行为
  python scripts/xhs_network_spy.py --account 账号A

  # 步骤2：对比两次录制
  python scripts/xhs_network_spy.py --compare \\
    output/network_spy/spy_human_xxx.json \\
    output/network_spy/spy_bot_xxx.json
"""
    )
    parser.add_argument("--account", "-a", default=None,
                        help="使用指定账号的 storageState 启动（账号名称）")
    parser.add_argument("--label", default=None,
                        help="录制文件的标签（默认 human 或 bot）")
    parser.add_argument("--compare", nargs=2, metavar=("HUMAN_JSON", "BOT_JSON"),
                        help="对比两个录制文件的差异")

    args = parser.parse_args()

    # 对比模式
    if args.compare:
        human_file = Path(args.compare[0])
        bot_file = Path(args.compare[1])
        if not human_file.exists():
            print(f"❌ 文件不存在: {human_file}")
            sys.exit(1)
        if not bot_file.exists():
            print(f"❌ 文件不存在: {bot_file}")
            sys.exit(1)
        compare_recordings(human_file, bot_file)
        return

    # 录制模式
    label = args.label or ("human" if not args.account else f"human_{args.account}")
    spy = NetworkSpy(mode="human", account_name=args.account)

    try:
        await spy.run_human_mode()
    except KeyboardInterrupt:
        print("\n\n⏹  监控已停止")
    finally:
        # page_data 在 run_human_mode() 关闭浏览器前已缓存到 spy._page_data
        page_data = await spy._get_page_data()
        if spy.records:
            spy.save_records(label=label, page_data=page_data)
            _print_quick_analysis(spy)
        else:
            print("⚠️  未录制到任何 XHS API 请求")


def _print_quick_analysis(spy: NetworkSpy):
    """快速打印关键反爬虫参数摘要"""
    summary = spy._build_summary()

    print("\n" + "=" * 60)
    print("🔍 关键参数速览")
    print("=" * 60)

    print(f"\n总请求数: {summary['total_requests']}")
    print(f"含签名头 (x-s) 的请求: {summary['signed_requests']}")

    ua_list = summary.get("unique_user_agents", [])
    if ua_list:
        print(f"\nUser-Agent: {ua_list[0]}")

    cookies = summary.get("first_cookies", {})
    if cookies:
        print("\n关键 Cookie 字段:")
        for k, v in cookies.items():
            print(f"  {k}: {v[:50]}")

    x_s_samples = summary.get("x_s_sample", [])
    if x_s_samples:
        print("\nx-s 签名样本:")
        for item in x_s_samples[:3]:
            print(f"  [{item['path'][:50]}]")
            print(f"    x-s={item['x-s']}")
            if item.get("x-t"):
                print(f"    x-t={item['x-t']}")
            if item.get("x-s-common"):
                print(f"    x-s-common={item['x-s-common']}")

    paths = summary.get("api_paths_visited", [])
    if paths:
        print(f"\n访问的 API 路径 (前10):")
        for p in paths[:10]:
            print(f"  {p}")

    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
