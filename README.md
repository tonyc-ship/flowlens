# FlowLens: Privacy-First Computer Use Agent with Local Visual Memory

FlowLens is a computer use and browser use framework with lightweight local multimodal models and observation-learning loop. These designs enable a fast, stable and privacy-first CUA compared to other frameworks. FlowLens comes with a Chrome extension and a thin desktop app. Currently there are task specific knowledge for Xiaohongshu research, WeChat use and AI chatbot comparison. 

## Quickstart

- Python 3.11+
- Node.js + npm and Rust toolchain, only if you want the desktop app
- Anthropic API key, or you can use fully local LLMs

Inside your preferred Python environment (don't omit the last dot):

```bash
pip install -e .
```

Or with `uv`:

```bash
uv sync
```

Download Local Models:
```bash
modelscope download --model mlx-community/Qwen3.5-2B-6bit --local_dir ~/.flowlens/weights/Qwen3.5-2B-6bit
modelscope download --model mlx-community/Qwen3.5-9B-MLX-4bit --local_dir ~/.flowlens/weights/Qwen3.5-9B-MLX-4bit
```

## Desktop

Only needed if you want the Tauri desktop app:

```bash
# Install Node.js and Rust however you prefer
npm --version
cargo --version

cd desktop_app
npm install
PATH="$HOME/.cargo/bin:$PATH" npm run tauri dev
```

macOS permissions you will likely need on first run:

- `Screen Recording` for the Python interpreter / terminal app that launches FlowLens
- `Accessibility` if you later use desktop automation flows
- `Automation` if you want browser URL capture via Apple Events

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
FLOWLENS_LLM_BACKEND=sonnet
FLOWLENS_WHISPER_CLI=
FLOWLENS_WHISPER_MODELS_DIR=
FLOWLENS_OBSERVER_DIFF_THRESHOLD=0.30
FLOWLENS_OBSERVER_VISION_ENABLED=1
FLOWLENS_OBSERVER_VISION_MODEL=Qwen3.5-2B-6bit
```

## Observer-Only Quickstart

If you want continuous desktop capture without the desktop app, this is the shortest path:

```bash
python -m flowlens observer install-agent
python -m flowlens observer status
```

You can check observer data in:

- `observer_data/observer.db`
- `observer_data/screenshots/`
- `observer_data/logs/capture.log`


## Load The Chrome Extension

1. Open `chrome://extensions/`
2. Enable `Developer mode`
3. Click `Load unpacked`
4. Select `chrome_extension/`

## Package Layout

Canonical Python packages are now:

- `flowlens.core`: bridge, runtime, recorder, reporting, DOM-first interaction + verification primitives
- `flowlens.observer`: background desktop observation, storage, summarization, and recall
- `flowlens.perception`: hosted/local vision, OCR, grounding, transcription, media preprocessing
- `flowlens.reasoning`: task understanding, planning, evaluation, reusable knowledge extraction
- `flowlens.platforms.xhs` / `flowlens.platforms.chat`: site-level adapters and platform knowledge
- `flowlens.workflows.xhs` / `flowlens.workflows.chat`: concrete task flows and workflow CLIs

Legacy `flowlens.agent`, `flowlens.chatbots`, and `flowlens.vision` paths have been removed.

## Common Commands

Smoke-test the desktop bridge without running a live XHS task:

```bash
python -m flowlens desktop run --prompt "研究露营装备" --dry-run
```

Start the chatbot fan-out CLI:

```bash
python -m flowlens chatbots "Explain quantum computing simply"
```

Run an XHS topic research task:

```bash
python -m flowlens "露营装备"
```

Reload the unpacked Chrome extension through the live bridge:

```bash
python -m flowlens extension reload
```

Inspect the observer subsystem state:

```bash
python -m flowlens observer status
```

This now includes aggregate timing stats and the latest capture-stage timings (`capture_image_ms`, `diff_ms`, `ocr_ms`, `visual_ms`, `total_ms`).

Capture the current desktop once into the observer database:

```bash
python -m flowlens observer capture-once
```

Install the background observer agent:

```bash
python -m flowlens observer install-agent
```

Generate a lightweight local journal without LLM calls:

```bash
python -m flowlens observer journal --no-llm
```

Run the installed desktop app end-to-end XHS watch-overlay smoke test:

```bash
python3 scripts/verify_packaged_xhs_overlay.py
```

This launches the installed `.app`, switches to the XHS view, starts the built-in `研究露营` preset, captures desktop screenshots, and writes a JSON summary under `task_runs/`.

Run the local-vs-cloud web-use benchmark (text, DOM, screenshot cases):

```bash
python3 scripts/benchmark_webuse_models.py
```

This writes a timestamped benchmark bundle under `task_runs/` with per-case outputs, timing, and simple quality scoring for `sonnet` vs `qwen-local`.

## Troubleshooting

- If `xcode-select -p` fails, run `xcode-select --install`.
- If `cargo` is not on your shell `PATH`, run `source "$HOME/.cargo/env"`.
- If the desktop app starts but tasks do not run, confirm `.env.local` contains `ANTHROPIC_API_KEY`.
- If the agent cannot reach Chrome, confirm the unpacked extension is loaded and connected.
