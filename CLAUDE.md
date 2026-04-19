# FlowLens

FlowLens is currently maintained as a layered browser automation framework built on:

- Core browser/runtime primitives in `flowlens.core`
- Background desktop observation in `flowlens.observer`
- Perception utilities in `flowlens.perception`
- Task understanding and knowledge in `flowlens.reasoning`
- Platform adapters in `flowlens.platforms`
- Task workflows in `flowlens.workflows`
- A Chrome extension in `chrome_extension/`
- A thin Tauri desktop shell in `desktop_app/`

The old screen-level MCP route has been archived under `archive/legacy_mcp/`.

## Current Architecture

```
flowlens/
├── chrome_extension/                 # MV3 extension: websocket, CDP, DOM extraction, watch mode
├── desktop_app/                      # Minimal Tauri desktop shell / future desktop control plane
├── flowlens/
│   ├── cli.py                        # Primary CLI entry
│   ├── __main__.py                   # `python -m flowlens`
│   ├── desktop_cli.py                # Desktop shell -> Python task bridge
│   ├── extension_cli.py              # `python -m flowlens extension ...`
│   ├── extension_ops.py              # Generic extension operational reports / commands
│   ├── server.py                     # Archived-route compatibility stub
│   ├── core/
│   │   ├── bridge.py                 # WebSocket bridge to the extension + tab routing
│   │   ├── composer.py               # DOM-first composer interaction helpers
│   │   ├── verification.py           # DOM-first verifier primitives
│   │   ├── recorder.py               # Session recording
│   │   ├── reporting.py              # Shared markdown/html rendering
│   │   └── runtime.py                # Local env / path discovery
│   ├── observer/
│   │   ├── cli.py                    # `python -m flowlens observer ...`
│   │   ├── paths.py                  # Observer data root / logs / screenshot paths
│   │   ├── store.py                  # SQLite storage for captures + project memory
│   │   ├── service.py                # Capture loop, screenshots, diffing, launchd
│   │   └── analysis.py               # Summaries, journals, memories, capture Q&A
│   ├── perception/
│   │   ├── local_llm.py              # Local MLX inference
│   │   ├── media.py                  # LLM calls (text+vision), OCR, transcription
│   │   ├── llm.py                    # Vision API wrapper + request profiles
│   │   ├── apple_ocr.py              # macOS native OCR
│   │   ├── detector.py               # Optional local UI detection
│   │   ├── grounding.py              # Optional local grounding backends
│   │   ├── ocr.py                    # OCR helpers
│   │   └── transcriber.py            # whisper.cpp integration
│   ├── reasoning/
│   │   ├── task_agent.py             # Generic task understanding / assessment
│   │   ├── tasks.py                  # Structured task definitions
│   │   └── knowledge/                # Reusable knowledge extraction + loading
│   ├── agent/                        # LLM-driven agent loop, tools, backends, knowledge-aware system prompt
│   ├── knowledge/sites/              # Per-site YAML knowledge files loaded into the agent prompt
│   ├── platforms/
│   │   ├── wechat/                   # WeChat macOS app helpers
│   │   └── xhs/                      # Xiaohongshu entities, capability catalog, multimodal processor
│   └── workflows/
│       └── wechat/                   # WeChat chat-summary workflow
├── tests/
│   ├── manual_local_llm.py           # Manual local-vs-remote backend comparison
│   ├── test_task_specs.py            # Structured task tests
│   ├── test_task_agent.py            # Task-agent parsing tests
│   ├── test_extension_ops.py         # Extension operation report tests
│   ├── test_observer.py              # Observer capture / storage / diff tests
│   └── test_reporting.py             # Shared report rendering tests
└── archive/
    └── legacy_mcp/                   # Archived screen-level MCP route
```

## Development Principles

### Never report untested code

Every change must be tested and verified before presenting to the user. No exceptions. No "needs re-test" or "not yet integrated". If it's not tested, it's not done.

