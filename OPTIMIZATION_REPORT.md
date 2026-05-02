# FlowLens 优化参考报告

> 基于对 browser-use、skyvern、understudy 三个开源项目的深度架构分析
> 生成日期：2026-04-02

---

## 一、三个参考项目速览

### Browser-Use
- **定位**: 通用 LLM 驱动的浏览器自动化框架（Python，MIT）
- **浏览器控制**: CDP 原生（cdp-use 库，非 Playwright）
- **DOM 处理**: 三树融合（DOM树 + 无障碍树 + 布局快照）→ 紧凑树格式
- **行动系统**: 装饰器注册 + Pydantic 结构化输出
- **核心理念**: LLM 是决策中心，DOM 树是感知输入

### Skyvern
- **定位**: 视觉 + DOM 双模态，生产级浏览器自动化平台（Python，MIT）
- **浏览器控制**: Playwright
- **DOM 处理**: JS 注入 domUtils.js（2971行）→ HTML 压缩格式
- **行动系统**: 差异化 mini-agent（复杂下拉、自动完成等单独处理）
- **核心理念**: 企业级，有完整 DB/API/工作流/计费体系

### Understudy
- **定位**: 跨平面计算机代理（浏览器 + GUI + Shell + 消息，TypeScript，MIT）
- **浏览器控制**: Playwright + CDP 扩展双模
- **DOM 处理**: Playwright ARIA snapshot + ref 标识符系统
- **行动系统**: 40+ 工具，插件化，技能文件系统
- **核心理念**: 规划模型与视觉定位模型分离的双模型架构

---

## 二、核心技术对比矩阵

| 维度 | Browser-Use | Skyvern | Understudy | **FlowLens** |
|------|-------------|---------|------------|----------------|
| 浏览器控制 | CDP 原生 | Playwright | Playwright + CDP扩展 | **Chrome扩展 + WebSocket** |
| DOM 表示 | 三树融合紧凑树 | JS注入→HTML格式 | ARIA snapshot | DOM提取 + 自定义格式 |
| LLM 架构 | 单模型 | 单模型（多引擎） | **双模型（规划+定位分离）** | 单模型 |
| 反爬处理 | 软策略+云浏览器 | 分类器+重试 | 无 | **XHS专项防爬策略** |
| 技能/知识层 | 装饰器注册 | 无固定技能层 | SKILL.md + 自动结晶 | capability catalog |
| 多平台支持 | 否 | 否 | **是（GUI+浏览器+消息）** | 否（XHS专项） |
| 生产化程度 | 轻量 SDK | **完整平台（DB+API）** | 中等 | 脚本级 |
| 本地视觉 | 否 | 否 | 部分 | **是（Apple OCR + MLX）** |
| 视频转录 | 否 | 否 | 否 | **是（Whisper）** |
| 会话录制 | 可选 GIF | 视频录制 | 无 | **GIF + HTML 报告** |

---

## 三、各项目最值得借鉴的技术点

### 3.1 从 Browser-Use 借鉴

#### ① 三树融合 DOM 提取（最值得深入研究）

Browser-Use 通过 CDP 同时拉取三个数据源并融合：

```
CDP: DOMSnapshot.captureSnapshot  → 布局 + 边界框 + 绘制顺序
CDP: DOM.getDocument              → 完整 DOM 结构 + shadow roots
CDP: Accessibility.getFullAXTree  → 无障碍角色 + 名称 + 属性
                  ↓ 融合
         EnhancedDOMTreeNode（三合一节点）
                  ↓ 序列化
      紧凑树格式（只保留语义化交互元素）
```

**对 FlowLens 的价值**：XHS 有大量自定义组件，纯 DOM 树缺少语义，纯无障碍树缺少布局信息。三树融合后 LLM 对元素的理解准确率会显著提升，减少错误点击。

LLM 看到的格式样例：
```
[33]<div />
    User form
    [35]<input type=text placeholder=Enter name />
    *[38]<button aria-label=Submit form />   ← *表示本步新出现的元素
        Submit
```

#### ② Paint Order 过滤（去掉被遮挡元素）

通过 CDP layout snapshot 的绘制顺序，过滤掉被其他元素视觉遮挡的 interactive elements，防止 LLM 尝试点击不可见元素。XHS 经常有浮层、弹窗遮罩，这个过滤很实用。

#### ③ 新元素标记 `*[index]`

每步之后，DOM 中新出现的元素用 `*` 标记，明确告知 LLM"你上一步操作导致这些元素出现了"。对处理 XHS 动态内容（弹窗、下拉菜单、autocomplete）非常关键。

#### ④ ActionLoopDetector + 搜索词归一化

```python
# 搜索词 tokenize + 排序后再 hash
# "buy red shoes" 和 "red shoes buy" 被识别为相同动作
# 渐进式 nudge（5→8→12步），不是硬中断，让 LLM 有机会自我修正
```

#### ⑤ BrowserError 双层记忆设计

```python
class BrowserError(Exception):
    short_term_memory: str | None  # 只展示一次给 LLM
    long_term_memory: str | None   # 跨步骤持久化
```

精准控制 LLM 对错误的关注度，避免错误信息占据过多 context window，非常适合 FlowLens 的长任务场景。

#### ⑥ 消息历史压缩（MessageManager）

当对话历史过长时，用一个 LLM 调用把旧消息总结压缩（默认 40,000 字符触发，保留最近 6 条，旧的压缩到 6,000 字以内，每 15 步触发一次）。对 FlowLens 的长时间研究任务很有参考价值。

---

### 3.2 从 Skyvern 借鉴

#### ① IncrementalScraping + MutationObserver（高价值）

```javascript
// domUtils.js 中设置 globalObserverForDOMIncrement
// 点击后只抓取 DOM 变化部分，不重新全量扫描
// 对 dropdown / autocomplete / modal 效果极好
```

**对 FlowLens 的价值**：XHS 的笔记详情弹窗、标签下拉、搜索建议每次打开后都需要等待并重新扫描全量 DOM。改用增量提取后，只抓新出现的元素，速度预计提升 30-50%。

#### ② SVG / CSS 图标哈希缓存

