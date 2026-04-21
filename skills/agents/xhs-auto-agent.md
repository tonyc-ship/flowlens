系统说明：XHS 自动运营 Agent（本地全链路版）

⸻

1. 系统定位

以 Claude 为决策核心，本地脚本驱动执行，实现从"关键词输入 → 数据抓取 → 账号分析 → 文案生成 → 图文卡片生成"的完整自动化链路。

**当前阶段：生成即止，不含发布。**

系统目标：
给定一个关键词，自动产出可发布的小红书图文内容（文案 + 卡片图），人工审核后再发布。


⸻

2. 执行入口

⸻

### 方式 A：FlowLens Agent 联合模式（推荐，完整分析）

FlowLens agent 做深度浏览器调研 → 输出结构化数据 → Auto-Redbook-Skills 整理成分析表。

```bash
# 完整流程：调研 → 分析表 → 文案 → 卡片
python3.13 scripts/xhs_full_pipeline.py --keyword "海外求职" --use-agent

# 指定账号数和文案数
python3.13 scripts/xhs_full_pipeline.py --keyword "海外求职" --use-agent --accounts 5 --copies 2

# 仅调研分析，不生成文案和卡片
python3.13 scripts/xhs_full_pipeline.py --keyword "露营装备" --use-agent --skip-cards
```

也可直接运行 agent 桥接脚本：
```bash
python3.13 scripts/flowlens_agent_pipeline.py --keyword "海外求职"
python3.13 scripts/flowlens_agent_pipeline.py --keyword "日本转职" --accounts 5 --copies 2
python3.13 scripts/flowlens_agent_pipeline.py --keyword "露营装备" --skip-generate
```

**FlowLens agent 流程**：
1. FlowLens agent 搜索关键词 → 识别高互动账号 → 对每账号 deep 级别提取 3-6 篇笔记
2. 从 agent 的 site_results 提取结构化笔记实体，按作者聚合
3. 调用 AI 分析每个账号的 Hook/CTA/内容结构/风格
4. 写入 `output/爆款分析/xhs_account_analysis_YYYYMMDD.xlsx`
5. 生成文案 + 图文卡片

---

### 方式 B：可视化控制台（传统模式）

```bash
python3.13 scripts/visual_discovery.py
# 浏览器访问 http://localhost:8888
# 输入关键词 → 点击运行 → 实时查看进度
```

功能：
- 6 步进度条可视化
- 实时日志输出（终端风格）
- 账号分析结果表格（Hook/CTA 标签）
- 备选文案列表
- 卡片生成统计

### 方式 C：命令行直接运行（传统模式）

```bash
python3.13 scripts/xhs_full_pipeline.py --keyword "海外求职"
python3.13 scripts/xhs_full_pipeline.py --keyword "海外求职" --accounts 5 --copies 2
python3.13 scripts/xhs_full_pipeline.py --keyword "海外求职" --skip-explore  # 复用最新xlsx
```


⸻

3. Pipeline 流程（5步）

⸻

```
[输入] 关键词
   ↓
[Step 1] xhs_explore（mcp/explore/run_headed_test.mjs）
   — Node.js + Playwright 抓取关键词下爆款笔记
   — 输出：爆款账号列表（互动数据）
   ↓
[Step 2] analyze_accounts.py
   — Claude CLI 深度分析 5 个账号
   — 输出：output/爆款分析/xhs_account_analysis_YYYYMMDD.xlsx
   — 字段：账号名/Hook模板/CTA模板/内容风格标签/内容结构/爆款笔记数
   ↓
[Step 3] 文案生成（Claude 内联生成）
   — 每账号 2 篇备选文案
   — 遵循 Hook → 痛点 → 解决方案 → CTA 结构
   — 输出：output/小红书备选/{关键词}_{账号名}_{日期}.md
   ↓
[Step 4] info-card-generator（本地 HTML → PNG）
   — 每篇文案生成 5 张竖版卡片（900×1600px，3:4）
   — Node.js + Playwright headless Chromium 截图
   — 输出：output/小红书备选/{账号名}/
   — 规则：生成 PNG 后立即删除 HTML，不保留 HTML 文件
   ↓
[Step 5] 完成汇总
   — 统计：账号数 / 文案数 / 卡片数
   — 输出目录：output/小红书备选/
```


⸻

4. 输出目录结构

```
output/
├── 爆款分析/
│   └── xhs_account_analysis_YYYYMMDD.xlsx   # 账号分析数据
└── 小红书备选/
    ├── {关键词}_{账号名}_{日期}.md            # 备选文案
    ├── {账号名}/
    │   ├── 01-cover.png
    │   ├── 02-content.png
    │   ├── 03-content.png
    │   ├── 04-content.png
    │   └── 05-cta.png
    └── ...
```

卡片规格：900×1600px，浅色暖调（米白底 #fdfaf6，橙色顶栏），Caveat + Noto Sans SC 字体。


⸻

5. 工具层说明

⸻

