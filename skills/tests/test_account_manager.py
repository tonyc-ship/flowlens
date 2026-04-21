import json
import os
import types
import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import account_manager
from account_manager import (
    AccountNotFoundError,
    _extract_display_name_from_user_info,
    _find_existing_account_name_by_display_name,
    _make_unique_account_name,
    _normalize_nickname,
    is_session_valid,
    extract_cookie_string,
    cookie_string_to_storage_state,
    get_next_account,
    mark_account_used,
    AllAccountsExpiredError,
    DuplicateAccountError,
    delete_account,
    get_account_cookie,
    list_accounts_detailed,
    save_cookie_account,
    set_account_active,
    update_account_nickname,
)


# --- is_session_valid ---

def test_is_session_valid_with_future_date():
    future = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    assert is_session_valid(future) is True

def test_is_session_valid_with_past_date():
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    assert is_session_valid(past) is False

def test_is_session_valid_with_none():
    assert is_session_valid(None) is False


# --- extract_cookie_string ---

def test_extract_cookie_string(tmp_path):
    storage = {
        "cookies": [
            {"name": "a1", "value": "abc123"},
            {"name": "web_session", "value": "sess456"},
        ],
        "origins": []
    }
    f = tmp_path / "storage.json"
    f.write_text(json.dumps(storage))
    result = extract_cookie_string(str(f))
    assert result == "a1=abc123; web_session=sess456"

def test_extract_cookie_string_missing_file():
    with pytest.raises(FileNotFoundError):
        extract_cookie_string("/nonexistent/path/storage.json")


# --- cookie_string_to_storage_state ---

def test_cookie_string_to_storage_state():
    result = cookie_string_to_storage_state("a1=abc; web_session=xyz")
    assert len(result["cookies"]) == 2
    assert result["cookies"][0]["name"] == "a1"
    assert result["cookies"][0]["value"] == "abc"
    assert result["cookies"][0]["domain"] == ".xiaohongshu.com"
    assert result["cookies"][0]["path"] == "/"
    assert result["cookies"][0]["secure"] is True
    assert result["origins"] == []

def test_cookie_string_to_storage_state_strips_whitespace():
    result = cookie_string_to_storage_state("  a1 = abc ;  web_session = xyz ")
    assert result["cookies"][0]["name"] == "a1"
    assert result["cookies"][0]["value"] == "abc"


def test_extract_display_name_from_user_info_prefers_nickname():
    payload = {
        "data": {
            "basic_info": {
                "nickname": "英国留学求职Lisa",
                "name": "备用名字",
            }
        }
    }
    assert _extract_display_name_from_user_info(payload) == "英国留学求职Lisa"


def test_make_unique_account_name_appends_suffix_when_conflicted():
    assert _make_unique_account_name("主账号", ["主账号", "主账号_2"]) == "主账号_3"


def test_find_existing_account_name_by_display_name_matches_nickname():
    accounts = [
        {"name": "default", "nickname": "英国留学求职Lisa"},
        {"name": "backup", "nickname": ""},
    ]
    assert _find_existing_account_name_by_display_name(accounts, "英国留学求职Lisa") == "default"


# --- get_next_account ---

def _make_accounts_json(tmp_path, accounts_data):
    p = tmp_path / "accounts.json"
    p.write_text(json.dumps({"accounts": accounts_data}))
    return str(p)

def _make_storage(tmp_path, name, cookies=None):
    cookies = cookies or [{"name": "a1", "value": "v1"}, {"name": "web_session", "value": "v2"}]
    d = tmp_path / name
    d.mkdir(parents=True, exist_ok=True)
    p = d / "storage.json"
    p.write_text(json.dumps({"cookies": cookies, "origins": []}))
    return str(p)

def test_get_next_account_selects_least_recently_used(tmp_path):
    storage_a = _make_storage(tmp_path, "A")
    storage_b = _make_storage(tmp_path, "B")
    future = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    earlier = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    later   = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

    accounts = [
        {"name": "A", "storage_path": storage_a, "last_used_at": later,
         "session_expires_at": future, "active": True, "added_at": "2026-01-01T00:00:00Z"},
        {"name": "B", "storage_path": storage_b, "last_used_at": earlier,
         "session_expires_at": future, "active": True, "added_at": "2026-01-01T00:00:00Z"},
    ]
    accounts_path = _make_accounts_json(tmp_path, accounts)
    cookie, name = get_next_account(accounts_path=accounts_path, fallback_to_env=False)
    assert name == "B"
    assert "a1=v1" in cookie
    assert "web_session=v2" in cookie

