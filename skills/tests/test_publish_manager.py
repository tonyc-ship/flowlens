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


def test_save_and_load_account_style_respects_confirmed_priority(tmp_path, monkeypatch):
    style_file = tmp_path / "account_styles.json"
    monkeypatch.setattr(publish_manager, "ACCOUNT_STYLE_FILE", style_file)

    publish_manager.save_account_style("acc1", {"style_desc": "分析风格"}, source="analysis")
    assert publish_manager.load_account_style("acc1")["style_desc"] == "分析风格"

    publish_manager.save_account_style("acc1", {"style_desc": "确认风格"}, source="confirmed")
    publish_manager.save_account_style("acc1", {"style_desc": "新的分析风格"}, source="analysis")

    loaded = publish_manager.load_account_style("acc1")
    assert loaded["style_desc"] == "确认风格"
    assert loaded["source"] == "confirmed"


def test_mark_ready_and_list_ready_drafts(tmp_path, monkeypatch):
    draft_dir = tmp_path / "drafts"
    draft_dir.mkdir()
    md_path = draft_dir / "demo.md"
    md_path.write_text("# 标题\n\n正文", encoding="utf-8")
    monkeypatch.setattr(publish_manager, "ACCOUNT_STYLE_FILE", tmp_path / "account_styles.json")
    monkeypatch.setattr(publish_manager, "DRAFT_DIR", draft_dir)

    pub = publish_manager.mark_ready(
        md_path=str(md_path),
        title="发布标题",
        desc="发布简介",
        image_path="/output/images/a.jpg",
        image_paths=["/output/images/a.jpg", "/output/images/b.jpg"],
        account="acc1",
        image_style="单图大字",
        writing_style="口语化",
    )

    assert pub["status"] == "ready"
    assert pub["image_paths"] == ["/output/images/a.jpg", "/output/images/b.jpg"]
    ready = publish_manager.list_ready_drafts(str(draft_dir))
    assert len(ready) == 1
    assert ready[0]["md_path"] == str(md_path)
    assert ready[0]["title"] == "发布标题"
    assert ready[0]["image_paths"] == ["/output/images/a.jpg", "/output/images/b.jpg"]


def test_do_publish_batch_marks_successful_item_as_published(tmp_path, monkeypatch):
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

    draft_dir = tmp_path
    monkeypatch.setattr(publish_manager, "DRAFT_DIR", draft_dir)
    monkeypatch.setattr(publish_manager, "PUBLISH_LOG_FILE", tmp_path / "publish_log.json")

    md_path = tmp_path / "draft.md"
    md_path.write_text("# 标题\n", encoding="utf-8")
    pub_path = tmp_path / "draft.pub.json"
    pub_path.write_text(json.dumps({
        "title": "测试标题",
        "desc": "测试简介",
        "image_path": "",
        "account": "acc1",
        "status": "ready",
        "confirmed_at": "2026-04-02T10:00:00",
    }, ensure_ascii=False), encoding="utf-8")

    mock_publisher = MagicMock()
    mock_publisher.publish.return_value = {"note_id": "abc"}

    with patch("publish_manager.LocalPublisher", return_value=mock_publisher):
        results = publish_manager.do_publish_batch(
            [{"draft_path": str(md_path), "account": "acc1"}],
            accounts_path=str(accounts_json),
        )

    key = f"acc1::{md_path.stem}"
    assert results[key]["status"] == "ok"
    saved = json.loads(pub_path.read_text(encoding="utf-8"))
    assert saved["status"] == "published"