### Keep CLAUDE.md in sync with the codebase

When a change materially affects architecture, runtime flow, testing entry points, or core operating rules, update `CLAUDE.md` in the same working session.

- Do not leave architecture docs stale after a refactor.
- If the change is operational rather than architectural, add the rule to local memory as well when it is likely to matter in future turns.
- Generated outputs and local run artifacts should stay out of git unless explicitly requested.

### Always rebuild and reinstall the desktop app after relevant changes

Whenever you modify the Tauri app under `desktop_app/` **or** any Python code that the installed app bundles (the `runtime_bundle/` copy of `flowlens/`), produce a fresh macOS `.app` bundle and install it in the same working session.

- Do not stop at `cargo check` / `npm run build` alone for Tauri changes.
- Do not stop at editing Python source — the installed `.app` bundles its own copy under `Contents/Resources/_up_/runtime_bundle/`, so source edits only take effect after a rebuild.
- Run the repo packaging path so the real desktop artifact exists after the change.
- Prefer `bash scripts/build_desktop_app.sh` unless the user explicitly asks for a different packaging flow.

After changes that affect the installed app + Chrome extension workflow together (for example `desktop_app/`, `chrome_extension/`, `flowlens/core/bridge.py`, or `flowlens/agent/`), rebuild the packaged app and smoke-test it manually.

> Note: the previous `scripts/verify_packaged_xhs_overlay.py` regression script targeted the legacy hardcoded XHS workflow and was removed when the workflow was deleted. A new agent-loop verification script needs to be written before this can be reinstated as an automated regression check.

When comparing local vs cloud reasoning / vision quality for web-use behavior, use the dedicated benchmark harness:

```bash
python3 scripts/benchmark_webuse_models.py
```

It runs a fixed matrix of text-only, DOM-like, and screenshot cases through `sonnet` and `qwen-local`, then writes a timestamped bundle under `task_runs/` with:
- raw outputs
- per-case timing + simple process metrics
- structured pass/fail scoring

### Test → Evaluate → Fix → Present

After every significant change, follow this mandatory workflow:
1. **Test** on real data (live site, not mocks)
2. **Screenshot** at key steps (search results, note detail, etc.)
3. **Time** every operation
4. **Generate** a visual HTML report with screenshots, images, OCR/Vision results, timing
5. **Self-evaluate** the results (check completeness, quality, timing)
6. **Fix** any issues found
7. **Re-run** if needed
8. **Present** final verified results to user

Never deliver only JSON or console output — always include a human-scannable visual HTML report.

### Session recording and reasoning logs

Every task run must produce:
- **Session recording** — Animated GIF of the Chrome window captured throughout the entire session (periodic CDP screenshots). Saved alongside the report.
- **Reasoning log** — All agent thinking and decision-making: why a note was picked, why a search term was chosen, what the agent observed and concluded. Not just "what happened" but "why". Included in the HTML report.

### Self-unblock with Accessibility tools