def test_get_next_account_null_last_used_is_highest_priority(tmp_path):
    storage_a = _make_storage(tmp_path, "A")
    storage_b = _make_storage(tmp_path, "B")
    future = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()

    accounts = [
        {"name": "A", "storage_path": storage_a,
         "last_used_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
         "session_expires_at": future, "active": True, "added_at": "2026-01-01T00:00:00Z"},
        {"name": "B", "storage_path": storage_b,
         "last_used_at": None, "session_expires_at": future,
         "active": True, "added_at": "2026-01-01T00:00:00Z"},
    ]
    accounts_path = _make_accounts_json(tmp_path, accounts)
    cookie, name = get_next_account(accounts_path=accounts_path, fallback_to_env=False)
    assert name == "B"
    assert "a1=v1" in cookie
    assert "web_session=v2" in cookie

def test_get_next_account_skips_inactive(tmp_path):
    storage_a = _make_storage(tmp_path, "A")
    storage_b = _make_storage(tmp_path, "B")
    future = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()

    accounts = [
        {"name": "A", "storage_path": storage_a, "last_used_at": None,
         "session_expires_at": future, "active": False, "added_at": "2026-01-01T00:00:00Z"},
        {"name": "B", "storage_path": storage_b, "last_used_at": None,
         "session_expires_at": future, "active": True, "added_at": "2026-01-01T00:00:00Z"},
    ]
    accounts_path = _make_accounts_json(tmp_path, accounts)
    cookie, name = get_next_account(accounts_path=accounts_path, fallback_to_env=False)
    assert name == "B"
    assert "a1=v1" in cookie
    assert "web_session=v2" in cookie

def test_get_next_account_skips_expired_session(tmp_path):
    storage_a = _make_storage(tmp_path, "A")
    storage_b = _make_storage(tmp_path, "B")
    past   = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    future = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()

    accounts = [
        {"name": "A", "storage_path": storage_a, "last_used_at": None,
         "session_expires_at": past, "active": True, "added_at": "2026-01-01T00:00:00Z"},
        {"name": "B", "storage_path": storage_b, "last_used_at": None,
         "session_expires_at": future, "active": True, "added_at": "2026-01-01T00:00:00Z"},
    ]
    accounts_path = _make_accounts_json(tmp_path, accounts)
    cookie, name = get_next_account(accounts_path=accounts_path, fallback_to_env=False)
    assert name == "B"
    assert "a1=v1" in cookie
    assert "web_session=v2" in cookie

def test_get_next_account_raises_when_all_expired(tmp_path):
    storage_a = _make_storage(tmp_path, "A")
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    accounts = [
        {"name": "A", "storage_path": storage_a, "last_used_at": None,
         "session_expires_at": past, "active": True, "added_at": "2026-01-01T00:00:00Z"},
    ]
    accounts_path = _make_accounts_json(tmp_path, accounts)
    with pytest.raises(AllAccountsExpiredError) as exc_info:
        get_next_account(accounts_path=accounts_path, fallback_to_env=False)
    assert "A" in str(exc_info.value)

def test_get_next_account_missing_storage_file_treated_as_expired(tmp_path):
    accounts = [
        {"name": "A", "storage_path": "/nonexistent/storage.json",
         "last_used_at": None,
         "session_expires_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
         "active": True, "added_at": "2026-01-01T00:00:00Z"},
    ]
    accounts_path = _make_accounts_json(tmp_path, accounts)
    with pytest.raises(AllAccountsExpiredError):
        get_next_account(accounts_path=accounts_path, fallback_to_env=False)


# --- mark_account_used ---

def test_mark_account_used_updates_timestamp(tmp_path):
    storage_a = _make_storage(tmp_path, "A")
    future = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    accounts = [
        {"name": "A", "storage_path": storage_a, "last_used_at": None,
         "session_expires_at": future, "active": True, "added_at": "2026-01-01T00:00:00Z"},
    ]
    accounts_path = _make_accounts_json(tmp_path, accounts)
    before = datetime.now(timezone.utc)
    mark_account_used("A", accounts_path=accounts_path)
    data = json.loads(Path(accounts_path).read_text())
    updated = data["accounts"][0]["last_used_at"]
    assert updated is not None
    updated_dt = datetime.fromisoformat(updated)
    assert updated_dt >= before


# --- management helpers ---

def test_save_cookie_account_creates_account_and_storage(tmp_path):
    accounts_path = tmp_path / "accounts.json"
    storage_dir = tmp_path / "storage"

    saved = save_cookie_account(
        "A",
        "a1=abc; web_session=xyz",
        accounts_path=str(accounts_path),
        storage_base_dir=str(storage_dir),
    )

    assert saved["name"] == "A"
    assert saved["active"] is True
    data = json.loads(accounts_path.read_text())
    assert data["accounts"][0]["name"] == "A"
    storage_path = storage_dir / "A" / "storage.json"
    assert storage_path.exists()
    state = json.loads(storage_path.read_text())
    assert state["cookies"][0]["name"] == "a1"


