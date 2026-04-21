# 发布多选 + 账号风格偏好 Design Spec

## Goal

重构发布管理流程：把 title/desc 编辑从发布弹窗移入文案生成区；发布弹窗改为"账号 × 就绪文案"分配界面，支持多账号各自独立多选文案；文案发布后标记 `published` 退出选项；账号风格偏好（来自分析阶段）静默存储，供 LLM 生成时复用，**不向用户暴露分析原始数据**。

---

## Architecture

三文件改动：

| 文件 | 变更类型 |
|------|----------|
| `scripts/publish_manager.py` | 新增 5 个函数 |
| `scripts/analyze_accounts.py` | 分析完成后写入账号风格偏好 |
| `scripts/visual_discovery.py` | 新增 3 个 HTTP 端点 + 前端重构 |

运行时生成文件：`output/account_styles.json`、`output/小红书备选/*.pub.json`

---

## Data Model

### Sidecar：`output/小红书备选/<name>.pub.json`

与同名 `.md` 文案文件一一对应，存放发布元数据。

```json
{
  "title": "AI求职必看这一篇",
  "desc": "正文摘要前100字...",
  "image_path": "output/images/task-abc123.jpg",
  "account": "账号昵称",
  "image_style": "单图大字信息流",
  "writing_style": "口语化",
  "status": "ready",
  "confirmed_at": "2026-04-02T10:00:00"
}
```

**status 生命周期：** `ready`（可分配）→ `published`（已发布，永久退出选项列表）

`image_path` 为空字符串时允许就绪（仅文字发布，无配图）。

### 账号风格偏好：`output/account_styles.json`

```json
{
  "账号昵称": {
    "avg_words": 300,
    "style_desc": "口语化、亲切自然",
    "image_style": "单图大字信息流",
    "emotion": "积极正向",
    "structure": "痛点引入 | 解决方案 | 行动号召",
    "hook_examples": "你知道吗 | 原来这才是正确姿势",
    "cta_examples": "收藏备用 | 关注我",
    "updated_at": "2026-04-02",
    "source": "analysis"
  }
}
```

**source 含义：**
- `"analysis"` — 分析阶段自动写入
- `"confirmed"` — 用户确认文案就绪时覆盖写入（优先级更高，更个性化）

**LLM 生成优先级：** `account_styles.json` 中该账号记录 > 当次分析数据 > 默认参数

---

## Backend Changes

### `scripts/publish_manager.py` 新增五个函数

```python
def list_ready_drafts(draft_dir=None) -> List[Dict]:
    """扫描 output/小红书备选/*.pub.json，返回 status=ready 的列表。
    每项：{name, title, desc, image_path, md_path, account}
    按 confirmed_at 降序排列。"""

def mark_ready(md_path: str, title: str, desc: str, image_path: str,
               account: str, image_style: str, writing_style: str) -> Dict:
    """写入同名 .pub.json（status=ready），同时调用
    save_account_style(account, style_params, source='confirmed')。
    返回写入的 pub dict。"""

def save_account_style(account: str, style_params: dict,
                       source: str = "analysis") -> None:
    """写入/更新 output/account_styles.json 中该账号条目。
    source='confirmed' 时无条件覆盖；source='analysis' 时仅在无记录
    或现有 source='analysis' 时写入（不覆盖 confirmed 记录）。"""

def load_account_style(account: str) -> Optional[dict]:
    """读取 output/account_styles.json 中指定账号的风格参数。
    不存在时返回 None。"""

def do_publish_batch(
    items: List[Dict],   # [{draft_path: str, account: str}]
    accounts_path=None,
    schedule_file=None,
) -> Dict:
    """批量发布。每个 item 从对应 .pub.json 读取 title/desc/image_path，
    向指定 account 发布。
    发布成功后将该 .pub.json 的 status 改为 published。
    返回：{"{account}::{draft_name}": {"status": "ok"|"error", ...}}"""
```

**`do_publish_batch` 详细逻辑：**

```python
for item in items:
    pub = load_pub_json(item["draft_path"])   # 读 .pub.json
    acc_info = get_account(item["account"])    # 从 accounts.json 获取 session
    images = [pub["image_path"]] if pub.get("image_path") else []
    result = publish_one(acc_info, pub["title"], pub["desc"], images)
    if result["status"] == "ok":
        pub["status"] = "published"
        save_pub_json(pub)                     # 写回 .pub.json
```

单个 item 失败不影响其他，所有结果汇总返回。

### `scripts/analyze_accounts.py` 改动

分析循环结束、写入 CSV/JSON 结果后，追加：

