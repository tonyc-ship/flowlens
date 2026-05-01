#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "scripts/build_desktop_app.sh is deprecated; building the replacement app/ instead." >&2
exec bash "$ROOT_DIR/scripts/build_app.sh" "$@"