def test_save_cookie_account_rejects_duplicate_by_default(tmp_path):
    accounts_path = tmp_path / "accounts.json"
    storage_dir = tmp_path / "storage"
    save_cookie_account(
        "A",
        "a1=abc; web_session=xyz",
        accounts_path=str(accounts_path),
        storage_base_dir=str(storage_dir),
    )
    with pytest.raises(DuplicateAccountError):
        save_cookie_account(
            "A",
            "a1=new; web_session=zzz",
            accounts_path=str(accounts_path),
            storage_base_dir=str(storage_dir),
        )


def test_set_account_active_updates_flag(tmp_path):
    accounts_path = tmp_path / "accounts.json"
    storage_dir = tmp_path / "storage"
    save_cookie_account(
        "A",
        "a1=abc; web_session=xyz",
        accounts_path=str(accounts_path),
        storage_base_dir=str(storage_dir),
    )

    updated = set_account_active("A", False, accounts_path=str(accounts_path))
    assert updated["active"] is False

    data = json.loads(accounts_path.read_text())
    assert data["accounts"][0]["active"] is False


def test_set_account_active_raises_for_missing_account(tmp_path):
    with pytest.raises(AccountNotFoundError):
        set_account_active("missing", False, accounts_path=str(tmp_path / "accounts.json"))


def test_delete_account_removes_record_and_storage(tmp_path):
    accounts_path = tmp_path / "accounts.json"
    storage_dir = tmp_path / "storage"
    save_cookie_account(
        "A",
        "a1=abc; web_session=xyz",
        accounts_path=str(accounts_path),
        storage_base_dir=str(storage_dir),
    )
    assert (storage_dir / "A" / "storage.json").exists()

    ok = delete_account("A", accounts_path=str(accounts_path), remove_storage=True)

    assert ok is True
    data = json.loads(accounts_path.read_text())
    assert data["accounts"] == []
    assert not (storage_dir / "A").exists()


def test_list_accounts_detailed_includes_inactive_when_requested(tmp_path):
    future = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    storage_a = _make_storage(tmp_path, "A")
    storage_b = _make_storage(tmp_path, "B")
    accounts_path = _make_accounts_json(tmp_path, [
        {"name": "A", "storage_path": storage_a, "last_used_at": None,
         "session_expires_at": future, "active": True, "added_at": "2026-01-01T00:00:00Z"},
        {"name": "B", "storage_path": storage_b, "last_used_at": None,
         "session_expires_at": future, "active": False, "added_at": "2026-01-01T00:00:00Z"},
    ])

    result = list_accounts_detailed(accounts_path=accounts_path, include_inactive=True)

    assert [item["name"] for item in result] == ["A", "B"]
    assert result[1]["active"] is False


def test_list_accounts_detailed_prefers_nickname_for_display_name(tmp_path):
    future = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    storage_a = _make_storage(tmp_path, "A")
    accounts_path = _make_accounts_json(tmp_path, [
        {"name": "A", "nickname": "主账号", "storage_path": storage_a, "last_used_at": None,
         "session_expires_at": future, "active": True, "added_at": "2026-01-01T00:00:00Z"},
    ])

    result = list_accounts_detailed(accounts_path=accounts_path, include_inactive=True)

    assert result[0]["nickname"] == "主账号"
    assert result[0]["display_name"] == "主账号"


def test_update_account_nickname_saves_and_can_clear(tmp_path):
    accounts_path = tmp_path / "accounts.json"
    storage_dir = tmp_path / "storage"
    save_cookie_account(
        "A",
        "a1=abc; web_session=xyz",
        accounts_path=str(accounts_path),
        storage_base_dir=str(storage_dir),
    )

    updated = update_account_nickname("A", "运营主号", accounts_path=str(accounts_path))
    assert updated["nickname"] == "运营主号"
    assert updated["display_name"] == "运营主号"

    cleared = update_account_nickname("A", "A", accounts_path=str(accounts_path))
    assert cleared["nickname"] == ""
    assert cleared["display_name"] == "A"


def test_update_account_nickname_ignores_separator_noise(tmp_path):
    accounts_path = tmp_path / "accounts.json"
    storage_dir = tmp_path / "storage"
    save_cookie_account(
        "A",
        "a1=abc; web_session=xyz",
        accounts_path=str(accounts_path),
        storage_base_dir=str(storage_dir),
    )

    updated = update_account_nickname("A", "============================================================", accounts_path=str(accounts_path))

    assert updated["nickname"] == ""
    assert updated["display_name"] == "A"


def test_normalize_nickname_keeps_non_ascii_symbol_runs():
    assert _normalize_nickname("A", "∞∞∞∞∞∞∞∞") == "∞∞∞∞∞∞∞∞"


