"""Capability registry for Xiaohongshu entity extraction."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum

from .spec import load_xhs_spec


class CapabilityCost(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ExtractionLevel(StrEnum):
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
    level: ExtractionLevel
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
    requested_sections: tuple[str, ...] = ("content", "engagement", "comments", "author")

    def to_dict(self) -> dict:
        data = asdict(self)
        data["level"] = self.level.value
        data["cost"] = self.cost.value
        data["requested_sections"] = list(self.requested_sections)
        return data


def _spec_capabilities() -> list[dict]:
    return list(load_xhs_spec().get("capabilities", []))


def _spec_plan(level: ExtractionLevel) -> dict:
    plans = load_xhs_spec().get("plans", {})
    return dict(plans.get(level.value, {}))


def capability_catalog() -> list[CapabilitySpec]:
    catalog: list[CapabilitySpec] = []
    for item in _spec_capabilities():
        catalog.append(
            CapabilitySpec(
                name=str(item["name"]),
                goal=str(item.get("goal", "")),
                fills_fields=[str(field) for field in item.get("fills_fields", [])],
                estimated_latency_s=tuple(int(v) for v in item.get("estimated_latency_s", [0, 0])),
                cost=CapabilityCost(str(item.get("cost", CapabilityCost.LOW.value))),
                notes=str(item.get("notes", "")),
            )
        )
    return catalog


def _plan_from_spec(level: ExtractionLevel) -> NoteExtractionPlan:
    item = _spec_plan(level)
    return NoteExtractionPlan(
        level=level,
        name=str(item.get("name", f"xhs.note.{level.value}")),
        description=str(item.get("description", "")),
        capabilities=[str(cap) for cap in item.get("capabilities", [])],
        fills_fields=[str(field) for field in item.get("fills_fields", [])],
        estimated_latency_s=tuple(int(v) for v in item.get("estimated_latency_s", [0, 0])),
        cost=CapabilityCost(str(item.get("cost", CapabilityCost.LOW.value))),
        include_comments=bool(item.get("include_comments", True)),
        max_comments=int(item.get("max_comments", 4)),
        max_comment_scrolls=int(item.get("max_comment_scrolls", 0)),
        use_media=bool(item.get("use_media", False)),
        use_image_ocr=bool(item.get("use_image_ocr", False)),
        use_image_vision=bool(item.get("use_image_vision", False)),
        use_video_transcript=bool(item.get("use_video_transcript", False)),
        use_video_frames=bool(item.get("use_video_frames", False)),
        max_images=int(item.get("max_images", 4)),
        max_video_frames=int(item.get("max_video_frames", 3)),
        requested_sections=tuple(str(v) for v in item.get("requested_sections", [])),
    )


def card_note_plan() -> NoteExtractionPlan:
    return _plan_from_spec(ExtractionLevel.CARD)


def lite_note_plan(*, max_comments: int = 4, max_comment_scrolls: int = 0) -> NoteExtractionPlan:
    plan = _plan_from_spec(ExtractionLevel.LITE)
    plan.max_comments = max_comments
    plan.max_comment_scrolls = max_comment_scrolls
    return plan


def deep_note_plan(
    *,
    max_comments: int = 12,
    max_comment_scrolls: int = 2,
    max_images: int = 6,
    max_video_frames: int = 4,
) -> NoteExtractionPlan:
    plan = _plan_from_spec(ExtractionLevel.DEEP)
    plan.max_comments = max_comments
    plan.max_comment_scrolls = max_comment_scrolls
    plan.max_images = max_images
    plan.max_video_frames = max_video_frames
    return plan


def plan_for_level(
    level: str,
    *,
    max_comments: int = 4,
    max_comment_scrolls: int = 0,
    max_images: int = 6,
    max_video_frames: int = 4,
    include_comments: bool | None = None,
    include_media: bool | None = None,
) -> NoteExtractionPlan:
    normalized = str(level or ExtractionLevel.LITE.value).strip().lower()
    if normalized == ExtractionLevel.CARD.value:
        plan = card_note_plan()
    elif normalized == ExtractionLevel.DEEP.value:
        plan = deep_note_plan(
            max_comments=max_comments,
            max_comment_scrolls=max_comment_scrolls,
            max_images=max_images,
            max_video_frames=max_video_frames,
        )
    else:
        plan = lite_note_plan(
            max_comments=max_comments,
            max_comment_scrolls=max_comment_scrolls,
        )

    if include_comments is not None:
        plan.include_comments = include_comments
    if include_media is not None:
        plan.use_media = include_media
        if not include_media:
            plan.use_image_ocr = False
            plan.use_image_vision = False
            plan.use_video_transcript = False
            plan.use_video_frames = False

    return plan


def capability_catalog_markdown() -> str:
    rows = [
        "| Capability | Cost | Latency (s) | Goal |",
        "|---|---|---|---|",
    ]
    for cap in capability_catalog():
        rows.append(
            f"| `{cap.name}` | {cap.cost.value} | {cap.estimated_latency_s[0]}-{cap.estimated_latency_s[1]} | {cap.goal} |"
        )
    return "\n".join(rows)

