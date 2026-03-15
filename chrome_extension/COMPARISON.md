# Chrome Extension (DOM) vs Vision Approach — 对比分析

## 实测结果 (2026-03-15)

用Playwright + 注入Chrome cookie实测DOM方案，同样的关键词("露营装备推荐", "露营好物清单")：

| | Vision方案 (research_agent.py) | DOM方案 (Playwright) |
|---|---|---|
| **搜索页卡片提取** | 9-10张/次 (LLM从截图提取) | **26张/次** (DOM直接读) |
| **笔记详情页** | ✅ 正常打开和提取 | ❌ **"当前笔记暂时无法浏览"** |
| **评论** | ✅ 提取到6-8条 | ❌ 0条 (页面被封) |
| **图片** | ✅ 浏览并描述每张 | ❌ 0张 (页面被封) |
| **耗时** | ~4min | ~87s (但大部分笔记内容为空) |
| **LLM调用** | ~35次Vision + ~8次grounding | 4次Text |

### 关键发现：XHS对笔记详情页有反自动化检测

搜索结果页可以正常访问，但**打开任何一篇笔记都被拦截**，显示"当前笔记暂时无法浏览 — 请打开小红书App扫码查看"。即使注入了真实的登录cookie也无效。

XHS的反自动化检测可能基于：
- **WebDriver标记** (`navigator.webdriver = true`)
- **Canvas/WebGL指纹**：headless浏览器的渲染指纹与真实浏览器不同
- **行为分析**：直接URL跳转到笔记页（无搜索→点击的自然浏览序列）
- **TLS指纹**：Chromium headless的TLS握手特征

Vision方案不受影响，因为它操作的是用户真实的Chrome浏览器进程，有完整的浏览器指纹和自然的用户行为序列。

### 那Chrome Extension（非headless）能绕过吗？

理论上可以——Chrome Extension的content script运行在真实的浏览器标签页内，`navigator.webdriver`为false，有真实的Canvas指纹。**但这需要实际测试验证。** Extension方案的反爬风险比headless Playwright低很多，但XHS的检测可能还包括请求频率、行为序列等因素。

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

## 关键结论

### Extension方案能完全替代Vision方案吗？

**实测结论：不能。** XHS对笔记详情页有反自动化检测，headless浏览器（即使注入真实cookie）也无法打开笔记内容。搜索列表页可以拿到更多卡片（26 vs 9），但笔记正文、评论、图片全部无法获取。

### Vision方案的不可替代价值

1. **绕过反自动化** — 操作真实浏览器进程，完全不可检测。这不是理论优势，是**实测验证的硬需求**。DOM/Playwright方案直接被XHS拦截。

2. **图片内容理解** — Extension只能拿到图片URL，不知道图片里画了什么。Vision方案能描述"这是一张露营装备清单，列出了帐篷48元、月亮椅50元..."。

3. **跨网站通用性** — Extension每换一个网站就要重写选择器和反反爬逻辑。Vision方案理论上只需要新的Skill描述。

### 推荐方案

考虑到XHS的反自动化现实，纯DOM方案不可行。可选路线：

**方案A：纯Vision（当前方案），优化效率**
```
Vision方案现状，但减少不必要的LLM调用：
├─ 状态检测：从每次LLM调用改为基于URL/简单像素特征
├─ 数据提取：保持Vision LLM（无法替代）
├─ 图片描述：保持Vision LLM + 加强OCR
└─ 导航：保持grounding + screen control
```

**方案B：Chrome Extension（真实标签页内）+ Vision辅助**
```
如果Extension的content script能绕过反自动化检测（待验证）：
├─ content.js：DOM提取搜索卡片、笔记正文、评论（快+准+免费）
├─ 导航：DOM事件点击（精确+快速）
├─ 图片理解：下载图片URL → Claude Vision描述（仅此需要LLM）
└─ LLM决策：关键词、选笔记、综合报告
```

**方案C：Vision + 轻量DOM辅助（最稳妥）**
```
主体仍然是Vision方案操作真实浏览器，但在可以用DOM的地方用DOM：
├─ 通过claude-in-chrome MCP在真实标签页内注入JS提取DOM数据
├─ 搜索卡片：DOM提取（更快更全，26张 vs 9张）
├─ 笔记正文：DOM提取（结构化、不需要Vision）
├─ 评论：DOM提取（一次拿全，不需要滚动）
├─ 导航/点击：仍用Vision grounding（最可靠）
├─ 图片理解：仍用Vision（不可替代）
└─ 状态检测：URL匹配 + 简单DOM检查
```

方案C最值得探索——在真实浏览器环境中（不被反自动化拦截），用DOM做数据提取（快+准），用Vision做导航和图片理解（可靠+智能）。

## 代码量对比

| | Vision | Extension |
|---|--------|-----------|
| 核心逻辑 | 831行 (research_agent.py) | ~200行 (background.js) |
| 数据提取 | 157行 (llm.py) + 提取prompts | ~100行 (content.js extractors) |
| 元素定位 | 355行 (grounding.py) | ~20行 (CSS选择器) |
| 屏幕控制 | 209行 (screen.py) | ~50行 (DOM事件) |
| **总计** | **~1550行** | **~370行** |

## 风险

- **XHS反自动化（已验证）**：Playwright headless无法打开笔记详情。Chrome Extension content script是否能绕过待验证。
- **DOM结构变化**：XHS用React + 可能的CSS Modules，class名可能在每次部署后变化。需要维护选择器映射。
- **懒加载**：某些数据（如完整评论列表、更多搜索结果）需要滚动触发加载，DOM查询只能拿到已加载的。

## 实测数据文件

- `tests/eval_report/dom_research/` — Playwright DOM方案输出
  - `00_homepage.png` — 首页（弹出登录框）
  - `search_露营装备推荐.png` — 搜索结果（cookie注入后成功加载26张卡片）
  - `note_*.png` — 笔记页（全部显示"当前笔记暂时无法浏览"）
  - `report.json` — 完整运行日志
- `tests/eval_report/final_research/` — Vision方案输出（同主题，4篇笔记全部成功）