When blocked by something that needs manual browser/UI interaction (reload Chrome extension, click dialogs, navigate chrome:// pages, approve permissions), use macOS Accessibility APIs (screen.py, pyautogui, AppleScript) to do it instead of asking the user. This also serves as self-hosting validation of FlowLens's own capabilities.

For extension reload specifically:
- Prefer the built-in runtime path first: `bridge.reload_extension()` or `python -m flowlens extension reload`
- If the extension is too broken or disconnected to reload itself, then fall back to macOS Accessibility on `chrome://extensions/`

### Autonomous long-horizon work

Do as much as possible autonomously — verify each step, then present the final result. Don't stop to ask the user for simple operational steps. Auto-open browsers/websites as needed.

### Always use a background Chrome window for live runs

When running live browser tests, create and use a **new background Chrome window** that reuses the user's existing Chrome profile and login state.

- Do **not** take over or overwrite the user's current foreground browsing tab/window.
- Keep automation in the background window unless the user explicitly asks to watch or interact with it.
- Treat the user's foreground browsing as independent from the automation target.

Exception:
- The WeChat desktop workflow is explicitly foreground and visible because it reads the real macOS app window rather than a background browser tab.

### Use Claude Vision to verify screenshots

During testing, use Claude Vision to inspect screenshots and verify correctness, not just check for non-empty data.

For UI / UX / window-management debugging, also verify that the screenshot itself is faithful to the intended target:
- Confirm the dominant app/window in the image is actually the one you meant to capture.
- Reject captures where another window is covering the target or where the image looks stitched across multiple Spaces/displays.
- Do not use an unverified screenshot as evidence for page state, selector bugs, or interaction bugs.

For packaged-app verification:
- capture screenshots from the actual desktop, not just CDP tab screenshots
- confirm the browser opened on-screen and the in-page XHS watch overlay is visible
- confirm the Tauri app state advances out of `RUNNING` when the task completes

### No pixel-heuristic CV

Prefer semantic understanding over pixel math. Don't use pixel-level heuristics for UI understanding.

### Strategic architecture

The project's goal is **robust agentic browser automation plus local desktop observation**, not a single-site scraper. Architecture is layered:
1. **Core layer** (`flowlens.core`) — bridge, tab/window control, DOM-first composer helpers, verification, recording, shared reports, runtime.
2. **Observer layer** (`flowlens.observer`) — background desktop capture, SQLite storage, screenshot archival, diff-based OCR / vision, journals, and recall.
3. **Perception layer** (`flowlens.perception`) — hosted/local vision, OCR, grounding, transcription, image preprocessing.
4. **Reasoning layer** (`flowlens.reasoning`) — structured tasks, planning, evaluation, and reusable knowledge extraction.
5. **Platform layer** (`flowlens.platforms`) — site-specific DOM extraction, navigation patterns, entity models, capability catalogs.
6. **Workflow layer** (`flowlens.workflows`) — concrete task orchestration such as XHS research and WeChat summaries.

New generic capabilities (background windows, dedup, session recording) belong in the generic layer. Site-specific DOM selectors and navigation belong in site skill modules.

## Runtime Flow

1. Python starts a local WebSocket server.
2. The Chrome extension connects from the logged-in browser profile.
3. `flowlens.agent.loop.run_agent` builds a system prompt from generic browser/vision tools plus per-site knowledge loaded from `flowlens/knowledge/sites/*.yaml`.
4. The agent loop drives an LLM (Anthropic Sonnet by default, or local MLX backends via `--backend qwen-local` / `--backend ui-tars-local`) through a `tool_use → execute → feed back result` cycle until the LLM returns a final text report.
5. Tool calls go through `flowlens.core.bridge` (CDP + extension messaging) and the extension's content scripts. Low-level site helpers live behind `extract_page_data`; mid-level site actions flow through `run_site_action`; normalized per-site entities and multimodal enrichment flow through `extract_site_entity`.
6. The agent writes screenshots, `report.md`, `agent_log.json`, `reasoning_log.jsonl`, and `resource_log.jsonl` into `task_runs/agent_<timestamp>_<slug>/` (or a custom run dir).

Observer runtime flow:

1. `python -m flowlens observer capture-loop` resolves an observer data root.
2. `ObserverCaptureService` polls the frontmost macOS app/window and browser URL.
3. Screenshots are captured across all active displays, concatenated horizontally, and archived by date.
4. The current screenshot is diffed against the previous cached frame. When the changed area ratio stays under the configured threshold, OCR and local vision operate on the diff crop instead of the full frame.
5. Apple OCR extracts text, local Qwen vision adds lightweight screen understanding, and both timing metrics and capture metadata are recorded.
6. `ObserverStore` persists captures, summaries, project memory, and timing data in `observer.db`.
7. The analysis layer can later generate journals, project memory, and capture Q&A without blocking the background capture loop.

## Extension Ops

Generic extension operational commands live outside the XHS task layer.

- `python -m flowlens extension reload`

This path exercises the real FlowLens bridge:
1. Python starts a local bridge server.
2. The running extension connects back.
3. Python sends `reload_extension`.
4. The background worker calls `chrome.runtime.reload()`.
5. Python waits for the fresh post-reload reconnection and writes a small HTML/JSON operation report.

This is the preferred path for “reload the extension” because it validates the actual runtime, not just the Chrome UI.

## Setup

### Install

```bash
pip install -e .
```

This default install now includes the runtime Python dependencies for OCR, local MLX vision, and observer capture. Only development tooling stays optional:

```bash
pip install -e ".[dev]"
```

### Chrome Extension

1. Open `chrome://extensions/`
2. Enable Developer Mode
3. Load `chrome_extension/` as an unpacked extension
4. Open the extension popup and connect it to the local port when running scripts

### Desktop Shell (Spike)

`desktop_app/` is a standalone Tauri 2.x shell used to explore a future local
desktop companion app.

Current scope:

- Basic navigation shell
- One Rust health-check command invoked from the frontend
- Placeholder views for XHS tasks, WeChat summaries, live runs, and settings

This path is intentionally separate from the Python runtime for now; treat it as
an app-shell spike, not the final packaging architecture.

The current bridge path is:

`desktop_app` -> Tauri command `start_task` -> `python -m flowlens desktop run ...`

Packaging helper:

`bash scripts/build_desktop_app.sh`

- stages `desktop_app/runtime_bundle/`
- runs `npm run tauri build`
- copies the finished `.app` into `/Applications/`

### Local Config

FlowLens loads runtime settings in this order:

1. Process env
2. `.env.local`
3. `.env`
4. `~/.zshrc.pre-oh-my-zsh` / `~/.zshrc` exports for known keys

Tracked example:

```bash
cp .env.example .env.local
```

Supported keys:

```bash
ANTHROPIC_API_KEY=...
OPENAI_API_KEY=...
DEEPSEEK_API_KEY=...               # DeepSeek (api.deepseek.com, OpenAI-compatible)
MOONSHOT_API_KEY=...               # Kimi / Moonshot AI (api.moonshot.cn)
DASHSCOPE_API_KEY=...              # 通义千问 / Alibaba DashScope
FLOWLENS_LLM_BACKEND=...           # "sonnet" (default), "openai", "deepseek", "kimi", "qwen", or "qwen-local"
FLOWLENS_DEEPSEEK_MODEL=...        # e.g. deepseek-chat, deepseek-reasoner
FLOWLENS_KIMI_MODEL=...            # e.g. kimi-k2-0905-preview, moonshot-v1-128k
FLOWLENS_QWEN_MODEL=...            # e.g. qwen-plus, qwen-max, qwen-vl-max
FLOWLENS_WHISPER_CLI=...
FLOWLENS_WHISPER_MODELS_DIR=...
FLOWLENS_APP_DATA_DIR=...
FLOWLENS_OBSERVER_ROOT=...
FLOWLENS_OBSERVER_CHECK_INTERVAL=...
FLOWLENS_OBSERVER_FORCE_CAPTURE_INTERVAL=...
FLOWLENS_OBSERVER_SCREENSHOT_STRATEGY=...
FLOWLENS_OBSERVER_DIFF_THRESHOLD=...   # default 0.30
FLOWLENS_OBSERVER_CAPTURE_ALL_DISPLAYS=...
FLOWLENS_OBSERVER_VISION_ENABLED=...
FLOWLENS_OBSERVER_VISION_MODEL=...
```

### Local LLM (optional)

FlowLens supports local inference via MLX as an alternative to the Anthropic API:

```bash
# Download the default lightweight observer model
modelscope download --model mlx-community/Qwen3.5-2B-6bit \
  --local_dir ~/.flowlens/weights/Qwen3.5-2B-6bit

# Optional larger model for heavier local reasoning / vision
modelscope download --model mlx-community/Qwen3.5-9B-MLX-4bit \
  --local_dir ~/.flowlens/weights/Qwen3.5-9B-MLX-4bit

# Switch to local backend
export FLOWLENS_LLM_BACKEND=qwen-local
```

The local backend uses Qwen MLX models via `mlx-vlm`, which are natively
multimodal (early-fusion). Observer defaults to **Qwen3.5-2B-6bit** for
background screenshot understanding so the steady-state cost is lower. Long-lived
observer processes keep the chosen local model loaded in-process after the first
request; one-shot CLI runs will pay the load cost each time.

Backend can also be set per-instance via `MediaConfig(backend="qwen-local")` or
`VisionLLM(backend="qwen-local")`.

## Running

Primary CLI — every free-form prompt now goes through the generic agent loop:

```bash
flowlens "在小红书上调研露营装备"
flowlens agent "在小红书上调研露营装备" --backend qwen-local
flowlens extension reload
```

Equivalent module form:

```bash
python -m flowlens "在小红书上调研露营装备"
python -m flowlens agent "在小红书上调研露营装备"
python -m flowlens extension reload
python -m flowlens observer status
python -m flowlens observer capture-once
python -m flowlens observer install-agent
```

Watch-mode live debugging:

```bash
python -m flowlens extension watch
```

## Manual Integration Scripts

```bash
python tests/manual_local_llm.py --local-only
```

These are manual scripts for live-browser validation, not stable unit tests.

## Debugging Rule

When implementation or tests hit a page-state bug and the DOM behavior is unclear:

1. Capture a screenshot first.
2. Inspect the screenshot with the available LLM vision capability.
3. First confirm that the screenshot faithfully shows the intended app/window and is not occluded or cross-Space corrupted.
4. Then use that visual diagnosis to confirm what the page is actually showing before changing selectors, state detection, or action logic.

Do not guess page state from code alone when a screenshot can disambiguate the issue quickly.

## XHS Anti-Bot Prior

Xiaohongshu's web app has several anti-bot / throttling states that must be treated as first-class runtime states:

- `error_page` / "The page isn't available right now" / "请扫码在手机上查看"
- `security_verification` / captcha / verification image
- soft throttling where direct detail-page navigation fails but a human-like click from search/profile still opens a modal note

Runtime implications:

- Prefer **human-like UI entry** into note detail from visible cards on search/profile pages.
- Avoid unnecessary direct navigation to `/explore/<note_id>` when the same note can be opened via in-page click.
- Prefer **human-like UI exit** from note detail (`X` close button or Escape) instead of refreshing/reloading the search page, because reloads cost time, reorder results, and add request pressure.
- Treat `error_page`, scan-on-phone prompts, and security verification as explicit anti-bot signals in logs/reports, not generic failures.
- When these states appear, slow down and reduce page-level navigations before retrying.

## Perception Status

The active product path is still DOM-first browser automation with vision/perception as verification and fallback.

The shared perception layer in `flowlens.perception` currently supports:

- Apple OCR on downloaded note images
- Apple OCR on observer screenshots and diff crops
- Hosted vision fallback when DOM extraction is weak
- Local Qwen MLX screenshot understanding reused by observer capture and analysis
- Optional local UI detection / grounding experiments
- Local whisper.cpp video transcription

## Observer Status

`flowlens.observer` is now the active desktop-observation subsystem. Current behavior:

- SQLite-backed durable storage in `observer_data/observer.db` by default
- Screenshot archival plus per-capture timing metrics in `observer_data/logs/capture.log`
- 5-second context polling with a 300-second forced capture fallback
- Multi-display screenshots concatenated horizontally
- Diff-aware OCR and visual understanding when the changed area ratio stays under the configured threshold
- `launchd` install/uninstall helpers via `python -m flowlens observer install-agent`

## Archive

`archive/legacy_mcp/` keeps the old screen-level MCP/server route out of the active runtime tree. It is preserved as reference only and is not part of the supported workflow anymore.
