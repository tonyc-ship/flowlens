# 安装手册

## 前置条件

- macOS 12+（Intel 或 Apple Silicon 均可）
- Google Chrome 已安装
- 网络可以访问（下载工具约 500MB）

## 第一步：解压文件

把收到的压缩包解压到任意目录，例如桌面：

```
桌面/
├── flowlens/        ← FlowLens 引擎
└── skills/          ← 运营工具（本项目）
```

## 第二步：一键安装

打开 **Terminal（终端）**，粘贴以下命令：

```bash
bash ~/Desktop/skills/setup.sh
```

> 如果解压位置不在桌面，把路径换成实际位置。

脚本会自动安装：Homebrew、Python、Node.js、所有依赖、Playwright 浏览器。  
**首次运行约需 5–15 分钟**，期间保持联网。

如果弹出「Xcode 命令行工具」安装对话框，点击「安装」，安装完成后重新执行上面的命令。

## 第三步：填写配置

安装完成后编辑 `skills/.env`（用任意文本编辑器打开）：

```bash
open -a TextEdit ~/Desktop/skills/.env
```

必填项：

| 配置项 | 说明 | 获取方式 |
|--------|------|----------|
| `ANTHROPIC_API_KEY` 或 `OPENAI_API_KEY` | AI 模型密钥 | 从 AI 服务商后台复制 |
| `XHS_COOKIE` | 小红书登录态 | 见下方说明 |

**获取小红书 Cookie：**
1. Chrome 打开 [https://www.xiaohongshu.com](https://www.xiaohongshu.com) 并登录
2. 按 F12 打开开发者工具 → Network 标签
3. 刷新页面，点击任意请求
4. 在 Request Headers 中找到 `Cookie:` 一行，复制完整内容
5. 粘贴到 `.env` 的 `XHS_COOKIE=` 后面

## 第四步：加载 Chrome 扩展

**先在 Chrome 里登录小红书账号**，FlowLens 会复用这个登录态，不会另开新窗口。

1. Chrome 地址栏输入 `chrome://extensions/` 回车
2. 页面**右上角**打开「**开发者模式**」（蓝色开关）
3. 点击「**加载已解压的扩展程序**」
4. 在弹出的文件选择框里，导航到解压目录，选中 `flowlens/chrome_extension` 这个**文件夹**（不是压缩包，是整个文件夹）
5. 扩展卡片出现后，确认右下角开关是**蓝色（已启用）**

**验证：** 点一下浏览器右上角扩展栏里的 FlowLens 图标，弹窗里会显示「未连接」——这是正常的，运行任务时会自动连接。

**如果运行任务时卡在 `Waiting for extension...`：**
- 检查 Chrome 是否已打开
- 检查 `chrome://extensions/` 里扩展开关是否为蓝色
- 点击扩展图标，弹窗里确认端口号与终端输出的端口一致

## 第五步：授权 macOS 权限（首次）

系统设置 → 隐私与安全：

- **辅助功能** → 添加 Terminal（或 iTerm2）
- **屏幕录制** → 添加 Terminal（或 iTerm2）

## 启动

```bash
bash ~/Desktop/skills/start.sh
```

浏览器会自动打开 `http://127.0.0.1:8888`，即运营页面。

---

## 常见问题

**Q: setup.sh 报错「command not found: brew」**  
A: 关闭 Terminal，重新打开，再运行一次。

**Q: 小红书显示验证码或「页面不可用」**  
A: Cookie 已过期，重新获取后更新 `.env` 中的 `XHS_COOKIE`。

**Q: 启动后终端卡在「Waiting for extension...」**  
A: ① Chrome 必须已打开；② `chrome://extensions/` 确认 FlowLens 扩展开关是蓝色；③ 点扩展图标，弹窗里端口号应与终端一致（默认 8765）。

**Q: 端口 8888 被占用**  
A: `start.sh` 会自动释放，若仍失败：`lsof -ti :8888 | xargs kill -9`
