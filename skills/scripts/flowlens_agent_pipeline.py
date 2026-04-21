#!/usr/bin/env python3
"""
FlowLens Agent 完整调研 → Auto-Redbook-Skills 分析表

流程：
  1. 调用 FlowLens run_agent 做完整的关键词深度调研
  2. 从 agent 的 site_results 提取结构化笔记实体
  3. 按作者聚合，调用 analyze_with_claude 分析每个账号
  4. 写入 Excel 分析表 + 生成文案内容

用法：
  python3 scripts/flowlens_agent_pipeline.py --keyword "英国留学求职"
  python3 scripts/flowlens_agent_pipeline.py --keyword "日本转职" --accounts 5 --copies 2
  python3 scripts/flowlens_agent_pipeline.py --keyword "露营装备" --skip-generate
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# ── 路径设置 ──────────────────────────────────────────────────────────────────

SCRIPTS_DIR = Path(__file__).resolve().parent
BASE_DIR = SCRIPTS_DIR.parent

sys.path.insert(0, str(SCRIPTS_DIR))

from flowlens_runtime import get_flowlens_root

FLOWLENS_ROOT = get_flowlens_root()
if str(FLOWLENS_ROOT) not in sys.path:
    sys.path.insert(0, str(FLOWLENS_ROOT))

from flowlens.core.auth import default_cloud_model          # type: ignore
from flowlens.agent.loop import run_agent                    # type: ignore

from analyze_accounts import (
    analyze_with_claude,
    build_style_model,
    compute_interaction_rate,
    compute_stats,
    compute_stickiness,
    filter_high_performance_notes,
    format_image_preference,
    HEADERS,
    save_style_model,
    write_excel,
)
from generate_content import generate_content, load_style_model, default_style_model

OUTPUT_DIR = BASE_DIR / "output"
ANALYSIS_DIR = OUTPUT_DIR / "爆款分析"
DRAFT_DIR = OUTPUT_DIR / "小红书备选"


def _read_local_env(name: str) -> str:
    """Read Auto-Redbook-Skills env vars without relying on the FlowLens repo env."""
    for env_path in [BASE_DIR / ".env", Path.home() / ".baoyu-skills" / ".env"]:
        try:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if line.startswith(name + "="):
                    return line.split("=", 1)[1].strip().strip('"')
        except Exception:
            pass
    return ""


def _resolve_flowlens_agent_model() -> str:
    """Prefer the local dashboard override before falling back to FlowLens defaults."""
    explicit = (
        _read_local_env("FLOWLENS_AGENT_MODEL")
        or _read_local_env("LLM_MODEL")
        or _read_local_env("MIDSCENE_TEXT_MODEL")
        or _read_local_env("MIDSCENE_MODEL_NAME")
    ).strip()
    return explicit or default_cloud_model()


# ── FlowLens agent 调用 ───────────────────────────────────────────────────────

_AGENT_PROMPT_TEMPLATE = """\
在小红书上深度调研关键词「{keyword}」。

步骤：
1. 用 xhs_topic_scan 搜索关键词「{keyword}」，获取笔记列表。
2. 基于搜索结果和 xhs_topic_scan 的结果，优先选择高相关、高互动、信息量大的代表性笔记继续深读。
3. 深读时优先保证样本多样性，不要为了凑同一作者的固定篇数而在搜索结果页反复滚动。
3. 不要切换到"用户"搜索 tab，始终在笔记搜索结果页操作。
4. 如果打开笔记弹窗失败，先调用 run_site_action(action="close_note") 关闭后重试，\
或直接用 run_site_action(action="read_note", note_id="...") 通过 note_id 打开。
5. 当你已经拿到足够的代表性样本时，停止继续滚动搜索结果，直接进入阅读和总结。
6. 完成后输出完整报告，总结各账号的内容风格、Hook 模式、CTA 模式和内容结构。
"""

_AGENT_MAX_TURNS = 30


def _inject_env_to_os() -> None:
    """把 .env 文件里的变量注入 os.environ，供 FlowLens run_agent 读取。"""
    import os
    for env_path in [BASE_DIR / ".env", Path.home() / ".baoyu-skills" / ".env"]:
        try:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"')
                if k and k not in os.environ:
                    os.environ[k] = v
        except Exception:
            pass


async def _call_flowlens_agent(keyword: str, notes_per_account: int | None = None) -> dict:
    """运行 FlowLens agent，返回 result payload（含 run_dir 和 site_results）。"""
    _inject_env_to_os()
    notes_hint = f"\n每个账号尽量深读 {notes_per_account} 篇笔记。" if notes_per_account else ""
    prompt = _AGENT_PROMPT_TEMPLATE.format(keyword=keyword) + notes_hint
    model = _resolve_flowlens_agent_model()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_kw = re.sub(r"[^\w\u4e00-\u9fff]+", "_", keyword)[:40]
    run_dir = FLOWLENS_ROOT / "task_runs" / f"arb_{timestamp}_{safe_kw}"
    run_dir.mkdir(parents=True, exist_ok=True)

    print(f"  🤖 调用 FlowLens agent: model={model}, max_turns={_AGENT_MAX_TURNS}, run_dir={run_dir.name}")
    result = await run_agent(task=prompt, model=model, run_dir=run_dir, max_turns=_AGENT_MAX_TURNS)

    payload = {
        "run_dir": str(run_dir),
        "report_md": str(run_dir / "report.md"),
        "turns": result.get("turns", 0),
        "total_duration_s": result.get("total_duration_s", 0),
        "result_text": result.get("result", ""),
        "site_results": result.get("site_results", []),
    }
    result_json_path = run_dir / "result.json"
    result_json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


# ── site_results 解析 ─────────────────────────────────────────────────────────

_PLACEHOLDER_STRINGS = {"赞", "收藏", "评论", "likes", "favorites", "comments", ""}


def _parse_count(value) -> int | None:
    """解析互动数。若为占位字符串（'赞'/'收藏'等）返回 None 表示数据缺失。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip().replace(",", "")
    if text in _PLACEHOLDER_STRINGS:
        return None
    m = re.search(r"[\d.]+", text)
    if not m:
        return None
    num = float(m.group())
    if "万" in text or "w" in text.lower():
        num *= 10000
    elif "k" in text.lower():
        num *= 1000
    return int(num)


