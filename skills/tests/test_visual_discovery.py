# tests/test_visual_discovery.py
"""
Tests for visual_discovery.py helper functions.
Importing the module starts the publish_manager scheduler daemon thread,
which is harmless (daemon=True exits when the test process exits).
"""
import json
import io
import re
import sys
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import visual_discovery


def _make_handler(module, body=None, path="/"):
    payload = json.dumps(body or {}).encode("utf-8")
    handler = module.Handler.__new__(module.Handler)
    handler.path = path
    handler.headers = {"Content-Length": str(len(payload))}
    handler.rfile = io.BytesIO(payload)
    handler.wfile = io.BytesIO()
    handler.send_response = lambda *args, **kwargs: None
    handler.send_header = lambda *args, **kwargs: None
    handler.end_headers = lambda *args, **kwargs: None
    captured = {}

    def _json(data):
        captured["json"] = data

    handler._json = _json
    handler._captured = captured
    return handler


# ── _load_llm_key ─────────────────────────────────────────────────────────────

def test_load_llm_key_uses_openai_key_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    key, url, model = visual_discovery._load_llm_key()
    assert key == "sk-openai-test"
    assert "openai.com" in url
    assert model == "gpt-4o-mini"


def test_load_llm_key_falls_back_to_dashscope(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-dashscope-test")
    key, url, model = visual_discovery._load_llm_key()
    assert key == "sk-dashscope-test"
    assert "dashscope" in url
    assert model == "qwen-plus"


def test_load_llm_key_openai_wins_over_dashscope(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-wins")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-dashscope-ignored")
    key, url, model = visual_discovery._load_llm_key()
    assert key == "sk-openai-wins"
    assert "openai.com" in url


def test_load_llm_key_returns_empty_when_no_key(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    # Point BASE_DIR to tmp_path so no real .env is read
    monkeypatch.setattr(visual_discovery, "BASE_DIR", tmp_path)
    key, url, model = visual_discovery._load_llm_key()
    assert key == ""
    assert url == ""
    assert model == ""


def test_load_llm_key_reads_openai_from_dotenv(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text('OPENAI_API_KEY=sk-from-file\n')
    monkeypatch.setattr(visual_discovery, "BASE_DIR", tmp_path)
    key, url, model = visual_discovery._load_llm_key()
    assert key == "sk-from-file"
    assert "openai.com" in url


def test_load_llm_key_reads_dashscope_from_dotenv(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text('DASHSCOPE_API_KEY=sk-ds-from-file\n')
    monkeypatch.setattr(visual_discovery, "BASE_DIR", tmp_path)
    key, url, model = visual_discovery._load_llm_key()
    assert key == "sk-ds-from-file"
    assert "dashscope" in url


# ── chat_with_gpt ─────────────────────────────────────────────────────────────

def test_chat_with_gpt_returns_error_when_no_key(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.setattr(visual_discovery, "BASE_DIR", tmp_path)
    result = visual_discovery.chat_with_gpt("draft", [], "请优化")
    assert "error" in result
    assert "DASHSCOPE_API_KEY" in result["error"]


def test_chat_with_gpt_calls_api_with_correct_url(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text('DASHSCOPE_API_KEY=sk-fake\n')
    monkeypatch.setattr(visual_discovery, "BASE_DIR", tmp_path)

    captured = {}

    class FakeResp:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            pass
        def read(self):
            return json.dumps({
                "choices": [{"message": {"content": "好的，以下是修改后的文案：\n```markdown\n新文案\n```"}}]
            }).encode()

    def fake_urlopen(req, timeout=None, context=None):
        captured["url"] = req.full_url
        captured["auth"] = req.get_header("Authorization")
        return FakeResp()

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    result = visual_discovery.chat_with_gpt("原始文案", [], "请优化")
    assert "dashscope" in captured["url"]
    assert captured["auth"] == "Bearer sk-fake"
    assert "reply" in result


# ── _load_model_config ────────────────────────────────────────────────────────────

def test_load_model_config_defaults(monkeypatch, tmp_path):
    monkeypatch.delenv("DASHSCOPE_TEXT_MODEL",  raising=False)
    monkeypatch.delenv("DASHSCOPE_IMAGE_MODEL", raising=False)
    monkeypatch.delenv("DASHSCOPE_IMAGE_SIZE",  raising=False)
    monkeypatch.setattr(visual_discovery, "BASE_DIR", tmp_path)
    cfg = visual_discovery._load_model_config()
    assert cfg["text_model"]  == "qwen-plus"
    assert cfg["image_model"] == "wanx2.1-t2i-turbo"
    assert cfg["image_size"]  == "1024*1024"


def test_load_model_config_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("DASHSCOPE_TEXT_MODEL",  "qwen-max")
    monkeypatch.setenv("DASHSCOPE_IMAGE_MODEL", "wanx2.1-t2i-plus")
    monkeypatch.setenv("DASHSCOPE_IMAGE_SIZE",  "768*768")
    monkeypatch.setattr(visual_discovery, "BASE_DIR", tmp_path)
    cfg = visual_discovery._load_model_config()
    assert cfg["text_model"]  == "qwen-max"
    assert cfg["image_model"] == "wanx2.1-t2i-plus"
    assert cfg["image_size"]  == "768*768"


def test_load_model_config_reads_from_dotenv(monkeypatch, tmp_path):
    monkeypatch.delenv("DASHSCOPE_TEXT_MODEL",  raising=False)
    monkeypatch.delenv("DASHSCOPE_IMAGE_MODEL", raising=False)
    monkeypatch.delenv("DASHSCOPE_IMAGE_SIZE",  raising=False)
    (tmp_path / ".env").write_text('DASHSCOPE_IMAGE_MODEL=wanx-custom\n')
    monkeypatch.setattr(visual_discovery, "BASE_DIR", tmp_path)
    cfg = visual_discovery._load_model_config()
    assert cfg["image_model"] == "wanx-custom"
    assert cfg["text_model"]  == "qwen-plus"   # default


def test_extract_draft_meta_parses_fields():
    content = (
        "**账号参考**：账号A\n"
        "**主题**：求职\n"
        "**图片风格**：单图大字\n"
        "**图文风格描述**：口语化、亲切自然\n"
    )
    meta = visual_discovery._extract_draft_meta(content)
    assert meta["account"] == "账号A"
    assert meta["topic"] == "求职"
    assert meta["image_style"] == "单图大字"
    assert meta["writing_style"] == "口语化、亲切自然"


def test_strip_card_hashtags_removes_tag_only_and_inline_tags():
    content = (
        "这是正文第一句 #海外求职 #求职攻略\n"
        "#留学生求职 #英国找工\n"
        "第二句保留 #AI求职 经验"
    )

    cleaned = visual_discovery._strip_card_hashtags(content)

    assert "#海外求职" not in cleaned
    assert "#求职攻略" not in cleaned
    assert "#留学生求职" not in cleaned
    assert "#AI求职" not in cleaned
    assert "这是正文第一句" in cleaned
    assert "第二句保留 经验" in cleaned


def test_strip_card_hashtags_keeps_plain_text_lines():
    content = "正常正文\n\n继续说明，不带标签"
    assert visual_discovery._strip_card_hashtags(content) == content


def test_strip_card_hashtags_preserves_paragraph_break_when_tag_line_removed():
    content = "第一段说明\n#海外求职 #求职攻略\n第二段说明"
    assert visual_discovery._strip_card_hashtags(content) == "第一段说明\n\n第二段说明"


def test_manage_account_nickname_endpoint_truncates_long_input(monkeypatch):
    captured = {}

    def mock_update_nickname(name, nickname, accounts_path=None):
        captured["name"] = name
        captured["nickname"] = nickname
        return {"name": name, "nickname": nickname, "display_name": nickname or name}

    monkeypatch.setattr(visual_discovery.account_manager, "update_account_nickname", mock_update_nickname)

    long_nickname = "昵" * 200
    handler = _make_handler(
        visual_discovery,
        body={"name": "testacct", "nickname": long_nickname},
        path="/api/manage_accounts/nickname",
    )
    handler.do_POST()

    assert captured["name"] == "testacct"
    assert len(captured["nickname"]) == 100
    assert handler._captured["json"]["ok"] is True


def test_load_draft_resets_generating_lock_in_html():
    # 验证 HTML 中内嵌 JS 包含生成锁重置语句。
    # 使用两个独立断言：先检查全局初始值声明，再检查函数内重置语句，
    # 避免对 loadDraft 函数边界做脆弱的正则解析。
    html = visual_discovery.HTML
    assert re.search(r'let\s+_isGeneratingCards\s*=\s*false\s*;', html), \
        "HTML 应声明 _isGeneratingCards 全局初始值为 false"
    # loadDraft 函数体内应包含重置语句（不限制空白格式）
    assert re.search(r'function\s+loadDraft\b[^{]*\{[^}]*_isGeneratingCards\s*=\s*false', html, re.DOTALL), \
        "loadDraft 函数内应将 _isGeneratingCards 重置为 false"


def test_setup_file_logging_creates_log_file(tmp_path, monkeypatch):
    monkeypatch.setattr(visual_discovery, "LOG_DIR", tmp_path / "logs")

    orig_stdout = sys.stdout
    orig_stderr = sys.stderr
    log_path = None
    try:
        log_path = visual_discovery.setup_file_logging()
        assert log_path.exists()
        assert log_path.suffix == ".log"
        assert log_path.parent == tmp_path / "logs"
    finally:
        sys.stdout = orig_stdout
        sys.stderr = orig_stderr
        if log_path is not None:
            log_path.unlink(missing_ok=True)


def test_setup_file_logging_removes_old_logs(tmp_path, monkeypatch):
    import time

    monkeypatch.setattr(visual_discovery, "LOG_DIR", tmp_path / "logs")
    (tmp_path / "logs").mkdir()

    old_log = tmp_path / "logs" / "server_20200101_000000.log"
    old_log.write_text("old", encoding="utf-8")
    old_ts = time.time() - 8 * 86400
    os.utime(old_log, (old_ts, old_ts))

    orig_stdout = sys.stdout
    orig_stderr = sys.stderr
    new_log = None
    try:
        new_log = visual_discovery.setup_file_logging()
        assert not old_log.exists()
    finally:
        sys.stdout = orig_stdout
        sys.stderr = orig_stderr
        if new_log is not None:
            new_log.unlink(missing_ok=True)


# ── _generate_draft_llm ───────────────────────────────────────────────────────

def test_generate_draft_llm_returns_content(monkeypatch, tmp_path):
    monkeypatch.setattr(visual_discovery, "BASE_DIR", tmp_path)

    import urllib.request

    class FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self):
            import json
            return json.dumps({
                "choices": [{"message": {"content": "# 测试标题\n\n正文内容\n\n#标签"}}]
            }).encode()

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None, context=None: FakeResp())

    style_params = {
        "avg_words": 300, "style_desc": "口语化", "emotion": "积极",
        "structure": "痛点 | 解法", "hook_examples": "你知道吗",
        "cta_examples": "收藏", "image_style": "单图大字"
    }
    result = visual_discovery._generate_draft_llm(
        "美国ai求职", style_params, "sk-fake",
        "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        "qwen-plus"
    )
    assert "测试标题" in result
    assert "正文内容" in result


def test_generate_draft_llm_prompt_contains_topic(monkeypatch, tmp_path):
    monkeypatch.setattr(visual_discovery, "BASE_DIR", tmp_path)
    import urllib.request
    captured = {}

    class FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self):
            import json
            return json.dumps({"choices": [{"message": {"content": "# title\n\nbody"}}]}).encode()

    def fake_urlopen(req, timeout=None, context=None):
        import json
        captured["body"] = json.loads(req.data)
        return FakeResp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    style_params = {
        "avg_words": 200, "style_desc": "", "emotion": "轻松",
        "structure": "A | B", "hook_examples": "", "cta_examples": "", "image_style": ""
    }
    visual_discovery._generate_draft_llm(
        "python副业", style_params, "sk-fake", "https://fake.url/v1/chat/completions", "qwen-plus"
    )
    user_content = captured["body"]["messages"][1]["content"]
    assert "python副业" in user_content
    assert "200" in user_content   # avg_words in prompt


# ── account login task ───────────────────────────────────────────────────────

def test_start_account_login_task_rejects_empty_name_for_refresh():
    try:
        visual_discovery._start_account_login_task("", "refresh")
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "账号名不能为空" in str(exc)


def test_start_account_login_task_allows_empty_name_for_scan(monkeypatch):
    class FakeThread:
        def __init__(self, target=None, daemon=None):
            self._target = target
        def start(self):
            self._target()

    class FakeCompleted:
        returncode = 0
        stdout = "👤 已识别账号：海外求职Lisa\n✅ 登录成功\n"
        stderr = ""

    monkeypatch.setattr(visual_discovery, "Thread", FakeThread)
    monkeypatch.setattr(visual_discovery.subprocess, "run", lambda *a, **k: FakeCompleted())

    task = visual_discovery._start_account_login_task("", "scan")

    saved = visual_discovery._login_tasks[task["id"]]
    assert saved["status"] == "done"
    assert saved["name"] == "新账号"


def test_start_account_login_task_marks_done(monkeypatch):
    class FakeThread:
        def __init__(self, target=None, daemon=None):
            self._target = target
        def start(self):
            self._target()

    class FakeCompleted:
        returncode = 0
        stdout = "🌐 打开小红书登录页\n✅ 登录成功\n"
        stderr = ""

    monkeypatch.setattr(visual_discovery, "Thread", FakeThread)
    monkeypatch.setattr(visual_discovery.subprocess, "run", lambda *a, **k: FakeCompleted())

    task = visual_discovery._start_account_login_task("测试账号", "scan")

    assert task["status"] == "running"
    saved = visual_discovery._login_tasks[task["id"]]
    assert saved["status"] == "done"
    assert "登录成功" in saved["message"]


def test_pick_account_task_message_skips_separator_noise():
    lines = [
        "🌐 打开小红书登录页",
        "============================================================",
        "👤 已识别账号：海外求职Lisa",
        "✅ 登录成功，storageState 已保存至 /tmp/storage.json",
        "📅 session 有效期更新至 2026-04-14",
    ]

    message = visual_discovery._pick_account_task_message(lines)

    assert message == "👤 已识别账号：海外求职Lisa"


# ── draft assets ──────────────────────────────────────────────────────────────

def test_normalize_draft_asset_url_maps_local_file_to_public_url(tmp_path, monkeypatch):
    draft_dir = tmp_path / "drafts"
    draft_dir.mkdir()
    card_dir = draft_dir / "demo"
    card_dir.mkdir()
    image = card_dir / "cover.png"
    image.write_bytes(b"png")
    draft = draft_dir / "demo.md"
    draft.write_text("# Demo", encoding="utf-8")
    monkeypatch.setattr(visual_discovery, "DRAFT_DIR", draft_dir)

    url = visual_discovery._normalize_draft_asset_url(str(image), draft)

    assert url == "/output/draft_cards/demo/cover.png"


def test_build_draft_summary_uses_saved_images_from_pub_json(tmp_path, monkeypatch):
    draft_dir = tmp_path / "drafts"
    draft_dir.mkdir()
    draft = draft_dir / "demo.md"
    draft.write_text("# 标题\n\n正文", encoding="utf-8")
    card_dir = draft_dir / "demo"
    card_dir.mkdir()
    (card_dir / "card_1.png").write_bytes(b"png")
    (card_dir / "card_2.png").write_bytes(b"png")
    draft.with_suffix(".pub.json").write_text(
        json.dumps({
            "image_path": "/output/draft_cards/demo/card_2.png",
            "image_paths": [
                "/output/draft_cards/demo/card_2.png",
                "/output/draft_cards/demo/card_1.png",
            ],
            "status": "ready",
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(visual_discovery, "DRAFT_DIR", draft_dir)

    summary = visual_discovery._build_draft_summary(draft)

    assert summary["title"] == "标题"
    assert summary["card_count"] == 2
    assert summary["preview_image"] == "/output/draft_cards/demo/card_2.png"
    assert summary["cards"] == [
        "/output/draft_cards/demo/card_2.png",
        "/output/draft_cards/demo/card_1.png",
    ]


# ── retrieval account selection ──────────────────────────────────────────────

def test_build_explore_cmd_includes_selected_account(monkeypatch):
    monkeypatch.setenv("XHS_EXPLORE_PER_AUTHOR_NOTES", "4")
    cmd = visual_discovery._build_explore_cmd("海外求职", 5, "主账号A")
    assert "--account" in cmd
    assert "主账号A" in cmd
    assert "--per-author-notes" in cmd


def test_build_explore_cmd_omits_account_when_not_selected():
    cmd = visual_discovery._build_explore_cmd("海外求职", 5, "")
    assert "--account" not in cmd


# ── _resolve_card_scheme ──────────────────────────────────────────────────────

def test_resolve_card_scheme_red_keyword():
    scheme = visual_discovery._resolve_card_scheme("红色大字风格")
    assert scheme[0] != ""   # accent color assigned


def test_resolve_card_scheme_unknown_defaults():
    scheme = visual_discovery._resolve_card_scheme("完全不相关的描述xyz")
    # 应返回默认 scheme（非空）
    assert isinstance(scheme, tuple)
    assert len(scheme) == 4
