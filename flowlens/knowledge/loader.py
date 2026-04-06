"""Load site knowledge from YAML files into agent context."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

import yaml


_SITES_DIR = Path(__file__).parent / "sites"

# Lazily built from YAML files — populated on first call to detect_site()
_domain_map: dict[str, str] | None = None


def _build_domain_map() -> dict[str, str]:
    """Scan all YAML files in sites/ and build a domain → filename mapping."""
    mapping: dict[str, str] = {}
    for path in _SITES_DIR.glob("*.yaml"):
        if path.stem.startswith("_"):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            site = data.get("site", {})
            domain = site.get("domain", "")
            if domain:
                mapping[domain] = path.stem
            # Also support aliases
            for alias in site.get("domain_aliases", []):
                mapping[alias] = path.stem
        except Exception:
            continue
    return mapping


def _get_domain_map() -> dict[str, str]:
    global _domain_map
    if _domain_map is None:
        _domain_map = _build_domain_map()
    return _domain_map


def detect_site(url: str) -> str | None:
    """Match a URL to a known site name by checking domain against YAML definitions."""
    domain_map = _get_domain_map()
    for domain, site_name in domain_map.items():
        if domain in url:
            return site_name
    return None


def load_site_knowledge(site_name: str) -> dict | None:
    """Load the YAML knowledge file for a site."""
    path = _SITES_DIR / f"{site_name}.yaml"
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def format_knowledge_for_prompt(knowledge: dict) -> str:
    """Format site knowledge into a text block for the system prompt."""
    lines = []
    site = knowledge.get("site", {})
    lines.append(f"## Site: {site.get('name', 'Unknown')}")
    if site.get("description"):
        lines.append(site["description"])
    lines.append("")

    # Anti-bot rules
    anti_bot = knowledge.get("anti_bot", {})
    if anti_bot:
        lines.append("### Anti-Bot Rules (IMPORTANT)")
        for rule in anti_bot.get("rules", []):
            lines.append(f"- **Signal:** {rule.get('signal', '')}")
            lines.append(f"  **Action:** {rule.get('action', '')}")
        if anti_bot.get("general_advice"):
            lines.append(f"\nGeneral advice: {anti_bot['general_advice']}")
        lines.append("")

    # Page types
    pages = knowledge.get("pages", {})
    if pages:
        lines.append("### Page Types")
        for page_key, page in pages.items():
            lines.append(f"\n**{page.get('name', page_key)}**")
            if page.get("url_pattern"):
                lines.append(f"URL pattern: `{page['url_pattern']}`")
            if page.get("description"):
                lines.append(page["description"])
            if page.get("navigation_tips"):
                lines.append("Tips:")
                for tip in page["navigation_tips"]:
                    lines.append(f"  - {tip}")
            if page.get("available_extractors"):
                lines.append(f"Extension extractors: {', '.join(page['available_extractors'])}")
        lines.append("")

    # Entities
    entities = knowledge.get("entities", {})
    if entities:
        lines.append("### Data Entities")
        for ent_key, ent in entities.items():
            lines.append(f"\n**{ent.get('name', ent_key)}**")
            if ent.get("description"):
                lines.append(ent["description"])
            if ent.get("key_fields"):
                lines.append("Key fields: " + ", ".join(ent["key_fields"]))
        lines.append("")

    # Reporting guidelines
    reporting = knowledge.get("reporting", {})
    if reporting:
        lines.append("### Reporting Guidelines (IMPORTANT)")
        if reporting.get("guidelines"):
            lines.append(reporting["guidelines"])
        lines.append("")

    # Navigation patterns
    nav = knowledge.get("navigation", {})
    if nav:
        lines.append("### Navigation Patterns")
        for nav_key, nav_item in nav.items():
            lines.append(f"- **{nav_key}:** {nav_item}")
        lines.append("")

    return "\n".join(lines)


def get_knowledge_for_url(url: str) -> str:
    """Load and format knowledge relevant to a URL."""
    site_name = detect_site(url)
    if not site_name:
        return ""
    knowledge = load_site_knowledge(site_name)
    if not knowledge:
        return ""
    return format_knowledge_for_prompt(knowledge)


def list_available_sites() -> list[str]:
    """List all sites with knowledge files."""
    return [p.stem for p in _SITES_DIR.glob("*.yaml") if not p.stem.startswith("_")]