def _entity_to_note(entity: dict) -> dict:
    """将 FlowLens NoteEntity dict 转换为 analyze_accounts 笔记格式。"""
    content = str(entity.get("content") or "").strip()
    if not content:
        key_points = entity.get("key_points") or []
        content = "\n".join(str(p) for p in key_points if p)
    return {
        "title": str(entity.get("title") or "").strip(),
        "desc": content,
        "word_count": len(content),
        "image_count": int(entity.get("image_count") or 0),
        "liked_count": _parse_count(entity.get("likes_value") if entity.get("likes_value") else entity.get("likes")),
        "collected_count": _parse_count(entity.get("favorites_value") if entity.get("favorites_value") else entity.get("favorites")),
        "comment_count": _parse_count(entity.get("comments_count_value") if entity.get("comments_count_value") else entity.get("comments_count")),
        "cover_url": "",
        "detail_fetched": bool(content),
        # 保留额外字段供后续使用
        "_url": str(entity.get("url") or ""),
        "_note_id": str(entity.get("note_id") or ""),
        "_author": str(entity.get("author") or ""),
        "_author_url": str(entity.get("author_url") or ""),
        "_cta_phrases": entity.get("cta_phrases") or [],
        "_key_points": entity.get("key_points") or [],
        "_screenshot": str(entity.get("screenshot") or ""),  # FlowLens 截图文件名
    }


def _extract_authors_from_site_results(run_dir: str) -> dict[str, dict]:
    """
    从 site_results/*.json 文件中提取笔记实体，按作者聚合。

    返回: {author_key: {"name": str, "profile_url": str, "notes": [...], "fans_count": int, ...}}
    """
    results_dir = Path(run_dir) / "site_results"
    if not results_dir.is_dir():
        print(f"  ⚠️  site_results 目录不存在: {results_dir}")
        return {}

    authors: dict[str, dict] = {}

    for path in sorted(results_dir.iterdir()):
        if path.suffix != ".json" or not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue

        action = data.get("action", "")
        entity_type = data.get("entity_type", "")

        def _ingest_note_entity(entity: dict) -> None:
            """将一个笔记 entity 按作者归档，自动去重。"""
            if not isinstance(entity, dict):
                return
            note_id = str(entity.get("note_id") or "").strip()
            if not note_id:
                return
            author_name = str(entity.get("author") or "").strip()
            author_url = str(entity.get("author_url") or "").strip()
            # 去掉 author_url 中的 query string，作为稳定的 key
            author_url_clean = author_url.split("?")[0].rstrip("/")
            author_key = author_url_clean or author_name
            if not author_key:
                return
            note = _entity_to_note(entity)
            if not note["title"] and not note["desc"]:
                return
            if author_key not in authors:
                authors[author_key] = {
                    "name": author_name or author_key,
                    "profile_url": author_url_clean,
                    "fans_count": 0,
                    "notes_count": 0,
                    "notes": [],
                    "seen_note_ids": set(),
                }
            grp = authors[author_key]
            if author_name and not grp["name"]:
                grp["name"] = author_name
            if author_url_clean and not grp["profile_url"]:
                grp["profile_url"] = author_url_clean
            if note_id not in grp["seen_note_ids"]:
                grp["seen_note_ids"].add(note_id)
                grp["notes"].append(note)

        # ── 笔记详情提取（read_note / extract_entity / entity_type=note） ────
        if action in ("read_note", "extract_entity") and "entity" in data:
            _ingest_note_entity(data["entity"])

        elif entity_type == "note" and "entity" in data:
            _ingest_note_entity(data["entity"])

        # ── topic_scan：notes 列表，每项含嵌套 entity ─────
        elif action == "xhs_topic_scan":
            for item in data.get("notes", []):
                _ingest_note_entity(item.get("entity") or {})
            # selected_cards 也可能带 entity
            for item in data.get("selected_cards", []):
                _ingest_note_entity(item.get("entity") or {})

        # ── search_cards（entity_type 模式，无 action 字段）─
        elif entity_type in ("xhs_search_cards", "xhs_cards") or (
            not action and "cards" in data
        ):
            for card in data.get("cards", []):
                if not isinstance(card, dict):
                    continue
                # 如果 card 内嵌了完整 entity，直接 ingest
                if "entity" in card:
                    _ingest_note_entity(card["entity"])
                else:
                    # 只有卡片级摘要，作为作者占位（无深度笔记内容）
                    author_name = str(card.get("author") or card.get("author_name") or "").strip()
                    author_url = str(card.get("author_url") or "").strip().split("?")[0].rstrip("/")
                    author_key = author_url or author_name
                    if not author_key or not author_name:
                        continue
                    if author_key not in authors:
                        authors[author_key] = {
                            "name": author_name,
                            "profile_url": author_url,
                            "fans_count": 0,
                            "notes_count": 0,
                            "notes": [],
                            "seen_note_ids": set(),
                        }

        # ── 作者主页（entity_type 模式） ──────────────────
        elif entity_type == "xhs_author_profile" or action in ("extract_author", "read_author"):
            entity = data.get("entity") or {}
            if not isinstance(entity, dict):
                continue
            author_name = str(entity.get("name") or "").strip()
            profile_url = str(entity.get("profile_url") or "").strip().split("?")[0].rstrip("/")
            author_key = profile_url or author_name
            if author_key in authors:
                grp = authors[author_key]
                grp["fans_count"] = _parse_count(entity.get("fans_count") or entity.get("followers"))
                grp["notes_count"] = _parse_count(entity.get("notes_count") or entity.get("post_count"))
            # 作者主页 notes 列表也可能含笔记 entity
            for note_item in entity.get("notes", []):
                _ingest_note_entity(note_item.get("entity") or note_item)

    # ── 按作者名合并重复条目（URL key vs name key 导致的重复）────
    name_to_key: dict[str, str] = {}
    for key, grp in list(authors.items()):
        name = grp["name"]
        if name in name_to_key:
            canonical = name_to_key[name]
            canon_grp = authors[canonical]
            # 合并笔记（去重）
            existing_ids = canon_grp["seen_note_ids"] if "seen_note_ids" in canon_grp else set()
            for note in grp.get("notes", []):
                nid = note.get("_url", "") or note.get("title", "")
                if nid not in existing_ids:
                    existing_ids.add(nid)
                    canon_grp["notes"].append(note)
            # 合并粉丝数、主页链接
            if not canon_grp["profile_url"] and grp["profile_url"]:
                canon_grp["profile_url"] = grp["profile_url"]
            if not canon_grp["fans_count"] and grp["fans_count"]:
                canon_grp["fans_count"] = grp["fans_count"]
            del authors[key]
        else:
            name_to_key[name] = key
            if "seen_note_ids" not in grp:
                grp["seen_note_ids"] = set()

    # 清理辅助字段
    for grp in authors.values():
        grp.pop("seen_note_ids", None)

    return authors