```python
# SVG 内容 SHA256 hash → LLM 图标描述 → 本地缓存
# 同一个 icon 只描述一次
# XHS 大量复用的点赞/收藏/分享图标只调用一次 LLM
```

可以显著降低每步的 token 消耗，对大批量笔记处理效果明显。

#### ③ Long URL Hashing（极简改造，高收益）

```jinja2
{# URL > 150 字符 → SHA256 hash → 替换为模板变量 #}
{{ href_vars.abc123 }}
```

XHS 的图片 URL、CDN 追踪参数通常很长，这个改造预计能减少 20-30% 的 prompt token。

#### ④ 失败类型分类器

```python
classify_from_failure_reason() → {
    ANTI_BOT_DETECTION,   # 反爬触发
    BROWSER_ERROR,        # 浏览器异常
    NAVIGATION_FAILURE,   # 导航失败
    PAGE_LOAD_TIMEOUT,    # 超时
    AUTH_FAILURE,         # 需要登录
    LLM_ERROR,            # 模型调用失败
    ...
}
```

FlowLens 已有 XHS 特定的反爬状态检测，但没有系统化分类。结构化失败类型能让重试策略更精准（比如 ANTI_BOT 触发时降速，LLM_ERROR 时切换模型）。

#### ⑤ Complete 二次验证

```
LLM 声明任务完成
    ↓
额外一次 LLM 调用确认
    ↓
返回 complete / terminate / continue_step
```

防止 LLM 过早声明研究任务完成，适用于 FlowLens 的质量保证环节。

#### ⑥ 元素树转 HTML 格式（非 JSON）

Skyvern 把内部 JSON 元素树转换回紧凑 HTML 发给 LLM，比 JSON 格式节省大量 token。`purgeable` 标志可以去掉包装元素只保留子节点。

---

### 3.3 从 Understudy 借鉴

#### ① 双模型架构（最具战略价值的设计）

```
用户任务
    ↓
规划模型（Claude/GPT）
→ 理解任务语义
→ 决定点击哪个元素（描述，不需要坐标）
    ↓
定位模型（轻量视觉模型）
→ 接收截图 + 元素描述
→ 预测精确像素坐标
→ 返回 bounding box + 置信度
```

规划模型永远不需要预测像素坐标，大幅降低其认知负担。定位模型可以单独优化（更小更快更便宜）。

**对 FlowLens 的映射**：
- 规划层：Claude Sonnet（理解 XHS 内容语义，决定下一步）
- 定位层：Apple OCR 或本地 MLX（精确定位，低延迟低成本）

定位模型还有一套精炼流水线：HiDPI 归一化 → 自适应缩放 → 小目标裁剪放大 → 仿真叠加验证 → 多轮重试。

#### ② Route Retry Guard Policy

```python
# 跟踪每条执行路径的连续失败次数
# DOM提取路径 / OCR路径 / Vision路径 / Shell路径
# 连续失败 2 次 → 下次 prompt 前插入警告，引导 LLM 换路径
# 成功后重置计数器
```

对 FlowLens 的价值：DOM提取 → OCR → Vision API 这三条路径可以用同样的机制实现自动降级，不需要硬编码 fallback 逻辑。

#### ③ Context Overflow Recovery（透明恢复）

```python
# LLM 返回 context-too-long 错误
# 原地压缩旧工具结果
# 保留最近消息
# 自动重试
# 不向上层抛出异常
```

#### ④ SKILL.md 声明式技能文件

```markdown
---
name: xhs_researcher
description: XHS content research and analysis
type: skill
---

When researching XHS content, you should...
```

放入对应目录自动加载。参考这种机制，可以让 FlowLens 的平台知识库更易维护和扩展。

#### ⑤ 工作流结晶化（Workflow Crystallization）

系统自动分析历史对话，识别重复工作模式，自动合成可复用技能。对 FlowLens 的启示：从历史成功任务中自动提炼 XHS 研究最佳实践，沉淀为下次任务的先验知识。

---

## 四、FlowLens 的核心护城河（不要丢）

对比三个项目，FlowLens 有一些他们完全没有的能力：

| FlowLens 独特能力 | 说明 |
|---------------------|------|
| **XHS 深度专项知识** | XHS 反爬策略、UI 交互模式、笔记结构、创作者数据——其他项目完全没有 |
| **本地视觉推理（MLX）** | 支持本地 Qwen3.5-9B MLX 模型，三个项目都依赖云端 API |
| **Apple OCR 集成** | macOS 原生 OCR，速度和成本优于云端 OCR |
| **Whisper 视频转录** | XHS 视频笔记内容提取，其他项目无此能力 |
| **Chrome 扩展 + WebSocket 架构** | 在真实用户 Chrome 环境运行，复用登录状态，无需额外反检测配置 |
| **完整 HTML 报告 + GIF 录制** | 可审计的任务运行记录，三个项目都没这么完整 |
| **桌面 App Shell（Tauri）** | 有完整桌面端集成路径，其他项目都是纯命令行工具 |

---

## 五、不建议重造的轮子

| 功能 | 推荐方案 | 原因 |
|------|----------|------|
| 通用 LLM 路由 / 降级 | 参考 Skyvern，引入 LiteLLM | 支持 100+ 模型，自带 fallback/retry/rate limit |
| ActionLoopDetector | 参考 browser-use 实现（~30行） | 逻辑简单，直接移植即可 |
| Context window 压缩 | 参考 browser-use MessageManager | 成熟策略，不需要自己设计 |
| Playwright 封装 | 不需要 | FlowLens 的 Chrome 扩展方案复用用户登录状态，比 Playwright 更适合 XHS |

---

## 六、优先级路线图

### 短期（1-2 周，高收益低成本）

1. **Long URL Hash 压缩**
   - 改造 `flowlens/core/bridge.py` 或 DOM 序列化层
   - XHS 图片/CDN URL 哈希替换
   - 预计 prompt token 减少 20%，改动 <50 行

2. **失败类型分类器**
   - 在 `flowlens/core/` 下新建 `failure_classifier.py`
   - 给 XHS 的各类异常（反爬/超时/验证码/空页）贴标签
   - 让重试策略按类型分化处理

