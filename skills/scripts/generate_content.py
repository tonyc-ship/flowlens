#!/usr/bin/env python3
"""
小红书内容生成脚本

根据风格模型和主题生成符合风格的内容
"""

import json
import random
from pathlib import Path
from time import sleep
from typing import Any, Dict, List


def default_style_model() -> Dict:
    """返回默认的风格模型"""
    return {
        "title_templates": [],
        "hook_templates": [],
        "content_structure": ["岗位亮点", "要求门槛", "投递方式"],
        "cta_templates": [],
        "tone": "紧迫+口语化",
        "image_style": "单图大字信息流"
    }


def _normalize_text_list(value: Any) -> List[str]:
    """将任意输入规范化为非空字符串列表"""
    if not isinstance(value, list):
        return []
    result: List[str] = []
    for item in value:
        text = str(item).strip()
        if text and text not in result:
            result.append(text)
    return result


def _ensure_style_model(model: Dict[str, Any]) -> Dict[str, Any]:
    """确保风格模型字段完整且类型正确"""
    defaults = default_style_model()
    normalized: Dict[str, Any] = {}
    normalized["title_templates"] = _normalize_text_list(model.get("title_templates", defaults["title_templates"]))
    normalized["hook_templates"] = _normalize_text_list(model.get("hook_templates", defaults["hook_templates"]))
    normalized["content_structure"] = _normalize_text_list(model.get("content_structure", defaults["content_structure"])) or defaults["content_structure"]
    normalized["cta_templates"] = _normalize_text_list(model.get("cta_templates", defaults["cta_templates"]))
    normalized["tone"] = str(model.get("tone", defaults["tone"])).strip() or defaults["tone"]
    normalized["image_style"] = str(model.get("image_style", defaults["image_style"])).strip() or defaults["image_style"]
    return normalized


def _build_section_content(section_name: str, topic: str, tone: str) -> str:
    """根据结构名生成对应卡片内容"""
    if section_name == "岗位亮点":
        return f"{topic}信息已经帮你整理好了，重点看岗位优势、适合人群和是否支持工签，整体节奏偏快，适合想尽快上岸的人。"
    if section_name == "要求门槛":
        return f"这类{topic}通常会看经验基础、英语沟通和身份条件，部分岗位可接受应届或转行，但简历一定要突出匹配点。"
    if section_name == "投递方式":
        return f"建议先准备一版针对{topic}优化过的简历，再按岗位要求投递；如果信息窗口期短，尽量当天完成沟通和递交。"
    return f"这部分围绕{topic}展开，建议重点提炼可执行信息，整体语气保持{tone}，让读者快速抓到重点。"


def load_style_model(path: str) -> Dict:
    """读取风格模型JSON文件"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            model = json.load(f)
        if not isinstance(model, dict):
            return default_style_model()
        return _ensure_style_model(model)
    except Exception:
        return default_style_model()


def generate_content(style_model: Dict, topic: str) -> str:
    """根据风格模型和主题生成内容"""
    try:
        sleep(random.uniform(1, 2))

        normalized_model = _ensure_style_model(style_model)
        title_templates = normalized_model["title_templates"]
        hook_templates = normalized_model["hook_templates"]
        cta_templates = normalized_model["cta_templates"]
        structure = normalized_model["content_structure"]
        tone = normalized_model["tone"]

        title = random.choice(title_templates) if title_templates else f"{topic}岗位信息整理"
        hook = random.choice(hook_templates) if hook_templates else "宝子们，今天整理一个岗位信息"
        cta = random.choice(cta_templates) if cta_templates else "感兴趣的话私我"

        cards: List[str] = []
        cards.append(f"## 卡片1\n{hook}")

        limited_structure = structure[:3]
        for index, struct_name in enumerate(limited_structure, start=2):
            struct_content = _build_section_content(struct_name, topic, tone)
            cards.append(f"## 卡片{index}\n{struct_content}")

        cards.append(f"## 卡片5\n{cta}")

        content = f"# {title}\n\n" + "\n\n".join(cards) + "\n"
        return content

    except Exception as e:
        print(f"[Error] generate_content 异常: {e}")
        return f"# {topic}\n\n## 卡片1\n生成失败\n\n## 卡片2\n请检查风格模型后重试\n"


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="小红书内容生成")
    parser.add_argument("style_model_path", help="风格模型JSON文件路径")
    parser.add_argument("topic", help="内容主题")

    args = parser.parse_args()

    # 读取风格模型
    style_model = load_style_model(args.style_model_path)

    # 生成内容
    markdown = generate_content(style_model, args.topic)

    # 保存到output
    output_dir = Path("output")
    output_dir.mkdir(parents=True, exist_ok=True)

    output_file = output_dir / "generated_content.md"
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(markdown)

    # 打印到控制台
    print(markdown)
    print(f"\n✅ 已保存到: {output_file}")
