#!/usr/bin/env python3
"""XHS 账号发现脚本，使用 FlowLens 检索接口返回热门账号列表。"""

import sys
from pathlib import Path
from typing import List, Dict

sys.path.insert(0, str(Path(__file__).parent))

from flowlens_xhs_backend import discover_accounts_via_flowlens


def discover_accounts(keyword: str, limit: int = 10) -> List[Dict]:
    """
    根据关键词发现账号（通过 FlowLens 搜索）

    Args:
        keyword: 搜索关键词
        limit: 返回账号数量（默认10）

    Returns:
        [{"user_id": "...", "nickname": "...", "count": 10}, ...]
    """
    try:
        data = discover_accounts_via_flowlens(
            keyword,
            search_limit=20,
            viral_threshold=0,
            author_limit=limit,
        )
        authors = data.get("authors", [])

        return [
            {
                "user_id": a.get("user_id", "") or a.get("author_hex_id", ""),
                "nickname": a.get("nickname", ""),
                "count": a.get("viral_count", 0) or a.get("post_count", 0),
            }
            for a in authors[:limit]
        ]

    except Exception as e:
        print(f"[Error] discover_accounts 异常: {e}")
        return []


if __name__ == "__main__":
    keyword = sys.argv[1] if len(sys.argv) > 1 else "护肤"
    print(f"搜索关键词: {keyword}")
    accounts = discover_accounts(keyword)
    print(json.dumps(accounts, ensure_ascii=False, indent=2))
