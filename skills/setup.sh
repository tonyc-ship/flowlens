#!/usr/bin/env bash
# =============================================================================
# Auto-Redbook-Skills + FlowLens 一键部署脚本（macOS）
# 在新 Mac 上执行：bash setup.sh
# 支持离线安装：解压包含 offline_packages/ 时自动跳过网络下载
# =============================================================================
set -euo pipefail

SKILLS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FLOWLENS_REPO="https://github.com/tonyc-ship/flowlens.git"

# 优先查找打包在压缩包里的兄弟目录，再退回 ~/小红书检索
_detect_flowlens() {
    [[ -n "${FLOWLENS_PROJECT_ROOT:-}" ]] && { echo "$FLOWLENS_PROJECT_ROOT"; return; }
    for sibling in "$SKILLS_DIR/../flowlens" "$SKILLS_DIR/../小红书检索"; do
        [[ -d "$sibling/flowlens" ]] && { python3 -c "import os; print(os.path.realpath('$sibling'))"; return; }
    done
    echo "$HOME/小红书检索"
}
FLOWLENS_DEFAULT="$HOME/小红书检索"

# ── 颜色输出 ─────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✓${NC} $*"; }
warn() { echo -e "${YELLOW}⚠${NC}  $*"; }
step() { echo -e "\n${GREEN}▶${NC} $*"; }
fail() { echo -e "${RED}✗${NC} $*"; exit 1; }

echo ""
echo "=========================================="
echo "  Auto-Redbook-Skills + FlowLens 部署"
echo "=========================================="

# ── 检测离线包目录 ────────────────────────────────────────────────────────────
OFFLINE_DIR=""
for candidate in "$SKILLS_DIR/../offline_packages" "$SKILLS_DIR/offline_packages"; do
    if [[ -d "$candidate/flowlens_wheels" ]]; then
        OFFLINE_DIR="$(cd "$candidate" && pwd)"
        break
    fi
done
[[ -n "$OFFLINE_DIR" ]] && ok "检测到离线包：$OFFLINE_DIR（将跳过网络下载）" || warn "未检测到离线包，将从网络安装"

# ── 1. Xcode CLI Tools ───────────────────────────────────────────────────────
step "检查 Xcode Command Line Tools"
if ! xcode-select -p &>/dev/null; then
    warn "未安装，正在触发安装对话框（请在弹窗中点击「安装」）..."
    xcode-select --install 2>/dev/null || true
    echo "安装完成后重新运行本脚本。"
    exit 0
fi
ok "Xcode CLI Tools 已就绪"

# ── 2. Homebrew ──────────────────────────────────────────────────────────────
step "检查 Homebrew"
if ! command -v brew &>/dev/null; then
    warn "未安装 Homebrew，尝试用清华镜像安装（国内网络友好）..."
    export HOMEBREW_BREW_GIT_REMOTE="https://mirrors.tuna.tsinghua.edu.cn/git/homebrew/brew.git"
    export HOMEBREW_CORE_GIT_REMOTE="https://mirrors.tuna.tsinghua.edu.cn/git/homebrew/homebrew-core.git"
    export HOMEBREW_BOTTLE_DOMAIN="https://mirrors.tuna.tsinghua.edu.cn/homebrew-bottles"
    /bin/bash -c "$(curl -fsSL https://mirrors.tuna.tsinghua.edu.cn/git/homebrew/install.sh)" || \
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    [[ -f /opt/homebrew/bin/brew ]] && eval "$(/opt/homebrew/bin/brew shellenv)"
fi
ok "Homebrew $(brew --version | head -1)"

# ── 3. Python 3.11+ ──────────────────────────────────────────────────────────
step "检查 Python 3.11+"
PYTHON=""
for candidate in python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" &>/dev/null; then
        VER=$("$candidate" -c "import sys; print(sys.version_info >= (3,11))" 2>/dev/null)
        if [[ "$VER" == "True" ]]; then
            PYTHON="$candidate"
            break
        fi
    fi
done