def test_playwright_login_warns_when_fallback_temp_name_is_used(tmp_path, monkeypatch, capsys):
    accounts_path = tmp_path / "accounts.json"
    storage_base = tmp_path / "storage"
    monkeypatch.setattr(account_manager, "DEFAULT_ACCOUNTS_PATH", accounts_path)
    monkeypatch.setattr(account_manager, "STORAGE_BASE_DIR", storage_base)

    class FakeResponse:
        def json(self):
            return {}

    class FakePage:
        def goto(self, *args, **kwargs):
            return None

        def wait_for_url(self, *args, **kwargs):
            return None

        def wait_for_response(self, *args, **kwargs):
            return FakeResponse()

    class FakeContext:
        def add_init_script(self, *_args, **_kwargs):
            return None

        def new_page(self):
            return FakePage()

        def storage_state(self, path):
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps({"cookies": [], "origins": []}), encoding="utf-8")

    class FakeBrowser:
        def new_context(self, **_kwargs):
            return FakeContext()

        def close(self):
            return None

    class FakePlaywright:
        chromium = types.SimpleNamespace(launch=lambda **_kwargs: FakeBrowser())

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    sync_api = types.ModuleType("playwright.sync_api")
    sync_api.sync_playwright = lambda: FakePlaywright()
    sync_api.TimeoutError = TimeoutError
    pkg = types.ModuleType("playwright")
    pkg.sync_api = sync_api
    monkeypatch.setitem(sys.modules, "playwright", pkg)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)

    account_manager._playwright_login(None, False)

    output = capsys.readouterr().out
    assert "未能识别账号昵称" in output
    assert "请在账号管理页手动设置显示名" in output


def test_playwright_login_raises_when_target_storage_owned_by_other_account(tmp_path, monkeypatch):
    accounts_path = tmp_path / "accounts.json"
    storage_base = tmp_path / "storage"
    final_dir = storage_base / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    accounts_path.write_text(
        json.dumps(
            {
                "accounts": [
                    {
                        "name": "other",
                        "nickname": "",
                        "storage_path": str(final_dir / "storage.json"),
                        "last_used_at": None,
                        "session_expires_at": None,
                        "added_at": "2026-01-01T00:00:00+00:00",
                        "active": True,
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(account_manager, "DEFAULT_ACCOUNTS_PATH", accounts_path)
    monkeypatch.setattr(account_manager, "STORAGE_BASE_DIR", storage_base)

    class FakeResponse:
        def json(self):
            return {"data": {"basic_info": {"nickname": "final"}}}

    class FakePage:
        def goto(self, *args, **kwargs):
            return None

        def wait_for_url(self, *args, **kwargs):
            return None

        def wait_for_response(self, *args, **kwargs):
            return FakeResponse()

    class FakeContext:
        def add_init_script(self, *_args, **_kwargs):
            return None

        def new_page(self):
            return FakePage()

        def storage_state(self, path):
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps({"cookies": [], "origins": []}), encoding="utf-8")

    class FakeBrowser:
        def new_context(self, **_kwargs):
            return FakeContext()

        def close(self):
            return None

    class FakePlaywright:
        chromium = types.SimpleNamespace(launch=lambda **_kwargs: FakeBrowser())

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    sync_api = types.ModuleType("playwright.sync_api")
    sync_api.sync_playwright = lambda: FakePlaywright()
    sync_api.TimeoutError = TimeoutError
    pkg = types.ModuleType("playwright")
    pkg.sync_api = sync_api
    monkeypatch.setitem(sys.modules, "playwright", pkg)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)

    with pytest.raises(RuntimeError) as exc_info:
        account_manager._playwright_login(None, False)

    assert "目标存储目录已被账号 other 占用" in str(exc_info.value)


def test_get_account_cookie_returns_cookie_for_named_account(tmp_path):
    accounts_path = tmp_path / "accounts.json"
    storage_dir = tmp_path / "storage"
    save_cookie_account(
        "A",
        "a1=abc; web_session=xyz",
        accounts_path=str(accounts_path),
        storage_base_dir=str(storage_dir),
    )

    cookie, name = get_account_cookie("A", accounts_path=str(accounts_path))

    assert name == "A"
    assert "a1=abc" in cookie


def test_get_account_cookie_rejects_inactive_account(tmp_path):
    future = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    storage_a = _make_storage(tmp_path, "A")
    accounts_path = _make_accounts_json(tmp_path, [
        {"name": "A", "storage_path": storage_a, "last_used_at": None,
         "session_expires_at": future, "active": False, "added_at": "2026-01-01T00:00:00Z"},
    ])

    with pytest.raises(ValueError):
        get_account_cookie("A", accounts_path=accounts_path)


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
