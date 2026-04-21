#!/usr/bin/env python3
"""
自动账号发现脚本

使用 xhs_explore agent 自动检索指定关键词的账号，
然后调用 analyze_accounts.py 进行分析
"""

import json
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime

# 添加脚本路径
sys.path.insert(0, str(Path(__file__).parent))

try:
    from analyze_accounts import write_excel, sanitize_filename
    from flowlens_xhs_backend import discover_accounts_via_flowlens
except ImportError as e:
    print(f"❌ 缺少本地依赖: {e}")
    sys.exit(1)


def run_explore_agent(keyword: str, search_limit: int = 20, viral_threshold: int = 60, author_limit: int = 20) -> Optional[Dict[str, Any]]:
    """
    运行 xhs_explore agent 来检索账号数据
    
    Args:
        keyword: 搜索关键词
        search_limit: 搜索笔记数量限制
        viral_threshold: 爆款分数阈值
        author_limit: 返回账号数量限制
    
    Returns:
        explore 结果字典，格式：
        {
            "keyword": "...",
            "total_fetched": 10,
            "viral_passed": 5,
            "authors": [
                {
                    "user_id": "...",
                    "nickname": "...",
                    "avatar": "...",
                    "desc": "...",
                    "followers": 1000,
                    "following": 100,
                    "notes_count": 50,
                    "post_count": 5,
                    "avg_viral_score": 75.5,
                    "viral_count": 3,
                    "viral_notes": [...]
                },
                ...
            ]
        }
    """
    try:
        print(f"🚀 启动 xhs_explore agent...")
        print(f"   关键词: {keyword}")
        print(f"   搜索限制: {search_limit}")
        print(f"   爆款阈值: {viral_threshold}")
        print(f"   账号限制: {author_limit}")

        explore_result = discover_accounts_via_flowlens(
            keyword,
            search_limit=search_limit,
            viral_threshold=viral_threshold,
            author_limit=author_limit,
        )
        
        print(f"✅ explore agent 运行成功")
        print(f"   总获取笔记: {explore_result.get('total_fetched', 0)}")
        print(f"   爆款笔记: {explore_result.get('viral_passed', 0)}")
        print(f"   发现账号: {len(explore_result.get('authors', []))}")
        
        return explore_result
        
    except Exception as e:
        print(f"❌ explore agent 异常: {e}")
        import traceback
        traceback.print_exc()
        return None


