# scripts/publish_manager.py
"""
发布管理模块 — 账号读取、卡片目录扫描、简介提炼、发布执行、定时调度
"""
import json
import os
import re
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── 账号级发布速率限制（防封控）─────────────────────────────────────────────────
# 同一账号两次发布之间至少间隔 MIN_PUBLISH_INTERVAL 秒
MIN_PUBLISH_INTERVAL = 15 * 60  # 15 分钟
_rate_limit_lock = threading.Lock()

# 路径常量
BASE_DIR = Path(__file__).parent.parent
ACCOUNTS_PATH = BASE_DIR / "accounts.json"
DRAFT_DIR = BASE_DIR / "output" / "小红书备选"
SCHEDULE_FILE = BASE_DIR / "output" / "publish_schedule.json"
ACCOUNT_STYLE_FILE = BASE_DIR / "output" / "account_styles.json"
PUBLISH_LOG_FILE = BASE_DIR / "output" / "account_publish_log.json"

sys.path.insert(0, str(Path(__file__).parent))
from account_manager import is_session_valid
from publish_xhs import LocalPublisher
from account_manager import extract_cookie_string


def _get_account_last_publish(account: str) -> float:
    """返回账号最近一次发布的 Unix 时间戳，未记录时返回 0.0。
    优先读取持久化日志文件，保证跨重启有效。
    调用方必须持有 _rate_limit_lock。
    """
    try:
        data = json.loads(PUBLISH_LOG_FILE.read_text(encoding="utf-8"))
        iso = data.get(account, "")
        if iso:
            return datetime.fromisoformat(iso).timestamp()
    except Exception:
        pass
    return 0.0


def _set_account_last_publish(account: str) -> None:
    """将账号最近发布时间写入持久化日志文件。
    调用方必须持有 _rate_limit_lock，确保读-改-写的原子性。
    """
    try:
        PUBLISH_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = json.loads(PUBLISH_LOG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        data[account] = datetime.now().isoformat(timespec="seconds")
        PUBLISH_LOG_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as e:
        print(f"[warn] 写入发布日志失败: {e}", flush=True)


def _clean_desc(text: str) -> str:
    """清理 markdown 语法和尾部 hashtag，避免格式异常触发审核。"""
    # 去掉尾部 #标签 行（小红书通过话题投递，不应出现在正文）
    lines = text.splitlines()
    body_lines = []
    for line in lines:
        stripped = line.strip()
        # 整行都是 hashtag 则丢弃
        if stripped and all(part.startswith('#') for part in stripped.split()):
            continue
        body_lines.append(line)
    text = "\n".join(body_lines).strip()
    # 去掉 markdown 格式符号
    text = re.sub(r'\*{1,3}([^*\n]+)\*{1,3}', r'\1', text)   # **bold** / *italic*
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)  # ## 标题
    text = re.sub(r'^>\s+', '', text, flags=re.MULTILINE)        # > 引用
    text = re.sub(r'`{1,3}[^`]*`{1,3}', '', text)               # `code`
    text = re.sub(r'\n{3,}', '\n\n', text)                       # 多余空行
    return text.strip()


def _perturb_image(src_path: str, dst_path: str) -> None:
    """在图片随机像素处加微小噪点，改变哈希指纹，防矩阵号识别。
    若 Pillow 未安装则直接复制原文件。
    """
    import shutil
    import random
    try:
        from PIL import Image
        import numpy as np
        img = Image.open(src_path).convert("RGB")
        arr = np.array(img, dtype=np.int16)
        # 在随机 5 个像素处加 ±1 噪点，肉眼不可见
        h, w = arr.shape[:2]
        for _ in range(5):
            y = random.randint(0, h - 1)
            x = random.randint(0, w - 1)
            arr[y, x] = np.clip(arr[y, x] + random.choice([-1, 1]), 0, 255)
        Image.fromarray(arr.astype(np.uint8)).save(dst_path)
    except Exception:
        shutil.copy2(src_path, dst_path)