3. **新元素标记机制**
   - DOM diff 时标出新出现的元素（`*` 前缀）
   - 改造 DOM 序列化，加入上一步状态对比

4. **ActionLoopDetector**
   - 参考 browser-use 实现，加入渐进式 nudge
   - 防止 XHS 任务陷入重复点击/搜索循环

### 中期（1-3 个月，战略价值）

5. **IncrementalDOM 提取**
   - 在 Chrome 扩展 background worker 中加入 MutationObserver
   - 弹窗/下拉打开后只发送 DOM diff
   - 预计速度提升 30-50%

6. **Paint Order 过滤 + 三树融合**
   - 扩展 `flowlens/core/bridge.py` 的 CDP 调用
   - 额外拉取 Accessibility tree 和 layout snapshot
   - 融合后过滤被遮挡元素

7. **双模型架构**
   - 将视觉定位拆分为独立模型调用
   - 规划模型（Claude Sonnet）专注语义决策
   - 定位模型可用本地 MLX 或轻量 OCR

8. **Route Guard Policy**
   - DOM提取 / OCR / Vision API 三条路径
   - 连续失败自动降级，成功后恢复
   - 解耦 fallback 逻辑，不再硬编码

### 长期（3 个月+，差异化护城河）

9. **平台 capability pack 架构**
   - 将 XHS 经验结构化为可迁移的 capability pack
   - 参考 Understudy 的 SKILL.md 机制做声明式定义
   - 为第二个平台（微博/抖音/B站）打好基础

10. **任务质量评估系统**
    - 内容覆盖度评分（搜集了多少相关笔记）
    - 提取质量评分（每条笔记的数据完整性）
    - 参考 Skyvern Complete 二次验证设计

11. **离线批量分析流水线**
    - 已采集内容 → 完全本地化深度分析
    - Apple OCR + MLX + Whisper 全链路离线
    - 面向隐私敏感和成本敏感的场景

---

## 六（续）、来自 Claude Code 的优化借鉴

> 基于对 Claude Code v2.1.88 源码（512,664行）的深度分析
> 完整分析报告见 `/Users/tonychong/claude-code-source-code/ANALYSIS_REPORT.md`

### ① 工具并发批处理（isConcurrencySafe 模式）

Claude Code 在 `toolOrchestration.ts` 中，把工具调用按只读/写分成两类批次：

```python
# 借鉴到 FlowLens 的思路
class Tool:
    def is_concurrency_safe(self, input) -> bool: ...

# 连续的只读操作 → 并发执行（截图/DOM提取/OCR同时进行）
# 写操作（点击/输入）→ 串行执行
# Context Modifier 队列化，批次完成后原子应用，防止状态竞争
```

**对 FlowLens 的价值**：XHS 任务中 `截图 + DOM提取 + 图片OCR` 完全可并发，目前是串行。改造后单卡片处理速度预计提升 40%+。

#### ② ToolSearch 延迟工具加载

```python
# 工具分两类
class Tool:
    should_defer: bool = False   # 延迟加载 schema
    always_load: bool = False    # 永远不延迟

# 初始 prompt 只含核心工具 schema
# 模型调用 ToolSearch(query) → 才获得具体工具 schema → 才能调用
```

**对 FlowLens 的价值**：FlowLens 的 capability catalog 已经有类似分层概念。将 `lite` 能力始终加载，`deep` 能力（视频转录、详细图片分析等）延迟加载，可大幅缩小初始系统提示。

#### ③ 工具结果预算 + 磁盘持久化

```python
# 每个工具定义最大结果长度
class Tool:
    max_result_size_chars: int = 50_000

# 超过时：
# 1. 结果写入 task_runs/<session>/.tool_results/<id>.txt
# 2. LLM 收到：摘要 + 文件路径（可用 FileRead 工具读取）
# 3. ContentReplacementState 记录替换，会话恢复时精确重建
```

**对 FlowLens 的价值**：XHS 批量研究任务的笔记内容、OCR 文本、图片描述可能很长，超出预算时存盘，让 LLM 按需读取，而非塞满 context window。

#### ④ 错误自愈的 isWithheld 模式

```python
# 不是让错误向上冒泡，而是原地自愈
class QueryLoop:
    def handle_api_error(self, error):
        if error.type == 'prompt_too_long':
            self.is_withheld_prompt_too_long = True
            self.compact_and_retry()          # 压缩后重试
            return  # SDK/UI 完全感知不到这个错误
        if error.type == 'max_output_tokens':
            self.increase_token_limit_and_retry()
            return
        raise error  # 真正无法自愈的才抛出
```

**对 FlowLens 的价值**：XHS 反爬触发时，当前实现是直接失败。用 isWithheld 模式改造后，可以先降速重试、换策略，对上层任务透明，提升任务完成率。

#### ⑤ Hook 生命周期标准化（20+ 事件）

参考 Claude Code 的完整 Hook 事件体系，为 FlowLens 定义标准生命周期：

```python
# 建议 FlowLens 标准化的 Hook 事件
HOOK_EVENTS = {
    # 工具层
    'PreToolUse',        # 可修改工具输入 / 覆盖权限决策
    'PostToolUse',
    'PostToolUseFailure',

    # 任务层
    'TaskStart',
    'TaskComplete',
    'TaskFailed',

    # XHS 特定
    'NoteFetched',       # 笔记数据抓取完成
    'AntiBot_Detected',  # 触发反爬
    'SearchComplete',    # 搜索结果页加载完成

    # 会话层
    'SessionStart',
    'SessionEnd',
    'ContextCompacted',
}
```

`PreToolUse` hook 输出支持 `updated_input`（修改工具参数）和 `permission_decision`（覆盖权限），这是 FlowLens 目前没有的。

#### ⑥ JSONL 会话存储 + 断点续跑

Claude Code 的 JSONL 格式非常适合 FlowLens 的任务记录：