def test_do_publish_batch_keeps_failed_item_ready(tmp_path, monkeypatch):
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

    draft_dir = tmp_path
    monkeypatch.setattr(publish_manager, "DRAFT_DIR", draft_dir)
    monkeypatch.setattr(publish_manager, "PUBLISH_LOG_FILE", tmp_path / "publish_log.json")

    md_path = tmp_path / "draft.md"
    md_path.write_text("# 标题\n", encoding="utf-8")
    pub_path = tmp_path / "draft.pub.json"
    pub_path.write_text(json.dumps({
        "title": "测试标题",
        "desc": "测试简介",
        "image_path": "",
        "account": "acc1",
        "status": "ready",
        "confirmed_at": "2026-04-02T10:00:00",
    }, ensure_ascii=False), encoding="utf-8")

    mock_publisher = MagicMock()
    mock_publisher.publish.side_effect = Exception("发布失败")

    with patch("publish_manager.LocalPublisher", return_value=mock_publisher):
        results = publish_manager.do_publish_batch(
            [{"draft_path": str(md_path), "account": "acc1"}],
            accounts_path=str(accounts_json),
        )

    key = f"acc1::{md_path.stem}"
    assert results[key]["status"] == "error"
    saved = json.loads(pub_path.read_text(encoding="utf-8"))
    assert saved["status"] == "ready"


def test_do_publish_batch_uses_all_selected_images(tmp_path, monkeypatch):
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

    draft_dir = tmp_path
    monkeypatch.setattr(publish_manager, "DRAFT_DIR", draft_dir)
    monkeypatch.setattr(publish_manager, "PUBLISH_LOG_FILE", tmp_path / "publish_log.json")

    md_path = tmp_path / "draft.md"
    md_path.write_text("# 标题\n", encoding="utf-8")
    image1 = tmp_path / "card_1.png"
    image2 = tmp_path / "card_2.png"
    image1.write_bytes(b"png-1")
    image2.write_bytes(b"png-2")
    pub_path = tmp_path / "draft.pub.json"
    pub_path.write_text(json.dumps({
        "title": "测试标题",
        "desc": "测试简介",
        "image_path": str(image1),
        "image_paths": [str(image1), str(image2)],
        "account": "acc1",
        "status": "ready",
        "confirmed_at": "2026-04-02T10:00:00",
    }, ensure_ascii=False), encoding="utf-8")

    mock_publisher = MagicMock()
    mock_publisher.publish.return_value = {"note_id": "abc"}

    with patch("publish_manager.LocalPublisher", return_value=mock_publisher):
        results = publish_manager.do_publish_batch(
            [{"draft_path": str(md_path), "account": "acc1"}],
            accounts_path=str(accounts_json),
        )

    key = f"acc1::{md_path.stem}"
    assert results[key]["status"] == "ok"
    publish_kwargs = mock_publisher.publish.call_args.kwargs
    assert len(publish_kwargs["images"]) == 2


def test_save_draft_metadata_merges_image_paths(tmp_path, monkeypatch):
    draft_dir = tmp_path / "drafts"
    draft_dir.mkdir()
    md = draft_dir / "demo.md"
    md.write_text("# demo", encoding="utf-8")
    monkeypatch.setattr(publish_manager, "DRAFT_DIR", draft_dir)

    saved = publish_manager.save_draft_metadata(
        str(md),
        image_path="/output/draft_cards/demo/card_1.png",
        image_paths=[
            "/output/draft_cards/demo/card_1.png",
            "/output/draft_cards/demo/card_2.png",
            "/output/draft_cards/demo/card_1.png",
        ],
    )

    assert saved["image_path"] == "/output/draft_cards/demo/card_1.png"
    assert saved["image_paths"] == [
        "/output/draft_cards/demo/card_1.png",
        "/output/draft_cards/demo/card_2.png",
    ]
    pub = json.loads(md.with_suffix(".pub.json").read_text(encoding="utf-8"))
    assert pub["image_path"] == "/output/draft_cards/demo/card_1.png"
    assert pub["image_paths"] == [
        "/output/draft_cards/demo/card_1.png",
        "/output/draft_cards/demo/card_2.png",
    ]


def test_normalize_image_paths_prefers_explicit_primary():
    normalized = publish_manager._normalize_image_paths(
        "/output/draft_cards/demo/card_2.png",
        [
            "/output/draft_cards/demo/card_1.png",
            "/output/draft_cards/demo/card_2.png",
        ],
    )

    assert normalized == [
        "/output/draft_cards/demo/card_2.png",
        "/output/draft_cards/demo/card_1.png",
    ]