def process_explore_result(explore_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    处理 explore 结果，为每个账号调用 analyze_accounts
    
    Args:
        explore_data: explore agent 返回的结果
    
    Returns:
        处理后的账号列表（包含分析数据）
    """
    keyword = explore_data.get("keyword", "unknown")
    authors = explore_data.get("authors", [])
    
    if not authors:
        print(f"⚠️  没有发现账号")
        return []
    
    print(f"\n📊 开始分析 {len(authors)} 个账号...")
    
    analyzed_accounts = []
    
    for idx, author in enumerate(authors, 1):
        try:
            user_id = author.get("user_id", "unknown")
            nickname = author.get("nickname", "unknown")
            
            print(f"\n[{idx}/{len(authors)}] 分析账号: {nickname} ({user_id})")
            
            # 计算账号数据
            account_data = {
                "user_id": user_id,
                "nickname": nickname,
                "avatar": author.get("avatar", ""),
                "desc": author.get("desc", ""),
                "followers": author.get("followers", 0),
                "following": author.get("following", 0),
                "notes_count": author.get("notes_count", 0),
                "post_count": author.get("post_count", 0),
                "avg_viral_score": author.get("avg_viral_score", 0),
                "viral_count": author.get("viral_count", 0),
                "explore_data": {
                    "keyword": keyword,
                    "viral_count": author.get("viral_count", 0),
                    "avg_viral_score": author.get("avg_viral_score", 0),
                    "rank": idx
                }
            }
            
            analyzed_accounts.append(account_data)
            
            print(f"   ✅ {nickname}")
            print(f"      粉丝: {account_data['followers']:,}")
            print(f"      笔记: {account_data['notes_count']}")
            print(f"      爆款数: {account_data['viral_count']}")
            print(f"      平均爆款分: {account_data['avg_viral_score']:.1f}")
            
        except Exception as e:
            print(f"   ❌ 分析失败: {e}")
            continue
    
    return analyzed_accounts


def export_results(accounts: List[Dict[str, Any]], keyword: str) -> Optional[str]:
    """
    将账号数据导出为 Excel
    
    Args:
        accounts: 账号列表
        keyword: 搜索关键词
    
    Returns:
        输出文件路径
    """
    if not accounts:
        print(f"⚠️  没有账号数据可导出")
        return None
    
    try:
        # 使用 analyze_accounts.py 的导出功能
        output_dir = Path(__file__).parent.parent / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_keyword = sanitize_filename(keyword)
        output_file = output_dir / f"xhs_discover_{safe_keyword}_{timestamp}.xlsx"
        
        # 调用 write_excel
        write_excel(accounts, str(output_file))
        
        print(f"\n✅ 数据已导出: {output_file}")
        return str(output_file)
        
    except Exception as e:
        print(f"❌ 导出失败: {e}")
        import traceback
        traceback.print_exc()
        return None


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description="自动发现小红书账号")
    parser.add_argument("keyword", help="搜索关键词")
    parser.add_argument("--search-limit", type=int, default=20, help="搜索笔记数量限制（默认20）")
    parser.add_argument("--viral-threshold", type=int, default=60, help="爆款分数阈值（默认60）")
    parser.add_argument("--author-limit", type=int, default=20, help="返回账号数量限制（默认20）")
    parser.add_argument("--no-export", action="store_true", help="不导出 Excel 文件")
    
    args = parser.parse_args()
    
    print("=" * 70)
    print("🚀 小红书账号自动发现系统")
    print("=" * 70)
    
    # 1. 运行 explore agent
    explore_result = run_explore_agent(
        keyword=args.keyword,
        search_limit=args.search_limit,
        viral_threshold=args.viral_threshold,
        author_limit=args.author_limit
    )
    
    if not explore_result:
        print(f"\n❌ 无法获取数据，程序退出")
        sys.exit(1)
    
    # 2. 处理结果
    accounts = process_explore_result(explore_result)
    
    if not accounts:
        print(f"\n❌ 没有发现任何账号，程序退出")
        sys.exit(1)
    
    # 3. 统计信息
    print(f"\n📈 统计信息")
    print(f"   发现账号数: {len(accounts)}")
    total_followers = sum(acc.get("followers", 0) for acc in accounts)
    avg_viral_score = sum(acc.get("avg_viral_score", 0) for acc in accounts) / len(accounts) if accounts else 0
    print(f"   总粉丝数: {total_followers:,}")
    print(f"   平均爆款分: {avg_viral_score:.1f}")
    
    # 4. 导出 Excel（可选）
    if not args.no_export:
        export_results(accounts, args.keyword)
    
    # 5. 打印账号表格
    print(f"\n📋 账号列表")
    print(f"{'排序':<4} {'账号名':<20} {'粉丝':<10} {'笔记数':<8} {'爆款数':<6} {'平均爆款分':<8}")
    print("-" * 70)
    for idx, acc in enumerate(accounts, 1):
        nickname = acc.get("nickname", "")[:18]
        followers = acc.get("followers", 0)
        notes = acc.get("notes_count", 0)
        viral_count = acc.get("viral_count", 0)
        viral_score = acc.get("avg_viral_score", 0)
        print(f"{idx:<4} {nickname:<20} {followers:<10,} {notes:<8} {viral_count:<6} {viral_score:<8.1f}")
    
    print(f"\n✅ 流程完成！")
    return 0


if __name__ == "__main__":
    sys.exit(main())
