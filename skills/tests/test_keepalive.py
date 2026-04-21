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


def test_keepalive_returns_error_for_missing_storage(tmp_path):
    """storageState 文件不存在时应返回 ok=False"""
    from xhs_keepalive import keepalive_account
    result = keepalive_account(
        {"name": "ghost", "storage_path": str(tmp_path / "nonexistent.json")}
    )
    assert result["ok"] is False
    assert "不存在" in result["message"]
