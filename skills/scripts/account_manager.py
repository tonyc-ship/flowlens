"""
XHS 多账号管理模块

用法（库）：
    from account_manager import get_next_account, mark_account_used, AllAccountsExpiredError

用法（CLI）：
    python scripts/account_manager.py list
    python scripts/account_manager.py add --name 账号A --cookie "a1=xxx; web_session=yyy"
    python scripts/account_manager.py scan --name 账号A
    python scripts/account_manager.py refresh --name 账号A
"""

import argparse
import json
import os
import re
import shutil
import sys
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ──────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────

DEFAULT_ACCOUNTS_PATH = Path(__file__).parent.parent / "accounts.json"
STORAGE_BASE_DIR = Path.home() / ".xhs-accounts"
SESSION_TTL_DAYS = 7


# ──────────────────────────────────────────────
# Exceptions
# ──────────────────────────────────────────────

class AllAccountsExpiredError(Exception):
    """所有 active 账号的 session 均已过期或 storageState 文件不存在。"""
    pass


class DuplicateAccountError(Exception):
    """账号名已存在。"""
    pass


class MissingEnvCookieError(Exception):
    """XHS_COOKIE 环境变量未设置。"""
    pass


class AccountNotFoundError(Exception):
    """指定账号不存在。"""
    pass


# ──────────────────────────────────────────────
# Session utilities
# ──────────────────────────────────────────────

def is_session_valid(session_expires_at: Optional[str]) -> bool:
    """判断 session 是否仍在有效期内。None 或过期均返回 False。"""
    if not session_expires_at:
        return False
    try:
        expires = datetime.fromisoformat(session_expires_at)
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return expires > datetime.now(timezone.utc)
    except ValueError:
        return False


# ──────────────────────────────────────────────
# storageState ↔ cookie string conversion
# ──────────────────────────────────────────────

def extract_cookie_string(storage_path: str) -> str:
    """从 Playwright storageState 文件提取 cookie 字符串（name=value; ...）。"""
    path = Path(os.path.expanduser(storage_path))
    if not path.exists():
        raise FileNotFoundError(f"storageState 文件不存在: {path}")
    with open(path) as f:
        state = json.load(f)
    return "; ".join(f"{c['name']}={c['value']}" for c in state.get("cookies", []))


