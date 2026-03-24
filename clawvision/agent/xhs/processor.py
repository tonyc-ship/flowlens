"""XHS Note Processor — entity-level understanding layer.

Owns ALL media processing for NoteEntity and VideoInfo: carousel image
collection, image download, OCR, Vision descriptions, video transcription.

Design principle: DOM-first, UX-fallback.
  - "DOM actions" are fast, invisible reads: extracting URLs from the page DOM,
    reading text content, getting metadata.
  - "UX actions" simulate human interaction: arrow-key carousel flipping,
    scrolling, clicking. These are slower and can trigger anti-bot detection.
  - Always try DOM first. Only use UX actions when DOM is incomplete.

Task agents (research.py, user_analysis.py) call processor.process_note()
and get back a fully-enriched NoteEntity without knowing about OCR, Vision,
carousel mechanics, or WebP format details.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..media import MediaProcessor
from .browser import XHSBrowser
from .capabilities import NoteExtractionPlan, deep_note_plan
from .entities import ImageInfo, NoteEntity, NoteType, VideoInfo


@dataclass
class ProcessorConfig:
    """Configuration for note-level media processing."""
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
    vision_prompt_template: str = (
        "Describe this image from a Xiaohongshu note titled '{title}'. "
        "Be concise (1-2 sentences). Focus on key visual content, "
        "any text/labels, products, and overall aesthetic."
    )
    video_poster_prompt: str = (
        "Describe this video thumbnail from a Xiaohongshu note titled "
        "'{title}'. What does the video appear to be about?"
    )
    video_frame_prompt_template: str = (
        "这是小红书视频《{title}》的一帧画面（第{index}帧）。"
        "请用中文简洁描述这一帧里的人物、动作、场景、物品，以及它对理解视频内容有什么帮助。"
    )
    transcript_summary_prompt: str = (
        "以下是一个小红书视频笔记的语音转录文本，标题是'{title}'。\n\n"
        "转录文本：\n{transcript}\n\n"
        "请用中文简洁概括这个视频的主要内容（2-3句话）。"
    )
    video_visual_summary_prompt: str = (
        "以下是一个小红书视频《{title}》的封面描述和若干关键帧描述。\n\n"
        "封面描述：{poster}\n\n"
        "关键帧描述：\n{frames}\n\n"
        "请用中文总结这个视频画面里发生了什么，重点概括场景、动作流程、产品/物体、氛围。输出2-4句话。"
    )


@dataclass
class TimingRecord:
    """Accumulated timing for each operation type."""
    counts: dict[str, int] = field(default_factory=dict)
    totals: dict[str, float] = field(default_factory=dict)

    def record(self, op: str, duration: float):
        self.counts[op] = self.counts.get(op, 0) + 1
        self.totals[op] = self.totals.get(op, 0) + duration

    def summary(self) -> dict[str, dict]:
        result = {}
        for op in sorted(self.totals):
            n = self.counts[op]
            total = self.totals[op]
            result[op] = {
                "count": n,
                "total_s": round(total, 2),
                "avg_s": round(total / n, 2) if n else 0,
            }
        return result


class NoteProcessor:
    """Entity-level note understanding: media download, OCR, Vision, transcription.

    DOM-first / UX-fallback:
      - If DOM already provides all image URLs → skip carousel navigation
      - If DOM text content is present → skip Vision-based extraction
      - Only simulate user actions when DOM extraction is incomplete
    """

    def __init__(
        self,
        browser: XHSBrowser,
        media: MediaProcessor,
        config: ProcessorConfig | None = None,
        log_fn=None,
    ):
        self.browser = browser
        self.media = media
        self.config = config or ProcessorConfig()
        self.timing = TimingRecord()
        self._log_fn = log_fn

    def _log(self, action: str, detail: str = "", duration: float | None = None):
        if self._log_fn:
            self._log_fn(action, detail, duration)

    # ── Main Entry Point ────────────────────────────────────────

    async def process_note(self, note: NoteEntity, plan: NoteExtractionPlan | None = None) -> None:
        """Fully process a NoteEntity's media: images OR video.

        After this call, note.images will have OCR + Vision descriptions,
        or note.video will have poster analysis + transcript.

        DOM-first: uses existing note.images URLs if sufficient,
        only flips carousel when DOM count < indicator total.
        """
        t0 = time.time()
        plan = plan or deep_note_plan(
            max_images=self.config.max_images,
            max_video_frames=self.config.max_video_frame_samples,
        )

        if not plan.use_media:
            note.refresh_derived_fields()
            return

        if note.note_type == NoteType.VIDEO:
            await self._process_video(note, plan)
        else:
            await self._process_images(note, plan)

        note.refresh_derived_fields()

        dt = time.time() - t0
        self.timing.record("process_note_media", dt)
        self._log("media_done", f"type={note.note_type.value} total={dt:.2f}s", dt)

    # ── Image Processing Pipeline ────────────────────────────────

    async def _process_images(self, note: NoteEntity, plan: NoteExtractionPlan) -> None:
        """Collect images (DOM-first, carousel-fallback), then parallel OCR + Vision."""
        await self._ensure_all_images(note)

        if not note.images:
            return

        images = note.images[: min(self.config.max_images, plan.max_images)]

        # Download all images in parallel
        t0 = time.time()
        download_tasks = [
            asyncio.to_thread(
                self.media.download_image, img.url, XHSBrowser.XHS_REFERER
            )
            for img in images
        ]
        downloaded = await asyncio.gather(*download_tasks, return_exceptions=True)
        dl_dt = time.time() - t0
        self.timing.record("image_download_batch", dl_dt)

        ok_count = sum(1 for d in downloaded if isinstance(d, bytes))
        self._log("image_download", f"{ok_count}/{len(images)} images", dl_dt)

        # Content-hash dedup: different URLs can serve identical images
        seen_hashes = {}
        deduped_pairs = []  # (img, img_bytes) pairs to keep
        for img, dl in zip(images, downloaded):
            if not isinstance(dl, bytes):
                continue
            h = hashlib.md5(dl).hexdigest()
            if h in seen_hashes:
                self._log("dedup_content", f"[{img.index+1}] duplicate of [{seen_hashes[h]+1}]")
                continue
            seen_hashes[h] = img.index
            deduped_pairs.append((img, dl))

        if len(deduped_pairs) < ok_count:
            self._log("dedup_content", f"{ok_count} downloaded → {len(deduped_pairs)} unique by content hash")
            # Rebuild note.images with only unique images
            note.images = [pair[0] for pair in deduped_pairs]
            for i, img in enumerate(note.images):
                img.index = i
                img.is_cover = (i == 0)
            note.image_count = len(note.images)

        # Parallel OCR + Vision on each image
        semaphore = asyncio.Semaphore(self.config.vision_concurrency)

        async def enrich_single(img: ImageInfo, img_bytes: bytes | None):
            if not isinstance(img_bytes, bytes) or not img_bytes:
                return
            if plan.use_image_ocr:
                await self._ocr_image(img, img_bytes)
            if plan.use_image_vision:
                await self._vision_image(img, img_bytes, note.title, semaphore)
            if img.is_cover and img.vision_description:
                note.cover_description = img.vision_description

        t0 = time.time()
        tasks = [
            enrich_single(img, dl)
            for img, dl in deduped_pairs
        ]
        await asyncio.gather(*tasks, return_exceptions=True)
        proc_dt = time.time() - t0
        self.timing.record("image_process_batch", proc_dt)

        n_vis = sum(1 for img in note.images if img.vision_description)
        n_ocr = sum(1 for img in note.images if img.ocr_text)
        self._log(
            "images_enriched",
            f"{len(note.images)} imgs: {n_vis} vision, {n_ocr} ocr",
            proc_dt,
        )

    async def _ensure_all_images(self, note: NoteEntity) -> None:
        """DOM-first: if DOM already has all unique image URLs, skip carousel.
        UX-fallback: flip carousel only when DOM is incomplete or has duplicates.

        XHS carousel renders multiple <img> elements but may give them all the
        same src (the currently visible slide). We detect this by checking for
        unique URLs — if DOM has 3 img tags but only 1 unique URL, we need
        to flip through the carousel to collect the rest.
        """
        dom_count = len(note.images)
        unique_urls = set(img.url for img in note.images if img.url)
        unique_count = len(unique_urls)
        indicator_total = note.image_count  # from DOM indicator like "3/7"

        if unique_count >= indicator_total and unique_count > 0:
            # DOM already has all unique URLs — no need for carousel
            if unique_count < dom_count:
                # Dedup: DOM had duplicates, rebuild with unique only
                deduped = []
                seen = set()
                for img in note.images:
                    if img.url and img.url not in seen:
                        seen.add(img.url)
                        img.index = len(deduped)
                        img.is_cover = (len(deduped) == 0)
                        deduped.append(img)
                note.images = deduped
                note.image_count = len(deduped)
                self._log(
                    "carousel_skip",
                    f"DOM had {dom_count} imgs ({unique_count} unique) — deduped, no flip needed",
                )
            else:
                self._log(
                    "carousel_skip",
                    f"DOM has {unique_count}/{indicator_total} unique images — no flip needed",
                )
            return

        # DOM is incomplete → use carousel UX action
        self._log(
            "carousel_needed",
            f"DOM has {unique_count} unique/{dom_count} total, indicator={indicator_total} — flipping carousel",
        )
        t0 = time.time()
        all_urls, debug = await self.browser.collect_carousel_images(
            max_images=self.config.max_images
        )
        dt = time.time() - t0
        self.timing.record("carousel_flip", dt)
        self._log("carousel_debug", f"debug={debug}, urls={len(all_urls)}")

        if all_urls and len(all_urls) > unique_count:
            note.images = [
                ImageInfo(url=url, index=i, is_cover=(i == 0))
                for i, url in enumerate(all_urls)
            ]
            note.image_count = len(all_urls)
            self._log(
                "carousel_collected",
                f"{unique_count} unique → {len(all_urls)} images via carousel",
                dt,
            )
        else:
            # Carousel didn't help — still dedup DOM images
            self._log("carousel_no_gain", f"Carousel found {len(all_urls or [])} (was {unique_count} unique)", dt)
            if unique_count < dom_count:
                deduped = []
                seen = set()
                for img in note.images:
                    if img.url and img.url not in seen:
                        seen.add(img.url)
                        img.index = len(deduped)
                        img.is_cover = (len(deduped) == 0)
                        deduped.append(img)
                note.images = deduped
                note.image_count = len(deduped)
                self._log(
                    "dedup_fallback",
                    f"Deduped {dom_count} → {len(deduped)} unique images",
                )

    async def _ocr_image(self, img: ImageInfo, img_bytes: bytes) -> None:
        """Run Apple OCR on a single image (local, fast).

        Retry strategy for flaky WebP OCR:
          1. Direct OCR on original bytes
          2. Retry once (Apple Vision.framework can be non-deterministic on WebP)
          3. Convert WebP→PNG and OCR the PNG (format conversion fixes some edge cases)
        """
        if not self.config.use_ocr:
            return
        t0 = time.time()
        ocr_text = await asyncio.to_thread(self.media.ocr_image, img_bytes)
        dt = time.time() - t0
        self.timing.record("ocr_single", dt)

        # Retry once if empty — Apple OCR on WebP can be flaky
        if not ocr_text.strip() and len(img_bytes) > 10_000:
            self._log("ocr_retry", f"[{img.index+1}] empty on {len(img_bytes)}B image, retrying")
            t0 = time.time()
            ocr_text = await asyncio.to_thread(self.media.ocr_image, img_bytes)
            dt2 = time.time() - t0
            self.timing.record("ocr_retry", dt2)

        # Final fallback: convert WebP→PNG and retry OCR
        if not ocr_text.strip() and len(img_bytes) > 10_000:
            try:
                t0 = time.time()
                png_bytes = await asyncio.to_thread(self._convert_to_png, img_bytes)
                if png_bytes:
                    ocr_text = await asyncio.to_thread(self.media.ocr_image, png_bytes)
                    dt3 = time.time() - t0
                    self.timing.record("ocr_png_fallback", dt3)
                    if ocr_text.strip():
                        self._log("ocr_png_fallback", f"[{img.index+1}] WebP→PNG recovered {len(ocr_text)} chars", dt3)
            except Exception:
                pass

        if ocr_text.strip():
            img.ocr_text = ocr_text
            self._log("ocr", f"[{img.index+1}] {len(ocr_text)} chars", dt)

    @staticmethod
    def _convert_to_png(img_bytes: bytes) -> bytes | None:
        """Convert image bytes (WebP/JPEG/etc) to PNG using Pillow or CoreGraphics."""
        try:
            from PIL import Image
            import io
            img = Image.open(io.BytesIO(img_bytes))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()
        except ImportError:
            return None
        except Exception:
            return None

    async def _vision_image(
        self, img: ImageInfo, img_bytes: bytes, title: str,
        semaphore: asyncio.Semaphore,
    ) -> None:
        """Run Vision API description on a single image (remote, slow)."""
        if not self.config.use_vision:
            return
        async with semaphore:
            t0 = time.time()
            try:
                prompt = self.config.vision_prompt_template.format(title=title)
                desc = await asyncio.to_thread(
                    self.media.describe_image, img_bytes, prompt, 512,
                )
                dt = time.time() - t0
                self.timing.record("vision_single", dt)
                img.vision_description = desc
                self._log("vision", f"[{img.index+1}] {desc[:80]}", dt)
            except Exception as e:
                self._log("vision_error", f"[{img.index+1}] {e}")

    # ── Video Processing Pipeline ────────────────────────────────

    async def _process_video(self, note: NoteEntity, plan: NoteExtractionPlan) -> None:
        """Process video note: poster OCR + Vision + Whisper, all in parallel."""
        if note.video is None:
            note.video = VideoInfo()

        video = note.video
        poster_url = video.poster_url or (note.images[0].url if note.images else "")
        downloadable_url = video.best_download_url()
        video.resolved_url = downloadable_url or video.best_source_url()
        video.stream_type = self._infer_video_stream_type(video.resolved_url)
        processing_source = downloadable_url
        if not downloadable_url:
            video.download_error = "no_downloadable_video_url"
            if video.resolved_url.startswith("blob:"):
                self._log("video_source", "Only blob: video URL found; cannot transcribe directly")
            else:
                self._log("video_source", "No downloadable video URL found in DOM/performance traces")
        else:
            video.download_error = ""
            self._log("video_source", f"{video.stream_type or 'remote'} source resolved")
            if self.config.cache_video_locally and video.stream_type in {"mp4", "mov", "m4v"}:
                t0 = time.time()
                suffix = f".{video.stream_type}" if video.stream_type else ".mp4"
                local_path = await asyncio.to_thread(
                    self.media.download_file,
                    downloadable_url,
                    XHSBrowser.XHS_REFERER,
                    suffix,
                )
                dt = time.time() - t0
                self.timing.record("video_download", dt)
                if local_path:
                    video.download_path = local_path
                    processing_source = local_path
                    self._log("video_download", Path(local_path).name, dt)
                else:
                    self._log("video_download_error", "failed to cache mp4 locally", dt)

        # Download poster once
        poster_bytes = None
        if poster_url:
            t0 = time.time()
            poster_bytes = await asyncio.to_thread(
                self.media.download_image, poster_url, XHSBrowser.XHS_REFERER
            )
            self.timing.record("poster_download", time.time() - t0)

        # Run poster analysis + transcription in parallel
        async def poster_vision():
            if not poster_bytes or not self.config.use_vision or not plan.use_image_vision:
                return
            t0 = time.time()
            try:
                prompt = self.config.video_poster_prompt.format(title=note.title)
                desc = await asyncio.to_thread(
                    self.media.describe_image, poster_bytes, prompt, 512,
                )
                dt = time.time() - t0
                self.timing.record("vision_poster", dt)
                video.poster_description = desc
                note.cover_description = desc
                self._log("vision_poster", desc[:80], dt)
            except Exception as e:
                self._log("vision_error", str(e)[:80])

        async def poster_ocr():
            if not poster_bytes or not self.config.use_ocr or not plan.use_image_ocr:
                return
            t0 = time.time()
            ocr_text = await asyncio.to_thread(self.media.ocr_image, poster_bytes)
            dt = time.time() - t0
            self.timing.record("ocr_poster", dt)
            if ocr_text.strip():
                video.poster_ocr = ocr_text
                self._log("ocr_poster", f"{len(ocr_text)} chars", dt)

        async def transcribe():
            if not processing_source or not self.config.use_whisper or not plan.use_video_transcript:
                return
            t0 = time.time()
            self._log("transcribe_start", "Downloading + transcribing video...")
            try:
                transcript = await self.media.transcribe_video(
                    processing_source,
                    "zh",
                    referer=XHSBrowser.XHS_REFERER if str(processing_source).startswith("http") else "",
                    max_audio_seconds=self.config.max_transcription_seconds,
                    timeout_s=self.config.transcription_timeout_s,
                )
                dt = time.time() - t0
                self.timing.record("whisper_transcribe", dt)
                if transcript.strip():
                    video.transcript = transcript
                    video.download_error = ""
                    self._log("transcript", f"{len(transcript)} chars", dt)

                    t0_s = time.time()
                    prompt = self.config.transcript_summary_prompt.format(
                        title=note.title,
                        transcript=transcript[:3000],
                    )
                    summary = await asyncio.to_thread(
                        self.media.call_text, prompt, 512,
                    )
                    dt_s = time.time() - t0_s
                    self.timing.record("llm_transcript_summary", dt_s)
                    video.transcript_summary = summary
                    self._log("transcript_summary", summary[:80], dt_s)
                else:
                    video.download_error = "empty_transcript"
            except Exception as e:
                video.download_error = str(e)
                self._log("transcribe_error", str(e)[:100])

        async def frame_understanding():
            if not processing_source or not self.config.use_vision or not plan.use_video_frames:
                return
            t0 = time.time()
            frame_paths = await self.media.extract_video_frames(
                processing_source,
                referer=XHSBrowser.XHS_REFERER if str(processing_source).startswith("http") else "",
                max_seconds=self.config.max_video_frame_seconds,
                num_frames=min(self.config.max_video_frame_samples, plan.max_video_frames),
                timeout_s=self.config.transcription_timeout_s,
            )
            dt = time.time() - t0
            self.timing.record("video_frame_extract", dt)
            if not frame_paths:
                self._log("video_frame_extract", "no frames extracted", dt)
                return

            video.frame_paths = frame_paths
            self._log("video_frame_extract", f"{len(frame_paths)} frames", dt)

            semaphore = asyncio.Semaphore(self.config.vision_concurrency)
            frame_results: list[str] = [""] * len(frame_paths)

            async def describe_frame(index: int, frame_path: str):
                async with semaphore:
                    try:
                        img_bytes = await asyncio.to_thread(Path(frame_path).read_bytes)
                        prompt = self.config.video_frame_prompt_template.format(
                            title=note.title,
                            index=index + 1,
                        )
                        t0_f = time.time()
                        desc = await asyncio.to_thread(
                            self.media.describe_image,
                            img_bytes,
                            prompt,
                            384,
                        )
                        dt_f = time.time() - t0_f
                        self.timing.record("vision_video_frame", dt_f)
                        if desc:
                            frame_results[index] = desc
                            self._log("video_frame_vision", f"[{index+1}] {desc[:80]}", dt_f)
                    except Exception as e:
                        self._log("video_frame_error", f"[{index+1}] {e}")

            await asyncio.gather(*[
                describe_frame(index, frame_path)
                for index, frame_path in enumerate(frame_paths)
            ])

            video.frame_descriptions = [desc for desc in frame_results if desc]

            if video.frame_descriptions:
                t0_s = time.time()
                summary = await asyncio.to_thread(
                    self.media.call_text,
                    self.config.video_visual_summary_prompt.format(
                        title=note.title,
                        poster=video.poster_description or video.poster_ocr or "无",
                        frames="\n".join(
                            f"{idx+1}. {desc}"
                            for idx, desc in enumerate(video.frame_descriptions)
                        ),
                    ),
                    512,
                )
                dt_s = time.time() - t0_s
                self.timing.record("llm_video_visual_summary", dt_s)
                video.visual_summary = summary
                self._log("video_visual_summary", summary[:80], dt_s)

        await asyncio.gather(poster_vision(), poster_ocr(), frame_understanding(), transcribe())

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

    # ── Image saving for reports ────────────────────────────────

    async def save_images(self, note: NoteEntity, output_dir: str) -> list[str]:
        """Save all downloaded images to disk for visual reports. Returns paths."""
        import os
        from pathlib import Path

        img_dir = Path(output_dir) / "images"
        img_dir.mkdir(parents=True, exist_ok=True)
        saved = []

        for img in note.images[:self.config.max_images]:
            if not img.url:
                continue
            img_bytes = await asyncio.to_thread(
                self.media.download_image, img.url, XHSBrowser.XHS_REFERER
            )
            if not isinstance(img_bytes, bytes):
                continue

            ext = "webp"
            mtype = self.media.detect_media_type(img_bytes)
            if "jpeg" in mtype:
                ext = "jpg"
            elif "png" in mtype:
                ext = "png"

            safe_title = "".join(c if c.isalnum() else "_" for c in note.title[:15])
            fname = f"{safe_title}_img{img.index+1}.{ext}"
            path = img_dir / fname
            path.write_bytes(img_bytes)
            saved.append(str(path))

        return saved

    async def save_video_frames(self, note: NoteEntity, output_dir: str) -> list[str]:
        """Copy extracted video frame samples to disk for reports."""
        if not note.video or not note.video.frame_paths:
            return []

        frame_dir = Path(output_dir) / "video_frames"
        frame_dir.mkdir(parents=True, exist_ok=True)
        saved: list[str] = []
        safe_title = "".join(c if c.isalnum() else "_" for c in note.title[:15]) or "video"

        for index, frame_path in enumerate(note.video.frame_paths):
            src = Path(frame_path)
            if not src.exists():
                continue
            dest = frame_dir / f"{safe_title}_frame{index+1}{src.suffix or '.jpg'}"
            dest.write_bytes(src.read_bytes())
            saved.append(str(dest))

        return saved
