"""XHS agent tool helpers."""

from flowlens.platforms.xhs.tools import _content_from_note_ocr


def test_extract_note_content_from_screenshot_ocr_lines() -> None:
    ocr_text = "\n".join(
        [
            "小火",
            "低成本赛道练习车",
            "欢迎大家周末来说思低成本练车，OTR专业赛车租赁，",
            "全程技术保障团队陪伴，免费驾驶进阶指导。",
            "Q 猜你想搜 OTR专业赛车租赁",
            "03-22 北京",
            "共 2 条评论",
            "我也能去吗",
        ]
    )

    assert _content_from_note_ocr(
        ocr_text,
        title="低成本赛道练习车",
        author="小火",
    ) == (
        "欢迎大家周末来说思低成本练车，OTR专业赛车租赁，\n"
        "全程技术保障团队陪伴，免费驾驶进阶指导。"
    )


def test_extract_note_content_ignores_loading_chrome() -> None:
    assert _content_from_note_ocr(
        "羽间星落\n坐标北京！平民赛车继续搞起\n刚刚\n加载中\n说点什么...\n收藏\n评论",
        title="坐标北京！平民赛车继续搞起",
        author="羽间星落",
    ) == ""