```python
# 每行一条记录，类型丰富
{"type": "message",   "role": "assistant", "content": "...", "timestamp": "..."}
{"type": "compact_boundary", "step": 15}           # 压缩操作边界
{"type": "content_replacement", "tool_use_id": "..."} # 工具结果替换记录
{"type": "task_summary", "notes_collected": 42}    # 周期性摘要

# 支持断点续跑：
# 1. 读取 content_replacement 记录，重建工具结果替换状态
# 2. 跳过压缩点之前的内容（SKIP_PRECOMPACT_THRESHOLD）
# 3. 快速尾部读取获取最新状态（不全量解析）
```

**对 FlowLens 的价值**：当前 FlowLens 的任务运行输出是 HTML 报告 + GIF，不支持恢复中断的任务。JSONL 格式可以让长时间 XHS 研究任务支持断点续跑。

### 短期可落地（直接加入上面的优先级路线图）

| 优化点 | 落地位置 | 预期收益 |
|--------|----------|----------|
| 工具并发批处理 | `flowlens/core/bridge.py` | 单卡片处理速度 +40% |
| 工具结果磁盘持久化 | `flowlens/core/` 新建 `result_budget.py` | 长任务 context 可控 |
| isWithheld 错误自愈 | `flowlens/workflows/xhs/` | 任务完成率提升 |
| JSONL 会话格式 | `flowlens/core/recorder.py` 扩展 | 支持断点续跑 |

---

## 七、各项目代码参考位置

### Browser-Use 关键文件

| 功能 | 文件路径 |
|------|----------|
| 三树融合 DOM | `browser_use/dom/service.py` |
| DOM 序列化 | `browser_use/dom/serializer/serializer.py` |
| Paint Order 过滤 | `browser_use/dom/serializer/paint_order.py` |
| ActionLoopDetector | `browser_use/agent/views.py` |
| 消息历史压缩 | `browser_use/agent/message_manager/service.py` |
| BrowserError 双层记忆 | `browser_use/browser/views.py` |
| 行动系统 | `browser_use/tools/service.py` + `browser_use/tools/registry/service.py` |

### Skyvern 关键文件

| 功能 | 文件路径 |
|------|----------|
| IncrementalScraping | `skyvern/webeye/scraper/scraper.py` |
| MutationObserver DOM | `skyvern/webeye/scraper/domUtils.js` |
| SVG 哈希缓存 | `skyvern/forge/agent_functions.py` |
| 失败分类器 | `skyvern/forge/failure_classifier.py` |
| 元素树转 HTML | `skyvern/webeye/scraper/scraped_page.py` (`json_to_html`) |
| Complete 二次验证 | `skyvern/forge/agent.py` (`check_user_goal_complete`) |
| LLM 路由配置 | `skyvern/forge/sdk/api/llm/config_registry.py` |

### Understudy 关键文件

| 功能 | 文件路径 |
|------|----------|
| 双模型架构 | `packages/tools/src/openai-grounding-provider.ts` |
| Route Guard Policy | `packages/core/src/runtime/policies/route-guard-policy.ts` |
| Context Overflow 恢复 | `packages/core/src/runtime/tool-result-context-guard.ts` |
| 技能文件加载 | `packages/core/src/skills/workspace.ts` |
| 工作流结晶化 | `packages/core/src/workflow-crystallization.ts` |
| 插件 API | `packages/plugins/src/types.ts` |

### Claude Code 关键文件

| 功能 | 文件路径 |
|------|----------|
| 代理循环 + 压缩策略 | `src/query.ts` |
| 工具并发批处理 | `src/services/tools/toolOrchestration.ts` |
| 单工具执行 + isWithheld | `src/services/tools/toolExecution.ts` |
| 投机性预执行 | `src/services/tools/StreamingToolExecutor.ts` |
| Tool 接口 + buildTool | `src/Tool.ts` |
| 工具注册 + ToolSearch | `src/tools.ts` |
| Hook 执行引擎 | `src/utils/hooks.ts` |
| JSONL 会话存储 | `src/utils/sessionStorage.ts` |
| 工具结果预算 | `src/utils/toolResultStorage.ts` |
| 权限规则评估 | `src/utils/permissions/permissions.ts` |
| CLAUDE.md 加载 | `src/utils/claudemd.ts` |
| 流式 API + prompt缓存 | `src/services/api/claude.ts` |
| MCP 客户端 | `src/services/mcp/client.ts` |
| 极简 Store | `src/state/store.ts` |

---

## 八、一句话总结

- **Browser-Use**：DOM 技术栈最精良，三树融合 + paint order 是行业最佳，但通用性限制了深度
- **Skyvern**：生产级完成度最高，增量 DOM + SVG 缓存很实用，但代码复杂度高（单文件 5000 行）
- **Understudy**：架构创新最激进，双模型分离 + 技能结晶是真正的差异化，但平台新且绑定 macOS
- **Claude Code**：工程质量最高，工具并发、错误自愈、Hook体系、JSONL存储均有直接借鉴价值
- **FlowLens**：XHS 领域知识最深、本地化能力最强、反爬策略最实战——这是四个项目都没有的护城河，应在此方向持续加深，同时按优先级引入上述核心技术点

---

## 九、本地模型（Qwen 9B MLX）作为独立任务线

> 追加于 2026-04-06，基于同一个 "claude code pro 额度" 研究任务在 Sonnet 4.6 和本地 Qwen3.5-9B-MLX-4bit 上的对比运行（`task_runs/test_sonnet_v2/` vs `task_runs/test_qwen_v3/`）。
>
> 目的：把本地模型运行质量拉到"可用但慢"的水平，作为离线 / 隐私场景下 Sonnet 的 fallback。这是一条独立的任务线，不阻塞云端主线。

### 9.1 当前差距（实测数据）

同一个任务、同一个 tool 集合、同一个系统提示下。v4 是在三处 prompt/extension 修复之后的版本（click_card 命令格式、close_note 替代 press_key Escape、no_note_modal_open 兜底）：