```python
import publish_manager
for acc in analyzed_accounts:
    publish_manager.save_account_style(
        acc["账号昵称"],
        {
            "avg_words":    acc.get("平均文字字数", 300),
            "style_desc":   acc.get("图文风格描述", ""),
            "image_style":  acc.get("图片风格", ""),
            "emotion":      acc.get("情绪标签", ""),
            "structure":    acc.get("内容结构", ""),
            "hook_examples": acc.get("Hook模板", ""),
            "cta_examples":  acc.get("CTA模板", ""),
        },
        source="analysis",
    )
```

不改动现有输出、日志和显示逻辑。

### `scripts/visual_discovery.py` 新增 HTTP 端点

| 方法 | 路径 | 请求体 | 说明 |
|------|------|--------|------|
| `GET` | `/api/ready_drafts` | — | 返回 `list_ready_drafts()` |
| `POST` | `/api/mark_ready` | `{md_path, title, desc, image_path, account, image_style, writing_style}` | 写 sidecar，返回 pub dict |
| `POST` | `/api/publish_batch` | `{items:[{draft_path, account}], scheduled_at?}` | 立即或定时批量发布 |

`/api/publish`（路径 B 旧入口）保留，不改动。

`/api/publish_batch` 处理逻辑：
- 无 `scheduled_at`：直接调 `do_publish_batch`
- 有 `scheduled_at`：调 `save_scheduled_task`（扩展支持 items 格式），由调度器在到期时调 `do_publish_batch`

### `_generate_draft_llm` 改动

```python
# 在 run_pipeline 的文案生成循环中，获取 style_params 前：
saved_style = _load_account_style(acc["账号昵称"])
style_params = saved_style if saved_style else style_params_from_analysis
```

新增模块级函数：

```python
def _load_account_style(account: str) -> Optional[dict]:
    """读取 output/account_styles.json 中指定账号风格。不存在返回 None。"""
    import publish_manager
    return publish_manager.load_account_style(account)
```

---

## Frontend Changes（`visual_discovery.py` HTML/JS）

### 文案生成区：新增"发布准备"子区域

**位置：** 图片预览 `imgPreview` div 下方，现有按钮行之前。

**HTML 结构：**

```html
<div id="pubReadySection" style="display:none;margin-top:12px;
     border-top:1px solid #eee;padding-top:12px">
  <div style="font-size:.82rem;font-weight:600;color:#555;margin-bottom:8px">
    📤 发布准备
  </div>
  <label style="font-size:.78rem;color:#888">标题（≤20字）</label>
  <input id="pubTitleInput" type="text" maxlength="20"
    style="width:100%;margin-bottom:6px;padding:6px 8px;border:1px solid #ddd;
           border-radius:6px;font-size:.82rem">
  <label style="font-size:.78rem;color:#888">简介（≤100字）</label>
  <textarea id="pubDescInput" maxlength="100" rows="2"
    style="width:100%;padding:6px 8px;border:1px solid #ddd;
           border-radius:6px;font-size:.82rem;resize:vertical"></textarea>
  <div style="display:flex;justify-content:space-between;
       align-items:center;margin-top:6px">
    <span id="pubDescCount" style="font-size:.72rem;color:#bbb">0 / 100</span>
    <button id="markReadyBtn" onclick="markReady()"
      style="padding:6px 16px;background:linear-gradient(135deg,#007aff,#5856d6);
             color:#fff;border:none;border-radius:8px;font-size:.82rem;cursor:pointer">
      ✅ 标记为发布就绪
    </button>
  </div>
  <div id="readyStatusMsg" style="font-size:.75rem;color:#aaa;margin-top:4px"></div>
</div>
```

**JS 行为：**

`loadDraft()` 加载文案后：
1. 从 `d.content` 客户端解析 title/desc（复用已有正则：`# 标题` 行 → title，正文摘要 → desc）
2. 填入 `pubTitleInput` / `pubDescInput`
3. 显示 `pubReadySection`

```js
// loadDraft() 末尾追加
const titleMatch = d.content.match(/^#\s+(.+)/m);
document.getElementById('pubTitleInput').value =
  titleMatch ? titleMatch[1].trim().slice(0, 20) : '';
const descMatch = d.content.replace(/^---[\s\S]*?---\n/m, '')
                            .replace(/^#+.+$/mg, '')
                            .replace(/\*\*[^*]+\*\*/g, '')
                            .trim().slice(0, 100);
document.getElementById('pubDescInput').value = descMatch;
updatePubDescCount();
document.getElementById('pubReadySection').style.display = 'block';
document.getElementById('readyStatusMsg').textContent = '';
document.getElementById('markReadyBtn').disabled = false;
```

`markReady()` 函数：

