#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="$ROOT_DIR/app"
APP_NAME="Socai Prototype.app"
BUILD_APP_PATH="$APP_DIR/src-tauri/target/release/bundle/macos/$APP_NAME"
INSTALL_APP_PATH="/Applications/$APP_NAME"
PNPM_BIN="${PNPM:-pnpm}"

if ! command -v "$PNPM_BIN" >/dev/null 2>&1; then
  echo "pnpm is required to build the Socai app. Install it with: corepack enable pnpm" >&2
  exit 1
fi

"$PNPM_BIN" --dir "$APP_DIR" install --frozen-lockfile
"$PNPM_BIN" --dir "$APP_DIR" exec tauri build --bundles app

if [[ ! -d "$BUILD_APP_PATH" ]]; then
  echo "Expected bundle missing: $BUILD_APP_PATH" >&2
  exit 1
fi

rm -rf "$INSTALL_APP_PATH"
for legacy_path in "/Applications/SocAI Prototype.app" "/Applications/Socai Desktop.app"; do
  if [[ "$legacy_path" != "$INSTALL_APP_PATH" && -e "$legacy_path" ]]; then
    rm -rf "$legacy_path"
    echo "Removed legacy app: $legacy_path"
  fi
done

ditto "$BUILD_APP_PATH" "$INSTALL_APP_PATH"

echo "Built app: $BUILD_APP_PATH"
echo "Installed app: $INSTALL_APP_PATH"
