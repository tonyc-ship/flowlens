#!/usr/bin/env python3
"""Create a marked Socai-controlled Chrome tab and verify CDP primitives."""
from __future__ import annotations

from socai.cdp.diagnostics import controlled_tab_main


if __name__ == "__main__":
    raise SystemExit(controlled_tab_main())
