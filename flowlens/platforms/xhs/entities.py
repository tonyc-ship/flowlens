"""XHS Entity Definitions — structured models for deep content understanding.

These entities define WHAT to extract from each XHS page element.
They serve as:
  1. Schema — what fields exist and what they mean
  2. Completeness checklist — agent knows when extraction is thorough vs shallow
  3. Cross-platform template — other platforms define similar entities

Design principle: a human browsing XHS would examine each note's images one
by one, read the full text, scroll through comments, check the author's
profile. These entities encode that level of thoroughness.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


def parse_count_text(raw: str) -> int:
    """Parse social count strings like '1.2万', '3,421', '98' into integers."""
    value = str(raw or "").strip().lower().replace(",", "").replace("+", "")
    if not value:
        return 0

    match = re.search(r"(\d+(?:\.\d+)?)(万|w|k)?", value)
    if not match:
        return 0

    number = float(match.group(1))
    unit = (match.group(2) or "").lower()
    if unit in {"万", "w"}:
        number *= 10_000
    elif unit == "k":
        number *= 1_000
    return int(round(number))


def _dedupe_keep_order(values: list[str]) -> list[str]:
    seen = set()
    result: list[str] = []
    for value in values:
        cleaned = str(value or "").strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
    return result


def extract_price_mentions(text: str) -> list[str]:
    """Extract lightweight price mentions from note text / OCR / transcript."""
    matches = re.findall(
        r"(?:[¥￥]\s?\d+(?:\.\d+)?(?:\s*[-~]\s*[¥￥]?\d+(?:\.\d+)?)?|\d+(?:\.\d+)?\s*(?:元|块|rmb|RMB))",
        text or "",
    )
    return _dedupe_keep_order(matches)


def infer_format_hints(title: str, content: str) -> list[str]:
    """Infer coarse note format tags from obvious title/content cues."""
    corpus = f"{title}\n{content}".lower()
    hints: list[str] = []
    rules = [
        ("checklist", ("清单", "合集", "list", "盘点")),
        ("comparison", ("对比", "vs", "不建议", "建议", "避雷")),
        ("tutorial", ("教程", "攻略", "步骤", "技巧", "怎么")),
        ("review", ("测评", "评测", "体验", "开箱", "review")),
        ("recommendation", ("推荐", "好物", "种草", "值得买")),
        ("summary", ("总结", "复盘", "经验", "记录")),
        ("vlog", ("vlog", "日常", "记录生活")),
    ]
    for hint, keywords in rules:
        if any(keyword.lower() in corpus for keyword in keywords):
            hints.append(hint)
    return hints


def extract_cta_phrases(text: str, limit: int = 5) -> list[str]:
    """Extract lightweight CTA phrases like '评论区见链接' or '记得收藏'."""
    candidates = re.split(r"[\n。！？!?\r]+", text or "")
    keywords = ("评论区", "私信", "链接", "收藏", "关注", "点赞", "转发", "蹲")
    phrases = [segment.strip() for segment in candidates if any(k in segment for k in keywords)]
    return _dedupe_keep_order(phrases)[:limit]


def extract_key_points(text: str, limit: int = 5) -> list[str]:
    """Extract concise bullet-like points from note text / OCR / transcript."""
    raw_segments = re.split(r"[\n\r]+", text or "")
    scored: list[tuple[int, int, str]] = []

    for index, segment in enumerate(raw_segments):
        cleaned = re.sub(r"\s+", " ", segment).strip(" -•·\t")
        if len(cleaned) < 8 or len(cleaned) > 120:
            continue

        score = 0
        if re.search(r"^(?:\d+[\.、)]|[•·-])", segment.strip()):
            score += 2
        if re.search(r"(建议|不建议|推荐|避雷|适合|优点|缺点|步骤|技巧|清单)", cleaned):
            score += 2
        if re.search(r"\d", cleaned):
            score += 1
        if score:
            scored.append((score, index, cleaned))

    if not scored:
        sentences = re.split(r"[。！？!?]+", text or "")
        for index, segment in enumerate(sentences):
            cleaned = re.sub(r"\s+", " ", segment).strip()
            if len(cleaned) < 10 or len(cleaned) > 80:
                continue
            if re.search(r"(建议|不建议|推荐|避雷|适合|值得|记得)", cleaned):
                scored.append((1, index, cleaned))

    scored.sort(key=lambda item: (-item[0], item[1]))
    return _dedupe_keep_order([item[2] for item in scored])[:limit]


def normalize_comment_key(username: str, text: str) -> str:
    user = re.sub(r"\s+", " ", username or "").strip().lower()
    content = re.sub(r"\s+", " ", text or "").strip()
    return f"{user}:{content[:80]}"


# ── Enums ────────────────────────────────────────────────────────

class NoteType(str, Enum):
    IMAGE = "image"
    VIDEO = "video"
    UNKNOWN = "unknown"


# ── Building Blocks ─────────────────────────────────────────────

@dataclass
class ImageInfo:
    """A single image within a note."""
    url: str = ""
    index: int = 0                   # position in carousel (0-based)
    ocr_text: str = ""               # text extracted via Apple OCR
    vision_description: str = ""     # Claude Vision description
    is_cover: bool = False           # first image = cover

    @property
    def is_complete(self) -> bool:
        return bool(self.url) and (bool(self.ocr_text) or bool(self.vision_description))


@dataclass
class VideoInfo:
    """Video content within a note."""
    url: str = ""                    # playback URL (may be blob:)
    poster_url: str = ""             # thumbnail/poster image
    duration_s: float | None = None
    source_urls: list[str] = field(default_factory=list)
    resolved_url: str = ""
    stream_type: str = ""
    download_path: str = ""
    download_error: str = ""
    transcript: str = ""             # whisper.cpp transcription
    transcript_summary: str = ""     # LLM summary of transcript
    poster_ocr: str = ""             # OCR on poster frame
    poster_description: str = ""     # Vision description of poster
    frame_paths: list[str] = field(default_factory=list)
    frame_descriptions: list[str] = field(default_factory=list)
    visual_summary: str = ""

    @staticmethod
    def _score_url(url: str) -> int:
        if not url:
            return -1
        lower = url.lower()
        if lower.startswith("blob:"):
            return 0
        if lower.startswith(("https://", "http://")):
            if ".mp4" in lower:
                return 5
            if ".m3u8" in lower:
                return 4
            if ".mov" in lower or ".m4v" in lower:
                return 3
            return 2
        return 1

    def all_source_urls(self) -> list[str]:
        return _dedupe_keep_order([self.resolved_url, self.url, *self.source_urls])

    def best_source_url(self) -> str:
        candidates = self.all_source_urls()
        if not candidates:
            return ""
        return max(candidates, key=self._score_url)

    def best_download_url(self) -> str:
        candidates = [
            candidate
            for candidate in self.all_source_urls()
            if candidate.startswith(("https://", "http://")) and not candidate.startswith("blob:")
        ]
        if not candidates:
            return ""
        return max(candidates, key=self._score_url)

    @property
    def is_complete(self) -> bool:
        has_visual = (
            bool(self.poster_description)
            or bool(self.poster_ocr)
            or bool(self.frame_descriptions)
            or bool(self.visual_summary)
        )
        has_audio = bool(self.transcript) or bool(self.transcript_summary)
        return has_visual and has_audio


@dataclass
class Comment:
    """A single comment on a note."""
    username: str = ""
    text: str = ""
    likes: str = ""
    like_count: int = 0
    is_author_reply: bool = False
    is_pinned: bool = False
    badge: str = ""
    time: str = ""
    reply_count: int = 0
    sub_comments: list[Comment] = field(default_factory=list)

    @property
    def dedupe_key(self) -> str:
        return normalize_comment_key(self.username, self.text)

    @property
    def heat_score(self) -> int:
        return self.like_count + self.reply_count * 3 + (5 if self.is_author_reply else 0) + (10 if self.is_pinned else 0)

    def merge(self, other: Comment) -> Comment:
        preferred = self if self.heat_score >= other.heat_score else other
        merged = Comment(
            username=preferred.username or self.username or other.username,
            text=max((self.text, other.text), key=len),
            likes=preferred.likes or self.likes or other.likes,
            like_count=max(self.like_count, other.like_count),
            is_author_reply=self.is_author_reply or other.is_author_reply,
            is_pinned=self.is_pinned or other.is_pinned,
            badge=self.badge or other.badge,
            time=self.time or other.time,
            reply_count=max(self.reply_count, other.reply_count),
            sub_comments=Comment.merge_many([*self.sub_comments, *other.sub_comments]),
        )
        if not merged.likes and merged.like_count:
            merged.likes = str(merged.like_count)
        if merged.reply_count < len(merged.sub_comments):
            merged.reply_count = len(merged.sub_comments)
        return merged

    @staticmethod
    def merge_many(comments: list[Comment]) -> list[Comment]:
        merged: dict[str, Comment] = {}
        order: list[str] = []
        for comment in comments:
            key = comment.dedupe_key
            if not key or key.endswith(":"):
                continue
            if key not in merged:
                merged[key] = comment
                order.append(key)
            else:
                merged[key] = merged[key].merge(comment)
        return sorted(
            [merged[key] for key in order],
            key=lambda c: (-c.heat_score, -c.like_count, not c.is_author_reply, c.time or ""),
        )

    @classmethod
    def from_dom_dict(cls, d: dict) -> Comment:
        """Convert raw DOM comment dict to Comment."""
        sub_comments = [
            Comment.from_dom_dict(sc)
            for sc in d.get("sub_comments", [])
        ]
        return cls(
            username=d.get("username", ""),
            text=d.get("text", ""),
            likes=d.get("likes", ""),
            like_count=d.get("like_count", parse_count_text(d.get("likes", ""))),
            is_author_reply=d.get("is_author_reply", False),
            is_pinned=d.get("is_pinned", False),
            badge=d.get("badge", ""),
            time=d.get("time", ""),
            reply_count=d.get("reply_count", len(sub_comments)),
            sub_comments=Comment.merge_many(sub_comments),
        )

    def to_report_dict(self) -> dict:
        return {
            "username": self.username,
            "text": self.text,
            "likes": self.likes,
            "like_count": self.like_count,
            "time": self.time,
            "is_author_reply": self.is_author_reply,
            "is_pinned": self.is_pinned,
            "reply_count": self.reply_count,
            "heat_score": self.heat_score,
            "sub_comments": [sc.to_report_dict() for sc in self.sub_comments],
        }


# ── Core Entities ───────────────────────────────────────────────

@dataclass
class NoteEntity:
    """A complete XHS note — the primary content unit.

    When the agent opens a note, it should populate ALL fields before
    moving on. Missing fields indicate incomplete extraction.

    Extraction strategy per field:
      - title, content, author_*, hashtags, date, engagement → DOM extraction
      - images → DOM gets URLs, then download each for OCR + Vision
      - video → DOM gets URL/poster, then Whisper + Vision
      - comments → DOM extraction + scroll for more
      - cover_description → Vision API on first image (overall aesthetic)
    """
    # Identity
    note_id: str = ""
    url: str = ""
    note_type: NoteType = NoteType.UNKNOWN

    # Author (inline — full author data is in AuthorEntity)
    author_name: str = ""
    author_id: str = ""
    author_avatar_url: str = ""
    author_url: str = ""

    # Text content
    title: str = ""
    content: str = ""              # full text body, preserve line breaks
    hashtags: list[str] = field(default_factory=list)
    date: str = ""
    location: str = ""
    ip_location: str = ""

    # Media
    images: list[ImageInfo] = field(default_factory=list)
    image_count: int = 0           # total images (may differ from len(images) if carousel)
    video: VideoInfo | None = None

    # Engagement
    likes: str = ""
    favorites: str = ""
    comments_count: str = ""
    shares: str = ""

    # Comments (sorted by relevance/likes)
    comments: list[Comment] = field(default_factory=list)

    # Derived / enriched
    cover_description: str = ""    # Vision API overall description
    screenshot_path: str = ""      # local path to screenshot
    format_hints: list[str] = field(default_factory=list)
    price_mentions: list[str] = field(default_factory=list)
    cta_phrases: list[str] = field(default_factory=list)
    key_points: list[str] = field(default_factory=list)
    # Source context (how this note was found)
    source_keyword: str = ""       # search keyword that led here
    source_context: str = ""       # "search", "profile", "recommendation"

    # Card-level engagement (from search results card, before opening note)
    card_likes: str = ""
    source_position: int = -1

    # Extraction metadata
    extraction_level: str = "deep"
    requested_sections: tuple[str, ...] = ("content", "media", "engagement", "comments", "author")
    applied_capabilities: list[str] = field(default_factory=list)

    @property
    def has_content(self) -> bool:
        """Minimum viability: at least title or body text extracted."""
        return bool(self.title) or bool(self.content)

    @property
    def has_media(self) -> bool:
        """Media (images or video) has been processed."""
        if self.note_type == NoteType.VIDEO:
            return self.video is not None and self.video.is_complete
        return any(img.is_complete for img in self.images)

    @property
    def has_engagement(self) -> bool:
        return bool(self.likes) or bool(self.favorites)

    @property
    def has_comments(self) -> bool:
        return len(self.comments) > 0

    @property
    def completeness(self) -> dict[str, bool]:
        """Check which aspects have been extracted."""
        raw = {
            "content": self.has_content,
            "media": self.has_media,
            "engagement": self.has_engagement,
            "comments": self.has_comments,
            "author": bool(self.author_name),
        }
        return {
            key: (value if key in self.requested_sections else True)
            for key, value in raw.items()
        }

    @property
    def completeness_score(self) -> float:
        """0.0 to 1.0 — how thoroughly this note has been extracted."""
        checks = self.completeness
        return sum(checks.values()) / len(checks)

    @staticmethod
    def sort_comments(comments: list[Comment]) -> list[Comment]:
        return sorted(
            comments,
            key=lambda c: (
                -c.heat_score,
                -c.like_count,
                not c.is_author_reply,
                c.time or "",
            ),
        )

    def hottest_comments(self, limit: int = 5) -> list[Comment]:
        return self.sort_comments(self.comments)[:limit]

    @staticmethod
    def merge_comments(comments: list[Comment]) -> list[Comment]:
        return Comment.merge_many(comments)

    def refresh_derived_fields(self) -> None:
        text_chunks = [self.title, self.content, *self.hashtags]
        text_chunks.extend(img.ocr_text for img in self.images if img.ocr_text)
        if self.video:
            text_chunks.extend([
                self.video.poster_ocr,
                self.video.visual_summary,
                self.video.transcript,
                self.video.transcript_summary,
            ])
            text_chunks.extend(self.video.frame_descriptions)
        text_corpus = "\n".join(chunk for chunk in text_chunks if chunk)
        self.format_hints = infer_format_hints(self.title, text_corpus)
        self.price_mentions = extract_price_mentions(text_corpus)
        self.cta_phrases = extract_cta_phrases(text_corpus)
        self.key_points = extract_key_points(text_corpus)

    def to_summary(self) -> dict:
        """Compact representation for LLM context (avoids token bloat)."""
        d = {
            "title": self.title,
            "author": self.author_name,
            "type": self.note_type.value,
            "likes": self.likes,
            "favorites": self.favorites,
            "content_preview": self.content[:300] if self.content else "",
            "hashtags": self.hashtags,
            "image_count": self.image_count,
            "comments_count": self.comments_count,
            "extraction_level": self.extraction_level,
            "source_position": self.source_position,
        }
        if self.cover_description:
            d["cover_description"] = self.cover_description
        if self.images:
            d["image_descriptions"] = [
                img.vision_description for img in self.images if img.vision_description
            ]
        if self.video and self.video.transcript_summary:
            d["video_summary"] = self.video.transcript_summary
        if self.video and self.video.visual_summary:
            d["video_visual_summary"] = self.video.visual_summary
        if self.comments:
            d["top_comments"] = [
                {"user": c.username, "text": c.text[:100], "likes": c.likes}
                for c in self.hottest_comments(5)
            ]
        if self.format_hints:
            d["format_hints"] = self.format_hints
        if self.price_mentions:
            d["price_mentions"] = self.price_mentions[:8]
        if self.cta_phrases:
            d["cta_phrases"] = self.cta_phrases
        if self.key_points:
            d["key_points"] = self.key_points
        return d

    @classmethod
    def from_dom_dict(cls, d: dict) -> NoteEntity:
        """Convert raw DOM extract_note_content() dict to NoteEntity.

        Only populates fields available from DOM extraction. Media processing
        (OCR, Vision, transcription) is done separately by the agent.
        """
        # Determine note type
        raw_type = d.get("type", "").lower()
        if raw_type == "video":
            note_type = NoteType.VIDEO
        elif raw_type in ("normal", "image"):
            note_type = NoteType.IMAGE
        else:
            note_type = NoteType.UNKNOWN

        # Build ImageInfo objects from image_urls
        image_urls = d.get("image_urls", [])
        images = [
            ImageInfo(
                url=url,
                index=i,
                is_cover=(i == 0),
            )
            for i, url in enumerate(image_urls)
        ]

        # Build VideoInfo if this is a video note
        video = None
        if note_type == NoteType.VIDEO:
            video_url = d.get("video_url", "")
            poster_url = d.get("poster_url", "") or (image_urls[0] if image_urls else "")
            source_urls = [v.get("url", "") for v in d.get("video_url_candidates", []) if v.get("url")]
            video = VideoInfo(
                url=video_url,
                poster_url=poster_url,
                duration_s=d.get("duration_s"),
                source_urls=source_urls,
            )

        # Build Comment objects
        comments = [
            Comment.from_dom_dict(c)
            for c in d.get("comments", [])
        ]

        note = cls(
            note_id=d.get("note_id", ""),
            url=d.get("url", ""),
            note_type=note_type,
            author_name=d.get("author", ""),
            author_id=d.get("author_id", ""),
            author_avatar_url=d.get("author_avatar_url", ""),
            author_url=d.get("author_url", ""),
            title=d.get("title", ""),
            content=d.get("content", ""),
            hashtags=d.get("hashtags", []),
            date=d.get("date", ""),
            location=d.get("location", ""),
            ip_location=d.get("ip_location", ""),
            images=images,
            image_count=d.get("image_count", len(image_urls)),
            video=video,
            likes=d.get("likes", ""),
            favorites=d.get("favorites", ""),
            comments_count=d.get("comments_count", ""),
            shares=d.get("shares", ""),
            comments=cls.merge_comments(comments),
        )
        note.refresh_derived_fields()
        return note

    def to_report_dict(self) -> dict:
        """Convert to dict for JSON report and HTML generation.

        Produces the same dict shape the existing HTML templates expect,
        so HTML generators don't need major changes.
        """
        d = {
            "note_id": self.note_id,
            "title": self.title,
            "author": self.author_name,
            "author_url": self.author_url,
            "content": self.content,
            "type": self.note_type.value,
            "hashtags": self.hashtags,
            "date": self.date,
            "location": self.location,
            "ip_location": self.ip_location,
            "likes": self.likes,
            "favorites": self.favorites,
            "comments_count": self.comments_count,
            "shares": self.shares,
            "image_count": self.image_count,
            "source_keyword": self.source_keyword,
            "source_position": self.source_position,
            "screenshot": self.screenshot_path,
            "extraction_level": self.extraction_level,
            "requested_sections": list(self.requested_sections),
            "applied_capabilities": self.applied_capabilities,
            # comments as list of dicts (existing format)
            "comments": [c.to_report_dict() for c in self.comments],
            "hot_comments": [c.to_report_dict() for c in self.hottest_comments(5)],
            # image descriptions from ImageInfo objects
            "image_descriptions": [img.vision_description for img in self.images if img.vision_description],
            # cover description
            "cover_description": self.cover_description,
            # OCR results in existing format
            "ocr_results": [{"image_index": img.index, "text": img.ocr_text} for img in self.images if img.ocr_text],
            "format_hints": self.format_hints,
            "price_mentions": self.price_mentions,
            "cta_phrases": self.cta_phrases,
            "key_points": self.key_points,
        }
        # Video-specific
        if self.video:
            d["video_url"] = self.video.url
            d["video_resolved_url"] = self.video.resolved_url
            d["video_source_urls"] = self.video.all_source_urls()
            d["video_stream_type"] = self.video.stream_type
            d["video_download_path"] = self.video.download_path
            d["video_download_error"] = self.video.download_error
            d["video_frame_paths"] = self.video.frame_paths
            d["video_frame_descriptions"] = self.video.frame_descriptions
            d["video_visual_summary"] = self.video.visual_summary
            d["transcript"] = self.video.transcript
            d["transcript_summary"] = self.video.transcript_summary
        # card_likes for user_analysis compatibility
        if self.card_likes:
            d["card_likes"] = self.card_likes
        return d


@dataclass
class NoteCard:
    """A card in search results or profile grid — lightweight preview.

    This is what you see BEFORE opening a note. Used for ranking/selection.
    """
    note_id: str = ""
    title: str = ""
    author_name: str = ""
    likes: str = ""
    note_type: NoteType = NoteType.UNKNOWN
    cover_url: str = ""
    link: str = ""
    position: int = 0             # position in grid

    @classmethod
    def from_dom_dict(cls, d: dict) -> NoteCard:
        """Convert raw DOM extraction dict to NoteCard."""
        # Map 'type' string to NoteType enum
        raw_type = d.get("type", "").lower()
        if raw_type == "video":
            note_type = NoteType.VIDEO
        elif raw_type in ("normal", "image"):
            note_type = NoteType.IMAGE
        else:
            note_type = NoteType.UNKNOWN

        return cls(
            note_id=d.get("note_id", ""),
            title=d.get("title", ""),
            author_name=d.get("author_name", d.get("author", "")),
            likes=d.get("likes", ""),
            note_type=note_type,
            cover_url=d.get("cover_url", ""),
            link=d.get("link", ""),
            position=d.get("position", 0),
        )


@dataclass
class AuthorEntity:
    """A complete XHS author/creator profile.

    Extraction strategy:
      - Profile header → DOM: name, bio, followers, following, total_likes, etc.
      - Notes grid → DOM + scroll: collect all NoteCards
      - Top notes → open each, populate NoteEntity (via CDP click)

    The profile page is a SPA — scrolling loads more note cards dynamically.
    """
    # Identity
    user_id: str = ""
    name: str = ""
    xhs_id: str = ""               # 小红书号
    avatar_url: str = ""

    # Bio / description
    bio: str = ""
    tags: list[str] = field(default_factory=list)

    # Verification
    verified: bool = False
    verify_text: str = ""

    # Stats
    followers: str = ""
    following: str = ""
    total_likes: str = ""          # 获赞与收藏

    # Content
    note_cards: list[NoteCard] = field(default_factory=list)     # all posts from grid
    detailed_notes: list[NoteEntity] = field(default_factory=list)  # top posts opened in detail

    # Derived
    profile_url: str = ""
    screenshot_path: str = ""
    content_analysis: str = ""     # LLM analysis of content strategy

    @property
    def completeness(self) -> dict[str, bool]:
        return {
            "profile_info": bool(self.name) and bool(self.followers),
            "notes_collected": len(self.note_cards) > 0,
            "notes_detailed": len(self.detailed_notes) > 0,
            "analysis": bool(self.content_analysis),
        }

    @property
    def completeness_score(self) -> float:
        checks = self.completeness
        return sum(checks.values()) / len(checks)

    @classmethod
    def from_dom_dict(cls, d: dict) -> AuthorEntity:
        """Convert raw DOM extract_profile_info() dict to AuthorEntity."""
        return cls(
            name=d.get("name", ""),
            xhs_id=d.get("xhs_id", ""),
            bio=d.get("bio", ""),
            avatar_url=d.get("avatar_url", ""),
            verified=d.get("verified", False),
            verify_text=d.get("verify_text", ""),
            followers=d.get("followers", ""),
            following=d.get("following", ""),
            total_likes=d.get("total_likes", ""),
            tags=d.get("tags", []),
        )

    def to_report_dict(self) -> dict:
        """Convert to dict for JSON report and HTML generation."""
        return {
            "name": self.name,
            "xhs_id": self.xhs_id,
            "bio": self.bio,
            "avatar_url": self.avatar_url,
            "verified": self.verified,
            "verify_text": self.verify_text,
            "followers": self.followers,
            "following": self.following,
            "total_likes": self.total_likes,
            "tags": self.tags,
            "screenshot": self.screenshot_path,
        }


@dataclass
class SearchResult:
    """A search results page on XHS.

    XHS search is keyword-based. Results are a waterfall grid of NoteCards.
    Filters: 全部 | 图文 | 视频 | 用户
    """
    query: str = ""
    active_filter: str = "全部"    # 全部, 图文, 视频, 用户
    cards: list[NoteCard] = field(default_factory=list)
    total_visible: int = 0
