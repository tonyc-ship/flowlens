#!/usr/bin/env python3
"""
XHS Session 保活脚本

用有效 cookies 访问小红书主页和 explore 页，触发服务端刷新 session，
将更新后的 storageState 写回磁盘，并将 session_expires_at 延长 7 天。

用法：
    python scripts/xhs_keepalive.py              # 保活所有 active 账号
    python scripts/xhs_keepalive.py --name default  # 仅指定账号
    python scripts/xhs_keepalive.py --headless   # 无头模式（默认有头，便于观察）
"""
import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
ACCOUNTS_PATH = BASE_DIR / "accounts.json"

# 真实用户登录后必经的初始化 API（来自 xhs_network_spy 录制结果）
WARMUP_PATHS = [
    "/api/sns/web/v2/user/me",
    "/api/sns/web/v1/system/config",
    "/api/sns/web/v1/zones",
    "/api/sns/web/unread_count",
    "/api/sns/web/v1/search/querytrending",
]


def _load_accounts() -> dict:
    if not ACCOUNTS_PATH.exists():
        return {"accounts": []}
    return json.loads(ACCOUNTS_PATH.read_text(encoding="utf-8"))


def _save_accounts(data: dict):
    ACCOUNTS_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def keepalive_account(account: dict, headless: bool = False) -> dict:
    """
    对单个账号执行保活：
    1. 用 storageState 启动浏览器（browser_config 统一画像）
    2. 访问 XHS 主页 + explore 页，等待真实 API 响应（不用 sleep）
    3. 保存更新后的 storageState（cookies 已被服务端刷新）
    4. 返回 {"ok": bool, "name": str, "message": str, "api_hits": dict}
    """
    name = account["name"]
    storage_path = Path(account.get("storage_path", "")).expanduser()

    if not storage_path.exists():
        return {"ok": False, "name": name, "message": f"storageState 不存在: {storage_path}"}

    from playwright.sync_api import sync_playwright
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).parent))
    from browser_config import launch_options, launch_options_fallback, context_options, NAVIGATOR_INIT_SCRIPT

    print(f"  [keepalive] 账号: {name}")
    api_results = {}

    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(**launch_options(headless=headless))
        except Exception:
            browser = pw.chromium.launch(**launch_options_fallback(headless=headless))

        context = browser.new_context(**context_options(storage_state=str(storage_path)))
        context.add_init_script(NAVIGATOR_INIT_SCRIPT)

        def _on_response(response):
            for path in WARMUP_PATHS:
                if path in response.url:
                    api_results[path] = response.status

        page = context.new_page()
        page.on("response", _on_response)

        try:
            # 1. 访问主页
            print(f"  [keepalive] 访问主页...")
            page.goto("https://www.xiaohongshu.com/", wait_until="domcontentloaded", timeout=30000)

            # 检查是否被踢到登录页
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

            # 2. 访问 explore 页，触发更多 cookie 刷新
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

            # 3. 保存更新后的 storageState
            context.storage_state(path=str(storage_path))
            print(f"  [keepalive] storageState 已更新: {storage_path}")

            hit = [p for p in WARMUP_PATHS if p in api_results]
            print(f"  [keepalive] API 预热命中: {len(hit)}/{len(WARMUP_PATHS)} — {hit}")

        except Exception as e:
            browser.close()
            return {"ok": False, "name": name, "message": f"浏览器异常: {e}"}

        browser.close()

    return {"ok": True, "name": name, "message": "保活成功", "api_hits": api_results}


def update_session_expires(accounts_data: dict, name: str, days: int = 7):
    """将指定账号的 session_expires_at 延长 days 天"""
    new_expires = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
    for acc in accounts_data.get("accounts", []):
        if acc["name"] == name:
            acc["session_expires_at"] = new_expires
            break


def run_keepalive(names: list = None, headless: bool = False) -> list:
    """对 accounts.json 中所有（或指定）active 账号执行保活，返回结果列表"""
    data = _load_accounts()
    accounts = [a for a in data.get("accounts", []) if a.get("active")]
    if names:
        accounts = [a for a in accounts if a["name"] in names]

    if not accounts:
        print("⚠️  无可保活账号")
        return []

    results = []
    for acc in accounts:
        result = keepalive_account(acc, headless=headless)
        results.append(result)
        if result["ok"]:
            update_session_expires(data, acc["name"], days=7)
            print(f"  ✅ {acc['name']} 保活成功，session 延长至 +7 天")
        else:
            print(f"  ❌ {acc['name']} 保活失败: {result['message']}")

    _save_accounts(data)
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", nargs="+", help="指定账号名称（默认所有 active 账号）")
    parser.add_argument("--headless", action="store_true", help="无头模式运行")
    args = parser.parse_args()

    print("🔄 XHS Session 保活")
    print("=" * 40)
    results = run_keepalive(names=args.name, headless=args.headless)
    ok = sum(1 for r in results if r["ok"])
    print(f"\n✅ 完成：{ok}/{len(results)} 个账号保活成功")
    sys.exit(0 if ok == len(results) else 1)
