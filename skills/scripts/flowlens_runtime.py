#!/usr/bin/env python3
"""Helpers for locating the FlowLens project and its Python interpreter."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _find_flowlens_root() -> str:
    explicit = os.environ.get("FLOWLENS_PROJECT_ROOT", "").strip()
    if explicit:
        return explicit
    # Monorepo: flowlens/ or 小红书检索/ next to the skills project
    skills_parent = Path(__file__).resolve().parent.parent.parent
    for sibling in ("flowlens", "小红书检索"):
        candidate = skills_parent / sibling
        if candidate.exists():
            return str(candidate)
    return str(Path.home() / "小红书检索")


DEFAULT_FLOWLENS_ROOT = Path(_find_flowlens_root()).expanduser()


def get_flowlens_root() -> Path:
    root = DEFAULT_FLOWLENS_ROOT
    if not root.exists():
        raise RuntimeError(
            f"FlowLens project not found: {root}. "
            "Set FLOWLENS_PROJECT_ROOT to the current FlowLens repo path."
        )
    return root


def get_flowlens_python() -> str:
    explicit = os.environ.get("FLOWLENS_PYTHON", "").strip()
    if explicit:
        return explicit

    root = get_flowlens_root()
    for rel in ("venv/bin/python", ".venv/bin/python"):
        candidate = root / rel
        if candidate.exists():
            return str(candidate)
    return sys.executable