# ── 截图视觉补全 ──────────────────────────────────────────────────────────────

_VISION_EXTRACT_PROMPT = """\
这是一张小红书笔记详情截图。请提取以下信息，用 JSON 格式返回（字段缺失时填空字符串）：
{
  "likes": "点赞数（数字字符串，如 1234 或 1.2万）",
  "favorites": "收藏数",
  "comments": "评论数",
  "content": "笔记正文内容（完整提取，包括所有文字段落）"
}
只返回 JSON，不要其他说明。
"""


def _load_vision_key() -> tuple[str, str, str]:
    """返回视觉API (api_key, base_url, model)。优先 DASHSCOPE_IMAGE_KEY（标准端点支持多模态）。"""
    import os as _os
    from pathlib import Path as _Path
    try:
        from dotenv import load_dotenv as _load_dotenv
        for ep in [_Path.cwd() / ".env", _Path(__file__).parent.parent / ".env"]:
            if ep.exists():
                _load_dotenv(ep)
                break
    except ImportError:
        pass
    # 优先使用标准 dashscope key（支持 qwen-vl-max 多模态）
    key = _os.getenv("DASHSCOPE_IMAGE_KEY", "") or _os.getenv("DASHSCOPE_API_KEY", "")
    if key:
        vision_model = _os.getenv("VISION_MODEL", "") or "qwen-vl-max"
        return key, "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions", vision_model
    # 备选：检查 MIDSCENE key 是否是标准端点（非 coding.dashscope）
    mid_key = _os.getenv("MIDSCENE_MODEL_API_KEY", "")
    mid_base = _os.getenv("MIDSCENE_MODEL_BASE_URL", "")
    if mid_key and "coding.dashscope" not in mid_base:
        mid_model = _os.getenv("VISION_MODEL", "") or _os.getenv("MIDSCENE_MODEL_NAME", "qwen-vl-max")
        if not mid_base.endswith("/chat/completions"):
            mid_base = mid_base.rstrip("/") + "/chat/completions"
        return mid_key, mid_base, mid_model
    return "", "", ""


