# Chrome Extension (DOM) vs Vision Approach — 对比分析

## 实测结果 Round 2 (2026-03-16) — 真实Chrome + AppleScript JS注入

用AppleScript在用户已登录的真实Chrome中注入JS执行DOM操作。**这是公平对比**——和Vision方案一样操作真实浏览器。

| | Vision方案 (research_agent.py) | DOM方案 (AppleScript JS注入) |
|---|---|---|
| **搜索页卡片提取** | 9-10张/次 (LLM从截图提取) | **16张/次** (DOM直接读) |
| **笔记详情页** | ✅ 正常打开和提取 (~90%) | ⚠️ **50%成功** (2/4篇提取到内容) |
| **评论** | ✅ 提取到6-8条 | ✅ **29条** (但有重复，每条出现2次) |
| **图片URL** | ✅ 逐张浏览+描述内容 | ⚠️ 不稳定 (11张/0张/0张) |
| **图片理解** | ✅ 描述图片内容和文字 | ❌ 只有URL，无内容理解 |
| **耗时** | ~4min | **64.7s** (含17s LLM综合) |
| **LLM调用** | ~35次Vision + ~8次grounding | **4次Text** |
| **反自动化** | ✅ 完全绕过 | ✅ **完全绕过** (真实Chrome) |

### 关键发现

1. **反自动化不是问题** — 在真实Chrome中，DOM方案和Vision方案一样不会被拦截。之前Playwright headless被拦截是因为headless浏览器指纹，不是DOM操作本身的问题。

2. **DOM提取可靠性不如Vision** — 50%的笔记内容提取失败（title/author/content全空），可能是视频类笔记的DOM结构不同。CSS选择器需要针对不同笔记类型维护。Vision方案通过截图理解不受此影响。

3. **评论提取数量远超Vision** — DOM一次拿到29条（虽然有重复bug），Vision需要滚动截图只拿到6-8条。

4. **点击精度问题** — 按index点击有偏移（想打开A笔记实际打开了B）。Vision方案通过grounding模型定位更准。

### Round 1 回顾 (2026-03-15) — Playwright headless（不公平对比）

之前用Playwright headless + cookie注入测试，搜索页拿到26张卡片，但所有笔记详情页被XHS拦截（"当前笔记暂时无法浏览"）。**这是headless浏览器指纹被检测的问题，不代表DOM方案本身的局限。**

---

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

## 关键结论 (更新于2026-03-16实测后)

### Extension方案能完全替代Vision方案吗？

**实测结论：不能完全替代，但比预期更接近。** 在真实Chrome中DOM方案不会被反自动化拦截（之前的结论是基于headless Playwright的不公平测试）。但DOM方案有两个硬伤：
1. **CSS选择器脆弱** — 50%笔记提取失败（视频笔记DOM结构不同），需要为每种笔记类型维护选择器
2. **无法理解图片内容** — 只能拿URL，无法描述图片里画了什么、有什么文字

### Vision方案的不可替代价值

1. ~~绕过反自动化~~ — **更正：在真实Chrome中DOM方案同样不被拦截。** 反自动化只针对headless浏览器。

2. **提取鲁棒性** — Vision通过截图理解内容，不依赖CSS选择器，对不同类型的笔记（图文、视频、长笔记）都能提取。DOM方案50%提取失败率不可接受。

3. **图片内容理解** — Extension只能拿到图片URL。Vision方案能描述图片内容和OCR文字。

4. **跨网站通用性** — Extension每换一个网站要重写选择器。Vision方案只需新prompt。

### 推荐方案

**方案C：Vision + DOM辅助（最优解）**
```
在真实Chrome中混合使用：
├─ 搜索卡片：DOM提取（更快更全，16张 vs 9张）
├─ 笔记正文：DOM提取优先，失败时fallback到Vision
├─ 评论：DOM提取（一次拿29条 vs Vision滚动截图6-8条）
├─ 导航/点击：Vision grounding（比DOM index更准）
├─ 图片理解：Vision（不可替代）
├─ 状态检测：URL匹配 + DOM检查（免费+即时）
└─ LLM决策：关键词、选笔记、综合报告
```

方案C结合了两者优势：DOM负责快速精确的数据提取，Vision负责导航定位和图片理解。预估效果：速度接近DOM方案（~90s），提取可靠性接近Vision方案（~90%+），图片理解保持Vision能力。

## 代码量对比

| | Vision | Extension |
|---|--------|-----------|
| 核心逻辑 | 831行 (research_agent.py) | ~200行 (background.js) |
| 数据提取 | 157行 (llm.py) + 提取prompts | ~100行 (content.js extractors) |
| 元素定位 | 355行 (grounding.py) | ~20行 (CSS选择器) |
| 屏幕控制 | 209行 (screen.py) | ~50行 (DOM事件) |
| **总计** | **~1550行** | **~370行** |

## 风险

- **XHS反自动化**：~~已验证DOM方案被拦截~~ → **更正：仅headless Playwright被拦截，真实Chrome中DOM方案正常工作。**
- **DOM结构变化**：XHS用React，不同类型笔记（图文vs视频）DOM结构不同。当前选择器对视频笔记50%失效。
- **评论去重**：DOM提取的评论每条出现2次，需要去重逻辑。
- **懒加载**：某些数据（完整评论列表、更多搜索结果）需要滚动触发加载。
- **点击精度**：按DOM index点击搜索卡片有偏移，可能打开错误的笔记。

## 实测数据文件

- `tests/eval_report/dom_research/report.json` — AppleScript DOM方案输出 (2026-03-16，真实Chrome)
  - 2个关键词, 16张卡片/关键词, 4篇笔记 (2篇提取成功)
  - 64.7s总耗时, 4次LLM text调用
- `tests/eval_report/final_research/` — Vision方案输出（同主题，3篇笔记全部成功）