# 已知各 cookie 的真实属性（来自浏览器 Network 面板观察）
# name: (httpOnly, sameSite)
_COOKIE_ATTRS: dict = {
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


# ──────────────────────────────────────────────
# accounts.json I/O
# ──────────────────────────────────────────────

def _load_accounts(accounts_path: str) -> dict:
    with open(accounts_path) as f:
        return json.load(f)


def _save_accounts(data: dict, accounts_path: str) -> None:
    with open(accounts_path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _resolve_accounts_path(accounts_path: Optional[str] = None) -> str:
    return accounts_path or str(DEFAULT_ACCOUNTS_PATH)


def _resolve_storage_base_dir(storage_base_dir: Optional[str] = None) -> Path:
    return Path(storage_base_dir).expanduser() if storage_base_dir else STORAGE_BASE_DIR


def _account_storage_path(name: str, storage_base_dir: Optional[str] = None) -> Path:
    return _resolve_storage_base_dir(storage_base_dir) / name / "storage.json"


def _account_storage_ref(name: str, storage_base_dir: Optional[str] = None) -> str:
    base_dir = _resolve_storage_base_dir(storage_base_dir)
    if base_dir == STORAGE_BASE_DIR:
        return f"~/.xhs-accounts/{name}/storage.json"
    return str(_account_storage_path(name, storage_base_dir))


def _sanitize_account_name(raw_name: str) -> str:
    candidate = re.sub(r'[\\/:*?"<>|\s]+', "_", (raw_name or "").strip())
    candidate = candidate.strip("._")
    return candidate[:40] or f"account_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def _make_unique_account_name(preferred: str, existing_names: List[str]) -> str:
    base = _sanitize_account_name(preferred)
    if base not in existing_names:
        return base
    idx = 2
    while f"{base}_{idx}" in existing_names:
        idx += 1
    return f"{base}_{idx}"


def _extract_display_name_from_user_info(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""

    candidates: List[str] = []
    seen: set[str] = set()

    def _append(value: Any):
        if not isinstance(value, str):
            return
        name = value.strip()
        if not name or name in seen:
            return
        seen.add(name)
        candidates.append(name)

    data = payload.get("data", payload)
    for obj in [data, data.get("basic_info", {}) if isinstance(data, dict) else {}]:
        if isinstance(obj, dict):
            for key in ("nickname", "nick_name", "nickName", "name"):
                _append(obj.get(key))

    return candidates[0] if candidates else ""


def _normalize_nickname(name: str, nickname: Optional[str]) -> str:
    raw = (nickname or "").strip()
    if not raw or raw == name:
        return ""
    compact = re.sub(r"\s+", "", raw)
    if compact and all(ch in "=-_*~:|+." for ch in compact):
        return ""
    separators = set("=-_*~:|+.—–·•◆★☆▶►")
    if len(set(compact)) <= 2 and len(compact) >= 8 and all(ch in separators for ch in compact):
        return ""
    return raw


def _find_existing_account_name_by_display_name(
    accounts: List[Dict[str, Any]],
    display_name: str,
) -> str:
    target = (display_name or "").strip()
    if not target:
        return ""
    for account in accounts:
        account_name = (account.get("name") or "").strip()
        nickname = _normalize_nickname(account_name, account.get("nickname"))
        if target in {account_name, nickname}:
            return account_name
    return ""


def _serialize_account(account: Dict[str, Any]) -> Dict[str, Any]:
    storage = Path(os.path.expanduser(account.get("storage_path", "")))
    valid = is_session_valid(account.get("session_expires_at")) and storage.exists()
    name = account.get("name", "")
    nickname = _normalize_nickname(name, account.get("nickname"))
    return {
        "name": name,
        "nickname": nickname,
        "display_name": nickname or name,
        "active": bool(account.get("active", True)),
        "valid": valid,
        "session_expires_at": account.get("session_expires_at"),
        "last_used_at": account.get("last_used_at"),
        "added_at": account.get("added_at"),
        "storage_path": account.get("storage_path", ""),
        "storage_exists": storage.exists(),
    }


def get_account_cookie(
    name: str,
    accounts_path: Optional[str] = None,
) -> Tuple[str, str]:
    """按账号名获取 cookie 字符串，返回 (cookie, account_name)。"""
    account_name = (name or "").strip()
    if not account_name:
        raise ValueError("账号名不能为空")

    resolved_path = _resolve_accounts_path(accounts_path)
    if not Path(resolved_path).exists():
        raise FileNotFoundError(f"accounts.json 不存在: {resolved_path}")

    data = _load_accounts(resolved_path)
    target = next((a for a in data.get("accounts", []) if a.get("name") == account_name), None)
    if not target:
        raise AccountNotFoundError(f"账号不存在: {account_name}")
    if not target.get("active"):
        raise ValueError(f"账号未启用: {account_name}")
    if not is_session_valid(target.get("session_expires_at")):
        raise ValueError(f"账号 session 已过期: {account_name}")

    storage_path = os.path.expanduser(target.get("storage_path", ""))
    if not Path(storage_path).exists():
        raise FileNotFoundError(f"storageState 文件不存在: {storage_path}")

    cookie = extract_cookie_string(storage_path)
    return cookie, account_name


def list_accounts_detailed(
    accounts_path: Optional[str] = None,
    include_inactive: bool = True,
) -> List[Dict[str, Any]]:
    """返回账号详细信息，供页面管理使用。"""
    resolved_path = _resolve_accounts_path(accounts_path)
    if not Path(resolved_path).exists():
        return []

    data = _load_accounts(resolved_path)
    accounts = data.get("accounts", [])
    result = []
    for account in accounts:
        if not include_inactive and not account.get("active", True):
            continue
        result.append(_serialize_account(account))

    result.sort(key=lambda item: (not item["active"], item["name"]))
    return result


def save_cookie_account(
    name: str,
    cookie_string: str,
    *,
    accounts_path: Optional[str] = None,
    storage_base_dir: Optional[str] = None,
    overwrite: bool = False,
) -> Dict[str, Any]:
    """将明文 cookie 保存为一个账号。"""
    name = (name or "").strip()
    cookie_string = (cookie_string or "").strip()
    if not name:
        raise ValueError("账号名不能为空")
    if not cookie_string:
        raise ValueError("Cookie 不能为空")

    resolved_path = _resolve_accounts_path(accounts_path)
    data = _ensure_accounts_file(resolved_path)
    existing = next((a for a in data["accounts"] if a.get("name") == name), None)
    if existing and not overwrite:
        raise DuplicateAccountError(f"账号名 '{name}' 已存在，请刷新登录或更换名称")

    storage_path = _account_storage_path(name, storage_base_dir)
    storage_path.parent.mkdir(parents=True, exist_ok=True)
    state = cookie_string_to_storage_state(cookie_string)
    with open(storage_path, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.chmod(storage_path, 0o600)

    now = datetime.now(timezone.utc).isoformat()
    expires_at = (datetime.now(timezone.utc) + timedelta(days=SESSION_TTL_DAYS)).isoformat()
    account_payload = {
        "name": name,
        "nickname": _normalize_nickname(name, existing.get("nickname") if existing else ""),
        "storage_path": _account_storage_ref(name, storage_base_dir),
        "last_used_at": existing.get("last_used_at") if existing else None,
        "session_expires_at": expires_at,
        "added_at": existing.get("added_at") if existing else now,
        "active": True,
    }
    if existing:
        existing.update(account_payload)
    else:
        data["accounts"].append(account_payload)
    _save_accounts(data, resolved_path)

    return _serialize_account(account_payload)


def set_account_active(
    name: str,
    active: bool,
    *,
    accounts_path: Optional[str] = None,
) -> Dict[str, Any]:
    """启用或停用指定账号。"""
    resolved_path = _resolve_accounts_path(accounts_path)
    data = _ensure_accounts_file(resolved_path)
    for account in data["accounts"]:
        if account.get("name") == name:
            account["active"] = bool(active)
            _save_accounts(data, resolved_path)
            return _serialize_account(account)
    raise AccountNotFoundError(f"账号不存在: {name}")


def update_account_nickname(
    name: str,
    nickname: str,
    *,
    accounts_path: Optional[str] = None,
) -> Dict[str, Any]:
    """更新账号昵称；空值或与原账号名相同会清空昵称。"""
    resolved_path = _resolve_accounts_path(accounts_path)
    data = _ensure_accounts_file(resolved_path)
    normalized_name = (name or "").strip()
    for account in data["accounts"]:
        if account.get("name") != normalized_name:
            continue
        account["nickname"] = _normalize_nickname(normalized_name, nickname)
        _save_accounts(data, resolved_path)
        return _serialize_account(account)
    raise AccountNotFoundError(f"账号不存在: {name}")


def delete_account(
    name: str,
    *,
    accounts_path: Optional[str] = None,
    remove_storage: bool = False,
) -> bool:
    """删除账号记录；可选同时删除本地 storageState。"""
    resolved_path = _resolve_accounts_path(accounts_path)
    data = _ensure_accounts_file(resolved_path)
    target = next((a for a in data["accounts"] if a.get("name") == name), None)
    if not target:
        raise AccountNotFoundError(f"账号不存在: {name}")

    data["accounts"] = [a for a in data["accounts"] if a.get("name") != name]
    _save_accounts(data, resolved_path)

    if remove_storage:
        storage_path = Path(os.path.expanduser(target.get("storage_path", "")))
        storage_dir = storage_path.parent
        # Guard: storage_dir must be named after the account (our helpers always
        # create {base}/{name}/storage.json), preventing accidental wide deletions.
        if storage_dir.name != name:
            raise ValueError(
                f"拒绝删除非账号目录: {storage_dir}（期望目录名为账号名 '{name}'）"
            )
        if storage_dir.exists():
            shutil.rmtree(storage_dir)
    return True


# ──────────────────────────────────────────────
# Core: get_next_account
# ──────────────────────────────────────────────

def get_next_account(
    accounts_path: Optional[str] = None,
    fallback_to_env: bool = True,
) -> Tuple[str, str]:
    """
    轮询选取下一个可用账号，返回 (cookie_string, account_name)。

    选取规则：
    1. 过滤 active=True 的账号
    2. 按 last_used_at 升序（None 最优先）
    3. 逐个检查 session_expires_at + storageState 文件是否存在
    4. 选中第一个有效账号，返回其 cookie 字符串（不更新 last_used_at）

    如果 accounts.json 不存在且 fallback_to_env=True，回退到 XHS_COOKIE 环境变量。
    所有账号均过期时抛出 AllAccountsExpiredError。
    """
    resolved_path = accounts_path or str(DEFAULT_ACCOUNTS_PATH)

    # 回退：accounts.json 不存在
    if not Path(resolved_path).exists():
        if fallback_to_env:
            cookie = _load_env_cookie()
            return cookie, "__env__"
        raise FileNotFoundError(f"accounts.json 不存在: {resolved_path}")

    data = _load_accounts(resolved_path)
    active_accounts = [a for a in data.get("accounts", []) if a.get("active")]

    # 排序：None last_used_at 最优先；正确解析 ISO 时间戳（兼容 Z 和 +00:00 后缀）
    def _parse_dt(ts):
        if not ts:
            return datetime.min.replace(tzinfo=timezone.utc)
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            return datetime.min.replace(tzinfo=timezone.utc)

    active_accounts.sort(key=lambda a: _parse_dt(a.get("last_used_at")))

    expired_list = []
    for account in active_accounts:
        name = account["name"]
        expires_at = account.get("session_expires_at")
        storage_path = os.path.expanduser(account.get("storage_path", ""))

        # 检查 session 有效期
        if not is_session_valid(expires_at):
            expired_list.append(name)
            continue

        # 检查 storageState 文件是否存在
        if not Path(storage_path).exists():
            expired_list.append(name)
            continue

        # 找到可用账号：提取 cookie 字符串
        try:
            cookie = extract_cookie_string(storage_path)
        except Exception:
            expired_list.append(name)
            continue

        if expired_list:
            print(f"⚠️  已跳过过期账号：{expired_list}，请运行：python scripts/account_manager.py refresh --name <账号名>")

        return cookie, name

    # 所有账号均无效
    if fallback_to_env:
        cookie = _load_env_cookie()
        print(f"⚠️  所有账号已过期：{expired_list}，回退到 XHS_COOKIE 环境变量")
        return cookie, "__env__"

    raise AllAccountsExpiredError(
        f"所有账号已过期或不可用：{expired_list}。请运行：python scripts/account_manager.py refresh --name <账号名>"
    )


def _load_env_cookie() -> str:
    """从 .env 文件或环境变量加载 XHS_COOKIE。"""
    try:
        from dotenv import load_dotenv
        for env_path in [
            Path.cwd() / ".env",
            Path(__file__).parent.parent / ".env",
        ]:
            if env_path.exists():
                load_dotenv(env_path)
                break
    except ImportError:
        pass

    cookie = os.getenv("XHS_COOKIE")
    if not cookie:
        print("❌ 错误: 未找到 XHS_COOKIE 环境变量，且 accounts.json 不存在或无可用账号")
        raise MissingEnvCookieError("XHS_COOKIE 环境变量未设置")
    return cookie


# ──────────────────────────────────────────────
# mark_account_used
# ──────────────────────────────────────────────

def mark_account_used(name: str, accounts_path: Optional[str] = None) -> None:
    """发布成功后更新账号的 last_used_at 时间戳。"""
    resolved_path = accounts_path or str(DEFAULT_ACCOUNTS_PATH)
    if not Path(resolved_path).exists():
        return  # 单账号模式，无需更新

    data = _load_accounts(resolved_path)
    found = False
    for account in data.get("accounts", []):
        if account["name"] == name:
            account["last_used_at"] = datetime.now(timezone.utc).isoformat()
            found = True
            break
    if not found:
        print(f"⚠️  mark_account_used: 账号 '{name}' 不存在于 accounts.json，跳过更新")
    _save_accounts(data, resolved_path)


# ──────────────────────────────────────────────
# CLI commands
# ──────────────────────────────────────────────

def _ensure_accounts_file(accounts_path: str) -> dict:
    """如果 accounts.json 不存在则创建空文件，并返回 data。"""
    p = Path(accounts_path)
    if not p.exists():
        data = {"accounts": []}
        p.parent.mkdir(parents=True, exist_ok=True)
        _save_accounts(data, accounts_path)
        return data
    return _load_accounts(accounts_path)


def cmd_add(args) -> None:
    """手动粘贴 Cookie 录入账号。"""
    saved = save_cookie_account(args.name, args.cookie)
    print(f"✅ 账号 '{saved['name']}' 已添加，storageState 保存至 {saved['storage_path']}")


def cmd_scan(args) -> None:
    """Playwright 有头模式扫码登录，保存 storageState。"""
    _playwright_login(args.name, is_refresh=False)


def cmd_refresh(args) -> None:
    """重新扫码，更新 storageState + session_expires_at。"""
    _playwright_login(args.name, is_refresh=True)


def _playwright_login(name: Optional[str], is_refresh: bool) -> None:
    """使用 Playwright 有头模式完成扫码登录，保存 storageState。"""
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
    except ImportError:
        print("❌ 错误: 缺少 playwright 依赖")
        print("请运行: pip install playwright && playwright install chromium")
        sys.exit(1)

    requested_name = (name or "").strip()
    if is_refresh and not requested_name:
        raise ValueError("刷新登录必须指定账号名")

    temp_name = requested_name or f"pending_{uuid.uuid4().hex[:8]}"
    storage_path = _account_storage_path(temp_name)
    storage_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"🌐 打开小红书登录页，请在 120 秒内完成手机扫码...")

    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).parent))
    from browser_config import launch_options, launch_options_fallback, context_options, NAVIGATOR_INIT_SCRIPT

    with sync_playwright() as p:
        detected_display_name = ""
        try:
            browser = p.chromium.launch(**launch_options(headless=False))
        except Exception:
            browser = p.chromium.launch(**launch_options_fallback(headless=False))
        context = browser.new_context(**context_options(viewport={"width": 1280, "height": 800}))
        context.add_init_script(NAVIGATOR_INIT_SCRIPT)
        page = context.new_page()
        page.goto("https://www.xiaohongshu.com/login")

        try:
            page.wait_for_url(lambda url: "login" not in url, timeout=120_000)
        except PlaywrightTimeout:
            print("❌ 扫码超时（120秒），未写入文件")
            browser.close()
            return

        try:
            page.goto("https://www.xiaohongshu.com/", wait_until="domcontentloaded", timeout=30000)
            user_resp = page.wait_for_response(
                lambda r: "/api/sns/web/v2/user/me" in r.url,
                timeout=10000,
            )
            detected_display_name = _extract_display_name_from_user_info(user_resp.json())
        except Exception:
            detected_display_name = ""

        context.storage_state(path=str(storage_path))
        browser.close()

    os.chmod(storage_path, 0o600)
    print(f"✅ 登录成功，storageState 已保存至 {storage_path}")
    if detected_display_name:
        print(f"👤 已识别账号：{detected_display_name}")

    # 更新 accounts.json
    accounts_path = str(DEFAULT_ACCOUNTS_PATH)
    data = _ensure_accounts_file(accounts_path)
    expires_at = (datetime.now(timezone.utc) + timedelta(days=SESSION_TTL_DAYS)).isoformat()
    added_at = datetime.now(timezone.utc).isoformat()

    final_name = requested_name
    if not final_name:
        matched_name = _find_existing_account_name_by_display_name(
            data.get("accounts", []), detected_display_name
        )
        existing_names = [a.get("name", "") for a in data["accounts"]]
        final_name = matched_name or _make_unique_account_name(
            detected_display_name or temp_name, existing_names
        )
        if final_name != temp_name:
            final_storage_path = _account_storage_path(final_name)
            dest_dir = final_storage_path.parent
            if dest_dir.exists():
                other_owners = [
                    a for a in data["accounts"]
                    if a.get("name") != final_name
                    and Path(os.path.expanduser(a.get("storage_path", ""))).parent == dest_dir
                ]
                if other_owners:
                    raise RuntimeError(f"目标存储目录已被账号 {other_owners[0]['name']} 占用，无法覆盖")
                shutil.rmtree(dest_dir, ignore_errors=True)
            try:
                storage_path.parent.rename(dest_dir)
            except OSError:
                shutil.copytree(str(storage_path.parent), str(dest_dir))
                shutil.rmtree(str(storage_path.parent), ignore_errors=True)
            storage_path = final_storage_path
        if not detected_display_name and final_name == temp_name:
            print(f"⚠️   未能识别账号昵称，已分配临时名称 '{final_name}'，请在账号管理页手动设置显示名")

    existing = next((a for a in data["accounts"] if a["name"] == final_name), None)
    if existing:
        existing["storage_path"] = _account_storage_ref(final_name)
        existing["session_expires_at"] = expires_at
        existing["active"] = True
        if detected_display_name:
            existing["nickname"] = _normalize_nickname(final_name, detected_display_name)
        else:
            existing["nickname"] = _normalize_nickname(final_name, existing.get("nickname"))
    else:
        if is_refresh:
            print(f"⚠️  账号 '{final_name}' 在 accounts.json 中不存在，将新建")
        data["accounts"].append({
            "name": final_name,
            "nickname": _normalize_nickname(final_name, detected_display_name),
            "storage_path": _account_storage_ref(final_name),
            "last_used_at": None,
            "session_expires_at": expires_at,
            "added_at": added_at,
            "active": True,
        })
    _save_accounts(data, accounts_path)
    print(f"🪪 系统账号ID：{final_name}")
    print(f"📅 session 有效期更新至 {expires_at[:10]}")


