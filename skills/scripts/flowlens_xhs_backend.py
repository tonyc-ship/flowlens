#!/usr/bin/env python3
"""FlowLens-backed Xiaohongshu retrieval helpers for Auto-Redbook-Skills."""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from flowlens_runtime import get_flowlens_root

BASE_DIR = Path(__file__).resolve().parent.parent


def _ensure_flowlens_path() -> Path:
    root = get_flowlens_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return root


_ensure_flowlens_path()

from flowlens.core.auth import default_cloud_model  # type: ignore  # noqa: E402
from flowlens.core.bridge import ExtensionBridge, ensure_extension_connection  # type: ignore  # noqa: E402
from flowlens.platforms.xhs.entities import parse_count_text  # type: ignore  # noqa: E402
from flowlens.platforms.xhs.processor import XHSSiteAdapter  # type: ignore  # noqa: E402
from flowlens.xhs_cli import _site_media_for_model  # type: ignore  # noqa: E402


def _note_link(payload: dict[str, Any]) -> str:
    return str(payload.get("url") or payload.get("link") or "").strip()


def _count_value(value: Any) -> int:
    if isinstance(value, (int, float)):
        return int(value)
    return parse_count_text(str(value or ""))


def _fallback_desc(note_payload: dict[str, Any]) -> str:
    content = str(note_payload.get("content") or "").strip()
    if content:
        return content

    key_points = note_payload.get("key_points") or []
    if isinstance(key_points, list):
        joined = "\n".join(str(item).strip() for item in key_points if str(item).strip())
        if joined:
            return joined

    cover_description = str(note_payload.get("cover_description") or "").strip()
    if cover_description:
        return cover_description

    video = note_payload.get("video") or {}
    if isinstance(video, dict):
        for key in ("transcript_summary", "visual_summary", "poster_description", "poster_ocr"):
            candidate = str(video.get(key) or "").strip()
            if candidate:
                return candidate

    return ""


def _profile_hex_id(profile_url: str) -> str:
    if not profile_url:
        return ""
    parsed = urlparse(profile_url)
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 3 and parts[0] == "user" and parts[1] == "profile":
        return parts[2]
    return ""


def _summarize_author_groups(
    *,
    keyword: str,
    note_summaries: list[dict[str, Any]],
    author_limit: int,
    viral_threshold: int,
) -> dict[str, Any]:
    grouped: dict[str, dict[str, Any]] = {}

    for note in note_summaries:
        author = str(note.get("author") or "").strip()
        profile_url = str(note.get("author_url") or "").strip()
        key = profile_url or author
        if not key:
            continue

        note_title = str(note.get("title") or "").strip()
        note_url = _note_link(note)
        likes_value = _count_value(note.get("likes_value") or note.get("likes"))
        content = _fallback_desc(note)

        group = grouped.setdefault(
            key,
            {
                "name": author or "未知作者",
                "author": author or "未知作者",
                "nickname": author or "未知作者",
                "profile_url": profile_url,
                "author_hex_id": _profile_hex_id(profile_url),
                "user_id": _profile_hex_id(profile_url),
                "xhs_id": "",
                "status": "爆款账号",
                "prefetched_notes": [],
                "viral_notes": [],
            },
        )

        prefetched = {
            "title": note_title,
            "content": content,
            "likes": likes_value,
            "saves": _count_value(note.get("favorites_value") or note.get("favorites")),
            "comments": _count_value(note.get("comments_count_value") or note.get("comments_count")),
            "url": note_url,
        }
        group["prefetched_notes"].append(prefetched)

        if likes_value >= viral_threshold:
            group["viral_notes"].append(
                {
                    "title": note_title,
                    "url": note_url,
                    "likes": likes_value,
                    "viral_score": likes_value,
                }
            )

    authors: list[dict[str, Any]] = []
    for group in grouped.values():
        notes = group["prefetched_notes"]
        if not notes:
            continue
        notes_sorted = sorted(notes, key=lambda item: int(item.get("likes") or 0), reverse=True)
        likes_values = [int(item.get("likes") or 0) for item in notes_sorted]
        top_note = notes_sorted[0]
        group["post_count"] = len(notes_sorted)
        group["notes_count"] = len(notes_sorted)
        group["viral_count"] = len(group["viral_notes"])
        group["avg_viral_score"] = round(sum(likes_values) / max(len(likes_values), 1), 1)
        group["max_viral_score"] = max(likes_values) if likes_values else 0
        group["top_post_title"] = top_note.get("title", "")
        group["top_post_url"] = top_note.get("url", "")
        group["explore_data"] = {
            "keyword": keyword,
            "viral_count": group["viral_count"],
            "max_viral_score": group["max_viral_score"],
            "top_post_title": group["top_post_title"],
            "top_post_url": group["top_post_url"],
            "prefetched_notes": notes_sorted[:6],
        }
        authors.append(group)

    authors.sort(
        key=lambda item: (
            int(item.get("max_viral_score") or 0),
            int(item.get("viral_count") or 0),
            int(item.get("post_count") or 0),
        ),
        reverse=True,
    )

    all_notes = [note for group in authors for note in group.get("prefetched_notes", [])]
    viral_passed = sum(1 for note in all_notes if int(note.get("likes") or 0) >= viral_threshold)

    return {
        "keyword": keyword,
        "total_fetched": len(all_notes),
        "viral_passed": viral_passed,
        "authors": authors[:author_limit],
    }


