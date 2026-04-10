"""User-facing CLI for Xiaohongshu tasks and extraction interfaces."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen, Request

from .core.auth import PROVIDER_OPENAI, default_cloud_model, resolve_model_provider
from .core.bridge import ExtensionBridge, ensure_extension_connection
from .perception.media import (
    BACKEND_OPENAI,
    BACKEND_QWEN_LOCAL,
    BACKEND_SONNET,
    BACKEND_UI_TARS_LOCAL,
    MediaConfig,
    MediaProcessor,
)
from .platforms.xhs.processor import XHSSiteAdapter
from .reasoning.tasks import (
    StructuredTask,
    make_topic_research_task,
)


PROFILE_URL_RE = re.compile(r"https?://www\.xiaohongshu\.com/user/profile/[^\s]+")
NOTE_URL_RE = re.compile(r"https?://www\.xiaohongshu\.com/(?:explore|discovery/item)/[^\s]+")
TOPIC_PREFIX_RE = re.compile(r"^(帮我)?(研究|分析|调研|搜索|看看|看一下|看|做一个)\s*")


def _clean_url(url: str) -> str:
    """Remove shell escape backslashes that zsh adds when pasting URLs."""
    return url.replace("\\?", "?").replace("\\=", "=").replace("\\&", "&")


def _clean_topic(prompt: str) -> str:
    topic = TOPIC_PREFIX_RE.sub("", prompt.strip())
    return topic.strip("：:，,。.！？!? ") or prompt.strip()


def _make_run_dir(prefix: str, slug: str) -> Path:
    safe_slug = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "_", slug.lower()).strip("_")[:80] or prefix
    path = Path("task_runs") / f"xhs_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{prefix}_{safe_slug}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _site_media_for_model(model: str) -> MediaProcessor:
    normalized = str(model or default_cloud_model()).strip()
    if normalized == "ui-tars-local" or normalized.startswith("UI-TARS"):
        backend = BACKEND_UI_TARS_LOCAL
    elif normalized == "qwen-local" or normalized.startswith("Qwen"):
        backend = BACKEND_QWEN_LOCAL
    elif resolve_model_provider(normalized) == PROVIDER_OPENAI:
        backend = BACKEND_OPENAI
    else:
        backend = BACKEND_SONNET
    return MediaProcessor(
        MediaConfig(
            backend=backend,
            model=normalized,
            use_whisper=True,
            use_vision=True,
            use_apple_ocr=True,
        )
    )


async def _run_structured_agent_task(task: StructuredTask) -> dict:
    from .agent.loop import run_agent

    model = default_cloud_model()
    prefix = "creator_research" if task.kind.value == "creator_growth_breakdown" else "search"
    output_dir = _make_run_dir(prefix, task.slug())
    result = await run_agent(
        task=task.to_prompt(),
        model=model,
        run_dir=output_dir,
    )
    payload = {
        "task": {
            "kind": task.kind.value,
            "title": task.title,
            "site": task.site,
        },
        "model": model,
        "run_dir": str(output_dir),
        "report_md": str(output_dir / "report.md"),
        "reasoning_log": result.get("reasoning_log", ""),
        "turns": result["turns"],
        "total_duration_s": result.get("total_duration_s", 0),
        "result": result["result"],
        "site_results": result.get("site_results", []),
    }
    _write_json(output_dir / "result.json", payload)
    return payload


def _normalize_agent_request(request: str) -> str:
    trimmed = str(request or "").strip()
    if not trimmed:
        raise ValueError("XHS agent request is empty")
    if re.search(r"(小红书|xiaohongshu|xhs)", trimmed, re.IGNORECASE):
        return trimmed
    return f"在小红书上{trimmed}"


async def _run_freeform_agent_request(request: str) -> dict:
    from .agent.loop import run_agent

    task = _normalize_agent_request(request)
    model = default_cloud_model()
    output_dir = _make_run_dir("agent", task)
    result = await run_agent(
        task=task,
        model=model,
        run_dir=output_dir,
    )
    payload = {
        "task": {
            "kind": "xhs_agent",
            "title": task,
            "site": "xiaohongshu",
        },
        "model": model,
        "run_dir": str(output_dir),
        "report_md": str(output_dir / "report.md"),
        "reasoning_log": result.get("reasoning_log", ""),
        "turns": result["turns"],
        "total_duration_s": result.get("total_duration_s", 0),
        "result": result["result"],
        "site_results": result.get("site_results", []),
    }
    _write_json(output_dir / "result.json", payload)
    return payload


async def _run_xhs_extract(
    *,
    label: str,
    target_url: str,
    slug: str,
    extractor,
    retries: int = 2,
) -> dict:
    model = default_cloud_model()
    output_dir = _make_run_dir(label, slug)
    bridge = ExtensionBridge()
    window_id: int | None = None
    try:
        await bridge.start()
        await ensure_extension_connection(bridge)
        created = await bridge.create_background_window(url="about:blank", lock=True, focused=False)
        window_id = int(created.get("windowId") or 0) or None
        tab_id = int(created.get("tabId") or 0)
        tab = bridge.tab(tab_id, window_id=window_id)
        await tab.navigate(target_url, wait_ms=5000)
        await asyncio.sleep(2.5)
        adapter = XHSSiteAdapter(
            tab,
            ext_bridge=bridge,
            media=_site_media_for_model(model),
            run_dir=output_dir,
        )
        last_error: Exception | None = None
        for attempt in range(1 + retries):
            try:
                payload = await extractor(tab, adapter, output_dir)
                break
            except Exception as exc:
                last_error = exc
                if attempt < retries:
                    print(f"  [重试] 第 {attempt + 1} 次失败，刷新页面重试...")
                    await tab.navigate(target_url, wait_ms=5000)
                    await asyncio.sleep(3)
        else:
            raise last_error  # type: ignore[misc]
        payload.update(
            {
                "model": model,
                "run_dir": str(output_dir),
            }
        )
        _write_json(output_dir / "result.json", payload)
        return payload
    finally:
        if window_id is not None:
            try:
                await bridge.close_window(window_id)
            except Exception:
                pass
        try:
            await bridge.stop()
        except Exception:
            pass


def _download_one_image(url: str, dest: Path) -> bool:
    """Download a single image URL to *dest*. Returns True on success."""
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=15) as resp:
            dest.write_bytes(resp.read())
        return True
    except Exception:
        return False


async def _download_images(images: list, output_dir: Path) -> list[tuple[int, str]]:
    """Download note images to output_dir/images/. Returns list of (index, local_filename)."""
    img_dir = output_dir / "images"
    img_dir.mkdir(parents=True, exist_ok=True)
    downloaded: list[tuple[int, str]] = []
    loop = asyncio.get_running_loop()

    for img in images:
        url = img.url if hasattr(img, "url") else str(img)
        if not url:
            continue
        idx = img.index if hasattr(img, "index") else len(downloaded)
        parsed = urlparse(url)
        ext = Path(parsed.path).suffix or ".jpg"
        if ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
            ext = ".jpg"
        filename = f"{idx:02d}{ext}"
        dest = img_dir / filename
        ok = await loop.run_in_executor(None, _download_one_image, url, dest)
        if ok:
            downloaded.append((idx, filename))
            if hasattr(img, "local_path"):
                img.local_path = str(dest)
    return downloaded


def _render_note_markdown(note, downloaded_images: list[tuple[int, str]]) -> str:
    """Render a NoteEntity into a human-readable markdown string."""
    lines: list[str] = []

    # Title and author
    lines.append(f"# {note.title or '(untitled)'}")
    lines.append("")
    meta_parts = []
    if note.author_name:
        meta_parts.append(f"**{note.author_name}**")
    if note.date:
        meta_parts.append(note.date)
    if note.ip_location:
        meta_parts.append(note.ip_location)
    if meta_parts:
        lines.append(" | ".join(meta_parts))
        lines.append("")

    # Stats
    stats = []
    if note.likes:
        stats.append(f"点赞 {note.likes}")
    if note.favorites:
        stats.append(f"收藏 {note.favorites}")
    if note.comments_count:
        stats.append(f"评论 {note.comments_count}")
    if note.shares:
        stats.append(f"分享 {note.shares}")
    if stats:
        lines.append(" | ".join(stats))
        lines.append("")

    # URL
    if note.url:
        lines.append(f"[原文链接]({note.url})")
        lines.append("")

    lines.append("---")
    lines.append("")

    # Content
    if note.content:
        lines.append(note.content)
        lines.append("")

    # Hashtags
    if note.hashtags:
        lines.append(" ".join(f"`{tag}`" for tag in note.hashtags))
        lines.append("")

    # Images
    if downloaded_images:
        lines.append("---")
        lines.append("")
        lines.append(f"## 图片 ({len(downloaded_images)})")
        lines.append("")
        for idx, filename in downloaded_images:
            lines.append(f"![image {idx}](images/{filename})")
            lines.append("")

    # Comments
    if note.comments:
        lines.append("---")
        lines.append("")
        lines.append(f"## 评论 ({len(note.comments)})")
        lines.append("")
        for c in note.comments:
            prefix = ""
            if c.is_pinned:
                prefix = "[置顶] "
            elif c.is_author_reply:
                prefix = "[作者] "
            like_str = f" ({c.likes} 赞)" if c.likes else ""
            lines.append(f"- **{c.username}**{like_str}: {prefix}{c.text}")
            for sc in c.sub_comments:
                sc_like = f" ({sc.likes} 赞)" if sc.likes else ""
                lines.append(f"  - **{sc.username}**{sc_like}: {sc.text}")
        lines.append("")

    return "\n".join(lines)


async def _run_note_extract(note_url: str) -> dict:
    async def extractor(tab, adapter, output_dir: Path) -> dict:
        note = await adapter.extract_note(level="lite", max_comments=10, include_comments=True, include_media=False)
        screenshot_path = output_dir / "note_detail.png"
        saved = await tab.save_screenshot(screenshot_path)
        note.screenshot_path = Path(saved).name if saved else ""

        # Download images locally
        downloaded = await _download_images(note.images, output_dir)
        if downloaded:
            print(f"  已下载 {len(downloaded)} 张图片到 {output_dir / 'images'}")

        # Generate readable note.md
        md_content = _render_note_markdown(note, downloaded)
        md_path = output_dir / "note.md"
        md_path.write_text(md_content, encoding="utf-8")

        return {
            "task": {"kind": "xhs_note", "title": note.title or note_url, "site": "xiaohongshu"},
            "result_file": str(output_dir / "result.json"),
            "note_md": str(md_path),
            "screenshot": str(screenshot_path),
            "entity": note.to_tool_dict(),
            "timing": adapter.timing.summary(),
        }

    return await _run_xhs_extract(label="note", target_url=note_url, slug=note_url, extractor=extractor)


async def _run_creator_extract(profile_url: str) -> dict:
    async def extractor(tab, adapter, output_dir: Path) -> dict:
        author = await adapter.extract_author_profile(include_notes=True)
        screenshot_path = output_dir / "creator_profile.png"
        saved = await tab.save_screenshot(screenshot_path)
        author.screenshot_path = Path(saved).name if saved else ""
        return {
            "task": {"kind": "xhs_creator", "title": author.name or profile_url, "site": "xiaohongshu"},
            "result_file": str(output_dir / "result.json"),
            "entity": author.to_tool_dict(),
            "timing": adapter.timing.summary(),
        }

    return await _run_xhs_extract(label="creator", target_url=profile_url, slug=profile_url, extractor=extractor)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="flowlens xhs",
        description="Run Xiaohongshu research, extraction, or custom agent tasks.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    search = subparsers.add_parser("search", help="Run the existing Xiaohongshu topic research report.")
    search.add_argument("request", help='Research topic, for example "调研露营装备".')

    note = subparsers.add_parser("note", help="Extract a structured note entity from a Xiaohongshu note URL.")
    note.add_argument("url", help="Xiaohongshu note URL, for example https://www.xiaohongshu.com/explore/...")

    author = subparsers.add_parser("author", help="Extract an author profile and visible note cards.")
    author.add_argument("url", help="Xiaohongshu author profile URL.")

    agent = subparsers.add_parser("agent", help="Run a free-form custom Xiaohongshu agent task.")
    agent.add_argument("request", help='Custom request, for example "找最近高互动的露营清单帖子".')

    return parser


def _print_result(payload: dict) -> int:
    task = payload.get("task") or {}
    if task:
        print(f"\n任务: {task.get('title', '')}")
        print(f"类型: {task.get('kind', '')}")
    if payload.get("turns") is not None:
        print(f"轮数: {payload['turns']}")
    if payload.get("total_duration_s"):
        print(f"耗时: {payload['total_duration_s']}s")
    if payload.get("result"):
        print("\n--- 结果摘要 ---\n")
        print(payload["result"])
    print("")
    if payload.get("run_dir"):
        print(f"输出目录: {payload['run_dir']}")
    if payload.get("report_md"):
        print(f"报告: {payload['report_md']}")
    if payload.get("note_md"):
        print(f"笔记: {payload['note_md']}")
    if payload.get("result_file"):
        print(f"结果JSON: {payload['result_file']}")
    if payload.get("screenshot"):
        print(f"截图: {payload['screenshot']}")
    return 0


def _check_api_key() -> None:
    """Check that at least one LLM provider is configured."""
    from .core.auth import provider_status, PROVIDER_ANTHROPIC, PROVIDER_OPENAI

    anth = provider_status(PROVIDER_ANTHROPIC)
    oai = provider_status(PROVIDER_OPENAI)
    if not anth.available and not oai.available:
        print("错误: 未配置 LLM API Key。请先运行:\n")
        print("  flowlens auth\n")
        raise SystemExit(1)


def _handle_error(exc: Exception) -> int:
    """Print a friendly Chinese error message for common failures."""
    msg = str(exc)
    if "extension" in msg.lower() or "websocket" in msg.lower() or "connection" in msg.lower():
        print(f"\n错误: 无法连接 Chrome Extension。\n")
        print("请确认:")
        print("  1. Chrome 浏览器已打开")
        print("  2. FlowLens Extension 已加载 (chrome://extensions/)")
        print("  3. Extension 已启用（没有被禁用）\n")
        return 1
    if "no_note_modal_open" in msg or "error_page" in msg:
        print(f"\n错误: 页面未正常加载，可能触发了小红书反爬机制。\n")
        print("建议:")
        print("  1. 在浏览器中手动打开该链接，确认能正常显示")
        print("  2. 稍等几分钟后重试\n")
        return 1
    print(f"\n错误: {msg}\n")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    _check_api_key()

    try:
        if args.command == "search":
            task = make_topic_research_task(_clean_topic(args.request))
            payload = asyncio.run(_run_structured_agent_task(task))
            return _print_result(payload)

        if args.command == "note":
            url = _clean_url(args.url)
            if not NOTE_URL_RE.search(url):
                parser.error("请提供小红书笔记链接，例如 https://www.xiaohongshu.com/explore/...")
            payload = asyncio.run(_run_note_extract(url))
            return _print_result(payload)

        if args.command == "author":
            url = _clean_url(args.url)
            if not PROFILE_URL_RE.search(url):
                parser.error("请提供小红书作者主页链接，例如 https://www.xiaohongshu.com/user/profile/...")
            payload = asyncio.run(_run_creator_extract(url))
            return _print_result(payload)

        if args.command == "agent":
            payload = asyncio.run(_run_freeform_agent_request(args.request))
            return _print_result(payload)

    except KeyboardInterrupt:
        print("\n已取消。")
        return 130
    except Exception as exc:
        return _handle_error(exc)

    parser.error(f"Unknown command: {args.command}")
    return 2
