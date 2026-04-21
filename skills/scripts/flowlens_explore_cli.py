#!/usr/bin/env python3
"""CLI wrapper that exposes FlowLens XHS discovery with the legacy JSON shape."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from flowlens_xhs_backend import discover_accounts_via_flowlens


def main() -> int:
    parser = argparse.ArgumentParser(description="Run XHS discovery through FlowLens")
    parser.add_argument("keyword", help="搜索关键词")
    parser.add_argument("--search-limit", type=int, default=30)
    parser.add_argument("--viral-threshold", type=int, default=30)
    parser.add_argument("--author-limit", type=int, default=20)
    args = parser.parse_args()

    data = discover_accounts_via_flowlens(
        args.keyword,
        search_limit=args.search_limit,
        viral_threshold=args.viral_threshold,
        author_limit=args.author_limit,
    )
    json.dump(data, sys.stdout, ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
