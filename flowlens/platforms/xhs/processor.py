"""Site-aware Xiaohongshu extraction and enrichment.

This module restores the valuable parts of the old hardcoded workflow:
- deterministic normalization
- costed extraction plans
- multimodal note enrichment

It deliberately does NOT reintroduce a fixed task workflow. The generic agent
selects when to invoke these capabilities through a single site-aware tool.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path

from ...core.bridge import ExtensionBridge, TabBridge
from ...perception.media import MediaProcessor
from .capabilities import NoteExtractionPlan, plan_for_level
from .entities import AuthorEntity, Comment, ImageInfo, NoteCard, NoteEntity, NoteType, VideoInfo

XHS_REFERER = "https://www.xiaohongshu.com"


@dataclass
class ProcessorConfig:
    max_images: int = 10
    use_ocr: bool = True
    use_vision: bool = True
    use_whisper: bool = True
    vision_concurrency: int = 3
    max_transcription_seconds: int = 90
    transcription_timeout_s: int = 300
    max_video_frame_seconds: int = 60
    max_video_frame_samples: int = 4
    cache_video_locally: bool = False
    image_prompt_template: str = (
        "Describe this Xiaohongshu note image from '{title}'. "
        "Be concise. Focus on key visuals, visible text, products, and structure."
    )
    video_poster_prompt: str = (
        "Describe this Xiaohongshu video cover from '{title}'. "
        "What does the video appear to be about?"
    )
    video_frame_prompt_template: str = (
        "这是小红书视频《{title}》的第{index}帧。"
        "请用中文简洁描述人物、动作、场景、物品，以及它对理解视频内容有什么帮助。"
    )
    transcript_summary_prompt: str = (
        "以下是一个小红书视频笔记的语音转录文本，标题是“{title}”。\n\n"
        "转录文本：\n{transcript}\n\n"
        "请用中文简洁概括这个视频的主要内容，2-4句。"
    )
    video_visual_summary_prompt: str = (
        "以下是一个小红书视频《{title}》的封面描述和若干关键帧描述。\n\n"
        "封面描述：{poster}\n\n"
        "关键帧描述：\n{frames}\n\n"
        "请用中文总结这个视频画面里发生了什么，重点概括场景、动作流程、产品/物体、氛围。输出2-4句话。"
    )


@dataclass
class TimingRecord:
    counts: dict[str, int] = field(default_factory=dict)
    totals: dict[str, float] = field(default_factory=dict)

    def record(self, op: str, duration: float) -> None:
        self.counts[op] = self.counts.get(op, 0) + 1
        self.totals[op] = self.totals.get(op, 0.0) + duration

    def summary(self) -> dict[str, dict]:
        result: dict[str, dict] = {}
        for op in sorted(self.totals):
            total = self.totals[op]
            count = self.counts.get(op, 0)
            result[op] = {
                "count": count,
                "total_s": round(total, 2),
                "avg_s": round(total / count, 2) if count else 0,
            }
        return result


class XHSSiteAdapter:
    """Bridge + media powered XHS entity extractor."""

    def __init__(
        self,
        bridge: ExtensionBridge | TabBridge,
        *,
        ext_bridge: ExtensionBridge,
        media: MediaProcessor,
        run_dir: str | Path,
        config: ProcessorConfig | None = None,
        log_fn=None,
    ):
        self.bridge = bridge
        self.ext_bridge = ext_bridge
        self.media = media
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.config = config or ProcessorConfig()
        self.timing = TimingRecord()
        self._log_fn = log_fn

    def _log(self, action: str, detail: str = "", duration: float | None = None) -> None:
        if self._log_fn:
            self._log_fn(action, detail, duration)

    async def _current_url(self) -> str:
        try:
            info = await self.bridge.get_tab_info()
        except Exception:
            return ""
        return str(info.get("url") or "")

    async def extract_search_cards(self) -> list[NoteCard]:
        result = await self.ext_bridge.send_command("extract_search_cards")
        cards = [NoteCard.from_dom_dict(item) for item in result.get("cards", [])]
        return cards

    async def get_search_page_state(self) -> dict:
        return await self.ext_bridge.send_command("get_search_page_state")

    async def search_notes(
        self,
        query: str,
        *,
        tab_label: str | None = None,
        wait_seconds: float = 4.0,
    ) -> dict:
        t0 = time.time()
        submit = await self.ext_bridge.send_command("submit_search_query", {"keyword": query})
        self.timing.record("search_submit", time.time() - t0)

        if wait_seconds > 0:
            await asyncio.sleep(wait_seconds)

        tab_result = None
        if tab_label and _normalize_search_tab(tab_label) != "全部":
            tab_result = await self.open_search_tab(tab_label, wait_seconds=1.5)

        cards = await self.extract_search_cards()
        state = await self.get_search_page_state()
        return {
            "query": query,
            "submit": submit,
            "tab": tab_result,
            "page_state": state,
            "cards": [card.to_tool_dict() for card in cards],
            "count": len(cards),
        }

    async def open_search_tab(self, label: str, *, wait_seconds: float = 1.5) -> dict:
        normalized = _normalize_search_tab(label)
        t0 = time.time()
        result = await self.ext_bridge.send_command("click_search_tab", {"label": normalized})
        self.timing.record("search_tab_switch", time.time() - t0)
        if wait_seconds > 0:
            await asyncio.sleep(wait_seconds)
        cards = await self.extract_search_cards()
        return {
            "label": normalized,
            "result": result,
            "count": len(cards),
            "cards": [card.to_tool_dict() for card in cards],
        }

    async def open_note(
        self,
        *,
        index: int | None = None,
        note_id: str = "",
        wait_seconds: float = 2.5,
    ) -> dict:
        if note_id:
            result = await self.ext_bridge.send_command("click_note_by_id", {"note_id": note_id})
        elif index is not None:
            result = await self.ext_bridge.send_command("click_card", {"index": int(index)})
        else:
            raise ValueError("open_note requires index or note_id")
        if wait_seconds > 0:
            await asyncio.sleep(wait_seconds)
        return result

    async def close_note(self) -> dict:
        return await self.ext_bridge.send_command("close_note")

    async def extract_author_profile(self, *, include_notes: bool = True) -> AuthorEntity:
        info = await self.ext_bridge.send_command("extract_profile_info")
        author = AuthorEntity.from_dom_dict(info.get("profile", {}))
        author.profile_url = await self._current_url()
        if include_notes:
            notes = await self.ext_bridge.send_command("extract_profile_notes")
            author.note_cards = [NoteCard.from_dom_dict(item) for item in notes.get("notes", [])]
        return author

    async def extract_note(
        self,
        *,
        level: str = "lite",
        max_comments: int = 4,
        max_images: int = 6,
        max_video_frames: int = 4,
        include_comments: bool | None = None,
        include_media: bool | None = None,
    ) -> NoteEntity:
        plan = plan_for_level(
            level,
            max_comments=max_comments,
            max_images=max_images,
            max_video_frames=max_video_frames,
            include_comments=include_comments,
            include_media=include_media,
        )

        raw = await self.ext_bridge.send_command("extract_note_content")
        if raw.get("error"):
            raise RuntimeError(raw.get("message") or raw["error"])

        note = NoteEntity.from_dom_dict(raw.get("note", {}))
        note.extraction_level = plan.level.value
        note.requested_sections = plan.requested_sections
        note.applied_capabilities = ["xhs.note.open_basic"]

        if plan.include_comments:
            note.comments = await self._collect_note_comments(plan)
            note.applied_capabilities.append("xhs.note.sample_comments")

        if plan.use_media:
            if note.note_type == NoteType.VIDEO:
                await self._process_video(note, plan)
            else:
                await self._process_images(note, plan)

        note.refresh_derived_fields()
        return note

    async def read_note(
        self,
        *,
        index: int | None = None,
        note_id: str = "",
        level: str = "lite",
        max_comments: int = 4,
        max_images: int = 6,
        max_video_frames: int = 4,
        include_comments: bool | None = None,
        include_media: bool | None = None,
        open_wait_seconds: float = 2.5,
        close_after: bool = False,
    ) -> NoteEntity:
        if index is not None or note_id:
            await self.open_note(index=index, note_id=note_id, wait_seconds=open_wait_seconds)

        note = await self.extract_note(
            level=level,
            max_comments=max_comments,
            max_images=max_images,
            max_video_frames=max_video_frames,
            include_comments=include_comments,
            include_media=include_media,
        )

        if close_after:
            try:
                await self.close_note()
            except Exception:
                pass

        return note

    async def _collect_note_comments(self, plan: NoteExtractionPlan) -> list[Comment]:
        merged: list[Comment] = []
        for round_idx in range(plan.max_comment_scrolls + 1):
            raw = await self.ext_bridge.send_command(
                "extract_comments",
                {"max_comments": plan.max_comments, "prefer_hot": True},
            )
            current = [
                Comment.from_dom_dict(item)
                for item in raw.get("comments", [])
            ]
            merged = Comment.merge_many([*merged, *current])
            if round_idx >= plan.max_comment_scrolls:
                break
            try:
                scroll_result = await self.ext_bridge.send_command("scroll_note", {"pixels": 420})
            except Exception:
                break
            if not scroll_result.get("ok", True):
                break
            await asyncio.sleep(0.8)
        return merged[: plan.max_comments]

    async def _process_images(self, note: NoteEntity, plan: NoteExtractionPlan) -> None:
        await self._ensure_all_images(note, plan.max_images)
        if not note.images:
            return

        images = note.images[: min(self.config.max_images, plan.max_images)]
        t0 = time.time()
        downloads = await asyncio.gather(*[
            asyncio.to_thread(self.media.download_image, img.url, note.url or XHS_REFERER)
            for img in images
        ], return_exceptions=True)
        self.timing.record("image_download_batch", time.time() - t0)

        deduped: list[tuple[ImageInfo, bytes]] = []
        seen_hashes: dict[str, int] = {}
        for image, payload in zip(images, downloads):
            if not isinstance(payload, bytes) or not payload:
                continue
            digest = hashlib.md5(payload).hexdigest()
            if digest in seen_hashes:
                continue
            seen_hashes[digest] = image.index
            image.local_path = self._save_image_bytes(note.note_id or "note", image.index, payload)
            deduped.append((image, payload))

        if deduped:
            note.images = [img for img, _ in deduped]
            for idx, image in enumerate(note.images):
                image.index = idx
                image.is_cover = idx == 0
            note.image_count = len(note.images)

        semaphore = asyncio.Semaphore(self.config.vision_concurrency)

        async def enrich_single(img: ImageInfo, img_bytes: bytes) -> None:
            if plan.use_image_ocr and self.config.use_ocr:
                t0_local = time.time()
                ocr_text = await asyncio.to_thread(self.media.ocr_image, img_bytes)
                self.timing.record("ocr_single", time.time() - t0_local)
                if ocr_text.strip():
                    img.ocr_text = ocr_text
            if plan.use_image_vision and self.config.use_vision:
                async with semaphore:
                    t0_local = time.time()
                    prompt = self.config.image_prompt_template.format(title=note.title or "未命名笔记")
                    img.vision_description = await asyncio.to_thread(
                        self.media.describe_image,
                        img_bytes,
                        prompt,
                        512,
                    )
                    self.timing.record("vision_single", time.time() - t0_local)
            if img.is_cover and img.vision_description:
                note.cover_description = img.vision_description

        t0 = time.time()
        await asyncio.gather(*[
            enrich_single(img, payload)
            for img, payload in deduped
        ], return_exceptions=True)
        self.timing.record("image_process_batch", time.time() - t0)

    async def _ensure_all_images(self, note: NoteEntity, max_images: int) -> None:
        dom_count = len(note.images)
        unique_urls = _unique_image_infos(note.images)
        indicator_total = max(note.image_count, len(unique_urls))
        if len(unique_urls) >= indicator_total and unique_urls:
            note.images = unique_urls[:max_images]
            note.image_count = len(note.images)
            return

        try:
            result = await self.ext_bridge.send_command(
                "collect_carousel_images",
                {"max_images": max(max_images, indicator_total)},
            )
        except Exception:
            note.images = unique_urls[:max_images]
            note.image_count = len(note.images)
            return

        urls = [str(url) for url in result.get("image_urls", []) if str(url).strip()]
        if urls and len(urls) > len(unique_urls):
            note.images = [
                ImageInfo(url=url, index=i, is_cover=(i == 0))
                for i, url in enumerate(urls[:max_images])
            ]
            note.image_count = len(note.images)
            return

        note.images = unique_urls[:max_images]
        note.image_count = len(note.images or []) or dom_count

    async def _process_video(self, note: NoteEntity, plan: NoteExtractionPlan) -> None:
        if note.video is None:
            note.video = VideoInfo()

        video = note.video
        poster_url = video.poster_url or (note.images[0].url if note.images else "")
        downloadable_url = video.best_download_url()
        video.resolved_url = downloadable_url or video.best_source_url()
        video.stream_type = self._infer_video_stream_type(video.resolved_url)
        referer = note.url or XHS_REFERER
        processing_source = downloadable_url

        if not downloadable_url:
            video.download_error = "no_downloadable_video_url"
        elif self.config.cache_video_locally and video.stream_type in {"mp4", "mov", "m4v"}:
            t0 = time.time()
            suffix = f".{video.stream_type}" if video.stream_type else ".mp4"
            local_path = await asyncio.to_thread(
                self.media.download_file,
                downloadable_url,
                referer,
                suffix,
            )
            self.timing.record("video_download", time.time() - t0)
            if local_path:
                video.download_path = local_path
                processing_source = local_path

        poster_bytes = None
        if poster_url:
            t0 = time.time()
            poster_bytes = await asyncio.to_thread(self.media.download_image, poster_url, referer)
            self.timing.record("poster_download", time.time() - t0)
            if isinstance(poster_bytes, bytes) and poster_bytes:
                saved = self._save_named_bytes(note.note_id or "video", "poster", poster_bytes)
                if note.images:
                    note.images[0].local_path = saved

        async def poster_vision() -> None:
            if not poster_bytes or not plan.use_image_vision or not self.config.use_vision:
                return
            t0_local = time.time()
            video.poster_description = await asyncio.to_thread(
                self.media.describe_image,
                poster_bytes,
                self.config.video_poster_prompt.format(title=note.title or "未命名视频"),
                512,
            )
            note.cover_description = video.poster_description
            self.timing.record("vision_poster", time.time() - t0_local)

        async def poster_ocr() -> None:
            if not poster_bytes or not plan.use_image_ocr or not self.config.use_ocr:
                return
            t0_local = time.time()
            video.poster_ocr = await asyncio.to_thread(self.media.ocr_image, poster_bytes)
            self.timing.record("ocr_poster", time.time() - t0_local)

        async def transcribe() -> None:
            if not processing_source or not plan.use_video_transcript or not self.config.use_whisper:
                return
            t0_local = time.time()
            transcript = await self.media.transcribe_video(
                processing_source,
                "zh",
                referer=referer if str(processing_source).startswith("http") else "",
                max_audio_seconds=self.config.max_transcription_seconds,
                timeout_s=self.config.transcription_timeout_s,
            )
            self.timing.record("whisper_transcribe", time.time() - t0_local)
            if transcript.strip():
                video.transcript = transcript
                t0_summary = time.time()
                video.transcript_summary = await asyncio.to_thread(
                    self.media.call_text,
                    self.config.transcript_summary_prompt.format(
                        title=note.title or "未命名视频",
                        transcript=transcript[:3000],
                    ),
                    512,
                )
                self.timing.record("llm_transcript_summary", time.time() - t0_summary)
            elif not video.download_error:
                video.download_error = "empty_transcript"

        async def frame_understanding() -> None:
            if not processing_source or not plan.use_video_frames or not self.config.use_vision:
                return
            t0_local = time.time()
            frame_paths = await self.media.extract_video_frames(
                processing_source,
                referer=referer if str(processing_source).startswith("http") else "",
                max_seconds=self.config.max_video_frame_seconds,
                num_frames=min(self.config.max_video_frame_samples, plan.max_video_frames),
                timeout_s=self.config.transcription_timeout_s,
            )
            self.timing.record("video_frame_extract", time.time() - t0_local)
            if not frame_paths:
                return

            video.frame_paths = frame_paths
            semaphore = asyncio.Semaphore(self.config.vision_concurrency)
            frame_descriptions: list[str] = []

            async def describe_frame(index: int, frame_path: str) -> None:
                async with semaphore:
                    try:
                        frame_bytes = await asyncio.to_thread(Path(frame_path).read_bytes)
                    except Exception:
                        return
                    t0_frame = time.time()
                    description = await asyncio.to_thread(
                        self.media.describe_image,
                        frame_bytes,
                        self.config.video_frame_prompt_template.format(
                            title=note.title or "未命名视频",
                            index=index + 1,
                        ),
                        384,
                    )
                    self.timing.record("vision_video_frame", time.time() - t0_frame)
                    if description:
                        frame_descriptions.append(description)

            await asyncio.gather(*[
                describe_frame(index, path)
                for index, path in enumerate(frame_paths)
            ], return_exceptions=True)
            video.frame_descriptions = frame_descriptions
            if frame_descriptions:
                t0_summary = time.time()
                video.visual_summary = await asyncio.to_thread(
                    self.media.call_text,
                    self.config.video_visual_summary_prompt.format(
                        title=note.title or "未命名视频",
                        poster=video.poster_description or video.poster_ocr or "无",
                        frames="\n".join(
                            f"{idx + 1}. {desc}"
                            for idx, desc in enumerate(frame_descriptions)
                        ),
                    ),
                    512,
                )
                self.timing.record("llm_video_visual_summary", time.time() - t0_summary)

        await asyncio.gather(
            poster_vision(),
            poster_ocr(),
            transcribe(),
            frame_understanding(),
            return_exceptions=True,
        )

    def _save_image_bytes(self, note_id: str, index: int, payload: bytes) -> str:
        media_type = self.media.detect_media_type(payload)
        ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}.get(media_type, ".img")
        path = self.run_dir / "site_media" / note_id / f"image_{index + 1:02d}{ext}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return str(path.relative_to(self.run_dir))

    def _save_named_bytes(self, note_id: str, stem: str, payload: bytes) -> str:
        media_type = self.media.detect_media_type(payload)
        ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}.get(media_type, ".img")
        path = self.run_dir / "site_media" / note_id / f"{stem}{ext}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return str(path.relative_to(self.run_dir))

    @staticmethod
    def _infer_video_stream_type(url: str) -> str:
        lower = (url or "").lower()
        if ".mp4" in lower:
            return "mp4"
        if ".m3u8" in lower:
            return "m3u8"
        if ".mov" in lower:
            return "mov"
        if ".m4v" in lower:
            return "m4v"
        if lower.startswith("blob:"):
            return "blob"
        return ""


def _unique_image_infos(images: list[ImageInfo]) -> list[ImageInfo]:
    seen: set[str] = set()
    result: list[ImageInfo] = []
    for image in images:
        url = str(image.url or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        image.index = len(result)
        image.is_cover = len(result) == 0
        result.append(image)
    return result


def _normalize_search_tab(label: str) -> str:
    normalized = str(label or "").strip().lower()
    mapping = {
        "all": "全部",
        "全部": "全部",
        "image": "图文",
        "images": "图文",
        "图文": "图文",
        "video": "视频",
        "videos": "视频",
        "视频": "视频",
        "user": "用户",
        "users": "用户",
        "用户": "用户",
    }
    return mapping.get(normalized, str(label or "全部").strip() or "全部")
