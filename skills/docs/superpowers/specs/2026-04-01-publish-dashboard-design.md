# 发布管理功能设计文档

**日期：** 2026-04-01
**功能：** 在小红书运营页追加「立即发布」和「定时发布」按钮

---

## 背景

`visual_discovery.py` 提供的运营页（`http://localhost:8888`）已支持 Pipeline 自动生成图文卡片，但发布仍需手动调用命令行。本次新增发布管理模块，允许用户在 Dashboard 中直接选择内容、选择账号、立即或定时发布到小红书。

---

## 架构

### 文件变动

| 文件 | 变动 |
|------|------|
| `scripts/publish_manager.py` | **新增** — 发布逻辑、定时调度线程、账号读取、简介提炼 |
| `scripts/visual_discovery.py` | **修改** — 新增 7 个 API 路由 + 「发布管理」HTML/JS 区块，import publish_manager |
| `output/publish_schedule.json` | **自动创建** — 持久化定时任务列表 |

### 模块职责

- **`publish_manager.py`**：所有发布业务逻辑，`visual_discovery.py` 只做路由转发
- **`visual_discovery.py`**：路由注册 + 前端 HTML/JS，不含发布业务逻辑

---

## 数据模型

### `output/publish_schedule.json`

```json
{
  "tasks": [
    {
      "id": "uuid4 字符串",
      "created_at": "2026-04-01T10:00:00",
      "scheduled_at": "2026-04-05T09:30:00",
      "folder": "/absolute/path/to/card/folder/",
      "title": "笔记标题（≤20字）",
      "desc": "100字以内简介",
      "accounts": ["default", "account2"],
      "status": "pending",
      "result": {}
    }
  ]
}
```

`accounts` 为空数组表示全部 active 账号。
`status` 取值：`pending` | `done` | `failed`

---

## API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/card_folders` | 返回所有可发布的图片文件夹（含卡片数、时间戳） |
| GET | `/api/card_summary?folder=...` | 从对应 `.md` 提炼 title + desc（≤100字） |
| GET | `/api/accounts` | 返回 `accounts.json` 中所有账号及有效期状态 |
| POST | `/api/publish` | 立即发布，body: `{folder, title, desc, accounts}` |
| POST | `/api/schedule_publish` | 定时发布，body: `{folder, title, desc, accounts, scheduled_at}` |
| GET | `/api/scheduled_tasks` | 返回全部定时任务列表 |
| DELETE | `/api/scheduled_tasks/<id>` | 取消一个 `pending` 任务 |

### 错误响应格式

```json
{ "error": "描述信息" }
```

---

## 定时调度

`publish_manager.py` 模块被 import 时自动启动一个 daemon 后台线程，每 30 秒：

1. 读取 `publish_schedule.json`
2. 找出 `status=pending` 且 `scheduled_at ≤ now` 的任务
3. 逐一调用（import 方式）`publish_xhs.LocalPublisher` 或 `ApiPublisher` 的 `.publish()` 方法，不使用 subprocess
4. 更新 `status` 为 `done` 或 `failed`

---

## 发布对象

发布内容 = 一个图片文件夹（`output/小红书备选/{stem}/` 下的所有 `.png` 文件，按文件名排序）。

- **标题**：从同目录同名 `.md` 文件的 `# 标题` 行提取，限 20 字
- **简介**：从 `.md` 中提取 Hook + 前两节摘要，截取 ≤100 字，用户可在弹窗内编辑

---

## UI

### 发布管理区块（页面底部）

```
┌─────────────────────────────────────────────────────┐
│ 📤 发布管理                                          │
│                                                     │
│  [立即发布]  [定时发布]                              │
│                                                     │
│  ── 定时任务列表 ──────────────────────────────────  │
│  时间           内容           账号    状态   操作   │
│  04-05 09:30   留英求职...    全部    待发   [取消]  │
│  04-06 12:00   面试3大...     2个     待发   [取消]  │
└─────────────────────────────────────────────────────┘
```

### 发布 Modal（立即/定时共用，定时版多一个时间选择器）

```
┌──────────────────────────────────────────┐
│  立即发布 / 定时发布                  [×] │
│                                          │
│  发布内容                                │
│  [下拉：选择图片文件夹 ▼]               │
│   └ 自动显示：5张图片 · 04-01 19:38     │
│                                          │
│  标题（可编辑，≤20字）                   │
│  [留英求职3个月我学到了什么_________]    │
│                                          │
│  简介（自动提炼，可编辑，≤100字）        │
│  [textarea · 字数计数器]                 │
│                                          │
│  发布账号  [全部发布 ▼ / 指定发布 ▼]    │
│   └ 指定时展开 checkbox 列表：           │
│     ☑ default（有效至 04-05）           │
│     ☐ account2（已过期 ⚠️）             │
│                                          │
│  ── 仅定时发布显示 ──────────────────── │
│  发布时间  [日期选择] [时间 HH:MM]      │
│                                          │
│  [取消]              [确认发布]          │
└──────────────────────────────────────────┘
```

### 交互细节

- 账号 session 已过期：显示 ⚠️，checkbox 不可勾选
- 简介字数 >100：计数器变红，禁用「确认发布」按钮
- 发布执行中：按钮 loading 状态
- 发布完成/失败：页面顶部 toast 提示

---

## 不在本次范围内

- 发布历史记录/结果查看
- 发布失败自动重试
- 多账号并发发布（当前按账号顺序串行）
