"""Declarative Xiaohongshu site schema loader.

The runtime entity classes still live in Python, but the site-facing schema
for entities, capabilities, and extraction plans is sourced from YAML so new
sites can add the same layer without cloning Python declarations.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml


_SPEC_PATH = Path(__file__).with_name("spec.yaml")


@dataclass(frozen=True)
class EntityFieldSpec:
    name: str
    description: str


@dataclass(frozen=True)
class EntitySchemaSpec:
    name: str
    description: str
    key_fields: tuple[EntityFieldSpec, ...]
    derived_fields: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "key_fields": [
                {"name": field.name, "description": field.description}
                for field in self.key_fields
            ],
            "derived_fields": list(self.derived_fields),
        }


@lru_cache(maxsize=1)
def load_xhs_spec() -> dict:
    with open(_SPEC_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def entity_schema_catalog() -> dict[str, EntitySchemaSpec]:
    entities = load_xhs_spec().get("entities", {})
    catalog: dict[str, EntitySchemaSpec] = {}
    for key, item in entities.items():
        key_fields = tuple(
            EntityFieldSpec(
                name=str(field.get("name", "")).strip(),
                description=str(field.get("description", "")).strip(),
            )
            for field in item.get("key_fields", [])
            if str(field.get("name", "")).strip()
        )
        catalog[key] = EntitySchemaSpec(
            name=str(item.get("name", key)),
            description=str(item.get("description", "")),
            key_fields=key_fields,
            derived_fields=tuple(str(v) for v in item.get("derived_fields", []) if str(v).strip()),
        )
    return catalog