| 维度 | Sonnet 4.6 | Qwen 9B v3（修复前） | Qwen 9B v4（修复后） |
|------|-----------|---------------------|---------------------|
| 总耗时 | ~180s | ~1815s | 1749s |
| 总 turn 数 | 30（自然结束） | 30（撞 max_turns） | 30（撞 max_turns） |
| 成功读取笔记数 | 4 篇（+评论） | 1 篇 + 5 次卡住重读 | **8 篇** |
| 单笔记平均成本 | ~45s/篇 | ~1815s/篇 | **~220s/篇** |
| 平均每 turn LLM 时间 | 5.3s | 53s | 49.5s |
| 每 turn 平均 tool 调用 | ~2（会 batch） | 始终 1 | 始终 1 |
| 截图数量 | 7（每篇一张）| 0 | 0 |
| 总输出 token | ~1.9k | ~7.1k | 3.0k |
| 总输入 token | ~540k | 362k | 264k |
| 工具错误数 | 0 | 多次（Unknown tool / Selector 不存在）| 1（card index 越界，1 turn 内自恢复）|

**关键变化**：v3→v4 同样没结束完，但单笔记成本从 ~1815s 降到 ~220s（**8 倍提速**），主要原因是不再被卡住的 modal 浪费 turn。Qwen 现在的瓶颈从"逻辑错误循环"切换到了"纯 prefill 慢"。

**Qwen vs Sonnet 真实差距**：220s/笔记 vs 45s/笔记 = **5x slower**（不是之前估计的 10x）。质量上 Qwen 实际读到的笔记数（8）比 Sonnet（4）多，但报告内容质量更差（见 9.2 P2 #9）。

#### 9.1.1 v4 实测的 prefill / decode 分布（新增日志字段）

新加的 `prefill_s` / `generation_s` 字段直接证实了"prefill 主导"的假设：

| 阶段 | 上下文 | prefill | decode | 总 dur |
|------|--------|---------|--------|--------|
| 启动期 (T1–T5) | 1.5–4.2k tok | 8–20s | 2–7s | 18–24s |
| 第一次 extract_search_cards 后 (T6) | 8.4k tok | **45s** | 39s（长 thinking 选笔记） | 84s |
| 稳态笔记循环 (T7–T30) | 7.6–11.6k tok | 38–60s | 2–8s | 40–68s |

- **decode 中位数 ~4s**，prefill 中位数 **~48s**。
- prefill 占整 turn LLM 时间的 **~92%**。
- `_trim_messages` 在 T11→T12 触发了一次（11.6k → 7.6k tok），prefill 立刻从 58s 降到 38s — 证明裁剪有效但触发太晚。
- 单笔记三步循环（click → extract → close）每个 turn 平均 ~50s，所以 30 turn 上限 = 最多 ~10 篇笔记。当前实际拿到 8 篇接近极限。

**最终结论**：在不动模型本身的前提下，能立刻收回 50% 时间的就是 prefill 优化（O1 + O2），这两条已经从"理论建议"变成"实测验证的最高优先级"。

### 9.2 当前已存在的问题（按优先级）

> 标记说明：✅ = v4 已验证修复 | ⚠️ = v4 未验证（需要重启 Chrome 扩展才生效） | ❌ = v4 验证仍是问题 | 🆕 = v4 跑出来的新发现

#### P0 — 系统层问题

1. **Prefill 主导的尾部延迟** ❌（v4 验证仍是核心瓶颈）
   - 实测：稳态 prefill 中位数 ~48s（10k token 上下文），decode 中位数 ~4s。
   - prefill 占整 turn LLM 时间的 92%。
   - 根因：每个 turn 都把完整的 `extract_search_cards` / `extract_note_content` JSON 塞回 message 历史，以及 ~2k token 的系统提示+工具定义。
   - 当前缓解：`LocalBackend.MAX_CONTEXT_TOKENS = 8000` + `_trim_messages()`，v4 在 T11→T12 触发了一次（11.6k → 7.6k），prefill 从 58s 降到 38s — **裁剪有效但阈值太晚**，应该在 9k 就触发。

2. **没有 prompt 缓存 / KV cache 复用** ❌
   - MLX-VLM 每次调用从头重跑 prefill，即使前缀只在末尾追加了几百 token。
   - 云端 Anthropic 有 prompt caching 自动生效，本地路径完全没有。
   - 这是当前 5x 差距里**最大的一块**。

3. **单 tool per turn 放大了 prefill 成本** ❌（v4 验证 = 模型能力上限，prompt 改不动）
   - v4 即使在 tool preamble 里显式说 "You can (and SHOULD) emit multiple tool_call blocks"，Qwen 9B 仍然 30/30 turn 全部只发一个 `<tool_call>` 块。
   - 这不是描述不够清楚，是模型能力限制。要修必须：(a) 加 few-shot 例子展示多 `<tool_call>` 块，或 (b) 换 constrained decoding 强制输出 tool_calls 数组。
   - 当前 workaround：把每个 turn 做便宜（O1 + O2 + O5），而不是减少 turn 数。

#### P1 — Agent 层问题

4. **Tool 描述被本地模型误解** ✅（v4 验证已修）
   - v3：`click_card` 被当成顶层工具 → "Unknown tool"。
   - v4：30/30 全部正确写成 `extract_page_data {command: "click_card", params: {index: N}}`，0 个 Unknown tool 错误。
   - 修复：`ExtractPageDataTool.description` 里显式说明 "This is ONE tool — these are commands, NOT separate top-level tools."

5. **press_key Escape 不关 XHS 模态框** ✅（v4 验证已修）
   - v3：5 次 press_key Escape → modal 全部没关 → 5 次 extract 同一个笔记。
   - v4：所有关闭操作都用 `extract_page_data close_note`，10 次 close_note 中 9 次走 `.close-circle` button、1 次走 escape fallback，**0 次卡住**。Qwen 成功循环 8 篇笔记的 click→extract→close 三步式。
   - 修复：tool preamble 里明确 "press_key Escape does NOT close the XHS modal, do not use it" + commands 列表里把 close_note 标成 ALWAYS。

6. **本地模型不主动截图** ❌（v4 验证：prompt 改不动）
   - v4 的 preamble 里规则 #1 写了 "call `screenshot` in the same turn as `navigate`"，仍然 30/30 turn 全部 0 截图。
   - 与 P0 #3 同根因：Qwen 9B 不会在一个 turn 里发 2 个 tool_call。
   - 短期 workaround：在 `loop.py` 里写一个隐式 hook — 每次 navigate 完成后 agent loop 自己塞一张 screenshot 到 message 历史，不依赖模型主动调用。这是 progressive disclosure 的反向应用：把"必看的视觉证据"隐式注入而非靠模型记得调。

