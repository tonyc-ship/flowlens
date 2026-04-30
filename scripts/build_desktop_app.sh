#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="$ROOT_DIR/desktop_app"
APP_NAME="SocAI Desktop.app"
BUILD_APP_PATH="$APP_DIR/src-tauri/target/release/bundle/macos/$APP_NAME"
INSTALL_APP_PATH="/Applications/$APP_NAME"

bash "$ROOT_DIR/scripts/prepare_desktop_runtime.sh"

cd "$APP_DIR"
npm install
npm run tauri build

if [[ ! -d "$BUILD_APP_PATH" ]]; then
  echo "Expected bundle missing: $BUILD_APP_PATH" >&2
  exit 1
fi

rm -rf "$INSTALL_APP_PATH"
ditto "$BUILD_APP_PATH" "$INSTALL_APP_PATH"

echo "Built app: $BUILD_APP_PATH"
echo "Installed app: $INSTALL_APP_PATH"
