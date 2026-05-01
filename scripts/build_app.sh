#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="$ROOT_DIR/app"
APP_NAME="Socai Prototype.app"
BUILD_APP_PATH="$APP_DIR/src-tauri/target/release/bundle/macos/$APP_NAME"
INSTALL_APP_PATH="/Applications/$APP_NAME"

cd "$APP_DIR"
npm install
npm run tauri build -- --bundles app

if [[ ! -d "$BUILD_APP_PATH" ]]; then
  echo "Expected bundle missing: $BUILD_APP_PATH" >&2
  exit 1
fi

rm -rf "$INSTALL_APP_PATH"
ditto "$BUILD_APP_PATH" "$INSTALL_APP_PATH"

echo "Built app: $BUILD_APP_PATH"
echo "Installed app: $INSTALL_APP_PATH"