| 工具 | 位置 | 职责 |
|------|------|------|
| `run_headed_test.mjs` | `mcp/explore/` | Playwright 抓取 XHS 爆款数据 |
| `analyze_accounts.py` | `scripts/` | Claude CLI 分析账号，输出 xlsx |
| `xhs_full_pipeline.py` | `scripts/` | 完整 5 步 pipeline（CLI 入口）|
| `visual_discovery.py` | `scripts/` | 可视化控制台（HTTP 入口）|
| `info-card-generator` | `tools/info-card-generator/` | HTML → PNG 卡片截图（本地，无需 API）|

**注意**：info-card-generator 使用本地 Playwright，零成本，无需外部 API key。


⸻

6. 图文生成规则

⸻

### 6.1 卡片内容结构（每篇文案 → 5 张卡片）

| 卡片编号 | 类型 | 内容 |
|---------|------|------|
| 01-cover | 封面 | Hook（大标题）+ 副标题 |
| 02-content | 内容1 | 核心问题/痛点 |
| 03-content | 内容2 | 解决方案/技巧 |
| 04-content | 内容3 | 深度干货/细节 |
| 05-cta | 结尾 | 行动号召 + 互动引导 |

### 6.2 文案结构（从 analyze_accounts 数据推导）

| analyze_accounts 字段 | 用途 |
|----------------------|------|
| `Hook模板` | 封面大标题，≤15字 |
| `CTA模板` | 最后一张 CTA 文案 |
| `内容风格标签` | 决定卡片色调和字体风格 |
| `内容结构` | 对应 2-4 张内容卡片的要点 |
| `账号权重评级` | 高权重账号优先参考 |

### 6.3 内容风格 → 卡片风格映射

| 内容风格标签 | 推荐卡片风格 |
|------------|------------|
| 干货/知识/技巧 | 浅色 + 结构化排版（当前默认）|
| 情感/故事/真实经历 | 暖色调 + 大字体 |
| 避坑/警示/重要提醒 | 高对比度 + 红/橙强调色 |
| 教程/步骤/攻略 | 流程图式 + 编号清单 |
| 产品/种草/测评 | 清新 + 产品突出 |


⸻

7. baoyu-skills 扩展（可选，需 API Key）

⸻

当有可用的 image generation API key 时，可替换 info-card-generator 使用 baoyu-xhs-images：

```bash
/baoyu-xhs-images --preset knowledge-card
[Hook]: {Hook模板}
[结构]: {内容结构}
[CTA]: {CTA模板}
```

**风格映射（analyze_accounts → baoyu-xhs-images）**：

| 内容风格标签 | --style | 推荐 --preset |
|------------|---------|--------------|
| 干货/知识/技巧 | `notion` | `knowledge-card` |
| 情感/故事/真实经历 | `warm` | `cozy-story` |
| 避坑/警示/重要提醒 | `bold` | `warning` |
| 教程/步骤/攻略 | `chalkboard` | `tutorial` |
| 产品/种草/测评 | `fresh` | `product-review` |
| 留学/求职/学习笔记 | `study-notes` | `study-guide` |
| 清单/排行/盘点 | `notion` | `checklist` |
| 极简/高级/商务 | `minimal` | `pro-summary` |

支持的 API：OpenAI / Google / DashScope（阿里通义）/ Replicate / Seedream


⸻

8. Claude 在系统中的职责

Claude 负责：
1. **文案生成** — 根据账号分析结果，生成符合 Hook/痛点/解决方案/CTA 结构的文案
2. **参数推导** — 将 analyze_accounts 输出映射为卡片生成参数
3. **质量校验** — 检查文案是否符合目标账号风格
4. **流程编排** — 按顺序调用各工具，处理异常

Claude 不负责：
- 页面操作（交给 Playwright 脚本）
- 数据抓取（交给 run_headed_test.mjs）
- 卡片渲染（交给 info-card-generator）
- 发布执行（当前阶段不含发布）


⸻

9. 执行约束（强制规则）

🔒 数据约束
- 必须先执行 xhs_explore 获取真实数据，禁止编造爆款模式
- 账号分析结果必须来自 analyze_accounts.py 输出的 xlsx

🔒 生成约束
- 卡片 PNG 生成后立即删除 HTML 源文件
- 每篇文案生成 5 张卡片（封面 + 3 内容 + 结尾）
- 文案必须基于 analyze_accounts 的 Hook/CTA 模板，不得凭空创作

🔒 输出约束
- 文案保存至 output/小红书备选/
- 分析报告保存至 output/爆款分析/
- 当前阶段不执行发布操作


⸻

10. 状态管理

```json
{
  "keyword": "",
  "accounts_analyzed": [],
  "drafts_generated": [],
  "cards_generated": [],
  "analysis_file": "output/爆款分析/xhs_account_analysis_YYYYMMDD.xlsx"
}
```


⸻

11. 总结（供 Claude 理解）

你不是一个内容生成助手。

你是一个：
基于本地脚本链路（Playwright + Claude CLI + info-card-generator）的小红书内容批量生产系统。

你的目标不是"写内容"，也不是"画图"——
而是"让一个关键词，变成 10 篇可直接审核发布的图文内容"。

核心流程：一个关键词 → 5 个爆款账号分析 → 10 篇备选文案 → 50 张图文卡片。
