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

from dataclasses import dataclass, field
from enum import Enum


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
    transcript: str = ""             # whisper.cpp transcription
    transcript_summary: str = ""     # LLM summary of transcript
    poster_ocr: str = ""             # OCR on poster frame
    poster_description: str = ""     # Vision description of poster

    @property
    def is_complete(self) -> bool:
        has_visual = bool(self.poster_description) or bool(self.poster_ocr)
        has_audio = bool(self.transcript) or not self.url  # blob: URLs can't be transcribed
        return has_visual and has_audio


@dataclass
class Comment:
    """A single comment on a note."""
    username: str = ""
    text: str = ""
    likes: str = ""
    is_author_reply: bool = False
    time: str = ""
    sub_comments: list[Comment] = field(default_factory=list)

    @classmethod
    def from_dom_dict(cls, d: dict) -> Comment:
        """Convert raw DOM comment dict to Comment."""
        return cls(
            username=d.get("username", ""),
            text=d.get("text", ""),
            likes=d.get("likes", ""),
            is_author_reply=d.get("is_author_reply", False),
            time=d.get("time", ""),
            sub_comments=[
                Comment.from_dom_dict(sc)
                for sc in d.get("sub_comments", [])
            ],
        )


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

    # Text content
    title: str = ""
    content: str = ""              # full text body, preserve line breaks
    hashtags: list[str] = field(default_factory=list)
    date: str = ""

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

    # Source context (how this note was found)
    source_keyword: str = ""       # search keyword that led here
    source_context: str = ""       # "search", "profile", "recommendation"

    # Card-level engagement (from search results card, before opening note)
    card_likes: str = ""

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
        return {
            "content": self.has_content,
            "media": self.has_media,
            "engagement": self.has_engagement,
            "comments": self.has_comments,
            "author": bool(self.author_name),
        }

    @property
    def completeness_score(self) -> float:
        """0.0 to 1.0 — how thoroughly this note has been extracted."""
        checks = self.completeness
        return sum(checks.values()) / len(checks)

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
        }
        if self.cover_description:
            d["cover_description"] = self.cover_description
        if self.images:
            d["image_descriptions"] = [
                img.vision_description for img in self.images if img.vision_description
            ]
        if self.video and self.video.transcript_summary:
            d["video_summary"] = self.video.transcript_summary
        if self.comments:
            d["top_comments"] = [
                {"user": c.username, "text": c.text[:100], "likes": c.likes}
                for c in self.comments[:5]
            ]
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
            poster_url = image_urls[0] if image_urls else ""
            video = VideoInfo(url=video_url, poster_url=poster_url)

        # Build Comment objects
        comments = [
            Comment.from_dom_dict(c)
            for c in d.get("comments", [])
        ]

        return cls(
            note_id=d.get("note_id", ""),
            url=d.get("url", ""),
            note_type=note_type,
            author_name=d.get("author", ""),
            author_id=d.get("author_id", ""),
            author_avatar_url=d.get("author_avatar_url", ""),
            title=d.get("title", ""),
            content=d.get("content", ""),
            hashtags=d.get("hashtags", []),
            date=d.get("date", ""),
            images=images,
            image_count=d.get("image_count", len(image_urls)),
            video=video,
            likes=d.get("likes", ""),
            favorites=d.get("favorites", ""),
            comments_count=d.get("comments_count", ""),
            shares=d.get("shares", ""),
            comments=comments,
        )

    def to_report_dict(self) -> dict:
        """Convert to dict for JSON report and HTML generation.

        Produces the same dict shape the existing HTML templates expect,
        so HTML generators don't need major changes.
        """
        d = {
            "note_id": self.note_id,
            "title": self.title,
            "author": self.author_name,
            "content": self.content,
            "type": self.note_type.value,
            "hashtags": self.hashtags,
            "date": self.date,
            "likes": self.likes,
            "favorites": self.favorites,
            "comments_count": self.comments_count,
            "shares": self.shares,
            "image_count": self.image_count,
            "source_keyword": self.source_keyword,
            "screenshot": self.screenshot_path,
            # comments as list of dicts (existing format)
            "comments": [{"username": c.username, "text": c.text, "likes": c.likes} for c in self.comments],
            # image descriptions from ImageInfo objects
            "image_descriptions": [img.vision_description for img in self.images if img.vision_description],
            # cover description
            "cover_description": self.cover_description,
            # OCR results in existing format
            "ocr_results": [{"image_index": img.index, "text": img.ocr_text} for img in self.images if img.ocr_text],
        }
        # Video-specific
        if self.video:
            d["video_url"] = self.video.url
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
