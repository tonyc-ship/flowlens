#!/usr/bin/env python3
"""Open Xiaohongshu in a FlowLens-controlled Chrome CDP tab."""
from __future__ import annotations

from flowlens.platforms.xhs.cdp_diagnostics import xhs_probe_main


if __name__ == "__main__":
    raise SystemExit(xhs_probe_main())
