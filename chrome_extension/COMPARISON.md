# Chrome Extension (DOM) vs Vision Approach — 对比分析

## 架构对比

```
Vision方案 (research_agent.py)              Extension方案 (content.js + background.js)
┌─────────────────────────────┐             ┌─────────────────────────────┐
│   research_agent.py (831行)  │             │   background.js (~200行)    │
│   流程控制 + 所有决策逻辑       │             │   流程控制 + LLM决策          │
│                              │             │                             │
│   ┌─ screen.py ──────────┐  │             │   ┌─ content.js ─────────┐  │
│   │  Quartz截图            │  │             │   │  DOM查询 (CSS选择器)    │  │
│   │  pyautogui点击/打字     │  │             │   │  原生DOM事件模拟        │  │
│   └──────────────────────┘  │             │   └──────────────────────┘  │
│                              │             │                             │
│   ┌─ grounding.py ───────┐  │             │         不需要               │
│   │  UI-TARS 7B (8s/次)   │  │             │                             │
│   │  或 Claude Vision      │  │             │                             │
│   └──────────────────────┘  │             │                             │
│                              │             │                             │
│   ┌─ llm.py ────────────┐  │             │   ┌─ callClaude() ───────┐  │
│   │  截图→Claude Vision    │  │             │   │  纯文本→Claude Text    │  │
│   │  状态检测 (每次)        │  │             │   │  不需要状态检测          │  │
│   │  数据提取 (每次)        │  │             │   │  不需要数据提取          │  │
│   │  图片描述 (每张)        │  │             │   │  不需要图片描述          │  │
│   │  关键词生成             │  │             │   │  关键词生成 (相同)       │  │
│   │  笔记挑选              │  │             │   │  笔记挑选 (相同)         │  │
│   │  报告综合              │  │             │   │  报告综合 (相同)         │  │
│   └──────────────────────┘  │             │   └──────────────────────┘  │
└─────────────────────────────┘             └─────────────────────────────┘
```

## 逐步对比：研究2个关键词 × 2篇笔记

### Vision方案的API调用

| 步骤 | 调用 | 类型 | 耗时 |
|------|------|------|------|
| 检测状态 (homepage) | Claude Vision | 截图理解 | ~3s |
| 定位搜索框 | UI-TARS | grounding | ~8s |
| 输入关键词+搜索 | pyautogui | 本地 | ~1s |
| **等待加载** | sleep | - | **5-15s** |
| 检测状态 (search_results) | Claude Vision | 截图理解 | ~3s |
| 提取卡片列表 | Claude Vision | 截图→JSON | ~5s |
| LLM选笔记 | Claude Text | 文本推理 | ~2s |
| 定位笔记卡片 | UI-TARS | grounding | ~8s |
| 检测状态 (note_detail) | Claude Vision | 截图理解 | ~3s |
| 提取笔记内容 | Claude Vision | 截图→JSON | ~5s |
| 描述图片1 | Claude Vision | 截图理解 | ~3s |
| 描述图片2 | Claude Vision | 截图理解 | ~3s |
| ... 每张图片 | Claude Vision | 截图理解 | ~3s |
| 提取评论 | Claude Vision | 截图→JSON | ~5s |
| 滚动+再提取 | Claude Vision | 截图→JSON | ~5s |
| 检测状态 (关闭后) | Claude Vision | 截图理解 | ~3s |
| × 重复第二篇笔记 | ... | ... | ... |
| × 重复第二个关键词 | ... | ... | ... |
| 综合报告 | Claude Text | 文本推理 | ~5s |

**单篇笔记 (5张图):** ~8次Claude Vision + 1次UI-TARS ≈ **~50s, ~$0.15**
**4篇笔记:** ~35次Claude Vision + ~8次UI-TARS ≈ **~4min, ~$0.60**

### Extension方案的API调用

| 步骤 | 调用 | 类型 | 耗时 |
|------|------|------|------|
| 检测状态 | URL匹配 | 本地 | **0ms** |
| 定位搜索框 | CSS选择器 | 本地 | **0ms** |
| 输入关键词+搜索 | DOM事件 | 本地 | ~0.2s |
| **等待加载** | sleep | - | **3-5s** |
| 提取卡片列表 | DOM查询 | 本地 | **0ms** |
| LLM选笔记 | Claude Text | 文本推理 | ~2s |
| 打开笔记 | link.click() | 本地 | ~2s |
| 提取笔记内容 | DOM查询 | 本地 | **0ms** |
| 获取所有图片URL | DOM查询 | 本地 | **0ms** |
| 提取评论 | DOM查询 | 本地 | **0ms** |
| 关闭笔记 | DOM事件 | 本地 | ~0.5s |
| × 重复 | ... | ... | ... |
| 综合报告 | Claude Text | 文本推理 | ~5s |

