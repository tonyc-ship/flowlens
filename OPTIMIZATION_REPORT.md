# ClawVision 优化参考报告

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

| 维度 | Browser-Use | Skyvern | Understudy | **ClawVision** |
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

**对 ClawVision 的价值**：XHS 有大量自定义组件，纯 DOM 树缺少语义，纯无障碍树缺少布局信息。三树融合后 LLM 对元素的理解准确率会显著提升，减少错误点击。

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

精准控制 LLM 对错误的关注度，避免错误信息占据过多 context window，非常适合 ClawVision 的长任务场景。

#### ⑥ 消息历史压缩（MessageManager）

当对话历史过长时，用一个 LLM 调用把旧消息总结压缩（默认 40,000 字符触发，保留最近 6 条，旧的压缩到 6,000 字以内，每 15 步触发一次）。对 ClawVision 的长时间研究任务很有参考价值。

---

### 3.2 从 Skyvern 借鉴

#### ① IncrementalScraping + MutationObserver（高价值）

```javascript
// domUtils.js 中设置 globalObserverForDOMIncrement
// 点击后只抓取 DOM 变化部分，不重新全量扫描
// 对 dropdown / autocomplete / modal 效果极好
```

**对 ClawVision 的价值**：XHS 的笔记详情弹窗、标签下拉、搜索建议每次打开后都需要等待并重新扫描全量 DOM。改用增量提取后，只抓新出现的元素，速度预计提升 30-50%。

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

ClawVision 已有 XHS 特定的反爬状态检测，但没有系统化分类。结构化失败类型能让重试策略更精准（比如 ANTI_BOT 触发时降速，LLM_ERROR 时切换模型）。

#### ⑤ Complete 二次验证

```
LLM 声明任务完成
    ↓
额外一次 LLM 调用确认
    ↓
返回 complete / terminate / continue_step
```

防止 LLM 过早声明研究任务完成，适用于 ClawVision 的质量保证环节。

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

**对 ClawVision 的映射**：
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

对 ClawVision 的价值：DOM提取 → OCR → Vision API 这三条路径可以用同样的机制实现自动降级，不需要硬编码 fallback 逻辑。

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

放入对应目录自动加载。参考这种机制，可以让 ClawVision 的平台知识库更易维护和扩展。

#### ⑤ 工作流结晶化（Workflow Crystallization）

系统自动分析历史对话，识别重复工作模式，自动合成可复用技能。对 ClawVision 的启示：从历史成功任务中自动提炼 XHS 研究最佳实践，沉淀为下次任务的先验知识。

---

## 四、ClawVision 的核心护城河（不要丢）

对比三个项目，ClawVision 有一些他们完全没有的能力：

| ClawVision 独特能力 | 说明 |
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
| Playwright 封装 | 不需要 | ClawVision 的 Chrome 扩展方案复用用户登录状态，比 Playwright 更适合 XHS |

---

## 六、优先级路线图

### 短期（1-2 周，高收益低成本）

1. **Long URL Hash 压缩**
   - 改造 `clawvision/core/bridge.py` 或 DOM 序列化层
   - XHS 图片/CDN URL 哈希替换
   - 预计 prompt token 减少 20%，改动 <50 行

2. **失败类型分类器**
   - 在 `clawvision/core/` 下新建 `failure_classifier.py`
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
   - 扩展 `clawvision/core/bridge.py` 的 CDP 调用
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
# 借鉴到 ClawVision 的思路
class Tool:
    def is_concurrency_safe(self, input) -> bool: ...

# 连续的只读操作 → 并发执行（截图/DOM提取/OCR同时进行）
# 写操作（点击/输入）→ 串行执行
# Context Modifier 队列化，批次完成后原子应用，防止状态竞争
```

**对 ClawVision 的价值**：XHS 任务中 `截图 + DOM提取 + 图片OCR` 完全可并发，目前是串行。改造后单卡片处理速度预计提升 40%+。

#### ② ToolSearch 延迟工具加载

```python
# 工具分两类
class Tool:
    should_defer: bool = False   # 延迟加载 schema
    always_load: bool = False    # 永远不延迟

# 初始 prompt 只含核心工具 schema
# 模型调用 ToolSearch(query) → 才获得具体工具 schema → 才能调用
```

**对 ClawVision 的价值**：ClawVision 的 capability catalog 已经有类似分层概念。将 `lite` 能力始终加载，`deep` 能力（视频转录、详细图片分析等）延迟加载，可大幅缩小初始系统提示。

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

**对 ClawVision 的价值**：XHS 批量研究任务的笔记内容、OCR 文本、图片描述可能很长，超出预算时存盘，让 LLM 按需读取，而非塞满 context window。

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

**对 ClawVision 的价值**：XHS 反爬触发时，当前实现是直接失败。用 isWithheld 模式改造后，可以先降速重试、换策略，对上层任务透明，提升任务完成率。

#### ⑤ Hook 生命周期标准化（20+ 事件）

参考 Claude Code 的完整 Hook 事件体系，为 ClawVision 定义标准生命周期：

```python
# 建议 ClawVision 标准化的 Hook 事件
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

`PreToolUse` hook 输出支持 `updated_input`（修改工具参数）和 `permission_decision`（覆盖权限），这是 ClawVision 目前没有的。

#### ⑥ JSONL 会话存储 + 断点续跑

Claude Code 的 JSONL 格式非常适合 ClawVision 的任务记录：

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

**对 ClawVision 的价值**：当前 ClawVision 的任务运行输出是 HTML 报告 + GIF，不支持恢复中断的任务。JSONL 格式可以让长时间 XHS 研究任务支持断点续跑。

### 短期可落地（直接加入上面的优先级路线图）

| 优化点 | 落地位置 | 预期收益 |
|--------|----------|----------|
| 工具并发批处理 | `clawvision/core/bridge.py` | 单卡片处理速度 +40% |
| 工具结果磁盘持久化 | `clawvision/core/` 新建 `result_budget.py` | 长任务 context 可控 |
| isWithheld 错误自愈 | `clawvision/workflows/xhs/` | 任务完成率提升 |
| JSONL 会话格式 | `clawvision/core/recorder.py` 扩展 | 支持断点续跑 |

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
- **ClawVision**：XHS 领域知识最深、本地化能力最强、反爬策略最实战——这是四个项目都没有的护城河，应在此方向持续加深，同时按优先级引入上述核心技术点

---

*参考项目版本：browser-use v0.12.2 | skyvern v1.0.28 | understudy（2026-04 main 分支）| claude-code v2.1.88*