def _load_json_file(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def _save_json_file(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)


def _pub_path_for_md(md_path: str) -> Path:
    md = Path(md_path)
    return md.with_suffix(".pub.json")


def _load_pub_json(md_path: str) -> Dict[str, Any]:
    pub_path = _pub_path_for_md(md_path)
    return _load_json_file(pub_path, {})


def _save_pub_json(md_path: str, data: Dict[str, Any]) -> None:
    _save_json_file(_pub_path_for_md(md_path), data)


def _normalize_image_paths(image_path: str = "", image_paths: Optional[List[str]] = None) -> List[str]:
    """统一为去重后的图片路径列表。"""
    items: List[str] = []
    if image_path and image_path.strip():
        items.append(image_path.strip())
    if image_paths:
        items.extend(str(p).strip() for p in image_paths if str(p).strip())

    result: List[str] = []
    seen = set()
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def move_pub_json(old_md_path: str, new_md_path: str) -> None:
    """文案重命名时同步迁移 sidecar。"""
    old_path = _pub_path_for_md(old_md_path)
    new_path = _pub_path_for_md(new_md_path)
    if old_path == new_path or not old_path.exists():
        return
    new_path.parent.mkdir(parents=True, exist_ok=True)
    old_path.rename(new_path)


def save_draft_metadata(md_path: str, **fields: Any) -> Dict[str, Any]:
    """合并保存文案侧车元数据，不强制进入 ready 状态。

    图片路径语义：
    - 传入非空 image_path / image_paths → 更新图片记录
    - 传入空字符串 + 空列表（归一化后为 []）→ 跳过图片字段，保留现有记录
    - 传入 None（不含该 key）→ 同上，保留现有记录
    前端区分"未操作图片"与"主动清空"时，应传 null（Python 侧 None）而非空列表。
    """
    md = Path(md_path).resolve()
    if not md.is_relative_to(DRAFT_DIR.resolve()):
        raise ValueError(f"非法路径：{md_path}")
    if not md.exists():
        raise FileNotFoundError(f"文案不存在: {md_path}")

    payload = _load_pub_json(str(md))
    if "image_path" in fields or "image_paths" in fields:
        base_path = fields.get("image_path") if fields.get("image_path") is not None else payload.get("image_path", "")
        base_paths = fields.get("image_paths") if fields.get("image_paths") is not None else payload.get("image_paths", [])
        normalized = _normalize_image_paths(base_path or "", base_paths or [])
        if normalized:
            fields = {
                **fields,
                "image_path": normalized[0],
                "image_paths": normalized,
            }
        else:
            fields = {k: v for k, v in fields.items() if k not in ("image_path", "image_paths")}
    for key, value in fields.items():
        if value is None:
            continue
        payload[key] = value.strip() if isinstance(value, str) else value
    payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
    _save_pub_json(str(md), payload)
    return payload


def _find_account(accounts_path: Optional[str], account_name: str) -> Optional[Dict[str, Any]]:
    path = Path(accounts_path or ACCOUNTS_PATH)
    data = _load_json_file(path, {"accounts": []})
    for acc in data.get("accounts", []):
        if acc.get("active") and acc.get("name") == account_name:
            return acc
    return None


def list_accounts(accounts_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """返回所有账号及有效性状态，跳过 active=False 的账号。"""
    path = Path(accounts_path or ACCOUNTS_PATH)
    if not path.exists():
        return []
    try:
        with open(path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []
    result = []
    for acc in data.get("accounts", []):
        if not acc.get("active"):
            continue
        storage = os.path.expanduser(acc.get("storage_path", ""))
        valid = is_session_valid(acc.get("session_expires_at")) and Path(storage).exists()
        nickname = (acc.get("nickname") or "").strip()
        result.append({
            "name": acc["name"],
            "nickname": nickname,
            "display_name": nickname or acc["name"],
            "valid": valid,
            "session_expires_at": acc.get("session_expires_at"),
            "storage_path": storage,
        })
    return result


def list_card_folders(draft_dir: Optional[str] = None) -> List[Dict[str, Any]]:
    """扫描 output/小红书备选/ 下含 PNG 的子目录，按修改时间降序返回。"""
    base = Path(draft_dir or DRAFT_DIR)
    if not base.exists():
        return []
    result = []
    for d in base.iterdir():
        if not d.is_dir():
            continue
        pngs = sorted(d.glob("*.png"))
        if not pngs:
            continue
        result.append({
            "name": d.name,
            "path": str(d),
            "png_count": len(pngs),
            "mtime": d.stat().st_mtime,
            "mtime_str": datetime.fromtimestamp(d.stat().st_mtime).strftime("%m-%d %H:%M"),
        })
    result.sort(key=lambda x: x["mtime"], reverse=True)
    return result


def extract_summary(folder: str, draft_dir: Optional[str] = None) -> Dict[str, str]:
    """
    从与 folder 同名的 .md 文件提炼标题和 ≤100 字简介。
    简介 = Hook + 各节正文片段拼接，截至 100 字。
    """
    import re
    folder_path = Path(folder)
    base = Path(draft_dir or DRAFT_DIR)
    md_path = base / f"{folder_path.name}.md"

    if not md_path.exists():
        return {"title": folder_path.name, "desc": ""}

    text = md_path.read_text(encoding="utf-8")

    # 提取标题
    title_m = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
    title = (title_m.group(1).strip()[:20]) if title_m else folder_path.name

    # 提取 Hook
    hook_m = re.search(r"\*\*Hook\*\*：(.+)", text)
    hook = hook_m.group(1).strip() if hook_m else ""

    # 提取各节正文（跳过元数据行）
    meta_keys = {"账号参考", "内容结构", "Hook", "情绪", "图片风格"}
    sections = re.findall(r"\*\*([^*\n]+)\*\*\n([^\n*#]+)", text)
    body_parts = [b.strip() for h, b in sections if h.strip() not in meta_keys]

    # 拼接并截取 100 字
    raw = " ".join([hook] + body_parts).strip()
    desc = raw[:100]

    return {"title": title, "desc": desc}


def list_ready_drafts(draft_dir: Optional[str] = None) -> List[Dict[str, Any]]:
    """扫描 *.pub.json，返回 status=ready 的文案列表，按 confirmed_at 降序。"""
    base = Path(draft_dir or DRAFT_DIR)
    if not base.exists():
        return []

    result: List[Dict[str, Any]] = []
    for pub_path in base.glob("*.pub.json"):
        data = _load_json_file(pub_path, {})
        if data.get("status") != "ready":
            continue
        md_path = pub_path.with_suffix("").with_suffix(".md")
        result.append({
            "name": md_path.stem,
            "title": data.get("title", ""),
            "desc": data.get("desc", ""),
            "image_path": data.get("image_path", ""),
            "image_paths": _normalize_image_paths(data.get("image_path", ""), data.get("image_paths", [])),
            "md_path": str(md_path),
            "account": data.get("account", ""),
            "image_style": data.get("image_style", ""),
            "writing_style": data.get("writing_style", ""),
            "confirmed_at": data.get("confirmed_at", ""),
            "status": data.get("status", ""),
        })

    result.sort(key=lambda x: x.get("confirmed_at", ""), reverse=True)
    return result


def save_account_style(account: str, style_params: dict, source: str = "analysis") -> None:
    """写入/更新账号风格偏好。confirmed 优先级高于 analysis。"""
    if not account:
        return
    path = ACCOUNT_STYLE_FILE
    data = _load_json_file(path, {})
    existing = data.get(account) or {}

    if source == "analysis" and existing.get("source") == "confirmed":
        return

    merged = dict(existing)
    merged.update({
        "avg_words": style_params.get("avg_words", merged.get("avg_words", 300)),
        "style_desc": style_params.get("style_desc", merged.get("style_desc", "")),
        "image_style": style_params.get("image_style", merged.get("image_style", "")),
        "emotion": style_params.get("emotion", merged.get("emotion", "")),
        "structure": style_params.get("structure", merged.get("structure", "")),
        "hook_examples": style_params.get("hook_examples", merged.get("hook_examples", "")),
        "cta_examples": style_params.get("cta_examples", merged.get("cta_examples", "")),
        "updated_at": datetime.now().strftime("%Y-%m-%d"),
        "source": source,
    })
    data[account] = merged
    _save_json_file(path, data)


def load_account_style(account: str) -> Optional[dict]:
    """读取指定账号风格偏好。"""
    data = _load_json_file(ACCOUNT_STYLE_FILE, {})
    return data.get(account)


def mark_ready(md_path: str, title: str, desc: str, image_path: str,
               account: str, image_style: str, writing_style: str,
               image_paths: Optional[List[str]] = None) -> Dict[str, Any]:
    """将文案标记为发布就绪，并同步记录账号风格偏好。
    
    Args:
        md_path: 文案 markdown 路径
        title: 发布标题（≤20字）
        desc: 发布简介（≤100字）
        image_path: 配图相对或绝对路径（可为空）
        account: 账号名称（必填）
        image_style: 图片风格描述
        writing_style: 文案风格描述
    
    Returns:
        Dict，包含已保存的 sidecar 内容
    
    Raises:
        ValueError: title 或 account 为空
        FileNotFoundError: md_path 对应文件不存在
    """
    if not title.strip():
        raise ValueError("请填写标题")
    if not account.strip():
        raise ValueError("账号（account）不能为空")
    md = Path(md_path).resolve()
    if not md.is_relative_to(DRAFT_DIR.resolve()):
        raise ValueError(f"非法路径：{md_path}")
    if not md.exists():
        raise FileNotFoundError(f"文案不存在: {md_path}")

    normalized_images = _normalize_image_paths(image_path, image_paths)
    payload = {
        "title": title.strip()[:20],
        "desc": desc.strip()[:100],
        "image_path": normalized_images[0] if normalized_images else "",
        "image_paths": normalized_images,
        "account": account.strip(),
        "image_style": image_style.strip(),
        "writing_style": writing_style.strip(),
        "status": "ready",
        "confirmed_at": datetime.now().isoformat(timespec="seconds"),
    }
    _save_pub_json(str(md), payload)

    save_account_style(
        payload["account"],
        {
            "style_desc": payload["writing_style"],
            "image_style": payload["image_style"],
        },
        source="confirmed",
    )
    return payload


def _load_schedule(schedule_file: str) -> Dict:
    path = Path(schedule_file)
    return _load_json_file(path, {"tasks": []})


def _save_schedule(data: Dict, schedule_file: str) -> None:
    _save_json_file(Path(schedule_file), data)


def save_scheduled_task(
    folder: str = "",
    title: str = "",
    desc: str = "",
    accounts: Optional[List[str]] = None,
    scheduled_at: str = "",
    items: Optional[List[Dict[str, Any]]] = None,
    schedule_file: Optional[str] = None,
) -> Dict[str, Any]:
    """保存一条定时发布任务，返回任务 dict。"""
    sf = schedule_file or str(SCHEDULE_FILE)
    data = _load_schedule(sf)
    accounts = accounts or []
    items = items or []
    task = {
        "id": str(uuid.uuid4()),
        "created_at": datetime.now().isoformat(),
        "scheduled_at": scheduled_at,
        "folder": folder,
        "title": title,
        "desc": desc,
        "accounts": accounts,
        "items": items,
        "status": "pending",
        "result": {},
    }
    data["tasks"].append(task)
    _save_schedule(data, sf)
    return task


def get_scheduled_tasks(schedule_file: Optional[str] = None) -> List[Dict[str, Any]]:
    """返回全部定时任务列表（按 scheduled_at 升序）。"""
    sf = schedule_file or str(SCHEDULE_FILE)
    data = _load_schedule(sf)
    tasks = data.get("tasks", [])
    tasks.sort(key=lambda t: t.get("scheduled_at", ""))
    return tasks


def cancel_task(task_id: str, schedule_file: Optional[str] = None) -> bool:
    """将指定 pending 任务标记为 cancelled，返回是否成功。"""
    sf = schedule_file or str(SCHEDULE_FILE)
    data = _load_schedule(sf)
    for task in data.get("tasks", []):
        if task["id"] == task_id and task["status"] == "pending":
            task["status"] = "cancelled"
            _save_schedule(data, sf)
            return True
    return False


def do_publish_batch(
    items: List[Dict[str, str]],
    accounts_path: Optional[str] = None,
    schedule_file: Optional[str] = None,
) -> Dict[str, Any]:
    """按 文案×账号 批量发布，单项失败不影响其他项。
    
    Args:
        items: List[Dict]，每个 Dict 包含：
            - draft_path (str): output/小红书备选/xxx.md 的相对或绝对路径
            - account (str): 账号名称
        accounts_path: accounts.json 路径（可选）
        schedule_file: 发布任务计划文件路径（可选，预留参数）
    
    Returns:
        Dict[str, Any]，格式为 {"{account}::{draft_name}": {"status": "ok"|"error", ...}}
    """
    del schedule_file  # 预留参数，保持签名与设计稿一致
    import shutil as _shutil
    import tempfile as _tempfile
    draft_base = DRAFT_DIR.resolve()
    results: Dict[str, Any] = {}
    for item in items:
        draft_path = item.get("draft_path", "")
        account_name = item.get("account", "")
        result_key = f"{account_name}::{Path(draft_path).stem}"
        _tmp_dir: Optional[Path] = None
        try:
            # 路径安全检查：防止目录遍历攻击
            draft_obj = Path(draft_path)
            resolved = draft_obj.resolve()
            if not resolved.exists():
                raise FileNotFoundError(f"文案不存在: {draft_path}")
            if not resolved.is_relative_to(draft_base):
                raise ValueError(f"非法路径：{draft_path}（仅允许绝对路径或 {DRAFT_DIR} 下文件）")
            
            pub = _load_pub_json(draft_path)
            if not pub:
                raise FileNotFoundError("未找到就绪 sidecar")
            if pub.get("status") == "published":
                raise ValueError(f"文案已于 {pub.get('published_at','')} 发布过，禁止重复发布")
            if pub.get("status") != "ready":
                raise ValueError("文案未处于 ready 状态")

            # desc 为空时从 .md 文件提取正文
            if not pub.get("desc", "").strip():
                try:
                    md_text = resolved.read_text(encoding="utf-8")
                    body = md_text.split("\n---\n", 1)[-1].strip()
                    # 去掉一级标题行
                    body = re.sub(r"^#[^\n]*\n", "", body, flags=re.MULTILINE).strip()
                    pub["desc"] = body[:1000]
                except Exception:
                    pass

            # 清理 markdown 语法和尾部 hashtag
            pub["desc"] = _clean_desc(pub.get("desc", ""))[:1000]

            acc = _find_account(accounts_path, account_name)
            if not acc:
                raise ValueError("账号不存在或未启用")

            storage_path = os.path.expanduser(acc.get("storage_path", ""))
            if not (is_session_valid(acc.get("session_expires_at")) and Path(storage_path).exists()):
                raise ValueError("账号 session 已过期")

            # 速率限制：同账号两次发布至少间隔 MIN_PUBLISH_INTERVAL 秒（持久化，跨重启有效）
            # 检查通过后立即写入临时时间戳，防止并发请求绕过限制（TOCTOU 修复）
            with _rate_limit_lock:
                last_ts = _get_account_last_publish(account_name)
                elapsed = time.time() - last_ts
                if elapsed < MIN_PUBLISH_INTERVAL:
                    wait_sec = MIN_PUBLISH_INTERVAL - elapsed
                    raise ValueError(
                        f"账号 {account_name} 发布过于频繁，"
                        f"距上次发布仅 {int(elapsed//60)} 分钟，"
                        f"请等待 {int(wait_sec//60)+1} 分钟后再试"
                    )
                # 在锁内写入临时时间戳，阻断同账号的并发请求
                _set_account_last_publish(account_name)

            cookie = extract_cookie_string(storage_path)
            publisher = LocalPublisher(cookie)
            publisher.init_client()

            # 处理图片路径：优先用 pub.json 指定路径，否则自动找同名卡片目录
            image_paths = _normalize_image_paths(pub.get("image_path", ""), pub.get("image_paths", []))
            raw_images = []
            for image_path in image_paths:
                img_path = Path(image_path)
                if not img_path.is_absolute():
                    img_path = BASE_DIR / img_path
                if img_path.exists():
                    raw_images.append(str(img_path))
                else:
                    print(f"[warn] 图片不存在，尝试自动发现卡片目录: {img_path}", flush=True)

            if not raw_images:
                # 自动发现：与 .md 同名的子目录里的 PNG，按文件名排序
                card_dir = resolved.parent / resolved.stem
                if card_dir.is_dir():
                    found = sorted(card_dir.glob("*.png"))
                    raw_images = [str(p) for p in found]
                    if raw_images:
                        print(f"[info] 自动使用卡片图片 {len(raw_images)} 张: {card_dir.name}", flush=True)

            # 对图片做微扰，改变哈希指纹，防矩阵号批量识别
            _tmp_dir = Path(_tempfile.mkdtemp(prefix="xhs_pub_"))
            images = []
            for orig in raw_images:
                dst = str(_tmp_dir / Path(orig).name)
                _perturb_image(orig, dst)
                images.append(dst)

            publish_result = publisher.publish(
                title=str(pub.get("title", ""))[:20],
                desc=str(pub.get("desc", "")),
                images=images,
                is_private=pub.get("is_private", False),
            )

            pub["status"] = "published"
            pub["published_at"] = datetime.now().isoformat(timespec="seconds")
            pub["published_account"] = account_name
            _save_pub_json(draft_path, pub)

            results[result_key] = {"status": "ok", "result": publish_result}
        except Exception as e:
            results[result_key] = {"status": "error", "error": str(e)}
        finally:
            # 清理微扰临时图片
            if _tmp_dir is not None:
                _shutil.rmtree(_tmp_dir, ignore_errors=True)
    return results


def do_publish(
    folder: str,
    title: str,
    desc: str,
    account_names: List[str],
    accounts_path: Optional[str] = None,
) -> Dict[str, Any]:
    """兼容旧接口：按账号发布同一素材目录。"""
    pngs = sorted(Path(folder).glob("*.png"))
    images = [str(p) for p in pngs]
    accounts = list_accounts(accounts_path)
    if account_names:
        accounts = [a for a in accounts if a["name"] in account_names]

    results: Dict[str, Any] = {}
    for acc in accounts:
        name = acc["name"]
        if not acc["valid"]:
            results[name] = {"status": "error", "error": "账号 session 已过期"}
            continue
        try:
            cookie = extract_cookie_string(acc["storage_path"])
            publisher = LocalPublisher(cookie)
            publisher.init_client()
            result = publisher.publish(
                title=title[:20],
                desc=desc,
                images=images,
                is_private=False,
            )
            results[name] = {"status": "ok", "result": result}
        except Exception as e:
            results[name] = {"status": "error", "error": str(e)}
    return results


# ── 定时调度 ──────────────────────────────────────────────────────────────────

_scheduler_started = False
_scheduler_lock = threading.Lock()


def _run_due_tasks(schedule_file: Optional[str] = None, accounts_path: Optional[str] = None) -> None:
    """检查并执行到期的 pending 任务（供调度线程调用）。
    每轮最多执行 1 个任务，防止积压时集中爆发触发频控。
    """
    sf = schedule_file or str(SCHEDULE_FILE)
    data = _load_schedule(sf)
    now = datetime.now().isoformat()
    for task in data.get("tasks", []):
        if task["status"] != "pending":
            continue
        if task["scheduled_at"] > now:
            continue
        try:
            result = do_publish_batch(
                items=task.get("items") or [],
                accounts_path=accounts_path,
                schedule_file=sf,
            )
            task["status"] = "done"
            task["result"] = result
        except Exception as e:
            task["status"] = "failed"
            task["result"] = {"error": str(e)}
        _save_schedule(data, sf)
        return  # 每轮只处理 1 个任务，下次轮询再处理下一个


def start_scheduler(schedule_file: Optional[str] = None, accounts_path: Optional[str] = None) -> None:
    """启动定时调度后台线程（每 30 秒检查一次，重复调用安全）。"""
    global _scheduler_started
    with _scheduler_lock:
        if _scheduler_started:
            return
        _scheduler_started = True

    def _loop():
        import time
        while True:
            try:
                _run_due_tasks(schedule_file, accounts_path)
            except Exception as e:
                print(f"[scheduler] 调度异常: {e}", flush=True)
            time.sleep(30)

    t = threading.Thread(target=_loop, daemon=True)
    t.start()