def test_save_draft_metadata_keeps_existing_primary_when_blank_value_passed(tmp_path, monkeypatch):
    draft_dir = tmp_path / "drafts"
    draft_dir.mkdir()
    md = draft_dir / "demo.md"
    md.write_text("# demo", encoding="utf-8")
    md.with_suffix(".pub.json").write_text(
        json.dumps(
            {
                "image_path": "/output/draft_cards/demo/card_1.png",
                "image_paths": [
                    "/output/draft_cards/demo/card_1.png",
                    "/output/draft_cards/demo/card_2.png",
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(publish_manager, "DRAFT_DIR", draft_dir)

    saved = publish_manager.save_draft_metadata(str(md), image_path="")

    assert saved["image_path"] == "/output/draft_cards/demo/card_1.png"
    assert saved["image_paths"] == [
        "/output/draft_cards/demo/card_1.png",
        "/output/draft_cards/demo/card_2.png",
    ]


def test_save_draft_metadata_does_not_wipe_images_when_empty_list_passed(tmp_path, monkeypatch):
    draft_dir = tmp_path / "drafts"
    draft_dir.mkdir()
    md = draft_dir / "draft.md"
    md.write_text("# 测试文案", encoding="utf-8")
    md.with_suffix(".pub.json").write_text(
        json.dumps(
            {
                "image_path": "/output/draft_cards/a.png",
                "image_paths": ["/output/draft_cards/a.png"],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(publish_manager, "DRAFT_DIR", draft_dir)

    result = publish_manager.save_draft_metadata(str(md), image_path="", image_paths=[])

    assert result["image_path"] == "/output/draft_cards/a.png"
    assert result["image_paths"] == ["/output/draft_cards/a.png"]


def test_save_draft_metadata_updates_images_when_nonempty_list_passed(tmp_path, monkeypatch):
    draft_dir = tmp_path / "drafts"
    draft_dir.mkdir()
    md = draft_dir / "draft.md"
    md.write_text("# 测试文案", encoding="utf-8")
    md.with_suffix(".pub.json").write_text(
        json.dumps(
            {
                "image_path": "/output/draft_cards/old.png",
                "image_paths": ["/output/draft_cards/old.png"],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(publish_manager, "DRAFT_DIR", draft_dir)

    result = publish_manager.save_draft_metadata(
        str(md),
        image_path="",
        image_paths=["/output/draft_cards/new.png"],
    )

    assert result["image_path"] == "/output/draft_cards/new.png"
    assert result["image_paths"] == ["/output/draft_cards/new.png"]


def test_move_pub_json_moves_sidecar_when_draft_is_renamed(tmp_path):
    old_md = tmp_path / "old.md"
    new_md = tmp_path / "new.md"
    old_md.write_text("# old", encoding="utf-8")
    old_pub = old_md.with_suffix(".pub.json")
    old_pub.write_text('{"image_path":"/output/draft_cards/old/card_1.png"}', encoding="utf-8")

    publish_manager.move_pub_json(str(old_md), str(new_md))

    assert not old_pub.exists()
    assert new_md.with_suffix(".pub.json").exists()


# ── _clean_desc ───────────────────────────────────────────────────────────────

import pytest

@pytest.mark.parametrize("raw,expected_absent,expected_present", [
    # markdown bold 被去除
    ("**重点内容** 正文", ["**"], ["重点内容"]),
    # ## 标题行被去除
    ("## 标题行\n正文", ["##"], ["标题行"]),
    # > 引用被去除
    ("> 引用文字\n正文", ["> "], ["引用文字"]),
    # 整行 hashtag 被过滤
    ("正文\n#海外求职 #求职攻略", ["#海外求职"], ["正文"]),
    # 行内 hashtag 不过滤（混合正文）
    ("正文 #某话题 继续", [], ["正文", "继续"]),
    # 反引号代码被去除
    ("使用 `pip install` 命令", ["`pip install`"], ["使用", "命令"]),
])
def test_clean_desc_strips_markdown(raw, expected_absent, expected_present):
    result = publish_manager._clean_desc(raw)
    for token in expected_absent:
        assert token not in result, f"应已去除 {token!r}，结果: {result!r}"
    for token in expected_present:
        assert token in result, f"应保留 {token!r}，结果: {result!r}"


# ── _perturb_image ─────────────────────────────────────────────────────────────

def test_perturb_image_changes_file(tmp_path):
    pytest.importorskip("PIL")
    import numpy as np
    from PIL import Image

    src = tmp_path / "src.png"
    # 纯白 10×10 图，全部像素值为 255，噪点 -1 后可检测
    img = Image.fromarray(np.full((10, 10, 3), 255, dtype=np.uint8))
    img.save(str(src))

    dst = tmp_path / "dst.png"
    publish_manager._perturb_image(str(src), str(dst))

    src_bytes = src.read_bytes()
    dst_bytes = dst.read_bytes()
    assert src_bytes != dst_bytes, "微扰后文件内容应与原图不同"


def test_perturb_image_fallback_without_pillow(tmp_path, monkeypatch):
    """Pillow 不可用时应退化为文件复制。"""
    import builtins
    real_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name in ("PIL", "PIL.Image", "numpy"):
            raise ImportError("mocked")
        return real_import(name, *args, **kwargs)

    src = tmp_path / "src.png"
    src.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    dst = tmp_path / "dst.png"

    monkeypatch.setattr(builtins, "__import__", mock_import)
    publish_manager._perturb_image(str(src), str(dst))
    assert dst.read_bytes() == src.read_bytes()


# ── 发布日志持久化 ──────────────────────────────────────────────────────────────

def test_account_publish_log_persists(tmp_path, monkeypatch):
    log_file = tmp_path / "pub_log.json"
    monkeypatch.setattr(publish_manager, "PUBLISH_LOG_FILE", log_file)

    # 初始无记录
    assert publish_manager._get_account_last_publish("acc1") == 0.0

    # 写入后可读回
    publish_manager._set_account_last_publish("acc1")
    ts = publish_manager._get_account_last_publish("acc1")
    import time
    assert time.time() - ts < 5, "时间戳应接近当前时间"

    # 多账号互不影响
    publish_manager._set_account_last_publish("acc2")
    assert publish_manager._get_account_last_publish("acc1") == ts


def test_rate_limit_blocks_second_publish(tmp_path, monkeypatch):
    """同账号两次调用之间不足 MIN_PUBLISH_INTERVAL 时应被拒绝。"""
    future = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    storage = tmp_path / "storage.json"
    storage.write_text(json.dumps({
        "cookies": [{"name": "a1", "value": "x"}, {"name": "web_session", "value": "y"}]
    }))
    accounts_json = tmp_path / "accounts.json"
    accounts_json.write_text(json.dumps({
        "accounts": [{"name": "acc1", "storage_path": str(storage),
                      "session_expires_at": future, "active": True, "last_used_at": None}]
    }))
    draft_dir = tmp_path
    monkeypatch.setattr(publish_manager, "DRAFT_DIR", draft_dir)
    log_file = tmp_path / "pub_log.json"
    monkeypatch.setattr(publish_manager, "PUBLISH_LOG_FILE", log_file)

    # 先写入"刚发布"时间戳
    with publish_manager._rate_limit_lock:
        publish_manager._set_account_last_publish("acc1")

    md_path = tmp_path / "draft2.md"
    md_path.write_text("# 标题\n", encoding="utf-8")
    (tmp_path / "draft2.pub.json").write_text(json.dumps({
        "title": "t", "desc": "d", "image_path": "",
        "account": "acc1", "status": "ready", "confirmed_at": "2026-04-02T10:00:00",
    }), encoding="utf-8")

    results = publish_manager.do_publish_batch(
        [{"draft_path": str(md_path), "account": "acc1"}],
        accounts_path=str(accounts_json),
    )
    key = f"acc1::{md_path.stem}"
    assert results[key]["status"] == "error"
    assert "频繁" in results[key]["error"]