def _call_vision_api(image_path: str) -> dict:
    """调用 DashScope 视觉 API 解析截图，返回 {likes, favorites, comments, content}。"""
    import base64
    import json as _json
    import urllib.request
    import urllib.error

    from analyze_accounts import strip_json_fences
    api_key, base_url, model_name = _load_vision_key()
    if not api_key:
        return {}

    try:
        img_bytes = Path(image_path).read_bytes()
    except Exception:
        return {}

    b64 = base64.b64encode(img_bytes).decode()
    ext = Path(image_path).suffix.lstrip(".").lower() or "png"
    mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}.get(ext, "image/png")

    messages = [{
        "role": "user",
        "content": [
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            {"type": "text", "text": _VISION_EXTRACT_PROMPT},
        ],
    }]

    payload = _json.dumps({"model": model_name, "messages": messages, "temperature": 0.0}).encode()
    req = urllib.request.Request(
        base_url,
        data=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = _json.loads(resp.read())
        raw = (((data or {}).get("choices") or [{}])[0].get("message") or {}).get("content", "")
        if isinstance(raw, list):
            raw = " ".join(b.get("text", "") for b in raw if isinstance(b, dict))
        return _json.loads(strip_json_fences(str(raw)))
    except Exception as e:
        print(f"    ⚠️  视觉 API 解析失败 ({Path(image_path).name}): {e}")
        return {}


def _enrich_notes_via_screenshots(authors: dict[str, dict], run_dir: str) -> None:
    """
    对缺失关键数据（正文或互动数）的笔记，用 FlowLens 截图调用视觉 API 补全。
    直接修改 authors 中的 note 数据，无返回值。
    """
    run_path = Path(run_dir)

    for grp in authors.values():
        for note in grp["notes"]:
            needs_content = not note.get("desc")
            # liked_count=None 表示 FlowLens 返回了占位字符串（"赞"）而非真实数值
            needs_stats = note.get("liked_count") is None

            if not needs_content and not needs_stats:
                continue

            # 优先用 entity.screenshot（笔记详情截图）
            screenshot_name = note.get("_screenshot", "")
            screenshot_path = run_path / screenshot_name if screenshot_name else None

            # 备选：site_media/{note_id}/image_01.jpg（笔记主图）
            if not screenshot_path or not screenshot_path.exists():
                note_id = note.get("_note_id", "")
                media_dir = run_path / "site_media" / note_id
                if media_dir.is_dir():
                    candidates = sorted(media_dir.glob("image_0*.jpg"))
                    screenshot_path = candidates[0] if candidates else None

            # 再备选：run_dir 里含 note_id 的截图
            if not screenshot_path or not screenshot_path.exists():
                note_id = note.get("_note_id", "")
                if note_id:
                    matches = sorted(run_path.glob(f"*{note_id[:8]}*.png")) + sorted(run_path.glob(f"*{note_id[:8]}*.jpg"))
                    screenshot_path = matches[0] if matches else None

            if not screenshot_path or not screenshot_path.exists():
                continue

            print(f"    🔍 视觉补全: {grp['name']} / {note.get('title','')[:20]} ← {screenshot_path.name}")
            extracted = _call_vision_api(str(screenshot_path))
            if not extracted:
                continue

            if needs_content and extracted.get("content"):
                note["desc"] = extracted["content"].strip()
                note["word_count"] = len(note["desc"])
                note["detail_fetched"] = True

            if needs_stats:
                v = _parse_count(extracted.get("likes"))
                if v is not None:
                    note["liked_count"] = v
                v = _parse_count(extracted.get("favorites"))
                if v is not None:
                    note["collected_count"] = v
                v = _parse_count(extracted.get("comments"))
                if v is not None:
                    note["comment_count"] = v


# ── analyze_accounts 格式转换 ─────────────────────────────────────────────────

def _build_excel_rows(authors: dict[str, dict], keyword: str) -> list[dict]:
    """将作者数据转换为 Excel 行（HEADERS 格式）。"""
    rows = []

    for author_key, grp in authors.items():
        nickname = grp["name"]
        notes = grp["notes"]
        if not notes:
            continue

        print(f"\n  📊 分析账号: {nickname}（{len(notes)} 篇笔记）")

        row: dict = {h: "N/A" for h in HEADERS}
        row["账号昵称"] = nickname
        row["状态"] = "爆款账号"
        row["搜索关键词"] = keyword

        # 互动统计
        stats = compute_stats(notes)
        row["粉丝数"] = grp.get("fans_count") or "N/A"
        row["笔记总数"] = grp.get("notes_count") or "N/A"
        row["分析笔记篇数"] = len(notes)
        row["平均图片张数"] = stats["avg_images"]
        row["图片偏好"] = format_image_preference(notes)
        row["平均文字字数"] = stats["avg_words"]
        row["平均点赞数"] = stats["avg_liked"]
        row["平均收藏数"] = stats["avg_collected"]
        row["平均评论数"] = stats["avg_comments"]

        fans = grp.get("fans_count") or 0
        row["互动率"] = compute_interaction_rate(stats["avg_liked"], stats["avg_collected"], fans)
        row["用户粘度"] = compute_stickiness(stats["avg_collected"], stats["avg_liked"])

        # 爆款数据
        sorted_notes = sorted(notes, key=lambda n: int(n.get("liked_count") or 0), reverse=True)
        top_note = sorted_notes[0]
        row["爆款笔记数"] = sum(1 for n in notes if int(n.get("liked_count") or 0) >= 30)
        row["最高爆款评分"] = int(top_note.get("liked_count") or 0)
        row["代表爆款标题"] = top_note.get("title", "")
        row["代表爆款链接"] = top_note.get("_url", "")

        # AI 分析（Hook/CTA/风格等）
        analysis = analyze_with_claude(notes, nickname)
        row["图文风格描述"] = analysis.get("图文风格描述", "分析失败")
        row["内容偏好"] = analysis.get("内容偏好", "分析失败")
        row["受众分析"] = analysis.get("受众分析", "分析失败")
        row["目标对象关联点"] = analysis.get("目标对象关联点", "分析失败")
        row["账号权重评级"] = analysis.get("账号权重评级", "分析失败")
        row["Hook模板"] = analysis.get("Hook模板", "分析失败")
        row["内容结构"] = analysis.get("内容结构", "分析失败")
        row["CTA模板"] = analysis.get("CTA模板", "分析失败")

        # 风格模型
        filtered_notes = filter_high_performance_notes(notes)
        style_model = build_style_model(filtered_notes)
        save_style_model(nickname, style_model)
        row["标题模板"] = " | ".join(style_model.get("title_templates", []))
        row["情绪标签"] = style_model.get("tone", "")
        row["图片风格"] = style_model.get("image_style", "")

        rows.append(row)

    return rows


# ── 文案生成 ──────────────────────────────────────────────────────────────────

def _generate_drafts(rows: list[dict], keyword: str, copies: int) -> list[Path]:
    """对每个账号生成 copies 篇文案，保存到 DRAFT_DIR。"""
    DRAFT_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d")
    generated_paths = []

    for row in rows:
        nickname = row.get("账号昵称", "unknown")
        style_model_path = BASE_DIR / "output" / "style_models" / f"{nickname}.json"

        if style_model_path.exists():
            style_model = load_style_model(str(style_model_path))
        else:
            style_model = default_style_model()

        for i in range(copies):
            topic = f"{keyword}（参考账号：{nickname}）"
            try:
                content = generate_content(style_model, topic)
            except Exception as e:
                print(f"    ⚠️  文案生成失败 ({nickname} #{i+1}): {e}")
                continue

            safe_kw = re.sub(r"[^\w\u4e00-\u9fff]+", "_", keyword)[:20]
            safe_nick = re.sub(r"[^\w\u4e00-\u9fff]+", "_", nickname)[:15]
            draft_path = DRAFT_DIR / f"{safe_kw}_{safe_nick}_{date_str}_{i+1}.md"
            draft_path.write_text(content, encoding="utf-8")
            generated_paths.append(draft_path)
            print(f"    ✅ 文案已保存: {draft_path.name}")

    return generated_paths


# ── 卡片生成 ──────────────────────────────────────────────────────────────────

def _generate_cards(draft_paths: list[Path]) -> int:
    """为每篇文案生成图文卡片，返回成功数量。"""
    card_tool = BASE_DIR / "tools" / "info-card-generator"
    if not card_tool.is_dir():
        print("  ⚠️  info-card-generator 未找到，跳过卡片生成")
        return 0

    success = 0
    for draft_path in draft_paths:
        out_dir = DRAFT_DIR / draft_path.stem
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            result = subprocess.run(
                ["node", "src/index.js", str(draft_path), "--output", str(out_dir)],
                cwd=str(card_tool),
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode == 0:
                success += 1
                print(f"    ✅ 卡片: {out_dir.name}/")
            else:
                print(f"    ⚠️  卡片生成失败: {result.stderr[:80]}")
        except Exception as e:
            print(f"    ⚠️  卡片生成异常: {e}")

    return success


# ── HTML 报告生成 ─────────────────────────────────────────────────────────────

def _generate_analysis_html(
    keyword: str,
    authors: dict[str, dict],
    rows: list[dict],
    run_dir: str,
    dest_path: Path,
) -> None:
    """生成与参考 report.html 格式一致的分析报告，截图使用 file:// 绝对路径。"""
    from html import escape as _esc

    run_path = Path(run_dir)
    date_str = datetime.now().strftime("%Y-%m-%d")

    # ── 收集所有笔记 ──────────────────────────────────────────
    all_notes: list[tuple[dict, dict]] = []
    for grp in authors.values():
        for note in grp["notes"]:
            all_notes.append((note, grp))

    total_notes = len(all_notes)

    # ── 从 site_results 获取搜索卡片列表 ─────────────────────
    search_cards: list[dict] = []
    results_dir = run_path / "site_results"
    if results_dir.is_dir():
        for p in sorted(results_dir.iterdir()):
            if p.suffix != ".json":
                continue
            try:
                data = json.loads(p.read_text("utf-8"))
            except Exception:
                continue
            action = data.get("action", "")
            entity_type = data.get("entity_type", "")
            if action == "xhs_topic_scan":
                for item in data.get("notes", []):
                    if isinstance(item, dict):
                        e = item.get("entity") or {}
                        card = {
                            "author": e.get("author") or item.get("author", ""),
                            "title": e.get("title") or item.get("title", ""),
                            "likes": e.get("likes") or e.get("likes_value") or item.get("likes", ""),
                            "type": e.get("type") or item.get("type", "image"),
                        }
                        if card["author"] or card["title"]:
                            search_cards.append(card)
            elif entity_type in ("xhs_search_cards", "xhs_cards") or (not action and "cards" in data):
                for card in data.get("cards", []):
                    if isinstance(card, dict):
                        search_cards.append(card)

    total_search = len(search_cards)
    total_comments = sum(int(note.get("comment_count") or 0) for note, _ in all_notes)
    detail_count = sum(1 for note, _ in all_notes if note.get("detail_fetched"))
    completeness = int(100 * detail_count / max(total_notes, 1))

    # ── 收集笔记详情截图（绝对路径，排除搜索页等非笔记截图）────────
    screenshots = sorted(run_path.glob("*_note_detail.png")) + sorted(run_path.glob("*_note_detail.jpg"))

    # ── CSS ───────────────────────────────────────────────────
    css = """*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif;background:#f5f5f7;color:#1d1d1f;line-height:1.6}
header{background:linear-gradient(135deg,#ff2d55,#ff6b6b);color:#fff;padding:36px 32px 28px}
header h1{font-size:26px;font-weight:700;margin-bottom:6px}
header p{font-size:13px;opacity:.85}
.meta-bar{display:flex;gap:16px;margin-top:14px;flex-wrap:wrap}
.meta-item{background:rgba(255,255,255,.2);padding:5px 14px;border-radius:20px;font-size:12px}
.container{max-width:1100px;margin:0 auto;padding:28px 20px}
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:28px}
.stat-card{background:#fff;border-radius:12px;padding:18px;text-align:center;box-shadow:0 2px 8px rgba(0,0,0,.07)}
.stat-num{font-size:32px;font-weight:700;color:#ff2d55}
.stat-label{font-size:12px;color:#6e6e73;margin-top:3px}
section{margin-bottom:36px}
section h2{font-size:19px;font-weight:600;margin-bottom:14px;padding-bottom:8px;border-bottom:2px solid #ff2d55}
.notes-grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}
@media(max-width:700px){.notes-grid,.stats{grid-template-columns:1fr}}
.note-card{background:#fff;border-radius:16px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,.08)}
.note-header{padding:14px 16px 0}
.note-badges{margin-bottom:6px}
.badge-deep,.badge-lite{display:inline-block;padding:3px 10px;border-radius:20px;font-size:11px;font-weight:600}
.badge-deep{background:#fff0f2;color:#ff2d55}
.badge-lite{background:#fff5e0;color:#c67000}
.note-title{font-size:15px;font-weight:600;line-height:1.4;margin-bottom:6px}
.note-meta{font-size:12px;color:#6e6e73;margin-bottom:10px}
.note-screenshot{width:100%;height:200px;object-fit:cover;cursor:pointer;display:block;background:#f0f0f0}
.note-no-screenshot{width:100%;height:80px;display:flex;align-items:center;justify-content:center;color:#aaa;font-size:12px;background:#f9f9fb}
.note-body{padding:14px 16px}
.note-stats{display:flex;gap:14px;font-size:13px;margin-bottom:10px;flex-wrap:wrap}
.content-block,.kp-block{background:#f9f9fb;border-radius:8px;padding:10px 12px;margin-bottom:10px}
.content-label,.kp-label{font-size:11px;color:#6e6e73;text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px;font-weight:600}
.content-text{font-size:13px;line-height:1.7;color:#3a3a3a;max-height:200px;overflow-y:auto}
.kp-text{font-size:13px;line-height:1.7;color:#3a3a3a}
.note-link{display:block;font-size:11px;color:#0066cc;text-decoration:none;margin-top:8px}
.note-link:hover{text-decoration:underline}
.cards-table,.analysis-table{width:100%;border-collapse:collapse;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.07);font-size:13px}
.cards-table th,.analysis-table th{background:#ff2d55;color:#fff;padding:10px 12px;text-align:left;font-weight:600}
.cards-table td,.analysis-table td{padding:9px 12px;border-bottom:1px solid #f0f0f0;vertical-align:top}
.cards-table tr:last-child td,.analysis-table tr:last-child td{border-bottom:none}
.cards-table tr:hover td,.analysis-table tr:hover td{background:#fff5f7}
.type-badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600}
.type-image{background:#e8f4fd;color:#0077b6}
.type-video{background:#fde8f4;color:#9b0077}
.likes-wrap{display:flex;align-items:center;gap:8px}
.likes-bar{height:6px;background:linear-gradient(90deg,#ff2d55,#ff6b6b);border-radius:3px;min-width:4px}
.screenshot-gallery{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}
.screenshot-gallery img{width:100%;border-radius:8px;box-shadow:0 2px 8px rgba(0,0,0,.1);cursor:pointer;transition:transform .2s}
.screenshot-gallery img:hover{transform:scale(1.03)}
#lightbox{display:none;position:fixed;inset:0;background:rgba(0,0,0,.88);z-index:9999;align-items:center;justify-content:center}
#lightbox.show{display:flex}
#lightbox img{max-width:92vw;max-height:92vh;border-radius:8px}
#lightbox-close{position:absolute;top:18px;right:26px;color:#fff;font-size:30px;cursor:pointer;line-height:1}
footer{text-align:center;padding:20px;color:#6e6e73;font-size:12px}"""

    # ── Notes grid ────────────────────────────────────────────
    notes_html_parts = []
    for note, grp in all_notes:
        screenshot_name = note.get("_screenshot", "")
        sc_path = run_path / screenshot_name if screenshot_name else None
        if not (sc_path and sc_path.exists()):
            note_id = note.get("_note_id", "")
            if note_id:
                matches = sorted(run_path.glob(f"*{note_id[:8]}*.png"))
                sc_path = matches[0] if matches else None

        if sc_path and sc_path.exists():
            img_html = f'<img class="note-screenshot" src="{_esc(sc_path.as_uri())}" onclick="openLightbox(this.src)" alt="截图">'
        else:
            img_html = ""

        badge = '<span class="badge-deep">DEEP 深度</span>' if note.get("detail_fetched") else '<span class="badge-lite">LITE 轻量</span>'
        title = _esc(note.get("title") or "（无标题）")
        author = _esc(grp["name"])
        liked = note.get("liked_count") or 0
        collected = note.get("collected_count") or 0
        comments_n = note.get("comment_count") or 0
        url = _esc(note.get("_url", ""))
        content = (note.get("desc") or "").strip()

        content_html = ""
        if content:
            content_html = f'<div class="content-block"><div class="content-label">📝 正文</div><div class="content-text">{_esc(content).replace(chr(10), "<br>")}</div></div>'

        key_points = note.get("_key_points") or []
        kp_html = ""
        if key_points:
            items = "".join(f"<li>{_esc(str(p))}</li>" for p in key_points[:5])
            kp_html = f'<div class="kp-block"><div class="kp-label">🔑 要点</div><div class="kp-text"><ul style="padding-left:14px">{items}</ul></div></div>'

        cta = note.get("_cta_phrases") or []
        cta_html = ""
        if cta:
            cta_html = f'<div style="margin-top:6px;font-size:12px;color:#c67000">📢 {_esc(" / ".join(str(c) for c in cta[:3]))}</div>'

        link_html = f'<a class="note-link" href="{url}" target="_blank">🔗 查看原笔记</a>' if url else ""

        notes_html_parts.append(f"""    <div class="note-card">
        <div class="note-header">
            <div class="note-badges">{badge}</div>
            <div class="note-title">{title}</div>
            <div class="note-meta">✍️ {author}</div>
        </div>
        {img_html}
        <div class="note-body">
            <div class="note-stats">
                <span>❤️ <strong>{liked}</strong></span>
                <span>⭐ <strong>{collected}</strong></span>
                <span>💬 <strong>{comments_n}</strong></span>
                <span>✅ 完整度 <strong>{"100%" if note.get("detail_fetched") else "部分"}</strong></span>
            </div>
            {content_html}{kp_html}{cta_html}{link_html}
        </div>
    </div>""")

    notes_html = "\n".join(notes_html_parts)

    # ── Search cards table ────────────────────────────────────
    search_section = ""
    if search_cards:
        max_likes_val = max(
            (int(_parse_count(c.get("likes") or c.get("likes_value") or c.get("liked_count")) or 0)
             for c in search_cards),
            default=1,
        ) or 1
        table_rows = ""
        for card in search_cards[:30]:
            a = _esc(str(card.get("author") or card.get("author_name") or ""))
            t = _esc(str(card.get("title") or "（无标题）"))
            raw = _parse_count(card.get("likes") or card.get("likes_value") or card.get("liked_count"))
            lk = raw or 0
            bar_w = max(4, int(80 * lk / max_likes_val))
            nt = str(card.get("type") or card.get("note_type") or "image").lower()
            bc = "type-video" if "video" in nt else "type-image"
            tl = "视频" if "video" in nt else "图文"
            table_rows += f"""<tr>
        <td>{a}</td><td>{t}</td>
        <td><div class="likes-wrap"><div class="likes-bar" style="width:{bar_w}px"></div><span>{lk}</span></div></td>
        <td><span class="type-badge {bc}">{tl}</span></td>
    </tr>"""
        search_section = f"""
  <section>
    <h2>📋 搜索结果全览（{total_search}条）</h2>
    <table class="cards-table">
      <thead><tr><th>作者</th><th>标题</th><th>点赞</th><th>类型</th></tr></thead>
      <tbody>{table_rows}</tbody>
    </table>
  </section>"""

    # ── Account analysis table ────────────────────────────────
    analysis_section = ""
    if rows:
        analysis_rows_html = ""
        for row in rows:
            nick = _esc(str(row.get("账号昵称", "")))
            hook = _esc(str(row.get("Hook模板", "")))
            cta_t = _esc(str(row.get("CTA模板", "")))
            style = _esc(str(row.get("图文风格描述", "")))
            audience = _esc(str(row.get("受众分析", "")))
            rating = _esc(str(row.get("账号权重评级", "")))
            analysis_rows_html += f"""<tr>
        <td><strong>{nick}</strong></td>
        <td style="max-width:200px;white-space:pre-wrap">{hook}</td>
        <td style="max-width:200px;white-space:pre-wrap">{cta_t}</td>
        <td style="max-width:160px">{style}</td>
        <td style="max-width:120px">{audience}</td>
        <td>{rating}</td>
    </tr>"""
        analysis_section = f"""
  <section>
    <h2>🔍 账号深度分析（{len(rows)} 个）</h2>
    <table class="analysis-table">
      <thead><tr><th>账号</th><th>Hook 模板</th><th>CTA 模板</th><th>内容风格</th><th>受众</th><th>权重评级</th></tr></thead>
      <tbody>{analysis_rows_html}</tbody>
    </table>
  </section>"""

    # ── Insights ──────────────────────────────────────────────
    insights_section = ""
    if rows:
        hooks = [r.get("Hook模板", "") for r in rows if r.get("Hook模板") not in ("", "N/A", "分析失败", None)]
        ctas = [r.get("CTA模板", "") for r in rows if r.get("CTA模板") not in ("", "N/A", "分析失败", None)]
        if hooks or ctas:
            hook_items = "".join(f"<li>{_esc(str(h)[:120])}</li>" for h in hooks[:4])
            cta_items = "".join(f"<li>{_esc(str(c)[:120])}</li>" for c in ctas[:4])
            insights_section = f"""
  <section>
    <h2>💡 核心洞察</h2>
    <div style="background:#fff;border-radius:16px;padding:22px;box-shadow:0 2px 8px rgba(0,0,0,.07)">
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px">
        <div>
          <h3 style="font-size:14px;margin-bottom:10px;color:#ff2d55">🪝 Hook 模式</h3>
          <ul style="font-size:13px;line-height:2;padding-left:16px">{hook_items}</ul>
        </div>
        <div>
          <h3 style="font-size:14px;margin-bottom:10px;color:#ff2d55">📢 CTA 模式</h3>
          <ul style="font-size:13px;line-height:2;padding-left:16px">{cta_items}</ul>
        </div>
      </div>
    </div>
  </section>"""

    # ── Screenshot gallery ────────────────────────────────────
    gallery_section = ""
    if screenshots:
        gallery_imgs = "\n      ".join(
            f'<img src="{_esc(sc.as_uri())}" onclick="openLightbox(this.src)" alt="">'
            for sc in screenshots[:16]
        )
        gallery_section = f"""
  <section>
    <h2>📸 截图记录</h2>
    <div class="screenshot-gallery">
      {gallery_imgs}
    </div>
  </section>"""

    # ── Final assembly ────────────────────────────────────────
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>小红书「{_esc(keyword)}」深度检索报告</title>
<style>{css}</style>
</head>
<body>
<header>
  <h1>📖 小红书「{_esc(keyword)}」深度检索报告</h1>
  <p>关键词：{_esc(keyword)} &nbsp;|&nbsp; FlowLens Deep 深度模式</p>
  <div class="meta-bar">
    <div class="meta-item">📅 {date_str}</div>
    <div class="meta-item">📝 {total_notes}篇深度笔记</div>
    <div class="meta-item">📋 {total_search}条搜索结果</div>
    <div class="meta-item">✅ completeness {completeness}%</div>
  </div>
</header>

<div class="container">

  <div class="stats">
    <div class="stat-card"><div class="stat-num">{total_notes}</div><div class="stat-label">完整提取笔记</div></div>
    <div class="stat-card"><div class="stat-num">{total_search}</div><div class="stat-label">搜索结果卡片</div></div>
    <div class="stat-card"><div class="stat-num">{total_comments}</div><div class="stat-label">热评条数</div></div>
    <div class="stat-card"><div class="stat-num">{completeness}%</div><div class="stat-label">数据完整度</div></div>
  </div>

  <section>
    <h2>📝 深度提取笔记</h2>
    <div class="notes-grid">
{notes_html}
    </div>
  </section>
{search_section}
{analysis_section}
{insights_section}
{gallery_section}

</div>

<div id="lightbox" onclick="closeLightbox()">
  <span id="lightbox-close">✕</span>
  <img id="lightbox-img" src="" alt="">
</div>

<footer>FlowLens Deep 检索报告 &nbsp;|&nbsp; {date_str} &nbsp;|&nbsp; 关键词：{_esc(keyword)}</footer>

<script>
function openLightbox(src){{document.getElementById("lightbox-img").src=src;document.getElementById("lightbox").classList.add("show")}}
function closeLightbox(){{document.getElementById("lightbox").classList.remove("show")}}
document.addEventListener("keydown",e=>{{if(e.key==="Escape")closeLightbox()}})
</script>
</body>
</html>"""

    dest_path.write_text(html, encoding="utf-8")


# ── 主流程 ────────────────────────────────────────────────────────────────────

def run_pipeline(
    keyword: str,
    accounts: int = 5,
    notes_per_account: int = 3,
    copies: int = 2,
    skip_generate: bool = False,
) -> dict:
    print(f"\n{'='*60}")
    print(f"FlowLens Agent → Auto-Redbook-Skills 联合流程")
    print(f"关键词: {keyword} | 输出账号: {accounts} | 每账号深读: {notes_per_account} 篇 | 文案数/账号: {copies}")
    print(f"{'='*60}\n")

    # Step 1: FlowLens agent 完整调研（prompt 指定每账号深读篇数）
    print("[Step 1/4] FlowLens agent 深度调研...")
    payload = asyncio.run(_call_flowlens_agent(keyword, notes_per_account))
    run_dir = payload["run_dir"]
    print(f"  ✅ Agent 完成: {payload['turns']} 轮, 耗时 {payload['total_duration_s']:.0f}s")
    print(f"  📁 输出目录: {run_dir}")

    # Step 2: 解析 site_results → 按作者聚合
    print("\n[Step 2/4] 解析 agent 输出，按作者聚合笔记...")
    authors = _extract_authors_from_site_results(run_dir)
    # 按笔记数量降序，取前 accounts 个
    authors_sorted = dict(
        sorted(authors.items(), key=lambda kv: len(kv[1]["notes"]), reverse=True)[:accounts]
    )
    total_notes = sum(len(g["notes"]) for g in authors_sorted.values())
    print(f"  ✅ 找到 {len(authors_sorted)} 个账号，共 {total_notes} 篇深度笔记")
    for key, grp in authors_sorted.items():
        print(f"     - {grp['name']}: {len(grp['notes'])} 篇笔记")

    if not authors_sorted:
        print("  ❌ 未找到任何账号数据，退出")
        return {"status": "error", "error": "no accounts found"}

    # Step 2.5: 截图视觉补全——对缺失正文或互动数的笔记用 FlowLens 截图补全
    print("\n[Step 2.5/4] 截图视觉补全缺失数据...")
    _enrich_notes_via_screenshots(authors_sorted, run_dir)

    # Step 3: AI 分析 → 生成 Excel
    print("\n[Step 3/4] AI 分析各账号，生成分析表...")
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    rows = _build_excel_rows(authors_sorted, keyword)

    date_str = datetime.now().strftime("%Y%m%d")
    xlsx_path = ANALYSIS_DIR / f"xhs_account_analysis_{date_str}.xlsx"
    write_excel(rows, str(xlsx_path))
    print(f"  ✅ 分析表已保存: {xlsx_path}")

    # 生成格式化 HTML 分析报告（截图使用绝对 file:// 路径，含账号分析数据）
    safe_kw = re.sub(r'[\\/:*?"<>|\s]', "_", keyword)[:20]
    html_dest = ANALYSIS_DIR / f"xhs_report_{safe_kw}_{date_str}.html"
    try:
        _generate_analysis_html(keyword, authors_sorted, rows, run_dir, html_dest)
        print(f"  ✅ 分析报告已保存: {html_dest.name}")
    except Exception as _e:
        import traceback
        print(f"  ⚠️  HTML 生成失败: {_e}\n{traceback.format_exc()}")

    if skip_generate:
        print("\n[Step 4/4] 跳过文案和卡片生成（--skip-generate）")
        return {"status": "success", "xlsx": str(xlsx_path), "run_dir": run_dir}

    # Step 4: 文案生成 + 卡片生成
    print(f"\n[Step 4/4] 生成文案（每账号 {copies} 篇）...")
    complete_rows = [r for r in rows if r.get("账号权重评级") not in ("分析失败", "N/A", "")]
    draft_paths = _generate_drafts(complete_rows, keyword, copies)
    print(f"  ✅ 共生成 {len(draft_paths)} 篇文案")

    print(f"\n  生成图文卡片...")
    card_count = _generate_cards(draft_paths)

    print(f"\n{'='*60}")
    print(f"✅ 全流程完成！")
    print(f"   分析表: {xlsx_path}")
    print(f"   文案目录: {DRAFT_DIR}")
    print(f"   卡片成功: {card_count}/{len(draft_paths)}")
    print(f"   FlowLens 报告: {run_dir}/report.html")
    print(f"{'='*60}\n")

    return {
        "status": "success",
        "xlsx": str(xlsx_path),
        "drafts": [str(p) for p in draft_paths],
        "cards_ok": card_count,
        "run_dir": run_dir,
    }


def main():
    parser = argparse.ArgumentParser(description="FlowLens Agent → Auto-Redbook-Skills 联合流程")
    parser.add_argument("--keyword", required=True, help="小红书搜索关键词")
    parser.add_argument("--accounts", type=int, default=5, help="输出分析表的账号数量（默认 5）")
    parser.add_argument("--notes", type=int, default=3, help="每账号深度读取篇数，写入 FlowLens prompt（默认 3）")
    parser.add_argument("--copies", type=int, default=2, help="每账号文案数量（默认 2）")
    parser.add_argument("--skip-generate", action="store_true", help="仅调研分析，跳过文案和卡片生成")
    args = parser.parse_args()

    result = run_pipeline(
        keyword=args.keyword,
        accounts=args.accounts,
        notes_per_account=args.notes,
        copies=args.copies,
        skip_generate=args.skip_generate,
    )
    sys.exit(0 if result.get("status") == "success" else 1)


if __name__ == "__main__":
    main()
