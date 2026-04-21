# Windows 快速上手指南

> 适用系统：Windows 10 / 11  
> 不需要代理，不需要 Claude 账号

---

## 第一步：安装 Python

1. 打开 https://www.python.org/downloads/
2. 下载最新版（3.12 以上），点击安装
3. **安装时勾选 "Add Python to PATH"**（这步很重要）
4. 安装完成后打开「命令提示符」或「PowerShell」验证：

```
python --version
```

显示版本号即成功。

---

## 第二步：下载项目

方式 A：有 Git 的话：
```
git clone https://github.com/seikachin/Auto-Redbook-Skills.git
cd Auto-Redbook-Skills
```

方式 B：没有 Git，直接去 GitHub 页面点 **Code → Download ZIP**，解压后进入文件夹。

---

## 第三步：安装依赖

在项目文件夹里打开命令提示符（地址栏输入 `cmd` 回车），执行：

```
pip install -r requirements.txt
playwright install chromium
```

等待下载完成（chromium 约 150MB，耐心等一下）。

---

## 第四步：配置 AI Key

在项目根目录新建一个文件，命名为 `.env`（注意没有后缀名），内容：

```
DASHSCOPE_API_KEY=你的阿里云DashScope密钥
```

**获取 DashScope Key：**
1. 登录 https://dashscope.console.aliyun.com
2. 左侧「API-KEY 管理」→「创建 API Key」
3. 复制密钥粘贴进 `.env`

> 阿里云 Coding Plan 用户直接用，有免费额度。

---

## 第五步：扫码登录小红书

```
python scripts/xhs_login.py
```

会自动弹出浏览器，打开小红书登录页，**用手机扫码**，登录成功后自动保存，窗口自动关闭。

> Cookie 有效期约 30 天，过期后重跑这一步即可。

---

## 第六步：启动

```
python scripts/visual_discovery.py
```

看到 `Serving on http://localhost:8765` 后，浏览器打开：

```
http://localhost:8765
```

---

## 常见问题

**Q：`pip` 不是内部命令**  
A：Python 安装时没有勾选"Add to PATH"，重新安装并勾选，或用 `py -m pip install -r requirements.txt`

**Q：`playwright install chromium` 下载很慢**  
A：可以挂代理，或等待——国内网络下载 Playwright Chromium 有时确实慢

**Q：扫码后浏览器没有弹出来**  
A：先确保电脑安装了 Chrome 浏览器，如果还是不行执行：
```
playwright install chrome
```

**Q：`.env` 文件建不了（Windows 不让以点开头命名）**  
A：打开命令提示符，执行：
```
copy env.example.txt .env
```
然后用记事本打开 `.env` 编辑。

**Q：启动后提示端口被占用**  
A：换一个端口，编辑 `scripts/visual_discovery.py` 第一行附近找到 `PORT = 8765` 改成其他数字，如 `8766`

**Q：关机后定时发布会失效吗**  
A：是的，定时发布依赖本地进程运行，关机或关闭命令窗口就停了。需要保持电脑开机且命令窗口不关闭。

---

## 日常使用流程

```
# 每次使用前只需要这一步
python scripts/visual_discovery.py

# 浏览器打开
http://localhost:8765
```

Cookie 过期时（约每月一次）：
```
python scripts/xhs_login.py
```
