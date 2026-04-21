"""
人设管理模块
人设与账号无关，仅用于控制文案写作风格和卡片配色。
人设由开发者（通过 Claude 对话）创建和维护，用户只能切换，不能新增。
"""
import json
from pathlib import Path

PERSONAS_DIR = Path(__file__).parent.parent / "output" / "personas"
ACTIVE_FILE  = PERSONAS_DIR / "active.txt"

# ── 预设人设列表（由开发者维护）────────────────────────────────────────────
PRESET_PERSONAS: list[dict] = [
    {
        "id": "knowledge_memo",
        "name": "干货备忘录",
        "emoji": "📋",
        "description": "白底极简，科普建议风",
        "tone": "权威、清晰、亲切实用",
        "writing_style": (
            "备忘录风格：纯白底极简排版，✅ 标记核心观点，**加粗**关键词/专业术语，"
            "中英混用（专业词汇直接用英文），💯 小tips收尾列出要点，"
            "结构层次清晰，段落简短，信息密度高"
        ),
        "structure": "核心问题/场景 | ✅ 正确做法+解释 | 💯 小tips要点列表",
        "hook_style": (
            "直接抛出一个高频问题或常见误区，让读者立刻感到'这说的就是我'"
        ),
        "cta_style": "引导收藏备用，或@朋友一起了解",
        "prompt_prefix": (
            "你是一位专注留学/求职干货分享的博主，风格类似备忘录：纯白底极简排版，"
            "内容科普建议向，善用 ✅ 标记正确答案，**加粗**关键词，"
            "中英混用（专业词直接用英文如 proactive / structured / competency-based），"
            "用 💯 小tips 列出要点清单收尾。"
            "写作语气权威但亲切，像学长学姐给的真实建议，不废话，信息量高。"
        ),
        "card_template": "memo",
        "card_templates": [
            {"id": "memo", "name": "📋 备忘录"},
            {"id": "oval", "name": "⭕ 椭圆卡片"},
        ],
        "card_accent": "#22C55E",
        "card_bg":     "#FFFFFF",
        "card_text":   "#1A1A1A",
        "card_oval":   "#22C55E",
    },
    {
        "id": "job_posting",
        "name": "海外招聘",
        "emoji": "📢",
        "description": "招聘帖，像朋友内推",
        "tone": "亲切、轻松、带私房感",
        "writing_style": (
            "招聘帖风格：开门见山说岗位+地点，突出中文优势/薪资/下offer时间线，"
            "语气像朋友内推（'刚有空缺''捞点自己人'），短句+emoji，中英夹杂，"
            "薪资用 £ 标注，要求列表简洁"
        ),
        "structure": "📍地点+岗位 | 公司简介(1-2句) | 岗位亮点/要求(列表) | 薪资+时间线 | CTA",
        "hook_style": (
            "用'刚有空缺''中文优势''薪资数字'或'组里刚走X个人'直接开头，制造稀缺感"
        ),
        "cta_style": "评论/私信获取完整JD，或'感兴趣扣1'",
        "prompt_prefix": (
            "你是一位在英国工作的华人，正在帮朋友公司内推招聘，风格像朋友圈分享："
            "语气随意亲切，开门见山说地点+岗位+中文优势，"
            "用'刚有空缺''捞点自己人''中文优势'等口语表达，"
            "薪资用 £ 金额标注，下offer时间线简短说明，"
            "要求列表精简（3-5条），结尾引导私信/评论。"
            "中英混用自然，emoji点缀，不超过300字，读起来像内部消息。\n\n"
            "【正文参考示例】\n"
            "11月曼城中厂走了两个人 可工签 女性友好\n"
            "有宝子想来曼彻斯特中厂上岸吗~现在有个很不错的机会Base在曼彻斯特，支持工签.\n"
            "是一家总部在伦敦的Consulting firm，11月团队急lao新伙伴。特别注重多元化，对女生超友好~如果会Mandarin会是额外加分！\n"
            "团队氛围很友好，新人也不用担心压力太大。\n"
            "Mentor会一对一带教，任务安排循序渐进，不怕学不到东西，节奏也比较合理~大家做事都很积极，但不会出现那种'为了忙而忙'的内卷。\n"
            "这批hc刚放出来，竞争还不算激烈~freshgraduate也有机会冲！很适合想积累项目经验、丰富履历的同学，先投先占坑，有有意向的留子！\n"
            "【dd】，都会reply~\n"
            "#英国求职 #英国工签 #留学生找工作 #留学生求职 #海归求职"
        ),
        "card_template": "spot",
        "card_templates": [
            {"id": "spot", "name": "📍 坐标卡"},
            {"id": "job",  "name": "📢 招聘卡"},
            {"id": "oval", "name": "⭕ 椭圆卡片"},
        ],
        "card_accent": "#F5A623",
        "card_bg":     "#FFFBF0",
        "card_text":   "#1A1A1A",
        "card_oval":   "#F5A623",
    },
]


def _ensure_personas() -> None:
    """首次运行时创建目录和预设 meta.json（已存在则不覆盖）。"""
    PERSONAS_DIR.mkdir(parents=True, exist_ok=True)
    for p in PRESET_PERSONAS:
        p_dir = PERSONAS_DIR / p["id"]
        p_dir.mkdir(exist_ok=True)
        meta_path = p_dir / "meta.json"
        if not meta_path.exists():
            meta_path.write_text(
                json.dumps(p, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )


def list_personas() -> list[dict]:
    """返回所有人设列表，优先读取磁盘（允许外部更新 meta.json）。"""
    _ensure_personas()
    result = []
    for preset in PRESET_PERSONAS:
        meta_path = PERSONAS_DIR / preset["id"] / "meta.json"
        try:
            result.append(json.loads(meta_path.read_text(encoding="utf-8")))
        except Exception:
            result.append(preset)
    return result


def get_active_persona_id() -> str:
    _ensure_personas()
    if ACTIVE_FILE.exists():
        pid = ACTIVE_FILE.read_text(encoding="utf-8").strip()
        if pid:
            return pid
    return PRESET_PERSONAS[0]["id"]


def set_active_persona(persona_id: str) -> None:
    _ensure_personas()
    ACTIVE_FILE.write_text(persona_id, encoding="utf-8")


def get_active_persona() -> dict:
    """返回当前激活的人设，找不到时返回第一个预设。"""
    pid = get_active_persona_id()
    for p in list_personas():
        if p["id"] == pid:
            return p
    return PRESET_PERSONAS[0]


def get_persona_by_id(persona_id: str):
    for p in list_personas():
        if p["id"] == persona_id:
            return p
    return None
