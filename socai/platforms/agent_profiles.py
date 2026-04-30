"""Registry for site-specific agent behavior profiles."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .xhs import agent_profile as xhs_profile


@dataclass(frozen=True)
class AgentSiteProfile:
    site_name: str
    default_start_url: str | None
    state_command: str | None
    task_matches: Callable[[str], bool]
    dynamic_extra_instructions: Callable[[str, str | None, str | None], str]
    active_tool_names: Callable[[str | None, bool], set[str] | None]
    postprocess_report: Callable[[str, list[dict]], str]


_PROFILES: dict[str, AgentSiteProfile] = {
    xhs_profile.SITE_NAME: AgentSiteProfile(
        site_name=xhs_profile.SITE_NAME,
        default_start_url=xhs_profile.DEFAULT_START_URL,
        state_command=xhs_profile.STATE_COMMAND,
        task_matches=xhs_profile.task_matches,
        dynamic_extra_instructions=xhs_profile.dynamic_extra_instructions,
        active_tool_names=lambda page_state, manual_allowed: xhs_profile.active_tool_names(
            page_state,
            manual_allowed=manual_allowed,
        ),
        postprocess_report=xhs_profile.append_note_screenshot_index,
    ),
}


def profile_for_site(site_name: str | None) -> AgentSiteProfile | None:
    return _PROFILES.get(str(site_name or "").strip().lower())


def profile_for_task(task: str) -> AgentSiteProfile | None:
    for profile in _PROFILES.values():
        if profile.task_matches(task):
            return profile
    return None


def default_start_url_for_task(task: str) -> str | None:
    profile = profile_for_task(task)
    return profile.default_start_url if profile else None


def state_command_for_site(site_name: str | None) -> str | None:
    profile = profile_for_site(site_name)
    return profile.state_command if profile else None


def dynamic_extra_instructions(task: str, site_name: str | None, page_state: str | None) -> str:
    profile = profile_for_site(site_name) or profile_for_task(task)
    if not profile:
        return ""
    return profile.dynamic_extra_instructions(task, site_name, page_state)


def active_tool_names(site_name: str | None, page_state: str | None, *, manual_allowed: bool) -> set[str] | None:
    profile = profile_for_site(site_name)
    if not profile:
        return None
    return profile.active_tool_names(page_state, manual_allowed)


def append_report_extras(report: str, site_results: list[dict]) -> str:
    updated = report
    for profile in _PROFILES.values():
        updated = profile.postprocess_report(updated, site_results)
    return updated
