"""High-level capability-pack catalog for the unified SocAI agent."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CapabilityPackSpec:
    pack_id: str
    title: str
    summary: str
    when_to_use: str
    details: str
    dependencies: tuple[str, ...] = ()
    requires_browser: bool = False
    requires_desktop: bool = False


CAPABILITY_PACKS: dict[str, CapabilityPackSpec] = {
    "browser_generic": CapabilityPackSpec(
        pack_id="browser_generic",
        title="Generic Browser",
        summary="Control Chrome for arbitrary websites with navigation, DOM reads, typing, clicking, scrolling, and screenshots.",
        when_to_use="Use for any website when no site-specific pack is a better fit.",
        details=(
            "This pack gives the agent low-level browser control. It is broad, but less reliable than a "
            "site-specific pack when one exists."
        ),
        requires_browser=True,
    ),
    "desktop_generic": CapabilityPackSpec(
        pack_id="desktop_generic",
        title="Generic Desktop",
        summary="Inspect and control visible macOS app windows with screenshots, focus, clicks, typing, keys, scroll, and OCR text targeting.",
        when_to_use="Use for native macOS apps or when browser automation is not the right surface.",
        details=(
            "This is the general computer-use layer for desktop apps. It works across programs, not only WeChat, "
            "and exposes window-level primitives."
        ),
        requires_desktop=True,
    ),
    "xiaohongshu": CapabilityPackSpec(
        pack_id="xiaohongshu",
        title="Xiaohongshu",
        summary="Use Xiaohongshu-specific browser macros for topic scans, note reads, creator extraction, and faster site workflows.",
        when_to_use="Use when the task is explicitly about Xiaohongshu / 小红书.",
        details=(
            "This pack depends on browser control and exposes higher-level Xiaohongshu tools that are faster and "
            "more reliable than generic browser clicking."
        ),
        dependencies=("browser_generic",),
        requires_browser=True,
    ),
    "wechat": CapabilityPackSpec(
        pack_id="wechat",
        title="WeChat Desktop",
        summary="Use WeChat desktop helpers to open a conversation, capture visible chat content, scroll history, and collect structured chat history.",
        when_to_use="Use when the task refers to WeChat conversations, groups, chat history, or the macOS WeChat app.",
        details=(
            "This pack depends on generic desktop control and adds WeChat-specific conversation operations and "
            "structured chat parsing."
        ),
        dependencies=("desktop_generic",),
        requires_desktop=True,
    ),
}


def capability_pack_spec(pack_id: str) -> CapabilityPackSpec | None:
    return CAPABILITY_PACKS.get(str(pack_id or "").strip())


def dependency_closure(pack_ids: set[str] | list[str] | tuple[str, ...]) -> list[str]:
    resolved: list[str] = []
    seen: set[str] = set()

    def add(pack_id: str) -> None:
        spec = capability_pack_spec(pack_id)
        if spec is None or spec.pack_id in seen:
            return
        for dependency in spec.dependencies:
            add(dependency)
        seen.add(spec.pack_id)
        resolved.append(spec.pack_id)

    for item in pack_ids:
        add(str(item or "").strip())
    return resolved


def pack_available(spec: CapabilityPackSpec, *, browser_available: bool, desktop_available: bool) -> bool:
    if spec.requires_browser and not browser_available:
        return False
    if spec.requires_desktop and not desktop_available:
        return False
    return True


def pack_unavailable_reason(
    spec: CapabilityPackSpec,
    *,
    browser_available: bool,
    desktop_available: bool,
) -> str:
    if spec.requires_browser and not browser_available:
        return "Browser bridge is not available for this run."
    if spec.requires_desktop and not desktop_available:
        return "Desktop control is not available for this run."
    return ""
