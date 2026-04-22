"""Structured Xiaohongshu entities and deterministic post-processing helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


def parse_count_text(raw: str) -> int:
    """Parse social count strings like ``1.2万`` or ``3.4k`` into integers."""
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
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = str(value or "").strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
    return result


def extract_price_mentions(text: str) -> list[str]:
    matches = re.findall(
        r"(?:[¥￥]\s?\d+(?:\.\d+)?(?:\s*[-~]\s*[¥￥]?\d+(?:\.\d+)?)?|\d+(?:\.\d+)?\s*(?:元|块|rmb|RMB))",
        text or "",
    )
    return _dedupe_keep_order(matches)


def infer_format_hints(title: str, content: str) -> list[str]:
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
    candidates = re.split(r"[\n。！？!?\r]+", text or "")
    keywords = ("评论区", "私信", "链接", "收藏", "关注", "点赞", "转发", "蹲")
    phrases = [segment.strip() for segment in candidates if any(k in segment for k in keywords)]
    return _dedupe_keep_order(phrases)[:limit]


def extract_key_points(text: str, limit: int = 5) -> list[str]:
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


def is_meaningful_note_content(text: str) -> bool:
    """Reject XHS loading/status chrome that can appear before body hydration."""
    lines = [
        re.sub(r"\s+", " ", line).strip()
        for line in str(text or "").splitlines()
    ]
    lines = [line for line in lines if line]
    if not lines:
        return False
    placeholders = {
        "刚刚", "加载中", "赞", "收藏", "评论", "分享", "发送", "取消",
        "已关注", "关注", "- THE END -", "THE END",
    }
    meaningful = [
        line for line in lines
        if line not in placeholders
        and not re.fullmatch(r"共\s*\d*\s*条评论", line)
        and not re.fullmatch(r"\d+\s*(?:秒|分钟|小时|天)前|昨天|前天", line)
    ]
    if not meaningful:
        return False
    return len("\n".join(meaningful).strip()) >= 8


def normalize_comment_key(username: str, text: str) -> str:
    user = re.sub(r"\s+", " ", username or "").strip().lower()
    content = re.sub(r"\s+", " ", text or "").strip()
    return f"{user}:{content[:80]}"


class NoteType(str, Enum):
    IMAGE = "image"
    VIDEO = "video"
    UNKNOWN = "unknown"


@dataclass
class ImageInfo:
    url: str = ""
    index: int = 0
    ocr_text: str = ""
    vision_description: str = ""
    is_cover: bool = False
    local_path: str = ""

    @property
    def is_complete(self) -> bool:
        return bool(self.url) and (bool(self.ocr_text) or bool(self.vision_description))

    def to_tool_dict(self) -> dict:
        return {
            "url": self.url,
            "index": self.index,
            "is_cover": self.is_cover,
            "ocr_text": self.ocr_text[:400],
            "vision_description": self.vision_description,
            "local_path": self.local_path,
        }


@dataclass
class VideoInfo:
    url: str = ""
    poster_url: str = ""
    duration_s: float | None = None
    source_urls: list[str] = field(default_factory=list)
    resolved_url: str = ""
    stream_type: str = ""
    download_path: str = ""
    download_error: str = ""
    transcript: str = ""
    transcript_summary: str = ""
    poster_ocr: str = ""
    poster_description: str = ""
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

    def to_tool_dict(self) -> dict:
        return {
            "duration_s": self.duration_s,
            "resolved_url": self.resolved_url or self.best_source_url(),
            "stream_type": self.stream_type,
            "download_error": self.download_error,
            "poster_description": self.poster_description,
            "poster_ocr": self.poster_ocr[:400],
            "transcript_summary": self.transcript_summary,
            "transcript_excerpt": self.transcript[:1200],
            "visual_summary": self.visual_summary,
            "frame_descriptions": self.frame_descriptions[:6],
            "download_path": self.download_path,
        }


@dataclass
class Comment:
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

    def to_tool_dict(self) -> dict:
        return {
            "username": self.username,
            "text": self.text[:240],
            "likes": self.likes,
            "like_count": self.like_count,
            "time": self.time,
            "is_author_reply": self.is_author_reply,
            "is_pinned": self.is_pinned,
            "reply_count": self.reply_count,
            "heat_score": self.heat_score,
            "sub_comments": [sc.to_tool_dict() for sc in self.sub_comments[:3]],
        }


@dataclass
class NoteEntity:
    note_id: str = ""
    url: str = ""
    note_type: NoteType = NoteType.UNKNOWN
    author_name: str = ""
    author_id: str = ""
    author_avatar_url: str = ""
    author_url: str = ""
    title: str = ""
    content: str = ""
    hashtags: list[str] = field(default_factory=list)
    date: str = ""
    location: str = ""
    ip_location: str = ""
    images: list[ImageInfo] = field(default_factory=list)
    image_count: int = 0
    video: VideoInfo | None = None
    likes: str = ""
    favorites: str = ""
    comments_count: str = ""
    shares: str = ""
    comments: list[Comment] = field(default_factory=list)
    cover_description: str = ""
    screenshot_path: str = ""
    format_hints: list[str] = field(default_factory=list)
    price_mentions: list[str] = field(default_factory=list)
    cta_phrases: list[str] = field(default_factory=list)
    key_points: list[str] = field(default_factory=list)
    media_key_points: list[str] = field(default_factory=list)
    source_keyword: str = ""
    source_context: str = ""
    card_likes: str = ""
    source_position: int = -1
    extraction_level: str = "deep"
    requested_sections: tuple[str, ...] = ("content", "media", "engagement", "comments", "author")
    applied_capabilities: list[str] = field(default_factory=list)
    extraction_debug: dict = field(default_factory=dict)
    stale_warning: str = ""

    @property
    def has_content(self) -> bool:
        return is_meaningful_note_content(self.content)

    @property
    def has_media(self) -> bool:
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
        checks = self.completeness
        return sum(checks.values()) / len(checks)

    @staticmethod
    def sort_comments(comments: list[Comment]) -> list[Comment]:
        return sorted(
            comments,
            key=lambda c: (-c.heat_score, -c.like_count, not c.is_author_reply, c.time or ""),
        )

    def hottest_comments(self, limit: int = 5) -> list[Comment]:
        return self.sort_comments(self.comments)[:limit]

    @staticmethod
    def merge_comments(comments: list[Comment]) -> list[Comment]:
        return Comment.merge_many(comments)

    def refresh_derived_fields(self) -> None:
        body_chunks = [self.title, self.content, *self.hashtags]
        media_chunks = [img.ocr_text for img in self.images if img.ocr_text]
        if self.video:
            media_chunks.extend([
                self.video.poster_ocr,
                self.video.visual_summary,
                self.video.transcript,
                self.video.transcript_summary,
            ])
            media_chunks.extend(self.video.frame_descriptions)
        text_corpus = "\n".join(chunk for chunk in body_chunks if chunk)
        media_corpus = "\n".join(chunk for chunk in media_chunks if chunk)
        self.format_hints = infer_format_hints(self.title, text_corpus)
        self.price_mentions = extract_price_mentions("\n".join([text_corpus, media_corpus]))
        self.cta_phrases = extract_cta_phrases(text_corpus)
        self.key_points = extract_key_points(text_corpus)
        self.media_key_points = extract_key_points(media_corpus)

    @classmethod
    def from_dom_dict(cls, d: dict) -> NoteEntity:
        raw_type = d.get("type", "").lower()
        if raw_type == "video":
            note_type = NoteType.VIDEO
        elif raw_type in ("normal", "image"):
            note_type = NoteType.IMAGE
        else:
            note_type = NoteType.UNKNOWN

        image_urls = d.get("image_urls", [])
        images = [
            ImageInfo(url=url, index=i, is_cover=(i == 0))
            for i, url in enumerate(image_urls)
        ]

        video = None
        if note_type == NoteType.VIDEO:
            video = VideoInfo(
                url=d.get("video_url", ""),
                poster_url=d.get("poster_url", "") or (image_urls[0] if image_urls else ""),
                duration_s=d.get("duration_s"),
                source_urls=[v.get("url", "") for v in d.get("video_url_candidates", []) if v.get("url")],
            )

        note = cls(
            note_id=d.get("note_id", ""),
            url=d.get("url", ""),
            note_type=note_type,
            author_name=d.get("author", ""),
            author_id=d.get("author_id", ""),
            author_avatar_url=d.get("author_avatar_url", ""),
            author_url=d.get("author_url", ""),
            title=d.get("title", ""),
            content=d.get("content", "") if is_meaningful_note_content(d.get("content", "")) else "",
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
            extraction_debug=d.get("extraction_debug", {}) if isinstance(d.get("extraction_debug"), dict) else {},
            stale_warning=str(d.get("_stale_warning", "") or ""),
        )
        if note.note_id:
            raw_url = str(note.url or "").strip()
            if not re.search(r"xiaohongshu\.com/(?:explore|discovery(?:/item)?)/", raw_url):
                note.url = f"https://www.xiaohongshu.com/explore/{note.note_id}"
        note.refresh_derived_fields()
        return note

    def to_tool_dict(self) -> dict:
        payload = {
            "note_id": self.note_id,
            "url": self.url,
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
            "likes_value": parse_count_text(self.likes),
            "favorites": self.favorites,
            "favorites_value": parse_count_text(self.favorites),
            "comments_count": self.comments_count,
            "comments_count_value": parse_count_text(self.comments_count),
            "shares": self.shares,
            "image_count": self.image_count,
            "cover_description": self.cover_description,
            "format_hints": self.format_hints,
            "price_mentions": self.price_mentions[:8],
            "cta_phrases": self.cta_phrases,
            "key_points": self.key_points,
            "media_key_points": self.media_key_points,
            "content_source": self.extraction_debug.get("content_source", ""),
            "screenshot": self.screenshot_path,
            "completeness": self.completeness,
            "completeness_score": round(self.completeness_score, 3),
            "applied_capabilities": self.applied_capabilities,
            "images": [img.to_tool_dict() for img in self.images[:8]],
            "top_comments": [c.to_tool_dict() for c in self.hottest_comments(8)],
        }
        if self.extraction_debug:
            payload["extraction_debug"] = self.extraction_debug
        if self.stale_warning:
            payload["stale_warning"] = self.stale_warning
        if self.video:
            payload["video"] = self.video.to_tool_dict()
        return payload


@dataclass
class NoteCard:
    note_id: str = ""
    title: str = ""
    author_name: str = ""
    likes: str = ""
    note_type: NoteType = NoteType.UNKNOWN
    cover_url: str = ""
    link: str = ""
    position: int = 0

    @classmethod
    def from_dom_dict(cls, d: dict) -> NoteCard:
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

    def to_tool_dict(self) -> dict:
        return {
            "note_id": self.note_id,
            "title": self.title,
            "author": self.author_name,
            "likes": self.likes,
            "likes_value": parse_count_text(self.likes),
            "type": self.note_type.value,
            "cover_url": self.cover_url,
            "link": self.link,
            "position": self.position,
        }


@dataclass
class AuthorEntity:
    user_id: str = ""
    name: str = ""
    xhs_id: str = ""
    avatar_url: str = ""
    bio: str = ""
    tags: list[str] = field(default_factory=list)
    verified: bool = False
    verify_text: str = ""
    followers: str = ""
    following: str = ""
    total_likes: str = ""
    note_cards: list[NoteCard] = field(default_factory=list)
    profile_url: str = ""
    screenshot_path: str = ""

    @classmethod
    def from_dom_dict(cls, d: dict) -> AuthorEntity:
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

    def to_tool_dict(self) -> dict:
        return {
            "name": self.name,
            "xhs_id": self.xhs_id,
            "bio": self.bio,
            "avatar_url": self.avatar_url,
            "verified": self.verified,
            "verify_text": self.verify_text,
            "followers": self.followers,
            "followers_value": parse_count_text(self.followers),
            "following": self.following,
            "following_value": parse_count_text(self.following),
            "total_likes": self.total_likes,
            "total_likes_value": parse_count_text(self.total_likes),
            "tags": self.tags,
            "profile_url": self.profile_url,
            "screenshot": self.screenshot_path,
            "note_cards": [card.to_tool_dict() for card in self.note_cards[:20]],
        }