class FlowLensXhsFetcher:
    """Reuse FlowLens' XHS bridge/adapter as a synchronous fetcher."""

    backend_name = "flowlens"

    def __init__(self, cache_dir: Optional[str | Path] = None):
        self._loop: asyncio.AbstractEventLoop | None = None
        self._bridge: ExtensionBridge | None = None
        self._window_id: int | None = None
        self._tab = None
        self._adapter: XHSSiteAdapter | None = None
        self._model = default_cloud_model()
        self._cache_dir = Path(cache_dir or (BASE_DIR / "output" / "flowlens_cache"))
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._current_profile_url = ""
        self._note_tokens: dict[str, str] = {}
        self._token_refetch_done = False

    def __enter__(self) -> "FlowLensXhsFetcher":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()

    @property
    def adapter(self) -> XHSSiteAdapter:
        if self._adapter is None:
            raise RuntimeError("FlowLensXhsFetcher is not started")
        return self._adapter

    @property
    def tab(self):
        if self._tab is None:
            raise RuntimeError("FlowLensXhsFetcher is not started")
        return self._tab

    def start(self) -> None:
        if self._loop is not None:
            return
        self._loop = asyncio.new_event_loop()
        self._loop.run_until_complete(self._start_async())

    def stop(self) -> None:
        if self._loop is None:
            return
        try:
            self._loop.run_until_complete(self._stop_async())
        finally:
            self._loop.close()
            self._loop = None
            self._bridge = None
            self._window_id = None
            self._tab = None
            self._adapter = None
            self._current_profile_url = ""

    async def _start_async(self) -> None:
        bridge = ExtensionBridge()
        await bridge.start()
        await ensure_extension_connection(bridge)
        created = await bridge.create_background_window(url="about:blank", lock=True, focused=False)
        self._window_id = int(created.get("windowId") or 0) or None
        tab_id = int(created.get("tabId") or 0)
        self._bridge = bridge
        self._tab = bridge.tab(tab_id, window_id=self._window_id)
        await self._tab.navigate("https://www.xiaohongshu.com/explore", wait_ms=5000)
        await asyncio.sleep(2.5)
        self._adapter = XHSSiteAdapter(
            self._tab,
            ext_bridge=bridge,
            media=_site_media_for_model(self._model),
            run_dir=self._cache_dir,
        )

    async def _stop_async(self) -> None:
        if self._bridge is not None and self._window_id is not None:
            try:
                await self._bridge.close_window(self._window_id)
            except Exception:
                pass
        if self._bridge is not None:
            try:
                await self._bridge.stop()
            except Exception:
                pass

    def _run(self, coro):
        if self._loop is None:
            raise RuntimeError("FlowLensXhsFetcher is not started")
        return self._loop.run_until_complete(coro)

    def reset_to_explore(self) -> None:
        self._run(self._reset_to_explore_async())

    async def _reset_to_explore_async(self) -> None:
        await self.tab.navigate("https://www.xiaohongshu.com/explore", wait_ms=5000)
        await asyncio.sleep(1.8)

    def search_notes(self, keyword: str, tab_label: str | None = None) -> dict[str, Any]:
        self.reset_to_explore()
        return self._run(self.adapter.search_notes(keyword, tab_label=tab_label, wait_seconds=3.0))

    def read_note_summary(self, note_id: str) -> dict[str, Any]:
        note = self._run(
            self.adapter.read_note(
                note_id=note_id,
                level="card",
                max_comments=0,
                max_images=1,
                max_video_frames=1,
                include_comments=False,
                include_media=False,
                open_wait_seconds=3.0,
                close_after=True,
            )
        )
        payload = note.to_tool_dict()
        self._note_tokens[note_id] = self._note_tokens.get(note_id, "")
        return payload

    def discover_accounts(
        self,
        *,
        keyword: str,
        search_limit: int = 20,
        viral_threshold: int = 60,
        author_limit: int = 20,
    ) -> dict[str, Any]:
        search = self.search_notes(keyword)
        cards = list(search.get("cards") or [])
        cards.sort(key=lambda item: int(item.get("likes_value") or 0), reverse=True)
        card_candidates = cards[:search_limit]

        note_summaries: list[dict[str, Any]] = []
        for card in card_candidates:
            note_id = str(card.get("note_id") or "").strip()
            if not note_id:
                continue
            try:
                note_payload = self.read_note_summary(note_id)
            except Exception:
                continue

            note_payload.setdefault("likes_value", int(card.get("likes_value") or 0))
            note_payload.setdefault("likes", card.get("likes", ""))
            note_payload.setdefault("title", card.get("title", ""))
            note_payload.setdefault("author", card.get("author", ""))
            if not note_payload.get("url"):
                note_payload["url"] = _note_link(card)
            note_summaries.append(note_payload)

        result = _summarize_author_groups(
            keyword=keyword,
            note_summaries=note_summaries,
            author_limit=author_limit,
            viral_threshold=viral_threshold,
        )
        result["search"] = search
        return result

    def fetch_profile_and_notes(self, user_ref: str, limit: int = 6) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        target_url = str(user_ref or "").strip()
        if not target_url:
            return {"fans_count": 0, "notes_count": 0}, []
        if not target_url.startswith("http"):
            target_url = f"https://www.xiaohongshu.com/user/profile/{target_url}"

        self._run(self._open_profile_async(target_url))
        author = self._run(self.adapter.extract_author_profile(include_notes=True, max_notes=limit))
        self._current_profile_url = author.profile_url or target_url

        profile = {
            "fans_count": parse_count_text(author.followers),
            "notes_count": max(parse_count_text(author.total_notes), len(author.note_cards)),
            "red_id": author.xhs_id,
        }
        notes: list[dict[str, Any]] = []
        for card in author.note_cards[:limit]:
            notes.append(
                {
                    "note_id": card.note_id,
                    "xsec_token": "",
                    "title": card.title,
                    "cover_url": card.cover_url,
                    "image_count": 1,
                    "liked_count": parse_count_text(card.likes),
                    "collected_count": 0,
                    "comment_count": 0,
                    "create_time": None,
                    "desc": "",
                }
            )
        return profile, notes

    async def _open_profile_async(self, profile_url: str) -> None:
        await self.tab.navigate(profile_url, wait_ms=5000)
        await asyncio.sleep(2.2)

    def fetch_note_details_concurrent(self, notes: list[dict[str, Any]], max_tabs: int = 2) -> list[dict[str, Any]]:
        del max_tabs
        return [self.fetch_note_detail(note) for note in notes]

    def fetch_note_detail(self, note: dict[str, Any]) -> dict[str, Any]:
        note_id = str(note.get("note_id") or "").strip()
        result = dict(note)
        result.setdefault("desc", "")
        result.setdefault("word_count", len(str(result.get("title") or "")))
        result["detail_fetched"] = False
        if not note_id:
            return result

        try:
            entity = self._run(
                self.adapter.read_note(
                    note_id=note_id,
                    level="deep",
                    max_comments=6,
                    max_images=6,
                    max_video_frames=4,
                    include_comments=True,
                    include_media=True,
                    open_wait_seconds=3.0,
                    close_after=True,
                )
            ).to_tool_dict()
        except Exception:
            return result

        desc = _fallback_desc(entity)
        result["desc"] = desc
        result["word_count"] = len(str(result.get("title") or "")) + len(desc)
        result["detail_fetched"] = bool(desc)
        result["liked_count"] = _count_value(entity.get("likes_value") or entity.get("likes") or result.get("liked_count"))
        result["collected_count"] = _count_value(
            entity.get("favorites_value") or entity.get("favorites") or result.get("collected_count")
        )
        result["comment_count"] = _count_value(
            entity.get("comments_count_value") or entity.get("comments_count") or result.get("comment_count")
        )
        result["image_count"] = max(_count_value(entity.get("image_count") or result.get("image_count")), 1)
        result["cover_url"] = (
            str((entity.get("images") or [{}])[0].get("url") or "")
            if entity.get("images")
            else str(result.get("cover_url") or "")
        )
        result["content"] = str(entity.get("content") or "")
        result["author_url"] = str(entity.get("author_url") or "")
        result["detail_source"] = "flowlens"
        return result

    def search_user_id(self, nickname: str, xhs_id: str = "") -> Optional[str]:
        keyword = f"{nickname} {xhs_id}".strip() if xhs_id else nickname
        search = self.search_notes(keyword)
        cards = list(search.get("cards") or [])
        cards.sort(key=lambda item: int(item.get("likes_value") or 0), reverse=True)
        fallback_url = ""

        for card in cards[:8]:
            note_id = str(card.get("note_id") or "").strip()
            if not note_id:
                continue
            try:
                payload = self.read_note_summary(note_id)
            except Exception:
                continue
            author_url = str(payload.get("author_url") or "").strip()
            author_name = str(payload.get("author") or "").strip()
            if author_url and not fallback_url:
                fallback_url = author_url
            if nickname and author_name and (nickname in author_name or author_name in nickname):
                return author_url or None
        return fallback_url or None


def discover_accounts_via_flowlens(
    keyword: str,
    *,
    search_limit: int = 20,
    viral_threshold: int = 60,
    author_limit: int = 20,
) -> dict[str, Any]:
    with FlowLensXhsFetcher() as fetcher:
        return fetcher.discover_accounts(
            keyword=keyword,
            search_limit=search_limit,
            viral_threshold=viral_threshold,
            author_limit=author_limit,
        )
