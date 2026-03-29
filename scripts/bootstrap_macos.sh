#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$ROOT_DIR/.venv"
MIN_PYTHON_MINOR=11

log() {
  printf '\n==> %s\n' "$1"
}

have_cmd() {
  command -v "$1" >/dev/null 2>&1
}

require_macos() {
  if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "ClawVision currently supports macOS only." >&2
    exit 1
  fi
}

require_xcode_clt() {
  if ! xcode-select -p >/dev/null 2>&1; then
    cat >&2 <<'EOF'
Xcode Command Line Tools are required.

Run:
  xcode-select --install

Then rerun:
  bash scripts/bootstrap_macos.sh
EOF
    exit 1
  fi
}

require_brew_if_missing() {
  local tool="$1"
  if have_cmd brew; then
    return 0
  fi

  cat >&2 <<EOF
$tool is missing and Homebrew is not installed.

Install Homebrew first:
  /bin/bash -c "\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

Then rerun:
  bash scripts/bootstrap_macos.sh
EOF
  exit 1
}

ensure_node() {
  if have_cmd node && have_cmd npm; then
    return 0
  fi

  require_brew_if_missing "Node.js"
  log "Installing Node.js"
  brew install node
}

# Check whether a python binary satisfies >=3.$MIN_PYTHON_MINOR.
python_is_suitable() {
  local bin="$1"
  [[ -x "$bin" ]] || return 1
  "$bin" -c "import sys; exit(0 if sys.version_info >= (3, $MIN_PYTHON_MINOR) else 1)" 2>/dev/null
}

# Try to find a suitable Python (>=3.11) already on the system.
# Falls back to installing python@3.11 via Homebrew.
resolve_python() {
  # 1. Check common names in PATH (prefer newer versions).
  for candidate in python3.13 python3.12 python3.11 python3; do
    if have_cmd "$candidate" && python_is_suitable "$(command -v "$candidate")"; then
      command -v "$candidate"
      return 0
    fi
  done

  # 2. Nothing suitable found — install via Homebrew.
  require_brew_if_missing "Python 3.11+"
  log "Installing Python 3.11 via Homebrew"
  brew install python@3.11
  echo "$(brew --prefix python@3.11)/bin/python3.11"
}

ensure_rust() {
  if have_cmd cargo && have_cmd rustc; then
    return 0
  fi

  log "Installing Rust toolchain via rustup"
  curl --proto '=https' --tlsv1.2 -fsSL https://sh.rustup.rs | sh -s -- -y --profile minimal
}

prepare_venv() {
  local python_bin="$1"

  if [[ -x "$VENV_DIR/bin/python" ]]; then
    if ! python_is_suitable "$VENV_DIR/bin/python"; then
      log "Existing .venv Python is too old — rebuilding"
      rm -rf "$VENV_DIR"
    fi
  fi

  if [[ ! -x "$VENV_DIR/bin/python" ]]; then
    log "Creating virtual environment"
    "$python_bin" -m venv "$VENV_DIR"
  fi

  log "Installing Python dependencies"
  "$VENV_DIR/bin/python" -m pip install --upgrade pip
  "$VENV_DIR/bin/pip" install -e "$ROOT_DIR[dev]"
}

install_desktop_deps() {
  if [[ ! -d "$ROOT_DIR/desktop_app" ]]; then
    return 0
  fi
  log "Installing desktop app npm dependencies"
  (cd "$ROOT_DIR/desktop_app" && npm install)
}

print_next_steps() {
  cat <<EOF

Bootstrap complete.

Next steps:
  1. Copy the sample env file:
       cp .env.example .env.local
  2. Add your Anthropic key to .env.local:
       ANTHROPIC_API_KEY=sk-ant-...
  3. Load chrome_extension/ as an unpacked extension in Chrome.
  4. Start the desktop app:
       cd desktop_app
       PATH="\$HOME/.cargo/bin:\$PATH" npm run tauri dev

Useful smoke tests:
  .venv/bin/python -m clawvision desktop run --prompt "研究露营装备" --dry-run
  .venv/bin/python -m clawvision chatbots --help
  .venv/bin/python -m clawvision extension reload --help
EOF
}

main() {
  require_macos
  require_xcode_clt
  ensure_node

  local python_bin
  python_bin="$(resolve_python)"

  ensure_rust
  if [[ -f "$HOME/.cargo/env" ]]; then
    # shellcheck disable=SC1091
    source "$HOME/.cargo/env"
  fi

  prepare_venv "$python_bin"
  install_desktop_deps
  print_next_steps
}

main "$@"
