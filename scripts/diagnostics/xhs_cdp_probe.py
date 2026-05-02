#!/usr/bin/env python3
"""Open Xiaohongshu in a Socai-controlled Chrome CDP tab."""
from __future__ import annotations

from socai.platforms.xhs.cdp_diagnostics import xhs_probe_main


if __name__ == "__main__":
    raise SystemExit(xhs_probe_main())
