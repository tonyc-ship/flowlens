#!/usr/bin/env python3
"""扫码登录小红书，自动提取并保存 Cookie 到 .env"""
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

ENV_PATH = Path(__file__).parent.parent / ".env"

async def main():
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=False, channel="chrome")
        except Exception:
            browser = await p.chromium.launch(headless=False)

        context = await browser.new_context()
        page = await context.new_page()

        print("🌐 打开小红书登录页，请用手机扫码...")
        await page.goto("https://www.xiaohongshu.com/login")

        # 等待登录成功（跳转离开 /login 页面）
        print("⏳ 等待扫码登录（最多 2 分钟）...")
        await page.wait_for_function(
            "() => { const p = window.location.pathname; return !p.includes('login') && !p.includes('website-login') && !p.includes('web-login'); }",
            timeout=120000,
        )
        await page.wait_for_timeout(2000)  # 等 Cookie 落齐

        current_url = page.url
        if any(k in current_url for k in ("website-login", "web-login", "/login", "error_code=300012")):
            raise RuntimeError(f"命中风控/登录页：{current_url}")

        body_text = await page.evaluate("() => document.body?.innerText || ''")
        if any(k in body_text for k in ("IP存在风险", "扫码登录", "请登录后继续")):
            raise RuntimeError("页面提示未登录或风控拦截（IP存在风险）")

        cookies = await context.cookies()
        xhs_cookies = [c for c in cookies if "xiaohongshu.com" in c.get("domain", "")]
        cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in xhs_cookies)

        ENV_PATH.write_text(f"XHS_COOKIE={cookie_str}\n", encoding="utf-8")
        print(f"✅ 登录成功！已保存 {len(xhs_cookies)} 个 Cookie 到 .env")

        await browser.close()

asyncio.run(main())