if [[ -z "$PYTHON" ]]; then
    # 优先使用离线包里的 Python 安装器
    PKG_PATH=""
    [[ -n "$OFFLINE_DIR" && -f "$OFFLINE_DIR/../python3.13.pkg" ]] && PKG_PATH="$(cd "$OFFLINE_DIR/.." && pwd)/python3.13.pkg"
    [[ -z "$PKG_PATH" && -f "$SKILLS_DIR/../python3.13.pkg" ]] && PKG_PATH="$SKILLS_DIR/../python3.13.pkg"

    if [[ -n "$PKG_PATH" ]]; then
        warn "未找到 Python 3.11+，正在从离线包安装 Python 3.13..."
        sudo installer -pkg "$PKG_PATH" -target /
        PYTHON="python3.13"
    else
        warn "未找到 Python 3.11+，通过 Homebrew 安装 Python 3.13..."
        brew install python@3.13
        PYTHON="$(brew --prefix)/bin/python3.13"
    fi
fi
ok "Python: $($PYTHON --version)"

# ── 4. Node.js ───────────────────────────────────────────────────────────────
step "检查 Node.js"
if ! command -v node &>/dev/null; then
    warn "未安装，通过 Homebrew 安装 Node.js..."
    # 使用清华镜像加速 bottle 下载
    export HOMEBREW_BOTTLE_DOMAIN="https://mirrors.tuna.tsinghua.edu.cn/homebrew-bottles"
    brew install node
fi
ok "Node.js: $(node --version),  npm: $(npm --version)"

# ── 5. Google Chrome ─────────────────────────────────────────────────────────
step "检查 Google Chrome"
CHROME_FOUND=false
for p in "/Applications/Google Chrome.app" "$HOME/Applications/Google Chrome.app"; do
    [[ -d "$p" ]] && CHROME_FOUND=true && break
done
if [[ "$CHROME_FOUND" == "false" ]]; then
    warn "未找到 Google Chrome，请手动安装后重新运行。"
    warn "下载地址：https://www.google.cn/chrome/"
    exit 1
fi
ok "Google Chrome 已安装"

# ── 6. FlowLens 项目 ──────────────────────────────────────────────────────────
step "检查 FlowLens 项目"
FLOWLENS_ROOT="$(_detect_flowlens)"
if [[ ! -d "$FLOWLENS_ROOT" ]]; then
    warn "未找到 FlowLens，从 GitHub 克隆到 $FLOWLENS_ROOT ..."
    git clone "$FLOWLENS_REPO" "$FLOWLENS_ROOT"
fi
ok "FlowLens 路径：$FLOWLENS_ROOT"

# ── 7. 共享 .env 配置 ─────────────────────────────────────────────────────────
step "配置 .env"
ENV_FILE="$SKILLS_DIR/.env"
if [[ ! -f "$ENV_FILE" ]]; then
    cp "$SKILLS_DIR/.env.example" "$ENV_FILE"
    warn ".env 已从模板创建，请编辑填入 API Key 和 Cookie："
    warn "  $ENV_FILE"
fi
ok ".env 就绪"

FL_ENV="$FLOWLENS_ROOT/.env"
if [[ ! -e "$FL_ENV" ]]; then
    ln -sf "$ENV_FILE" "$FL_ENV"
    ok "已创建软链接：$FL_ENV -> $ENV_FILE"
elif [[ -L "$FL_ENV" ]]; then
    ok "软链接已存在：$FL_ENV"
else
    warn "$FL_ENV 已存在且不是软链接，请手动同步配置"
fi

if [[ "$FLOWLENS_ROOT" != "$FLOWLENS_DEFAULT" ]]; then
    if ! grep -q "^FLOWLENS_PROJECT_ROOT=" "$ENV_FILE" 2>/dev/null; then
        echo "" >> "$ENV_FILE"
        echo "FLOWLENS_PROJECT_ROOT=$FLOWLENS_ROOT" >> "$ENV_FILE"
        ok "已将 FLOWLENS_PROJECT_ROOT 写入 .env"
    fi
fi

# ── 8. FlowLens Python venv ───────────────────────────────────────────────────
step "安装 FlowLens Python 依赖"
FL_VENV="$FLOWLENS_ROOT/venv"
[[ ! -d "$FL_VENV" ]] && "$PYTHON" -m venv "$FL_VENV"
FL_PIP="$FL_VENV/bin/pip"
"$FL_PIP" install --upgrade pip -q
if [[ -n "$OFFLINE_DIR" ]]; then
    "$FL_PIP" install -e "$FLOWLENS_ROOT[observer]" \
        --no-index --find-links="$OFFLINE_DIR/flowlens_wheels" -q
else
    "$FL_PIP" install -e "$FLOWLENS_ROOT[observer]" -q
fi
ok "FlowLens 依赖安装完成（含 observer 扩展）"