def cmd_list(args) -> None:
    """打印所有账号信息。"""
    accounts_path = str(DEFAULT_ACCOUNTS_PATH)
    if not Path(accounts_path).exists():
        print("accounts.json 不存在，暂无账号")
        return

    data = _load_accounts(accounts_path)
    accounts = data.get("accounts", [])
    if not accounts:
        print("暂无账号")
        return

    print(f"{'名称':<12} {'状态':<6} {'session 有效期':<22} {'上次使用':<22}")
    print("-" * 70)
    for a in accounts:
        status = "✅ 活跃" if a.get("active") else "⛔ 停用"
        expires = a.get("session_expires_at") or "未知"
        if expires != "未知":
            expires = expires[:19].replace("T", " ")
            valid = "✅" if is_session_valid(a.get("session_expires_at")) else "❌过期"
            expires = f"{expires} {valid}"
        last_used = a.get("last_used_at") or "从未使用"
        if last_used != "从未使用":
            last_used = last_used[:19].replace("T", " ")
        print(f"{a['name']:<12} {status:<6} {expires:<30} {last_used}")


# ──────────────────────────────────────────────
# CLI entry point
# ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="XHS 多账号管理")
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add", help="手动粘贴 Cookie 录入账号")
    p_add.add_argument("--name", required=True, help="账号名（唯一）")
    p_add.add_argument("--cookie", required=True, help="Cookie 字符串，如 a1=xxx; web_session=yyy")
    p_add.set_defaults(func=cmd_add)

    p_scan = sub.add_parser("scan", help="Playwright 扫码登录新账号")
    p_scan.add_argument("--name", help="账号名（可选；不填时自动识别原账号名）")
    p_scan.set_defaults(func=cmd_scan)

    p_refresh = sub.add_parser("refresh", help="重新扫码刷新 session")
    p_refresh.add_argument("--name", required=True, help="账号名")
    p_refresh.set_defaults(func=cmd_refresh)

    p_list = sub.add_parser("list", help="列出所有账号")
    p_list.set_defaults(func=cmd_list)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
