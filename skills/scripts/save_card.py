#!/usr/bin/env python3
"""
卡片归档脚本 - 保存生成的卡片，防止被覆盖

使用方法:
    python scripts/save_card.py --name "我的卡片" --copy-to-desktop
"""

import argparse
import shutil
import json
from pathlib import Path
from datetime import datetime


def save_card(name: str = None, copy_to_desktop: bool = False):
    """保存当前生成的卡片到归档目录"""
    
    # 准备目录
    archive_dir = Path("output/generated/archive")
    archive_dir.mkdir(parents=True, exist_ok=True)
    
    # 生成时间戳和名称
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    name_prefix = name or "card"
    safe_name = "".join(c for c in name if c.isalnum() or c in "-_") if name else name_prefix
    
    # 源文件
    card_src = Path("output/generated/card_1.png")
    md_src = Path("output/generated/test_account.md")
    style_src = Path("output/style_models/test_account.json")
    
    results = {}
    
    # 保存卡片
    if card_src.exists():
        card_dest = archive_dir / f"{safe_name}_{timestamp}.png"
        shutil.copy(card_src, card_dest)
        results["card"] = str(card_dest)
        print(f"✅ 卡片已保存: {card_dest.relative_to('.')}")
    
    # 保存内容
    if md_src.exists():
        md_dest = archive_dir / f"{safe_name}_{timestamp}.md"
        shutil.copy(md_src, md_dest)
        results["markdown"] = str(md_dest)
        print(f"✅ 内容已保存: {md_dest.relative_to('.')}")
    
    # 保存风格模型
    if style_src.exists():
        style_dest = archive_dir / f"{safe_name}_{timestamp}_style.json"
        shutil.copy(style_src, style_dest)
        results["style_model"] = str(style_dest)
        print(f"✅ 风格模型已保存: {style_dest.relative_to('.')}")
    
    # 复制到桌面（可选）
    if copy_to_desktop:
        desktop_dir = Path.home() / "Desktop" / "Auto-Redbook-Test" / "output" / "generated" / "archive"
        desktop_dir.mkdir(parents=True, exist_ok=True)
        
        if card_src.exists():
            desktop_card = desktop_dir / f"{safe_name}_{timestamp}.png"
            shutil.copy(card_src, desktop_card)
            print(f"📱 卡片已复制到桌面: {desktop_card.relative_to(Path.home())}")
    
    # 保存元数据
    metadata = {
        "timestamp": timestamp,
        "name": safe_name,
        "files": results,
        "created_at": datetime.now().isoformat()
    }
    
    metadata_file = archive_dir / f"{safe_name}_{timestamp}_info.json"
    with open(metadata_file, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)
    
    print(f"📋 元数据已保存: {metadata_file.relative_to('.')}")
    print(f"\n💾 所有文件已永久保存，不会被覆盖！")
    
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="保存生成的卡片到归档目录")
    parser.add_argument("--name", help="卡片名称（用于区分不同的卡片）", default=None)
    parser.add_argument("--copy-to-desktop", action="store_true", help="同时复制到桌面")
    
    args = parser.parse_args()
    save_card(name=args.name, copy_to_desktop=args.copy_to_desktop)
