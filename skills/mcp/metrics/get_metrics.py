#!/usr/bin/env python3
"""
获取小红书笔记数据统计
Usage:
    python get_metrics.py --note-ids id1 id2 id3
    python get_metrics.py --note-ids id1 --summary
"""
import argparse
import json
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    print(json.dumps({"error": "缺少依赖: python-dotenv，请运行 pip install python-dotenv"}))
    sys.exit(1)

try:
    from xhs import XhsClient
except ImportError:
    print(json.dumps({"error": "缺少依赖: xhs，请运行 pip install xhs"}))
    sys.exit(1)

# 加载 .env
env_path = Path(__file__).parent.parent.parent / '.env'
if env_path.exists():
    load_dotenv(env_path)

import os
cookie = os.getenv('XHS_COOKIE', '')

if not cookie:
    print(json.dumps({"error": "未找到 XHS_COOKIE，请在 .env 文件中配置"}))
    sys.exit(1)

def sign_func(uri, data=None, a1_param="", web_session=""):
    return {"x-s": "", "x-t": ""}

client = XhsClient(cookie=cookie, sign=sign_func)

def main():
    parser = argparse.ArgumentParser(description='获取小红书笔记数据统计')
    parser.add_argument('--note-ids', '-n', nargs='+', required=True, help='笔记ID列表')
    parser.add_argument('--summary', action='store_true', help='获取摘要（含封面/标题等）')
    args = parser.parse_args()

    try:
        if args.summary:
            result = client.get_notes_summary(args.note_ids)
        else:
            result = client.get_notes_statistics(args.note_ids)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        sys.exit(1)

if __name__ == '__main__':
    main()
