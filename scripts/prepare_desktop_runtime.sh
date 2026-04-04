#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="$ROOT_DIR/desktop_app/runtime_bundle"

mkdir -p "$RUNTIME_DIR"
rsync -a --delete \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  "$ROOT_DIR/flowlens/" \
  "$RUNTIME_DIR/flowlens/"

if [[ -f "$ROOT_DIR/desktop_app/runtime_bundle/bin/flowlens" ]]; then
  chmod +x "$ROOT_DIR/desktop_app/runtime_bundle/bin/flowlens"
fi

echo "Prepared desktop runtime at $RUNTIME_DIR"
