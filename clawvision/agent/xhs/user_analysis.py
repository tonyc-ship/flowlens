"""XHS User Analyzer — deep analysis of a single Xiaohongshu creator.

Uses XHSBrowser for DOM extraction and CDP click-based note opening.
Uses MediaProcessor for LLM calls, Apple OCR, Whisper transcription, and Vision API.
Uses entity models (AuthorEntity, NoteEntity, NoteCard) for structured extraction.

Navigates to user profile, collects all posts as NoteCards, opens top notes
via CDP click (avoids anti-bot), populates NoteEntity for each, checks
completeness, and generates an HTML report.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

from ..bridge import ExtensionBridge
from ..media import MediaProcessor
from .browser import XHSBrowser
from .entities import (
    AuthorEntity, Comment, ImageInfo, NoteCard, NoteEntity, NoteType, VideoInfo,
)


@dataclass
class UserAnalysisConfig:
    max_scroll_rounds: int = 30
    max_notes_to_detail: int = 10
    max_images_per_note: int = 5
    max_comments_per_note: int = 20
    max_comment_scrolls: int = 2
    use_apple_ocr: bool = True
    use_whisper: bool = True
    use_vision_for_covers: bool = True
    whisper_model: str = "large-v3-turbo"
    screenshot_dir: str = "screenshots"


class XHSUserAnalyzer:
    """Deep analysis of a single XHS user/creator using entity-driven extraction."""

    def __init__(
        self,
        output_dir: str = "user_analysis",
        port: int = 8765,
        config: UserAnalysisConfig | None = None,
        browser: XHSBrowser | None = None,
        media: MediaProcessor | None = None,
    ):
        self.config = config or UserAnalysisConfig()
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        (self.output_dir / self.config.screenshot_dir).mkdir(exist_ok=True)

        if browser:
            self.browser = browser
        else:
            bridge = ExtensionBridge(port=port)
            self.browser = XHSBrowser(bridge)
            bridge.on_log(self._log_step)

        self.media = media or MediaProcessor()

        self._step = 0
        self._log: list[dict] = []
        self._t0 = 0

    def _log_step(self, action: str, detail: str = ""):
        self._step += 1
        elapsed = time.time() - self._t0 if self._t0 else 0
        entry = {
            "step": self._step,
            "time": time.strftime("%H:%M:%S"),
            "elapsed_s": round(elapsed, 1),
            "action": action,
            "detail": detail[:200],
        }
        self._log.append(entry)
        print(f"  [{self._step:03d} {elapsed:5.1f}s] {action}: {detail[:100]}")

    # ── Screenshot ──────────────────────────────────────────────

    async def _take_screenshot(self, label: str) -> str:
        try:
            path = await self.browser.bridge.save_screenshot(
                self.output_dir / self.config.screenshot_dir / f"{label}.png"
            )
            if path:
                self._log_step("screenshot", f"{label}: saved")
            return path
        except Exception as e:
            self._log_step("screenshot_error", f"{label}: {e}")
            return ""

    # ── Main Flow ───────────────────────────────────────────────

    async def analyze(self, user_url: str) -> dict:
        """Run full user analysis. Returns report dict with AuthorEntity data."""
        self._t0 = time.time()
        self._step = 0
        self._log = []

        self._log_step("start", f"Analyzing user: {user_url}")

        await self.browser.bridge.start()
        self._log_step("bridge_ready", f"WebSocket on port {self.browser.bridge.port}")
        print("\n  >>> Waiting for Chrome Extension to connect. <<<\n")
        await self.browser.bridge.wait_for_connection(timeout=120)

        # Navigate to profile
        profile_url = await self.browser.navigate_to_profile(user_url)
        self._log_step("navigate", profile_url)

        # Screenshot profile
        profile_screenshot = await self._take_screenshot("profile_header")

        # Extract profile info → AuthorEntity
        raw_profile = await self.browser.extract_profile_info()
        author = AuthorEntity.from_dom_dict(raw_profile)
        author.profile_url = profile_url
        if profile_screenshot:
            author.screenshot_path = profile_screenshot
        self._log_step("profile", f"{author.name} | followers={author.followers}")

        # Collect all post cards by scrolling → NoteCards
        author.note_cards = await self._collect_all_notes()
        self._log_step("cards_total", f"{len(author.note_cards)} posts collected")

        # Sort by engagement for priority processing
        def parse_likes(s: str) -> float:
            s = str(s).replace('万', '0000').replace('w', '0000').replace(',', '')
            try:
                return float(s)
            except (ValueError, TypeError):
                return 0
        author.note_cards.sort(key=lambda c: parse_likes(c.likes), reverse=True)

        # Process top notes in detail → NoteEntities
        anti_bot_strikes = 0
        max_detail = min(self.config.max_notes_to_detail, len(author.note_cards))
        for i, card in enumerate(author.note_cards[:max_detail]):
            self._log_step("process_note", f"[{i+1}/{max_detail}] {card.title[:40]}")
            result = await self._process_note(card, profile_url)
            if result is None:
                continue
            if result == "anti_bot":
                anti_bot_strikes += 1
                self._log_step("anti_bot", f"Strike {anti_bot_strikes} — backing off 15s")
                await asyncio.sleep(15)
                if anti_bot_strikes >= 3:
                    self._log_step("anti_bot_stop", "3 strikes — stopping note collection")
                    break
            else:
                anti_bot_strikes = 0
                author.detailed_notes.append(result)
            if i < max_detail - 1:
                await asyncio.sleep(2)

        # LLM synthesis
        elapsed_collect = time.time() - self._t0
        self._log_step("synthesize", f"Collection done in {elapsed_collect:.1f}s")

        author.content_analysis = self._analyze_content_strategy(author)

        elapsed_total = time.time() - self._t0
        self._log_step("done", f"Total: {elapsed_total:.1f}s")

        # Log author completeness
        comp = author.completeness
        score = author.completeness_score
        missing = [k for k, v in comp.items() if not v]
        self._log_step("author_completeness", f"{score:.0%} — missing: {missing}" if missing else f"{score:.0%} — complete")

        # Build report (using entity conversion for HTML-compatible dicts)
        report = {
            "profile": author.to_report_dict(),
            "profile_url": profile_url,
            "all_cards": [
                {"note_id": c.note_id, "title": c.title, "author": c.author_name,
                 "likes": c.likes, "type": c.note_type.value, "link": c.link}
                for c in author.note_cards
            ],
            "detailed_notes": [n.to_report_dict() for n in author.detailed_notes],
            "analysis": author.content_analysis,
            "timing": {
                "data_collection_s": round(elapsed_collect, 1),
                "total_s": round(elapsed_total, 1),
            },
            "log": self._log,
        }

        self._save_report(report)
        await self.browser.bridge.stop()
        return report

    # ── Collect All Post Cards ──────────────────────────────────

    async def _collect_all_notes(self) -> list[NoteCard]:
        all_cards: list[NoteCard] = []
        seen_ids: set[str] = set()

        for round_idx in range(self.config.max_scroll_rounds):
            raw_cards = await self.browser.extract_profile_notes()
            new_count = 0
            for raw in raw_cards:
                card = NoteCard.from_dom_dict(raw)
                nid = card.note_id or card.link
                if nid and nid not in seen_ids:
                    seen_ids.add(nid)
                    all_cards.append(card)
                    new_count += 1

            if new_count == 0 and round_idx > 0:
                self._log_step("scroll_done", f"No new cards after round {round_idx+1}")
                break

            self._log_step("scroll", f"Round {round_idx+1}: {new_count} new, {len(all_cards)} total")
            await self.browser.scroll_page(800)
            await asyncio.sleep(1.5)

        return all_cards

    # ── Process Single Note ─────────────────────────────────────

    async def _process_note(
        self, card: NoteCard, profile_url: str
    ) -> NoteEntity | str | None:
        """Open a note from profile, extract into NoteEntity, return to profile.

        Returns:
          NoteEntity — success
          "anti_bot" — blocked by anti-bot
          None — skip (no id/link)
        """
        note_id = card.note_id
        opened_as_overlay = False

        if not note_id and not card.link:
            return None

        # Strategy 1: CDP real mouse click on card cover
        try:
            opened_as_overlay = await self.browser.open_note_on_profile(note_id)
            if not opened_as_overlay:
                raise RuntimeError("Overlay did not open")
        except Exception as e:
            self._log_step("click_failed", f"Card click failed: {e}")
            note_url = f"https://www.xiaohongshu.com/explore/{note_id}" if note_id else card.link
            await self.browser.navigate(note_url, wait_ms=5000)
            await asyncio.sleep(3)

            if await self.browser.is_anti_bot_page():
                self._log_step("anti_bot_detected", "Page blocked by anti-bot")
                await self.browser.navigate(profile_url, wait_ms=5000)
                await asyncio.sleep(2)
                return "anti_bot"

        # Screenshot
        safe_label = re.sub(r'[^\w]', '_', card.title[:20]).strip('_') or note_id[:8]
        note_screenshot = await self._take_screenshot(f"note_{safe_label}")

        # Extract content → NoteEntity
        raw_note = await self.browser.extract_note_content()
        note = NoteEntity.from_dom_dict(raw_note)
        note.note_id = note_id
        note.card_likes = card.likes
        note.source_context = "profile"
        if note_screenshot:
            note.screenshot_path = note_screenshot

        # Detect empty content (may indicate anti-bot or loading failure)
        if not note.has_content:
            try:
                state = await self.browser.detect_state()
                if state.get("state") in ("error", "unknown"):
                    self._log_step("anti_bot_detected", f"Empty content, state={state.get('state')}")
                    if opened_as_overlay:
                        await self.browser.close_note()
                        await asyncio.sleep(1)
                    else:
                        await self.browser.navigate(profile_url, wait_ms=5000)
                        await asyncio.sleep(2)
                    return "anti_bot"
            except Exception:
                pass

        self._log_step(
            "extract",
            f"type={note.note_type.value} title='{note.title[:30]}' "
            f"content_len={len(note.content)}"
        )

        # Process media: populate ImageInfo / VideoInfo objects
        if note.note_type == NoteType.VIDEO:
            await self._process_video(note)
        else:
            await self._process_images(note)

        # Comments → Comment entities
        raw_comments = await self.browser.extract_comments()
        for _ in range(self.config.max_comment_scrolls):
            await self.browser.scroll_note(400)
            more = await self.browser.extract_comments()
            existing_keys = {f"{c.get('username', '')}:{c.get('text', '')[:30]}" for c in raw_comments}
            for c in more:
                key = f"{c.get('username', '')}:{c.get('text', '')[:30]}"
                if key not in existing_keys:
                    raw_comments.append(c)
                    existing_keys.add(key)

        note.comments = [
            Comment.from_dom_dict(c) for c in raw_comments[:self.config.max_comments_per_note]
        ]
        self._log_step("comments", f"{len(note.comments)} comments")

        # Log completeness
        comp = note.completeness
        score = note.completeness_score
        missing = [k for k, v in comp.items() if not v]
        self._log_step("completeness", f"{score:.0%} — missing: {missing}" if missing else f"{score:.0%} — complete")

        # Return to profile
        if opened_as_overlay:
            await self.browser.close_note()
            await asyncio.sleep(1.5)
        else:
            await self.browser.navigate(profile_url, wait_ms=5000)
            await asyncio.sleep(2)

        return note

    # ── Image Processing → ImageInfo enrichment ─────────────────

    async def _process_images(self, note: NoteEntity):
        """Download images, run Apple OCR + Vision, populate ImageInfo objects."""
        for img in note.images[:self.config.max_images_per_note]:
            if not img.url:
                continue
            try:
                img_bytes = self.media.download_image(img.url, referer=XHSBrowser.XHS_REFERER)
                if not img_bytes:
                    continue

                # Apple OCR on every image (free + fast)
                ocr_text = self.media.ocr_image(img_bytes)
                if ocr_text.strip():
                    img.ocr_text = ocr_text
                    self._log_step("ocr", f"[{img.index+1}] {len(ocr_text)} chars extracted")

                # Vision API for cover image only
                if img.is_cover and self.config.use_vision_for_covers:
                    try:
                        desc = self.media.describe_image(
                            img_bytes,
                            f"Describe this cover image from a Xiaohongshu note titled "
                            f"'{note.title}'. Be concise (2-3 sentences). "
                            f"Focus on visual content, products, text overlays, and aesthetic.",
                            max_tokens=512,
                        )
                        img.vision_description = desc
                        note.cover_description = desc
                        self._log_step("vision_cover", desc[:80])
                    except Exception as e:
                        self._log_step("vision_error", str(e)[:200])

            except Exception as e:
                self._log_step("image_error", f"[{img.index+1}] {e}")

    # ── Video Processing → VideoInfo enrichment ─────────────────

    async def _process_video(self, note: NoteEntity):
        """Process video: Vision on poster, OCR, Whisper transcription → VideoInfo."""
        if note.video is None:
            note.video = VideoInfo()

        video = note.video
        poster_url = video.poster_url or (note.images[0].url if note.images else "")

        # Vision API on video poster/thumbnail
        if poster_url and self.config.use_vision_for_covers:
            try:
                img_bytes = self.media.download_image(poster_url, referer=XHSBrowser.XHS_REFERER)
                if img_bytes:
                    desc = self.media.describe_image(
                        img_bytes,
                        f"Describe this video thumbnail from a Xiaohongshu note titled "
                        f"'{note.title}'. What does the video appear to be about?",
                        max_tokens=512,
                    )
                    video.poster_description = desc
                    note.cover_description = desc
                    self._log_step("vision_poster", desc[:80])
            except Exception as e:
                self._log_step("vision_error", str(e)[:80])

        # OCR on poster
        if poster_url:
            try:
                img_bytes = self.media.download_image(poster_url, referer=XHSBrowser.XHS_REFERER)
                if img_bytes:
                    ocr_text = self.media.ocr_image(img_bytes)
                    if ocr_text.strip():
                        video.poster_ocr = ocr_text
                        self._log_step("ocr_poster", f"{len(ocr_text)} chars")
            except Exception:
                pass

        # Whisper transcription
        if video.url:
            try:
                self._log_step("transcribe_start", "Downloading + transcribing video...")
                transcript = await self.media.transcribe_video(video.url, "zh")
                if transcript.strip():
                    video.transcript = transcript
                    self._log_step("transcript", f"{len(transcript)} chars transcribed")

                    summary = self.media.call_text(
                        f"以下是一个小红书视频笔记的语音转录文本，标题是'{note.title}'。\n\n"
                        f"转录文本：\n{transcript[:3000]}\n\n"
                        f"请用中文简洁概括这个视频的主要内容（2-3句话）。",
                        512,
                    )
                    video.transcript_summary = summary
                    self._log_step("transcript_summary", summary[:80])
            except Exception as e:
                self._log_step("transcribe_error", str(e)[:100])

    # ── LLM Analysis ────────────────────────────────────────────

    def _analyze_content_strategy(self, author: AuthorEntity) -> str:
        """Use Claude to analyze the creator's content strategy."""
        # All posts overview
        post_summaries = []
        for c in author.note_cards:
            post_summaries.append(f"- {c.title[:50]} | likes={c.likes} | type={c.note_type.value}")

        # Detailed note summaries via entity method
        note_summaries = [n.to_summary() for n in author.detailed_notes]

        prompt = (
            f"请对以下小红书用户进行深度分析，用中文输出一份详细的研究报告。\n\n"
            f"## 用户信息\n"
            f"昵称: {author.name}\n"
            f"简介: {author.bio}\n"
            f"粉丝: {author.followers}\n"
            f"关注: {author.following}\n"
            f"获赞与收藏: {author.total_likes}\n"
            f"认证: {author.verify_text or '无'}\n"
            f"标签: {', '.join(author.tags)}\n\n"
            f"## 全部帖子概览 ({len(author.note_cards)} 篇)\n"
            + "\n".join(post_summaries[:50]) + "\n\n"
            f"## 详细分析的帖子 ({len(note_summaries)} 篇)\n"
            f"{json.dumps(note_summaries, ensure_ascii=False, indent=1)}\n\n"
            f"请从以下角度进行分析：\n"
            f"1. **赛道定位**：这个账号属于什么垂直领域？细分赛道是什么？\n"
            f"2. **内容策略**：发布频率、内容形式（图文vs视频比例）、标题风格、标签策略\n"
            f"3. **爆款分析**：哪些帖子数据最好？为什么？有什么共同特点？\n"
            f"4. **人设打造**：这个创作者的人设/定位是什么？TA是如何建立信任的？\n"
            f"5. **涨粉策略**：从粉丝量和内容质量推测TA是怎么涨起来的\n"
            f"6. **值得学习的点**：对于想模仿/学习这个账号的人，有哪些可复制的经验？\n"
            f"7. **不足与建议**：有哪些可以改进的地方？\n"
        )
        return self.media.call_text(prompt, 4096)

    # ── Report ──────────────────────────────────────────────────

    def _save_report(self, report: dict):
        json_path = self.output_dir / "report.json"
        with open(json_path, "w") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        self._log_step("saved", f"JSON: {json_path}")

        html = self._generate_html(report)
        html_path = self.output_dir / "report.html"
        with open(html_path, "w") as f:
            f.write(html)
        self._log_step("saved", f"HTML: {html_path}")

    def _generate_html(self, data: dict) -> str:
        def _esc(s):
            return str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        profile = data.get("profile", {})
        all_cards = data.get("all_cards", [])
        detailed = data.get("detailed_notes", [])

        parts = [
            "<!DOCTYPE html><html><head><meta charset='utf-8'>",
            f"<title>XHS User Analysis: {_esc(profile.get('name', ''))}</title>",
            "<style>",
            "body{font-family:-apple-system,sans-serif;max-width:1100px;margin:0 auto;padding:20px;line-height:1.6;color:#333}",
            "h1{color:#ff2442}h2{color:#333;border-bottom:2px solid #ff2442;padding-bottom:5px}h3{color:#555}",
            ".profile-header{background:#fff;border:1px solid #eee;border-radius:12px;padding:24px;margin:16px 0;display:flex;gap:20px;align-items:flex-start;box-shadow:0 2px 8px rgba(0,0,0,0.05)}",
            ".profile-stats{display:flex;gap:24px;margin:12px 0}.profile-stats .stat{text-align:center}.profile-stats .stat .num{font-size:20px;font-weight:bold;color:#333}.profile-stats .stat .label{font-size:12px;color:#888}",
            ".note{background:#fff;border:1px solid #eee;border-radius:8px;padding:16px;margin:12px 0;box-shadow:0 1px 3px rgba(0,0,0,0.05)}",
            ".meta{color:#888;font-size:13px}",
            ".tag{background:#fff0f0;color:#ff2442;padding:2px 8px;border-radius:12px;font-size:12px;margin:2px;display:inline-block}",
            ".analysis{background:#f8f8f8;padding:20px;border-radius:8px;margin:20px 0;white-space:pre-wrap;line-height:1.8}",
            ".comment{background:#fafafa;padding:8px 10px;margin:4px 0;border-radius:4px;font-size:13px}",
            ".comment strong{color:#333}.comment .likes{color:#999;font-size:11px;margin-left:8px}",
            "img.screenshot{max-width:100%;max-height:500px;border:1px solid #ddd;border-radius:6px;margin:8px 0}",
            ".ocr{background:#fffde7;border:1px solid #ffd54f;border-radius:6px;padding:10px;margin:8px 0;font-size:13px;white-space:pre-wrap}",
            ".transcript{background:#e8f5e9;border:1px solid #81c784;border-radius:6px;padding:10px;margin:8px 0;font-size:13px;white-space:pre-wrap}",
            ".cards-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:8px;margin:12px 0}",
            ".card-mini{background:#fafafa;padding:8px;border-radius:4px;font-size:12px;border:1px solid #eee}",
            ".card-mini .title{font-weight:bold;color:#333}.card-mini .stats{color:#888;font-size:11px}",
            ".timing{background:#e8f5e9;padding:12px;border-radius:6px;font-size:13px;margin:10px 0}",
            ".log{font-family:monospace;font-size:11px;color:#666;max-height:400px;overflow-y:auto;background:#f5f5f5;padding:10px;border-radius:6px}",
            "</style></head><body>",
        ]

        parts.append(f"<h1>XHS User Analysis: {_esc(profile.get('name', ''))}</h1>")

        timing = data.get("timing", {})
        parts.append(
            f"<div class='timing'>Total posts: {len(all_cards)} | "
            f"Detailed: {len(detailed)} | "
            f"Data collection: {timing.get('data_collection_s', '?')}s | "
            f"Total: {timing.get('total_s', '?')}s</div>"
        )

        # Profile header
        parts.append("<h2>Profile</h2><div class='profile-header'>")
        if profile.get("avatar_url"):
            parts.append(f"<img src='{profile['avatar_url']}' style='width:80px;height:80px;border-radius:50%'>")
        parts.append("<div>")
        parts.append(f"<h3 style='margin:0'>{_esc(profile.get('name', ''))}")
        if profile.get("verified"):
            parts.append(f" <span style='color:#ff2442'>✓ {_esc(profile.get('verify_text', ''))}</span>")
        parts.append("</h3>")
        if profile.get("xhs_id"):
            parts.append(f"<p class='meta'>小红书号: {_esc(profile['xhs_id'])}</p>")
        if profile.get("bio"):
            parts.append(f"<p>{_esc(profile['bio'])}</p>")
        parts.append("<div class='profile-stats'>")
        for key, label in [("following", "关注"), ("followers", "粉丝"), ("total_likes", "获赞与收藏")]:
            parts.append(f"<div class='stat'><div class='num'>{_esc(profile.get(key, ''))}</div><div class='label'>{label}</div></div>")
        parts.append("</div>")
        if profile.get("tags"):
            parts.append("<p>" + " ".join(f"<span class='tag'>{_esc(t)}</span>" for t in profile["tags"]) + "</p>")
        parts.append("</div></div>")

        if profile.get("screenshot"):
            rel = os.path.relpath(profile["screenshot"], str(self.output_dir))
            parts.append(f'<img class="screenshot" src="{rel}" alt="profile">')

        # Analysis
        parts.append(f"<h2>Strategy Analysis</h2><div class='analysis'>{_esc(data.get('analysis', ''))}</div>")

        # All posts overview
        parts.append(f"<h2>All Posts ({len(all_cards)})</h2>")
        parts.append("<div class='cards-grid'>")
        for c in all_cards:
            icon = "🎬" if c.get("type") == "video" else "📷"
            parts.append(
                f"<div class='card-mini'>"
                f"<div class='title'>{icon} {_esc(c.get('title', 'Untitled')[:40])}</div>"
                f"<div class='stats'>❤️ {_esc(c.get('likes', '?'))}</div>"
                f"</div>"
            )
        parts.append("</div>")

        # Detailed notes
        parts.append(f"<h2>Detailed Notes ({len(detailed)})</h2>")
        for i, note in enumerate(detailed):
            parts.append(f"<div class='note'><h3>{i+1}. {_esc(note.get('title', 'Untitled'))}</h3>")
            parts.append(
                f"<p class='meta'>Type: {note.get('type', '?')} | "
                f"Likes: {_esc(note.get('likes', '?'))} | "
                f"Favorites: {_esc(note.get('favorites', '?'))} | "
                f"Comments: {_esc(note.get('comments_count', '?'))} | "
                f"Images: {note.get('image_count', '?')}</p>"
            )

            if note.get("hashtags"):
                tags = " ".join(f"<span class='tag'>{_esc(t)}</span>" for t in note["hashtags"])
                parts.append(f"<p>{tags}</p>")

            content = note.get("content", "")
            if content:
                parts.append(f"<p>{_esc(content[:800])}</p>")

            if note.get("cover_description"):
                parts.append(f"<h4>Cover Image (Vision)</h4><p>{_esc(note['cover_description'])}</p>")

            if note.get("ocr_results"):
                parts.append("<h4>Text in Images (OCR)</h4>")
                for r in note["ocr_results"]:
                    parts.append(f"<div class='ocr'>Image {r['image_index']+1}:\n{_esc(r['text'][:500])}</div>")

            if note.get("transcript_summary"):
                parts.append(f"<h4>Video Transcript Summary</h4><div class='transcript'>{_esc(note['transcript_summary'])}</div>")
            if note.get("transcript"):
                parts.append(f"<details><summary>Full transcript ({len(note['transcript'])} chars)</summary><div class='transcript'>{_esc(note['transcript'][:2000])}</div></details>")

            if note.get("screenshot"):
                rel = os.path.relpath(note["screenshot"], str(self.output_dir))
                parts.append(f'<img class="screenshot" src="{rel}">')

            if note.get("comments"):
                parts.append(f"<h4>Comments ({len(note['comments'])})</h4>")
                for c in note["comments"][:8]:
                    likes_str = f"<span class='likes'>{c.get('likes', '')}</span>" if c.get("likes") else ""
                    parts.append(
                        f"<div class='comment'><strong>{_esc(c.get('username', ''))}</strong>: "
                        f"{_esc(c.get('text', '')[:200])}{likes_str}</div>"
                    )

            parts.append("</div>")

        # Log
        parts.append("<h2>Execution Log</h2><div class='log'>")
        for entry in data.get("log", []):
            parts.append(
                f"<div>[{entry.get('step', '')} {entry.get('elapsed_s', '')}s] "
                f"{_esc(entry.get('action', ''))}: {_esc(entry.get('detail', ''))}</div>"
            )
        parts.append("</div></body></html>")

        return "\n".join(parts)


async def run_user_analysis(
    user_url: str,
    output_dir: str = "user_analysis",
    port: int = 8765,
    config: UserAnalysisConfig | None = None,
) -> dict:
    """Convenience function to run user analysis."""
    analyzer = XHSUserAnalyzer(config=config, output_dir=output_dir, port=port)
    report = await analyzer.analyze(user_url)

    print(f"\n{'='*60}")
    print(f"User Analysis Complete — {report['timing']['total_s']}s")
    print(f"{'='*60}")
    profile = report["profile"]
    print(f"User: {profile.get('name', '?')} | Followers: {profile.get('followers', '?')}")
    print(f"Posts: {len(report['all_cards'])} total, {len(report['detailed_notes'])} detailed")
    print(f"\nReport: {output_dir}/report.html")

    return report
