import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import xhs_full_pipeline


def test_select_recent_analysis_rows_returns_tail():
    rows = [
        {"账号昵称": "old_1"},
        {"账号昵称": "old_2"},
        {"账号昵称": "new_1"},
    ]

    selected = xhs_full_pipeline._select_recent_analysis_rows(rows, 2)

    assert [row["账号昵称"] for row in selected] == ["old_2", "new_1"]


def test_main_skip_explore_uses_fixed_workbook_and_tail_rows(monkeypatch, tmp_path):
    workbook = tmp_path / "爆款分析" / "xhs_account_analysis.xlsx"
    workbook.parent.mkdir(parents=True, exist_ok=True)
    workbook.write_text("stub", encoding="utf-8")

    captured = {}

    monkeypatch.setattr(xhs_full_pipeline, "ANALYSIS_XLSX", workbook)
    monkeypatch.setattr(xhs_full_pipeline, "load_analysis_xlsx", lambda path: [
        {"账号昵称": "old_1"},
        {"账号昵称": "old_2"},
        {"账号昵称": "new_1"},
    ])
    monkeypatch.setattr(xhs_full_pipeline, "run_generate_copies", lambda accounts, copies_per_account=2: captured.setdefault("accounts", accounts) or [])
    monkeypatch.setattr(xhs_full_pipeline, "run_generate_cards", lambda draft_paths, accounts: {})
    monkeypatch.setattr(xhs_full_pipeline.argparse.ArgumentParser, "parse_args", lambda self: type("Args", (), {
        "keyword": "海外求职",
        "accounts": 2,
        "copies": 1,
        "skip_explore": True,
        "skip_cards": True,
    })())

    xhs_full_pipeline.main()

    assert [row["账号昵称"] for row in captured["accounts"]] == ["old_2", "new_1"]


def test_main_uses_rows_returned_by_current_run(monkeypatch):
    captured = {}

    monkeypatch.setattr(xhs_full_pipeline, "run_explore", lambda keyword, author_limit=20: [{"name": "A"}, {"name": "B"}])
    monkeypatch.setattr(xhs_full_pipeline, "run_analyze", lambda authors, keyword, target_count=5: (
        Path("/tmp/xhs_account_analysis.xlsx"),
        [{"账号昵称": "本次A"}, {"账号昵称": "本次B"}],
    ))
    monkeypatch.setattr(xhs_full_pipeline, "run_generate_copies", lambda accounts, copies_per_account=2: captured.setdefault("accounts", accounts) or [])
    monkeypatch.setattr(xhs_full_pipeline, "run_generate_cards", lambda draft_paths, accounts: {})
    monkeypatch.setattr(xhs_full_pipeline.argparse.ArgumentParser, "parse_args", lambda self: type("Args", (), {
        "keyword": "海外求职",
        "accounts": 2,
        "copies": 1,
        "skip_explore": False,
        "skip_cards": True,
    })())

    xhs_full_pipeline.main()

    assert [row["账号昵称"] for row in captured["accounts"]] == ["本次A", "本次B"]