7. **思考内容冗余且重复** ⚠️（v4 部分缓解）
   - v4 总输出 token 3.0k，比 v3 的 7.1k 减少 58%，但重复模式仍然存在（每个简单步骤都生成 50–100 token 的废话）。
   - T6（选笔记）单 turn 输出 747 token、decode 39s — 这是 v4 唯一一次 decode 时间 ≈ prefill 时间的情况。本地模型在"决策"步骤上 thinking 会爆掉。
   - 潜在方向：根据步骤复杂度动态开关 thinking（简单 action 直出 tool_call，复杂决策才 enable thinking）。

8. **`extract_note_content` 在无 modal 时返回搜索页 URL** ⚠️（修复已写但 v4 未验证）
   - v4 T22 返回 `search_result?k...` 而非 explore note URL — 修复后的 `no_note_modal_open` 检测应该已经拦住了，但 Chrome 扩展在 v4 启动时没重载，所以跑的是旧 content.js。
   - v4 T29 返回的 note_id 与 T25 相同（应该被 `_stale_warning` 捕获），但日志里看不到 warning 字段。
   - **下次跑前必须先重载扩展**（或把扩展 fix 改成自动检测 hot reload）。

#### 🆕 9. **Qwen 在写最终报告时严重混淆 / 编造内容**（v4 新发现）
   - v4 实际 extract 到的笔记标题包括 "Claude code 额度解析"、"6 个技巧让 Claude 额度不再秒光" 等真实标题（来自 v4 日志 T7/T10/T13/T16/T19/T22/T25/T29）。
   - 但 v4 最终 `report.md` 里写的笔记标题变成了 "Claude code pro 使用技巧"、"Claude Code Pro 免费试用"、"Claude Code Pro 功能介绍"，作者全部归到一个不存在的 "AI 工具推荐"。
   - 根因猜测：30 turn 后 message 历史早被 `_trim_messages` 砍掉前半段，max_turns 兜底报告时模型只能凭最近 turn 残留 + thinking 印象编。
   - 启发：max_turns 兜底报告必须从 **持久化的工具结果存储**（即 O1 的 artifact ref 系统）里 reload 历史数据，不能依赖被 trim 过的 message 历史。
   - 提示了一个新优化方向 → **O11**（见 9.3）。

#### 🆕 10. **`extract_search_cards` 是单步上下文最大的台阶**（v4 实测）
   - T5 → T6 上下文从 4252 → 8396 token，**单步增加 4.1k 个 token**。
   - 这一步把 prefill 时间从 20s 一下推到 45s。
   - 后续每个 click→extract 又稳定追加 ~1k token。
   - 直接验证了 O1 的最高优先级：`extract_search_cards` 默认应该只返回 top N 卡片的 [position, title, likes] 三个字段（~600 token 而不是 4k token）。

#### P2 — 日志 / 可观测问题

11. **`thinking` 和 `output_text` 仍然被 join 到一个 field**
    - 看起来像 "模型复读"，其实是日志结构把 `<think>` 段和 post-`</think>` 的输出段合并了。
    - 已通过 `prefill_tokens` / `prefill_s` / `generation_tokens` / `generation_s` 字段部分缓解，但 thinking 和 output 本身仍然 join。
    - 待办：在 `loop.py::log_entry` 里把 `text_blocks` 拆成 thinking-prefix 块和其余块。

12. **🆕 Chrome 扩展不会自动重载**
    - v4 跑的时候我同步改了 `content.js`（`no_note_modal_open` + `_stale_warning`），但 Chrome 扩展在 agent 启动后不会自动 hot reload，所以扩展级修复在 v4 上完全没生效。
    - 修复方向：agent 启动时调一次 `python -m flowlens extension reload`，或者在 `bridge.start()` 里加一个 file watcher。

### 9.3 未来优化方向（任务线）

#### 短期（1–2 周级）

**O1. 工具结果预算 + 增量上下文（借鉴 Claude Code）**
- 对每个工具结果设字节预算（比如 `extract_search_cards` → 2k，`extract_note_content` → 3k）。
- 超出部分落到 `task_runs/<run>/artifacts/` 并在 message 里只留引用。
- 对应 Claude Code 的 `toolResultStorage.ts` 思路。
- 预期收益：Qwen 上下文从 14k 降到 ~5k，prefill 从 70s 降到 25s，整体提速 2–3x。

**O2. 更激进的上下文裁剪**
- 当前 `MAX_CONTEXT_TOKENS=8000` 触发阈值太高。
- 改成：始终只保留 [task, 最近 3 个 turn 的完整内容, 被引用的关键 tool 结果]。
- 结合 O1 可以把前 N-3 个 turn 的 tool 结果自动替换为 summary。

**O3. Thinking 开关按步骤动态切换**
- 简单机械步骤（`wait`, `screenshot`, `close_note`）→ `enable_thinking=False` 直出 tool_call。
- 复杂规划步骤（选哪个笔记、是否继续）→ `enable_thinking=True`。
- 需要一个 lightweight 分类器或关键词启发式。

**O4. 分离 thinking / output_text 日志字段**
- `loop.py::log_entry` 里把 `response.text_blocks` 拆成 thinking 前缀块和其余块。

#### 中期（数周级）

**O5. Prompt cache / KV cache 复用（最大收益项）**
- MLX-LM / MLX-VLM 已有 `make_prompt_cache` API，可以在连续调用之间复用前缀的 KV。
- 需要维护：每次调用检测"新 prompt 是否以上次 prompt 为前缀"，是则从 cache 续跑，否则重建。
- 实现难点：系统提示 + 已有历史 = 公共前缀，追加的 user message 和 tool result 是增量。
- 预期收益：连续 turn 的 prefill 从 O(n) 降到 O(delta)，尾部 turn 可能从 70s 降到 5–10s。**这是本地模型追平云端体验的关键**。
- 对应 Sonnet 的 anthropic prompt caching（云端自动做了这件事）。

