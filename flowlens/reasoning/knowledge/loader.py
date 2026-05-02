"""Progressive disclosure loader — feeds site knowledge into agent context on demand.

Design principle: the agent's context window is not free. Instead of dumping all
knowledge into every prompt, we use a two-level approach:

  Level 0 (always loaded): catalog.json — one-paragraph summary + section names.
    This tells the agent what knowledge exists and lets it decide what to look up.

  Level 1 (on demand): individual section files (interactions.json, navigation.json,
    intents.json). Loaded only when the agent requests them.

Usage in TaskAgent:
    loader = KnowledgeLoader("knowledge/sites/xiaohongshu")

    # Level 0: include in every prompt
    catalog_text = loader.get_catalog_summary()

    # Level 1: agent decides it needs interaction details
    interactions = loader.get_section("interactions")
"""

from __future__ import annotations

import json
from pathlib import Path


class KnowledgeLoader:
    """Loads site knowledge with progressive disclosure."""

    def __init__(self, knowledge_dir: str | Path):
        self.dir = Path(knowledge_dir)
        self._catalog: dict | None = None

    @property
    def catalog(self) -> dict:
        if self._catalog is None:
            catalog_path = self.dir / "catalog.json"
            if catalog_path.exists():
                self._catalog = json.loads(catalog_path.read_text())
            else:
                self._catalog = {}
        return self._catalog

    @property
    def available(self) -> bool:
        return bool(self.catalog)

    def get_catalog_summary(self) -> str:
        """Level 0: one-paragraph summary for every prompt.

        Returns empty string if no knowledge available.
        """
        cat = self.catalog
        if not cat:
            return ""

        summary = cat.get("summary", "")
        sections = cat.get("sections", {})

        # Build a compact index of what's available
        section_lines = []
        for name, info in sections.items():
            if name == "interactions":
                names = info.get("names", [])
                section_lines.append(
                    f"  - interactions ({info.get('count', '?')} primitives): {', '.join(names)}")
            elif name == "navigation":
                pages = info.get("page_types", [])
                section_lines.append(
                    f"  - navigation ({info.get('transition_count', '?')} transitions, "
                    f"pages: {', '.join(pages)})")
            elif name == "intents":
                section_lines.append(
                    f"  - intents ({info.get('sessions', '?')} sessions analyzed)")

        sections_text = "\n".join(section_lines)
        return f"""{summary}

Available knowledge sections (request by name for details):
{sections_text}"""

    def get_section(self, section_name: str) -> dict:
        """Level 1: load a specific knowledge section on demand.

        Returns the parsed JSON content, or empty dict if not found.
        """
        sections = self.catalog.get("sections", {})
        section_info = sections.get(section_name, {})
        filename = section_info.get("file", f"{section_name}.json")

        filepath = self.dir / filename
        if filepath.exists():
            return json.loads(filepath.read_text())
        return {}

    def get_section_text(self, section_name: str) -> str:
        """Level 1: load a section and format as text for LLM prompt injection."""
        data = self.get_section(section_name)
        if not data:
            return f"(no {section_name} knowledge available)"
        return json.dumps(data, ensure_ascii=False, indent=2)

    def get_interactions_for_page(self, page_type: str) -> list[dict]:
        """Convenience: get interaction primitives relevant to a page type.

        Filters primitives that mention the page type in their notes or
        that are general-purpose (not page-specific).
        """
        interactions = self.get_section("interactions")
        primitives = interactions.get("primitives", [])
        # Return all — the agent can filter further
        # In future, primitives could have a "page_types" field for filtering
        return primitives

    def get_navigation_from(self, current_page_type: str) -> list[dict]:
        """Convenience: get possible transitions from current page type."""
        navigation = self.get_section("navigation")
        transitions = navigation.get("transitions", [])
        return [t for t in transitions if t.get("from") == current_page_type]

    def list_sections(self) -> list[str]:
        """List available knowledge section names."""
        return list(self.catalog.get("sections", {}).keys())
