#!/usr/bin/env python3
"""
小红书自动闭环主流程

抓取账号内容 → 过滤高表现 → 生成 style_model → 保存 style_model → 
读取 style_model → 生成 markdown → 渲染 → 发布
"""

import json
import subprocess
import sys
from time import sleep
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent))

try:
    from analyze_accounts import (
        filter_high_performance_notes,
        build_style_model,
        save_style_model,
        sanitize_filename
    )
    from generate_content import default_style_model, generate_content, load_style_model
except ImportError as e:
    print(f"缺少本地依赖: {e}")
    sys.exit(1)


ENABLE_PUBLISH = False


def _ensure_directories() -> None:
    """确保所需目录存在"""
    dirs = ["output", "output/generated", "output/style_models"]
    for dir_path in dirs:
        Path(dir_path).mkdir(parents=True, exist_ok=True)


def _resolve_safe_name(name: Optional[str]) -> str:
    """统一使用 analyze_accounts 中的安全文件名逻辑"""
    return sanitize_filename(name or "unknown_account")


def _load_notes_from_json(notes_json_path: str) -> List[Dict[str, Any]]:
    """加载 JSON 格式的笔记数据
    
    支持两种格式：
    1. 顶层是 list：直接返回其中的所有 dict 项
    2. 顶层是 dict 且包含 "notes" 字段：返回 notes 字段中的 list 项
    """
    try:
        with open(notes_json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"⚠️  无法读取 notes_json 文件: {e}")
        return []
    
    # 格式 1: 顶层是 list
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    
    # 格式 2: 顶层是 dict，包含 "notes" 字段
    if isinstance(data, dict):
        notes = data.get("notes")
        if isinstance(notes, list):
            return [item for item in notes if isinstance(item, dict)]
    
    print("⚠️  notes_json 内容格式不正确，期望为 list 或 {\"notes\": [...]}。")
    return []


def _prepare_style_model(account_name: str, notes_json_path: Optional[str]) -> Dict[str, Any]:
    """准备风格模型，优先从 notes_json 生成，然后尝试已有文件，最后使用默认
    
    Args:
        account_name: 账号名称
        notes_json_path: 抓取内容 JSON 文件路径（可选）
    
    Returns:
        包含 style_model、style_model_path、source 的字典
    """
    safe_account = _resolve_safe_name(account_name)
    style_model_path = Path("output/style_models") / f"{safe_account}.json"
    
    # 优先从 notes_json 生成
    if notes_json_path:
        print(f"[1/6] 从抓取内容构建风格模型...")
        notes = _load_notes_from_json(notes_json_path)
        
        if notes:
            try:
                filtered_notes = filter_high_performance_notes(notes)
                style_model = build_style_model(filtered_notes)
                saved_path = save_style_model(account_name, style_model)
                print(f"✅ 风格模型已保存: {saved_path}")
                return {
                    "style_model": style_model,
                    "style_model_path": saved_path,
                    "source": "notes_json"
                }
            except Exception as e:
                print(f"⚠️  从 notes_json 构建风格模型失败: {e}")
                print(f"    继续尝试本地已有的风格模型")
        else:
            print(f"⚠️  notes_json 中未找到有效的笔记数据")
            print(f"    继续尝试本地已有的风格模型")
    
    # 尝试加载已有的 style_model 文件
    if style_model_path.exists():
        print(f"[1/6] 加载风格模型...")
        style_model = load_style_model(str(style_model_path))
        print(f"✅ 已加载: {style_model_path}")
        return {
            "style_model": style_model,
            "style_model_path": str(style_model_path),
            "source": "existing_json"
        }
    
    # 使用默认风格模型
    print(f"[1/6] 加载风格模型...")
    print(f"⚠️  未找到风格模型，使用默认风格模型")
    return {
        "style_model": default_style_model(),
        "style_model_path": "",
        "source": "default"
    }