**O6. 双模型架构（借鉴 Understudy）**
- 规划模型：Qwen3.5-9B（或更大），每几个 turn 跑一次，产出高层计划。
- 执行模型：更小的 Qwen3.5-2B / 3B，每 turn 跑一次，只负责从固定动作集里选一个。
- 执行模型只需要"当前页面摘要 + 计划第 N 步 + 动作集"，上下文可以稳定在 2k 以下。
- 当前项目里已经有 2B 模型用于 observer 背景场景，可以直接复用。

**O7. 结构化输出替代 `<tool_call>` 文本解析**
- 当前：模型生成 `<tool_call>...</tool_call>`，再正则解析，错了就"Unknown tool"。
- 改进：用 MLX 的 logits processor 做 constrained decoding，直接强制输出符合 tool schema 的 JSON。
- 附带好处：永远不会出现 `click_card` 被当成顶层工具的误解。

**O8. Progressive disclosure 的系统提示架构**（见第 9.4 节）

#### 长期（探索性）

**O9. Per-site 工具子集加载**
- 当前每次都把所有 11 个 tool 的定义塞进系统提示（~1.5k token）。
- 改成：只在访问 XHS 时加载 XHS 相关工具定义，访问 ChatGPT 时加载聊天相关工具定义。
- 对应云端的 MCP 服务器动态加载模式。

**O10. 大上下文 + KV cache 的 9B 替代模型调研**
- 如果 MLX 生态出现 Qwen3.5-14B-MLX-4bit（或同等尺寸的 Mixtral）并支持 KV cache 复用，可能单纯换模型就能拿到 2–3x 质量提升。

#### 由 v4 实测引出的新优化项

**O11. max_turns 兜底报告必须从 artifact 存储 rehydrate**（v4 验证刚需）
- v4 的最终 report 把真实笔记标题全部编造成"AI 工具推荐 / Claude Code Pro 使用技巧"等不存在的内容。
- 根因：max_turns 触发时 message 历史已经被 `_trim_messages` 砍掉，模型只能凭印象写。
- 修复方向：把每次 tool_result 的完整内容写到 `task_runs/<run>/tool_results/<turn>_<tool>.json`，max_turns 兜底时把所有 result JSON 串成一个长 user message 重新塞给模型让它写报告。
- 这是 O1（tool result 预算 + ref）的子集，但优先级独立——即使没做完整的 progressive disclosure，也应该立刻做"持久化 + 兜底重读"。

**O12. Agent 启动时自动重载 Chrome 扩展**（v4 验证刚需）
- v4 跑的时候 `chrome_extension/content.js` 的两个修复（`no_note_modal_open`、`_stale_warning`）完全没生效，因为扩展没重载。
- `flowlens/extension_ops.py` 里已经有 `extension reload` 的实现，agent loop 启动时调一次即可。
- 或者：在 `bridge.start()` 里加一个 mtime 检测，content.js 比上次扩展加载新就触发 reload。
- 工作量极小，收益是"修了的 bug 真的有效"。

**O13. Implicit screenshot hook**（v4 验证刚需）
- 9B 模型 prompt-改不动它在 navigate 之后自动截图（P0 #3 同根因）。
- 改 agent loop：navigate / click_card 工具执行成功后，loop 自己追加一张 screenshot 进入下一个 turn 的 message 历史，不依赖模型主动调。
- 风险：会增加 vision token 成本——所以要和 O1 的 screenshot ref 机制配套（默认只塞 ref，模型显式 expand 时才塞像素）。

### 9.4 Progressive Disclosure 的讨论（受 Claude Code 启发）

Claude Code 的核心理念之一是 **progressive disclosure**：系统提示和工具不是一次性全部加载，而是根据当前步骤按需展开。具体表现包括：

- 不会在 session 开始就把所有文件的全部内容塞给模型。
- `ToolSearch` / MCP 的 deferred tool loading：只展示工具名，需要用时才 fetch 完整 schema。
- CLAUDE.md 分层加载（repo / subdir / user 级），`file_path@line` 级精准引用。
- 工具结果可以设预算，超出的部分做 "view more" 式的 lazy expand。

对 FlowLens 本地模型路径，这个理念特别契合，因为 **prefill 是本地模型的唯一瓶颈**：

1. **工具定义 lazy disclosure**
   - 系统提示里只列 tool name + 一行描述（~300 token）。
   - 模型想用某个工具时先声明 `tool_search: extract_page_data`，返回完整 schema。
   - 已用过的工具 schema 保留在 cache 里。

2. **Tool result lazy disclosure**
   - `extract_search_cards` 默认只返回 top 5 卡片的标题 + 作者 + 点赞 + position（~500 token），完整字段 + 全部卡片放进一个 ref。
   - 模型要详细数据时调 `expand_tool_result {ref}`。

3. **Site knowledge lazy disclosure**
   - 当前 `get_knowledge_for_url(url)` 一次性把整个 site YAML 塞进系统提示。
   - 改成：先塞一段"此站可用的高层能力列表"，模型要具体示例时再调 `get_site_knowledge {section}`。

4. **Screenshot lazy disclosure**
   - 当前每张截图都全尺寸塞进 message。
   - 改成：默认只塞"有这张图片 + 简短描述 + ref"；模型要看像素时再调 `load_screenshot {ref}`。
   - 对本地 9B 尤其有用，因为截图占大量 vision token。

5. **Reasoning lazy disclosure**
   - 简单步骤 → 直接 tool_call，不输出 thinking。
   - 复杂步骤 → 允许 thinking，但限制在 ~200 token 内。

这个方向的代价是：需要实现一套 "tool result store + ref/expand"基础设施，和一套"自动压缩已看过的上下文"策略，工程量相当于重写 agent loop。收益是本地模型在复杂任务上可能从现在的 10x 差距收敛到 3x 差距，而且副作用是云端主线也能省 token。

**建议做法**：把 O1（工具结果预算）作为这个方向的最小可用验证，跑通后再评估是否做完整的 progressive disclosure 重构。

### 9.5 本地模型任务线的验收指标

一条本地模型任务线的最终成功标准（baseline 来自 v4 实测）：