```js
async function markReady() {
  const title = document.getElementById('pubTitleInput').value.trim();
  const desc  = document.getElementById('pubDescInput').value.trim();
  if (!title) {
    document.getElementById('readyStatusMsg').textContent = '请填写标题';
    return;
  }
  document.getElementById('markReadyBtn').disabled = true;
  const res = await fetch('/api/mark_ready', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      md_path:       currentDraftFile,
      title,
      desc,
      image_path:    document.getElementById('genImgEl').src
                       .replace(/\?t=\d+$/, '').replace(location.origin, '') || '',
      account:       currentTopic ? '' : '',   // 从文案元数据 **账号参考** 读取
      image_style:   currentImageStyle,
      writing_style: '',                        // 从文案元数据 **图文风格描述** 读取
    })
  }).then(r => r.json());
  document.getElementById('markReadyBtn').disabled = false;
  if (res.error) {
    document.getElementById('readyStatusMsg').textContent = '❌ ' + res.error;
  } else {
    document.getElementById('readyStatusMsg').textContent = '✅ 已就绪，可在发布管理中选择发布';
    document.getElementById('markReadyBtn').textContent = '✅ 已就绪';
  }
}
```

`account` 和 `writing_style` 从 `currentDraftContent` 解析 `**账号参考**` 和 `**图文风格描述**` 字段。在 `loadDraft()` 中同步提取并存入模块级变量 `currentAccount` / `currentWritingStyle`。

### 发布弹窗：重构为账号 × 文案分配

**弹窗 HTML 结构（替换现有 `#pubModal` 内容）：**

```
立即发布 / 定时发布
─────────────────────────────
账号 & 内容分配

[ ☑ 账号 A ]  [AI求职必看 ×] [面试技巧 ×]  [ ＋ 选择文案 ]
[ ☑ 账号 B ]  [转行攻略 ×]                 [ ＋ 选择文案 ]
[ ☐ 账号 C ]  （行整体置灰）

─────────────────────────────
[定时模式] 发布时间  日期输入  时间输入
─────────────────────────────
[ 取消 ]                      [ 确认发布 ]
```

每个账号行：
- 左侧 checkbox 勾选账号
- 中部芯片区（已选文案，每个带 × 移除按钮）
- 右侧「＋ 选择文案」按钮 → 打开内层选择弹窗

**内层选择弹窗（`#draftPickModal`）：**

```
选择文案（账号 A）
─────────────────
☐ AI求职必看这一篇
☐ 面试技巧超全汇总
☐ 美国程序员转行攻略
─────────────────
[ 取消 ]  [ 确认 ]
```

- 列出所有 `status=ready` 且**当前 session 内未被其他账号选中**的文案 title
- 支持多选（checkbox）
- 确认后以芯片写回账号行，关闭内层弹窗

**JS 核心数据结构：**

```js
// 当前 session 内的账号×文案分配
// { "账号A": ["文案1路径", "文案2路径"], "账号B": ["文案3路径"] }
let _pubAssignments = {};

// 所有就绪文案（从 /api/ready_drafts 加载）
// [{name, title, md_path, ...}]
let _readyDrafts = [];

// 已被任意账号选中的文案路径集合（session 内互斥）
function _assignedPaths() {
  return new Set(Object.values(_pubAssignments).flat());
}
```

**「确认发布」行为：**

```js
const items = [];
for (const [account, paths] of Object.entries(_pubAssignments)) {
  for (const draft_path of paths) {
    items.push({ draft_path, account });
  }
}
// 立即发布
fetch('/api/publish_batch', {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({ items })
})
// 定时发布
fetch('/api/publish_batch', {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({ items, scheduled_at: '2026-04-03T09:00:00' })
})
```

发布/定时提交成功后：刷新 `_readyDrafts`（已发布项不再出现），重置 `_pubAssignments`，关闭弹窗。

### 发布管理面板

定时任务表格（`schedTbl`）"内容"列：
- 路径 A 任务显示文案 title（从 task 的 items 字段读取）
- 路径 B 旧任务显示 folder 名（向后兼容）

---

## Error Handling

| 场景 | 处理 |
|------|------|
| `mark_ready` 无 title | 返回 400，前端提示"请填写标题" |
| `mark_ready` 无 image_path | 允许（仅文字发布） |
| `do_publish_batch` 单 item 失败 | 不影响其他；失败 item 保持 `status=ready` 可重试 |
| 账号 session 过期 | 账号行 checkbox 禁用，标注 ⚠️ 已过期 |
| 就绪文案列表为空 | 弹窗内显示提示"暂无就绪文案，请先在文案区标记就绪" |
| 内层选择弹窗无可选文案 | 提示"所有就绪文案已被其他账号选中" |

---

## Out of Scope

- 路径 B（卡片文件夹）多选支持：保留现有 `/api/publish` 入口，不改动
- 账号风格偏好的可视化编辑界面
- 发布历史详情页
- 文案就绪后的内容预览（目前仅显示 title）
