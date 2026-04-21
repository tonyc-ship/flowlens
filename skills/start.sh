#!/usr/bin/env bash
# 启动运营页面（visual_discovery.py Flask 服务）
set -euo pipefail

SKILLS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILLS_VENV="$SKILLS_DIR/venv"
PORT="${PORT:-8888}"

if [[ ! -d "$SKILLS_VENV" ]]; then
    echo "未找到 venv，请先运行：bash setup.sh"
    exit 1
fi

# 如果端口占用，先释放
lsof -ti :"$PORT" | xargs kill -9 2>/dev/null || true

echo "启动运营页面：http://127.0.0.1:$PORT"
"$SKILLS_VENV/bin/python" "$SKILLS_DIR/scripts/visual_discovery.py" --port "$PORT"