def run_pipeline(
    account_name: Optional[str] = None,
    topic: Optional[str] = None,
    notes_json: Optional[str] = None,
) -> Dict[str, Any]:
    """
    执行主流程
    
    Args:
        account_name: 账号名称
        topic: 内容主题
        notes_json: 抓取内容的 JSON 文件路径，可用于即时生成 style_model
    
    Returns:
        流程结果字典
    """
    try:
        _ensure_directories()
        
        # 校验输入
        if not account_name and not topic:
            print("❌ 必须至少提供 account_name 或 topic")
            return {"status": "error", "error": "account_name 和 topic 不能同时为空"}
        
        effective_name = account_name or topic or "default"
        
        result = {
            "status": "success",
            "account_name": effective_name,
            "topic": topic or effective_name,
            "style_model_path": "",
            "markdown_path": "",
            "render_status": "未执行",
            "publish_status": "未执行"
        }
        
        print(f"\n{'='*60}")
        print(f"小红书自动闭环流程")
        print(f"{'='*60}\n")
        
        # Step 1-2: 准备风格模型
        prepared = _prepare_style_model(effective_name, notes_json)
        style_model = prepared["style_model"]
        result["style_model_path"] = prepared["style_model_path"]
        
        # Step 3: 生成内容
        print(f"\n[2/6] 生成内容...")
        markdown_content = generate_content(style_model, result["topic"])
        print(f"✅ 内容生成完成")
        
        # Step 4: 保存markdown
        print(f"\n[3/6] 保存markdown...")
        safe_name = _resolve_safe_name(effective_name)
        markdown_path = Path("output/generated") / f"{safe_name}.md"
        with open(markdown_path, "w", encoding="utf-8") as f:
            f.write(markdown_content)
        result["markdown_path"] = str(markdown_path)
        print(f"✅ 已保存: {markdown_path}")
        
        # Step 5: 调用 render_xhs.py
        print(f"\n[4/6] 渲染卡片...")
        try:
            render_script = Path(__file__).parent / "render_xhs.py"
            render_cmd = [
                sys.executable, str(render_script),
                str(markdown_path),
                "-o", "output/generated",
                "-t", "default"
            ]
            render_result = subprocess.run(
                render_cmd,
                capture_output=True,
                text=True,
                timeout=120
            )
            
            if render_result.returncode == 0:
                result["render_status"] = "成功"
                print(f"✅ 渲染完成")
            else:
                result["render_status"] = "失败"
                print(f"⚠️  渲染失败: {render_result.stderr[:100]}")
        
        except Exception as e:
            result["render_status"] = "异常"
            print(f"⚠️  渲染异常: {e}")
        
        # Step 6: 调用 publish_xhs.py（可选）
        print(f"\n[5/6] 检查发布配置...")
        if ENABLE_PUBLISH:
            print(f"[6/6] 发布内容...")
            try:
                publish_script = Path(__file__).parent / "publish_xhs.py"
                publish_cmd = [sys.executable, str(publish_script)]
                publish_result = subprocess.run(
                    publish_cmd,
                    capture_output=True,
                    text=True,
                    timeout=120
                )
                if publish_result.returncode == 0:
                    result["publish_status"] = "成功"
                    print("✅ 发布完成")
                else:
                    result["publish_status"] = "失败"
                    print(f"⚠️  发布失败: {publish_result.stderr[:100]}")
            except Exception as e:
                result["publish_status"] = "异常"
                print(f"⚠️  发布异常: {e}")
        else:
            result["publish_status"] = "已跳过（ENABLE_PUBLISH=False）"
            print("⚠️  发布已跳过（ENABLE_PUBLISH=False）")
        
        print(f"\n{'='*60}")
        print(f"✅ 流程完成！")
        print(f"{'='*60}\n")
        
        return result
    
    except Exception as e:
        print(f"\n❌ 流程异常: {e}")
        import traceback
        traceback.print_exc()
        return {"status": "error", "error": str(e)}


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="小红书自动闭环流程")
    parser.add_argument("--account", help="账号名称", default=None)
    parser.add_argument("--topic", help="内容主题", default=None)
    parser.add_argument("--notes-json", help="抓取内容JSON文件路径", default=None)
    
    args = parser.parse_args()
    
    run_pipeline(account_name=args.account, topic=args.topic, notes_json=args.notes_json)