**单篇笔记:** 0次Claude调用 ≈ **~3s, $0.00**
**4篇笔记:** 3次Claude Text (选笔记×2 + 综合) ≈ **~30s, ~$0.02**

## 核心差异总结

| 维度 | Vision方案 | Extension方案 |
|------|-----------|-------------|
| **速度** | ~4min/4篇 | ~30s/4篇 (10x快) |
| **成本** | ~$0.60/session | ~$0.02/session (30x便宜) |
| **数据完整性** | 只能看到屏幕上可见的内容 | 拿到DOM全部数据，包括不可见的 |
| **图片** | 用Vision描述截图，有OCR能力 | 只拿到URL，无内容理解 |
| **评论** | 需要滚动+截图，容易遗漏 | 一次DOM查询拿全部已加载评论 |
| **元素定位** | grounding模型猜坐标(89%) | CSS选择器精准匹配(~100%) |
| **反爬** | 像素级操作，无法检测 | DOM查询可被检测，但风险低 |
| **通用性** | 换网站只需要新prompt | 换网站需要重写CSS选择器 |
| **维护** | 网站改版不影响(视觉语义不变) | 网站改DOM结构就挂(选择器失效) |
| **图片理解** | ✅ 能描述图片内容和文字 | ❌ 只能拿URL，无法理解内容 |
| **部署** | 需要macOS + Accessibility权限 | 只需Chrome浏览器 |

## 关键结论

### Extension方案能完全替代Vision方案吗？

**对于XHS数据采集：基本能，且大部分指标更好。** 标题、正文、评论、作者、互动数据、图片URL——全部可以从DOM直接拿，比Vision更快更准更便宜。

### Vision方案还有什么不可替代的价值？

1. **图片内容理解** — Extension拿到的是图片URL，不知道图片里画了什么、写了什么。Vision方案能描述"这是一张露营装备清单，列出了帐篷48元、月亮椅50元..."。如果研究需要理解图片内容（而非仅仅收集），Vision不可替代。

2. **跨网站通用性** — Extension每换一个网站就要重写选择器。Vision方案理论上只需要新的Skill描述（自然语言），不需要reverse engineer DOM结构。

3. **对抗DOM变化** — XHS改版频繁。class名变了Extension就挂，Vision方案看的是像素语义，改版影响小。

### 推荐方案

**混合方案：Extension做数据采集 + Vision做图片理解。**

```
Extension (fast, cheap)           Vision (slow, expensive, smart)
├─ 状态检测：URL匹配
├─ 元素定位：CSS选择器
├─ 数据提取：DOM查询
├─ 导航操作：DOM事件
├─ 图片URL收集                    → 下载图片 → Claude Vision描述
├─ LLM决策：关键词、选笔记
└─ LLM综合：研究报告
```

这样：
- 速度从4分钟降到~30秒
- 成本从$0.60降到~$0.05 (只有图片理解用Vision)
- 数据完整性比纯Vision更好 (DOM有全部数据)
- 保留了图片内容理解能力

## 代码量对比

| | Vision | Extension |
|---|--------|-----------|
| 核心逻辑 | 831行 (research_agent.py) | ~200行 (background.js) |
| 数据提取 | 157行 (llm.py) + 提取prompts | ~100行 (content.js extractors) |
| 元素定位 | 355行 (grounding.py) | ~20行 (CSS选择器) |
| 屏幕控制 | 209行 (screen.py) | ~50行 (DOM事件) |
| **总计** | **~1550行** | **~370行** |

## 风险

- **XHS反爬**：Extension的DOM查询模式可能被检测。但XHS目前主要防的是API爬虫，对浏览器内JS不太敏感。
- **DOM结构变化**：XHS用React + 可能的CSS Modules，class名可能在每次部署后变化。需要维护选择器映射。
- **懒加载**：某些数据（如完整评论列表、更多搜索结果）需要滚动触发加载，DOM查询只能拿到已加载的。
