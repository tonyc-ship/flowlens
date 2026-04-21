# Auto-Redbook-Skills

> 以 Claude 为决策核心，本地脚本驱动执行，实现从关键词输入到小红书图文内容生成的完整自动化链路。

⚠️ 使用前请阅读官方公告：[关于打击AI托管运营账号的治理公告](http://xhslink.com/o/7WxTddvbmTu)

---

## 功能概览

- **关键词 → 爆款分析**：抓取关键词下的热门笔记，自动分析账号风格、Hook/CTA 模板
- **文案生成**：基于分析结果，由 Claude 生成多篇备选文案
- **卡片渲染**：8 套主题 × 4 种分页模式，生成小红书竖版图片卡片
- **多账号管理**：扫码登录、Cookie 保活、轮询使用，支持多账号并发
- **可视化控制台**：浏览器 Web UI，全链路实时进度展示

---

## 快速开始

### 1. 克隆项目

```bash
git clone https://github.com/seikachin/Auto-Redbook-Skills.git
cd Auto-Redbook-Skills
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
playwright install chromium
```

### 3. 配置环境变量

```bash
cp env.example.txt .env
```

编辑 `.env`，填入必要的 Key：

```env
# AI 文案生成（二选一）
DASHSCOPE_API_KEY=你的阿里云DashScope密钥     # 推荐，有免费额度
ANTHROPIC_API_KEY=your_anthropic_key           # 备选

# 小红书 Cookie（单账号模式，多账号模式用扫码登录代替）
XHS_COOKIE=a1=xxx; web_session=yyy
```

### 4. 扫码登录小红书

```bash
python scripts/xhs_login.py
```

会弹出浏览器，手机扫码登录后自动保存 storageState，Cookie 有效期约 30 天。

### 5. 启动可视化控制台

```bash
python scripts/visual_discovery.py
```

打开浏览器访问 `http://localhost:8888`，输入关键词，一键运行完整链路。

---

## 核心脚本说明

| 脚本 | 用途 |
|------|------|
| `scripts/visual_discovery.py` | 可视化控制台（推荐入口） |
| `scripts/xhs_full_pipeline.py` | 命令行全链路运行 |
| `scripts/analyze_accounts.py` | 爆款账号深度分析 |
| `scripts/render_xhs.py` | 图片卡片渲染（8主题×4分页） |
| `scripts/publish_xhs.py` | 发布笔记到小红书 |
| `scripts/account_manager.py` | 多账号管理 CLI |
| `scripts/xhs_keepalive.py` | Session 保活 |
| `scripts/xhs_network_spy.py` | 网络请求诊断工具 |
| `scripts/browser_config.py` | 浏览器统一启动配置 |

---

## Pipeline 流程

```
关键词输入
    ↓
Step 1  xhs_explore — 抓取关键词热门笔记 & 爆款账号
    ↓
Step 2  analyze_accounts — Claude 分析账号风格 / Hook / CTA
    ↓                      输出：output/爆款分析/*.xlsx
Step 3  文案生成 — 每账号生成 2 篇备选文案
    ↓              输出：output/小红书备选/*.md
Step 4  卡片渲染 — 每篇文案生成 5 张竖版卡片（900×1600px）
    ↓              输出：output/小红书备选/{账号名}/0N-*.png
Step 5  汇总完成 — 人工审核后手动发布
```

---

## 多账号管理

多账号模式下无需手动维护 Cookie，系统自动轮询可用账号。

```bash
# 扫码登录，保存为指定账号
python scripts/account_manager.py scan --name 账号A

# 添加已有 Cookie
python scripts/account_manager.py add --name 账号B --cookie "a1=xxx; web_session=yyy"

# 查看所有账号状态
python scripts/account_manager.py list

# 手动保活（自动保活每 2 天触发一次）
python scripts/xhs_keepalive.py
python scripts/xhs_keepalive.py --name 账号A
```

账号数据存储在 `accounts.json`，storageState 文件存储在 `~/.xhs-accounts/{账号名}/storage.json`。

---

## 卡片渲染

### 主题（`-t`）

| 主题名 | 风格 |
|--------|------|
| `sketch`（默认） | 手绘素描 |
| `default` | 简约灰 |
| `playful-geometric` | 几何撞色 |
| `neo-brutalism` | 新粗野主义 |
| `botanical` | 植物自然 |
| `professional` | 商务简洁 |
| `retro` | 复古风 |
| `terminal` | 终端代码风 |

### 分页模式（`-m`）

| 模式 | 说明 |
|------|------|
| `separator`（默认） | 按 `---` 手动分页 |
| `auto-split` | 按渲染高度自动拆分 |
| `auto-fit` | 固定尺寸自动整体缩放 |
| `dynamic` | 动态调整卡片高度 |

```bash
# 默认主题 + 手动分页
python scripts/render_xhs.py content.md

# 推荐：内容长度不定时用 auto-split
python scripts/render_xhs.py content.md -m auto-split

# 切换主题
python scripts/render_xhs.py content.md -t neo-brutalism -m auto-split

# 自定义尺寸
python scripts/render_xhs.py content.md -t retro --width 1080 --height 1440 --dpr 2
```

---

## 发布笔记

```bash
# 默认仅自己可见（先预览确认）
python scripts/publish_xhs.py \
  --title "笔记标题" \
  --desc "笔记描述" \
  --images cover.png card_1.png card_2.png

# 确认无误后公开发布
python scripts/publish_xhs.py \
  --title "笔记标题" \
  --desc "笔记描述" \
  --images cover.png card_1.png card_2.png \
  --public
```

---

## 项目结构

```
Auto-Redbook-Skills/
├── scripts/
│   ├── visual_discovery.py   # 可视化控制台（推荐入口）
│   ├── xhs_full_pipeline.py  # 命令行全链路
│   ├── analyze_accounts.py   # 账号分析
│   ├── render_xhs.py         # 卡片渲染（8主题+4分页）
│   ├── publish_xhs.py        # 发布脚本
│   ├── account_manager.py    # 多账号管理
│   ├── browser_config.py     # 浏览器统一配置
│   ├── xhs_keepalive.py      # Session 保活
│   └── xhs_network_spy.py    # 网络诊断工具
├── assets/
│   ├── cover.html            # 封面模板
│   ├── card.html             # 正文卡片模板
│   ├── styles.css            # 公共样式
│   └── themes/               # 8 套主题 CSS
├── agents/
│   └── xhs-auto-agent.md     # Agent 系统说明
├── references/
│   └── params.md             # 完整参数文档
├── docs/
│   └── windows-quickstart.md # Windows 快速上手
├── output/                   # 生成结果目录
│   ├── 爆款分析/             # 账号分析 xlsx
│   └── 小红书备选/           # 文案 md + 卡片 png
├── SKILL.md                  # Agent 技能描述
├── requirements.txt
└── env.example.txt
```

---

## Windows 快速上手

参见 [docs/windows-quickstart.md](docs/windows-quickstart.md)

---

## 注意事项

1. **Cookie 安全**：`.env` 和 `accounts.json` 不要提交到 Git 或共享。
2. **发布频率**：避免短时间内高频发布，以免触发平台风控。
3. **当前阶段**：生成即止，不含自动发布，人工审核后再发布。
4. **浏览器指纹**：所有 Playwright 入口共用 `browser_config.py`，统一画像降低被检测风险。

---

## 依赖

- Python 3.12+
- [Playwright](https://playwright.dev/) — 浏览器自动化
- [xhs](https://github.com/ReaJason/xhs) — 小红书 API 客户端
- Node.js 18+（`mcp/explore` 抓取模块）

---

## License

MIT License © 2026