| 指标 | Sonnet 基线 | v3 基线 | v4 当前 | 目标 |
|------|------------|---------|---------|------|
| 单笔记平均成本 | 45s | ~1815s（卡住）| 220s | **≤ 135s（≤ 3x Sonnet）** |
| prefill_s / turn 中位数 | n/a | ~60s | 48s | **< 20s** |
| 30 turn 内成功读取笔记数 | 4–6 篇 | 1 篇 | 8 篇 | **≥ 8 篇并且自然结束** |
| 工具错误数 | 0 | 多次 | 1（自恢复）| **0** |
| 最终报告内容真实度 | 与抓取一致 | 与抓取一致 | **严重编造**（O11 修） | 100% 引用真实抓取 |
| 截图数量 | 7 | 0 | 0 | **≥ 5（O13 隐式 hook）** |

**优先级**：O11（兜底报告 rehydrate）和 O12（扩展自动重载）是工作量极小、收益立刻可见的两条，应该作为下一个 commit 立刻做掉；O1（result 预算）是把 prefill 中位数从 48s 降到 20s 的最直接路径，应该作为本地模型任务线的第一阶段重点。

### 9.6 引入持久化 Run State 之后，仍与 Claude Code 存在的核心架构差距

在本轮改造后，FlowLens 已经补上了一个关键短板：**运行态不再只存在于聊天上下文里**。现在已经有：

- `run_state/plan.json`：任务计划
- `run_state/evidence.json`：结构化证据账本
- `run_state/artifacts.json`：artifact 索引
- `run_state/events.jsonl`：事件流
- `run_state/working_memory.md`：面向模型的工作记忆投影
- 通用状态工具：`update_task_plan` / `read_run_state` / `read_saved_artifact`
- 最终报告前的 grounding pass：强制模型先回读持久化状态，再写结论

这已经明显比旧版“最近几轮消息 + 截断字符串”的模式稳得多，但和 Claude Code 相比，仍有几处核心架构差距没有补齐：

**G1. 还没有“完整事实层”和“投影上下文层”的严格分离**
- 现在虽然有 `run_state/`，但 live context 里混合了三种来源：最近消息、旧的 `context_memory` 压缩串、`run_state.context_block()`。
- Claude Code 的做法更彻底：**完整 transcript / tool results 持久化** 与 **当前 prompt 投影** 是两层明确分离的系统，并且有 replacement bookkeeping，知道“哪段原始内容被哪条摘要替代了”。
- FlowLens 现在还做不到精确回放某一轮当时的“原始上下文视图”，只能重建一个近似投影。

**G2. 还没有基于相关性的动态记忆检索器**
- 当前 `working_memory` 是从 plan / evidence / recent events 规则化渲染出来的，不是根据“当前问题”动态挑选最相关历史。
- Claude Code 更接近“按任务目标选择需要拉回哪些记忆块”，而不是统一模板式注入。
- FlowLens 下一步需要的是：按当前 turn 的目标、待回答问题、正在使用的工具，对 `run_state` 做 relevance-based retrieval，而不是每次都塞固定摘要块。

**G3. 还没有真正的 result ref / expand 机制**
- 现在 artifact 已经会落盘，但大多数工具结果仍然会先作为普通 tool result 回到模型，再被压缩进入 memory。
- Claude Code 的关键能力不是“能存盘”，而是**超预算结果默认只给摘要 + 稳定引用**，模型要细看时再显式 expand。
- 这决定了上下文是否能长期稳定。FlowLens 现在只是“多了一个可回读的后备仓库”，还不是“默认引用式上下文管理”。

**G4. 还没有第一类的任务图 / 待办执行层**
- 现在 `update_task_plan` 只是一个持久化 checklist，主要用于让模型少忘事。
- Claude Code 的 todo / task 状态更接近一个独立控制面：哪些步骤完成、哪些阻塞、当前正在推进什么，会直接影响后续交互和 UI 呈现。
- FlowLens 还缺一层“任务图驱动 agent”的闭环：比如由执行器自动检测重复读取、遗漏证据、未完成步骤，并反过来约束 agent 的下一步。

**G5. 还没有后台压缩器 / 分支执行 / 子任务状态体系**
- Claude Code 的多轮任务管理不只是单线程 loop，它有更成熟的 background state maintenance 思路，包括分支、子任务、独立摘要、回收。
- FlowLens 当前仍是单主循环 + 单 run_state，缺少：
  - 子任务级状态隔离
  - 分支探索后回收主线
  - 后台 compact / summarize worker
  - 长任务中的局部上下文重建
- 这会限制后续把 FlowLens 扩展到多站点、多来源、跨页面的大任务。

**G6. 最终报告还不是完全由证据账本驱动的确定性产物**
- 这次改造已经让模型在收尾前显式回读 `run_state`，报告质量会更稳。
- 但最终报告仍然主要是“LLM 基于账本再生成一遍自然语言”，而不是“结构化证据 ledger + renderer 模板”的确定性输出。
- Claude Code 的架构价值之一是：当信息已经结构化保存后，最终展示层可以越来越薄，幻觉空间越来越小。
- FlowLens 未来应考虑把最终报告拆成：
  1. 确定性汇总层：来源、标题、作者、正文、评论、点赞、截图引用
  2. LLM 分析层：对这些确定性数据做归纳和结论

**总结**

本轮改造解决的是“agent 查过的内容会忘”和“max turns 后靠印象写报告”这类基础问题，方向是正确的，而且这一步必须先做。

但从架构成熟度看，FlowLens 现在更接近：

- 有持久化记忆的单线程 agent

而 Claude Code 更接近：

- 有完整事实仓库、上下文投影层、按需检索、引用式展开、任务控制面和长任务维护机制的 agent runtime

因此，接下来最值得做的不是继续堆 prompt，而是沿着下面这条顺序推进：

1. `tool result -> ref/expand` 预算化
2. relevance-based run-state retrieval
3. task graph / duplicate-prevention execution policy
4. deterministic evidence renderer + LLM analysis split
5. 子任务 / 分支 / 后台压缩器

---

*参考项目版本：browser-use v0.12.2 | skyvern v1.0.28 | understudy（2026-04 main 分支）| claude-code v2.1.88*
