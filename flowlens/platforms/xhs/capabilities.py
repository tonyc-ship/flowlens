"""XHS capability registry and extraction plans.

Separates:
  - entity schema: what fields exist
  - capability/plan: how much work we spend to fill those fields
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum


class CapabilityCost(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class NoteExtractionLevel(StrEnum):
    CARD = "card"
    LITE = "lite"
    DEEP = "deep"


@dataclass(frozen=True)
class CapabilitySpec:
    name: str
    goal: str
    fills_fields: list[str]
    estimated_latency_s: tuple[int, int]
    cost: CapabilityCost
    notes: str = ""

    def to_dict(self) -> dict:
        data = asdict(self)
        data["cost"] = self.cost.value
        return data


@dataclass
class NoteExtractionPlan:
    level: NoteExtractionLevel
    name: str
    description: str
    capabilities: list[str]
    fills_fields: list[str]
    estimated_latency_s: tuple[int, int]
    cost: CapabilityCost
    include_comments: bool = True
    max_comments: int = 4
    max_comment_scrolls: int = 0
    use_media: bool = False
    use_image_ocr: bool = False
    use_image_vision: bool = False
    use_video_transcript: bool = False
    use_video_frames: bool = False
    max_images: int = 4
    max_video_frames: int = 3
    use_vision_fallback: bool = False
    requested_sections: tuple[str, ...] = ("content", "engagement", "comments", "author")

    def to_dict(self) -> dict:
        data = asdict(self)
        data["level"] = self.level.value
        data["cost"] = self.cost.value
        data["requested_sections"] = list(self.requested_sections)
        return data


def capability_catalog() -> list[CapabilitySpec]:
    return [
        CapabilitySpec(
            name="xhs.search.collect_cards",
            goal="Collect visible search result cards across scroll rounds.",
            fills_fields=["card.title", "card.author", "card.likes", "card.type", "card.link"],
            estimated_latency_s=(2, 8),
            cost=CapabilityCost.LOW,
            notes="Cheap wide scan; no note opening.",
        ),
        CapabilitySpec(
            name="xhs.profile.collect_cards",
            goal="Collect profile note cards across the creator grid.",
            fills_fields=["card.title", "card.likes", "card.type", "card.position"],
            estimated_latency_s=(4, 15),
            cost=CapabilityCost.LOW,
            notes="Cheap timeline / inventory scan.",
        ),
        CapabilitySpec(
            name="xhs.note.open_basic",
            goal="Open a note and extract DOM-visible basics.",
            fills_fields=[
                "note.title",
                "note.author",
                "note.content",
                "note.date",
                "note.location",
                "note.ip_location",
                "note.likes",
                "note.favorites",
                "note.comments_count",
                "note.type",
            ],
            estimated_latency_s=(3, 8),
            cost=CapabilityCost.LOW,
            notes="Best for breadth-first scanning.",
        ),
        CapabilitySpec(
            name="xhs.note.sample_comments",
            goal="Collect a small hot-comment sample from an opened note.",
            fills_fields=["note.comments[*].text", "note.comments[*].like_count"],
            estimated_latency_s=(2, 8),
            cost=CapabilityCost.LOW,
            notes="Useful for semantic signal without full deep dive.",
        ),
        CapabilitySpec(
            name="xhs.note.image_understanding",
            goal="Download note images and run OCR + vision.",
            fills_fields=["note.cover_description", "note.images[*].ocr_text", "note.images[*].vision_description"],
            estimated_latency_s=(8, 30),
            cost=CapabilityCost.MEDIUM,
            notes="Helpful for image-heavy and tutorial posts.",
        ),
        CapabilitySpec(
            name="xhs.note.video_audio",
            goal="Resolve video URL, download/transcribe audio, summarize transcript.",
            fills_fields=["note.video.transcript", "note.video.transcript_summary"],
            estimated_latency_s=(15, 70),
            cost=CapabilityCost.HIGH,
            notes="High value when spoken content matters.",
        ),
        CapabilitySpec(
            name="xhs.note.video_visual",
            goal="Extract video frames and summarize visual content.",
            fills_fields=["note.video.frame_descriptions", "note.video.visual_summary"],
            estimated_latency_s=(10, 45),
            cost=CapabilityCost.HIGH,
            notes="Useful for visually driven videos.",
        ),
    ]


def lite_note_plan(*, max_comments: int = 4, max_comment_scrolls: int = 0) -> NoteExtractionPlan:
    return NoteExtractionPlan(
        level=NoteExtractionLevel.LITE,
        name="xhs.note.lite_read",
        description="Open a note, capture basic fields, and sample a few hot comments. Skip heavy media work.",
        capabilities=["xhs.note.open_basic", "xhs.note.sample_comments"],
        fills_fields=[
            "title",
            "author",
            "content",
            "date",
            "location",
            "ip_location",
            "likes",
            "favorites",
            "comments_count",
            "hashtags",
            "comments",
        ],
        estimated_latency_s=(5, 14),
        cost=CapabilityCost.LOW,
        include_comments=True,
        max_comments=max_comments,
        max_comment_scrolls=max_comment_scrolls,
        use_media=False,
        use_vision_fallback=True,
        requested_sections=("content", "engagement", "comments", "author"),
    )


def deep_note_plan(
    *,
    max_comments: int = 12,
    max_comment_scrolls: int = 2,
    max_images: int = 6,
    max_video_frames: int = 4,
) -> NoteExtractionPlan:
    return NoteExtractionPlan(
        level=NoteExtractionLevel.DEEP,
        name="xhs.note.deep_read",
        description="Open a note, sample comments, and run full multimodal understanding.",
        capabilities=[
            "xhs.note.open_basic",
            "xhs.note.sample_comments",
            "xhs.note.image_understanding",
            "xhs.note.video_audio",
            "xhs.note.video_visual",
        ],
        fills_fields=[
            "title",
            "author",
            "content",
            "date",
            "location",
            "ip_location",
            "likes",
            "favorites",
            "comments_count",
            "hashtags",
            "comments",
            "cover_description",
            "image_descriptions",
            "ocr_results",
            "transcript",
            "transcript_summary",
            "video_visual_summary",
            "video_frame_descriptions",
        ],
        estimated_latency_s=(18, 90),
        cost=CapabilityCost.HIGH,
        include_comments=True,
        max_comments=max_comments,
        max_comment_scrolls=max_comment_scrolls,
        use_media=True,
        use_image_ocr=True,
        use_image_vision=True,
        use_video_transcript=True,
        use_video_frames=True,
        max_images=max_images,
        max_video_frames=max_video_frames,
        use_vision_fallback=True,
        requested_sections=("content", "engagement", "comments", "author", "media"),
    )


def capabilities_for_task(kind: str) -> list[CapabilitySpec]:
    # Current v1 optimization keeps a shared catalog and uses only a subset per workflow.
    return capability_catalog()


def capability_catalog_markdown(kind: str) -> str:
    rows = [
        "| Capability | Cost | Latency (s) | What it fills | Notes |",
        "| --- | --- | --- | --- | --- |",
    ]
    for cap in capabilities_for_task(kind):
        rows.append(
            f"| `{cap.name}` | {cap.cost.value} | {cap.estimated_latency_s[0]}-{cap.estimated_latency_s[1]} "
            f"| {', '.join(cap.fills_fields[:4])}{'...' if len(cap.fills_fields) > 4 else ''} "
            f"| {cap.notes} |"
        )
    return "\n".join(rows)
