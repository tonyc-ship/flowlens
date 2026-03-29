# ClawVision

ClawVision is a macOS-first browser automation project for Xiaohongshu research, Chrome extension control, and a small Tauri desktop shell.

Current repo highlights:

- `clawvision/`: Python runtime, agent orchestration, reporting, and browser bridge logic
- `chrome_extension/`: MV3 extension used for Chrome-side automation and DOM extraction
- `desktop_app/`: Tauri desktop shell that launches the local Python runtime

## Requirements

- macOS
- Google Chrome
- Xcode Command Line Tools (`xcode-select --install`)
- Python 3.11+ (the bootstrap script installs via Homebrew if needed)
- Node.js + npm
- Rust toolchain (installed automatically by the bootstrap script via `rustup`)
- Anthropic API key for the default hosted vision/LLM path

## Quickstart

Clone the repo and run the bootstrap script:

```bash
git clone https://github.com/tonyc-ship/clawvision.git
cd clawvision
bash scripts/bootstrap_macos.sh
```

The script will:

- install Python 3.11+ with Homebrew if no suitable version is found
- install the Rust toolchain with `rustup` if needed
- create `.venv/`
- install the Python package in editable mode with dev dependencies
- install `desktop_app/` npm dependencies

## Configure Local Env

Create a local env file:

```bash
cp .env.example .env.local
```

Minimum config for the default hosted path:

```bash
ANTHROPIC_API_KEY=...
```

Optional keys:

```bash
CLAWVISION_LLM_BACKEND=sonnet
CLAWVISION_WHISPER_CLI=
CLAWVISION_WHISPER_MODELS_DIR=
```

## Load The Chrome Extension

1. Open `chrome://extensions/`
2. Enable `Developer mode`
3. Click `Load unpacked`
4. Select `chrome_extension/`

## Start The Desktop App

```bash
cd desktop_app
PATH="$HOME/.cargo/bin:$PATH" npm run tauri dev
```

On first launch, Tauri and Cargo dependencies can take a while to compile.

## Common Commands

Smoke-test the desktop bridge without running a live XHS task:

```bash
.venv/bin/python -m clawvision desktop run --prompt "研究露营装备" --dry-run
```

Start the chatbot fan-out CLI:

```bash
.venv/bin/python -m clawvision chatbots "Explain quantum computing simply"
```

Run an XHS topic research task:

```bash
.venv/bin/python -m clawvision.agent "露营装备"
```

Reload the unpacked Chrome extension through the live bridge:

```bash
.venv/bin/python -m clawvision extension reload
```

## Optional Extras

Base bootstrap keeps the install light. If you want optional local models or extra media backends:

```bash
.venv/bin/pip install -e ".[detect]"
.venv/bin/pip install -e ".[local-llm]"
.venv/bin/pip install -e ".[ocr]"
```

## Troubleshooting

- If `xcode-select -p` fails, run `xcode-select --install`.
- If `cargo` is not on your shell `PATH`, run `source "$HOME/.cargo/env"`.
- If the desktop app starts but tasks do not run, confirm `.env.local` contains `ANTHROPIC_API_KEY`.
- If the agent cannot reach Chrome, confirm the unpacked extension is loaded and connected.