# ── 9. Auto-Redbook-Skills Python 依赖 ───────────────────────────────────────
step "安装 Auto-Redbook-Skills Python 依赖"
SKILLS_VENV="$SKILLS_DIR/venv"
[[ ! -d "$SKILLS_VENV" ]] && "$PYTHON" -m venv "$SKILLS_VENV"
SKILLS_PIP="$SKILLS_VENV/bin/pip"
"$SKILLS_PIP" install --upgrade pip -q
if [[ -n "$OFFLINE_DIR" ]]; then
    "$SKILLS_PIP" install -r "$SKILLS_DIR/requirements.txt" openpyxl \
        --no-index --find-links="$OFFLINE_DIR/skills_wheels" -q
else
    "$SKILLS_PIP" install -r "$SKILLS_DIR/requirements.txt" -q
    "$SKILLS_PIP" install openpyxl -q
fi
ok "Skills Python 依赖安装完成"

# ── 10. Playwright 浏览器 ─────────────────────────────────────────────────────
step "安装 Playwright Chromium"
if [[ -n "$OFFLINE_DIR" && -d "$OFFLINE_DIR/playwright_browsers" ]]; then
    PW_CACHE="$HOME/Library/Caches/ms-playwright"
    mkdir -p "$PW_CACHE"
    cp -Rn "$OFFLINE_DIR/playwright_browsers/." "$PW_CACHE/" 2>/dev/null || true
    ok "Playwright Chromium 已从离线包安装"
else
    "$SKILLS_VENV/bin/python" -m playwright install chromium 2>/dev/null && \
        ok "Python Playwright Chromium 就绪" || \
        warn "安装失败，稍后手动运行：$SKILLS_VENV/bin/python -m playwright install chromium"
fi

# Node.js 依赖
cd "$SKILLS_DIR"
if [[ -n "$OFFLINE_DIR" && -d "$OFFLINE_DIR/node_modules" ]]; then
    [[ ! -d "$SKILLS_DIR/node_modules" ]] && cp -R "$OFFLINE_DIR/node_modules" "$SKILLS_DIR/node_modules"
    ok "node_modules 已从离线包复制"
else
    npm install --silent 2>/dev/null && ok "npm install 完成" || warn "npm install 失败"
    npx playwright install chromium --with-deps 2>/dev/null && \
        ok "Node Playwright Chromium 就绪" || \
        warn "安装失败，稍后手动运行：npx playwright install chromium"
fi

# ── 11. 验证 ──────────────────────────────────────────────────────────────────
step "验证安装"
echo ""
"$FL_VENV/bin/python" -c "import flowlens; print('  FlowLens import OK')" 2>/dev/null && \
    ok "FlowLens 可导入" || warn "FlowLens import 失败，检查上方报错"
"$SKILLS_VENV/bin/python" -c "import playwright, openpyxl, markdown, yaml; print('  Skills deps OK')" 2>/dev/null && \
    ok "Skills 依赖可导入" || warn "Skills 依赖 import 失败"
node -e "require('js-yaml'); require('marked'); console.log('  Node deps OK')" 2>/dev/null && \
    ok "Node.js 依赖可加载" || warn "Node 依赖加载失败"

# ── 12. 完成提示 ──────────────────────────────────────────────────────────────
echo ""
echo "=========================================="
echo "  后续手动步骤"
echo "=========================================="
echo ""
echo "1. 加载 Chrome 扩展"
echo "   - 打开 Chrome → chrome://extensions/"
echo "   - 启用「开发者模式」"
echo "   - 点击「加载已解压的扩展」"
echo "   - 选择目录：$FLOWLENS_ROOT/chrome_extension"
echo ""
echo "2. 填写 .env 配置（必填项）"
echo "   - MIDSCENE_MODEL_API_KEY（阿里云 DashScope Key）"
echo "   - DASHSCOPE_API_KEY（同上）"
echo "   - XHS_COOKIE（从 Chrome DevTools Network 面板复制）"
echo "   - 文件位置：$ENV_FILE"
echo ""
echo "3. macOS 权限"
echo "   - 系统设置 → 隐私与安全 → 辅助功能 → 添加 Terminal"
echo "   - 系统设置 → 隐私与安全 → 屏幕录制 → 添加 Terminal"
echo ""
echo "4. 启动运营页面"
echo "   bash $SKILLS_DIR/start.sh"
echo ""
echo -e "${GREEN}✓ 部署完成！${NC}"
