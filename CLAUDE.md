# ClawVision

ClawVision is currently maintained as a layered browser automation framework built on:

- Core browser/runtime primitives in `clawvision.core`
- Perception utilities in `clawvision.perception`
- Task understanding and knowledge in `clawvision.reasoning`
- Platform adapters in `clawvision.platforms`
- Task workflows in `clawvision.workflows`
- A Chrome Extension in `chrome_extension/`

The old screen-level MCP route has been archived under `archive/legacy_mcp/`.

## Current Architecture

```
clawvision/
├── chrome_extension/                 # MV3 extension: websocket, CDP, DOM extraction, watch mode
├── desktop_app/                      # Minimal Tauri desktop shell / future desktop control plane
├── clawvision/
│   ├── cli.py                        # Primary CLI entry
│   ├── __main__.py                   # `python -m clawvision`
│   ├── desktop_cli.py                # Desktop shell -> Python task bridge
│   ├── extension_cli.py              # `python -m clawvision extension ...`
│   ├── extension_ops.py              # Generic extension operational reports / commands
│   ├── server.py                     # Archived-route compatibility stub
│   ├── core/
│   │   ├── bridge.py                 # WebSocket bridge to the extension + tab routing
│   │   ├── composer.py               # DOM-first composer interaction helpers
│   │   ├── verification.py           # DOM-first verifier primitives
│   │   ├── recorder.py               # Session recording
│   │   ├── reporting.py              # Shared markdown/html rendering
│   │   └── runtime.py                # Local env / path discovery
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
│   ├── platforms/
│   │   ├── xhs/                      # XHS browser adapters, schemas, capability catalog
│   │   └── chat/                     # Chat site descriptors + visible verification helpers
│   └── workflows/
│       ├── xhs/                      # Topic research / creator analysis workflows
│       └── chat/                     # Ask-all-chatbots workflow + companion
├── tests/
│   ├── manual_xhs_research.py        # Manual integration script
│   ├── manual_xhs_user_analysis.py   # Manual integration script
│   ├── manual_xhs_carousel.py        # Manual media pipeline script
│   ├── manual_local_llm.py           # Manual local-vs-remote backend comparison
│   ├── manual_xhs_task_workflows.py  # Manual task-layer integration script
│   ├── test_task_specs.py            # Structured task tests
│   ├── test_task_agent.py            # Task-agent parsing tests
│   ├── test_xhs_capabilities.py      # Capability / extraction-plan tests
│   ├── test_extension_ops.py         # Extension operation report tests
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

Whenever you modify the Tauri app under `desktop_app/` **or** any Python code that the installed app bundles (the `runtime_bundle/` copy of `clawvision/`), produce a fresh macOS `.app` bundle and install it in the same working session.

- Do not stop at `cargo check` / `npm run build` alone for Tauri changes.
- Do not stop at editing Python source — the installed `.app` bundles its own copy under `Contents/Resources/_up_/runtime_bundle/`, so source edits only take effect after a rebuild.
- Run the repo packaging path so the real desktop artifact exists after the change.
- Prefer `bash scripts/build_desktop_app.sh` unless the user explicitly asks for a different packaging flow.

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

When blocked by something that needs manual browser/UI interaction (reload Chrome extension, click dialogs, navigate chrome:// pages, approve permissions), use macOS Accessibility APIs (screen.py, pyautogui, AppleScript) to do it instead of asking the user. This also serves as self-hosting validation of ClawVision's own capabilities.

For extension reload specifically:
- Prefer the built-in runtime path first: `bridge.reload_extension()` or `python -m clawvision extension reload`
- If the extension is too broken or disconnected to reload itself, then fall back to macOS Accessibility on `chrome://extensions/`

### Autonomous long-horizon work

Do as much as possible autonomously — verify each step, then present the final result. Don't stop to ask the user for simple operational steps. Auto-open browsers/websites as needed.

### Always use a background Chrome window for live runs

When running live browser tests, create and use a **new background Chrome window** that reuses the user's existing Chrome profile and login state.

- Do **not** take over or overwrite the user's current foreground browsing tab/window.
- Keep automation in the background window unless the user explicitly asks to watch or interact with it.
- Treat the user's foreground browsing as independent from the automation target.

Exception:
- The desktop multi-chat fan-out flow intentionally opens **three visible Chrome windows** for ChatGPT, Gemini, and Claude because that product mode is explicitly user-facing and must be confirmed on-screen.
- That flow still reuses the user's existing Chrome profile, pre-cleans stale temp-profile Chrome helpers, and uses the local visual-debug stack to verify the real visible windows.

### Use Claude Vision to verify screenshots

During testing, use Claude Vision to inspect screenshots and verify correctness, not just check for non-empty data.

For UI / UX / window-management debugging, also verify that the screenshot itself is faithful to the intended target:
- Confirm the dominant app/window in the image is actually the one you meant to capture.
- Reject captures where another window is covering the target or where the image looks stitched across multiple Spaces/displays.
- Do not use an unverified screenshot as evidence for page state, selector bugs, or interaction bugs.

### No pixel-heuristic CV

Prefer semantic understanding over pixel math. Don't use pixel-level heuristics for UI understanding.

### Strategic architecture

The project's goal is **robust agentic browser automation**, not a single-site scraper. Architecture is layered:
1. **Core layer** (`clawvision.core`) — bridge, tab/window control, DOM-first composer helpers, verification, recording, shared reports, runtime.
2. **Perception layer** (`clawvision.perception`) — hosted/local vision, OCR, grounding, transcription, image preprocessing.
3. **Reasoning layer** (`clawvision.reasoning`) — structured tasks, planning, evaluation, and reusable knowledge extraction.
4. **Platform layer** (`clawvision.platforms`) — site-specific DOM extraction, navigation patterns, entity models, capability catalogs.
5. **Workflow layer** (`clawvision.workflows`) — concrete task orchestration such as XHS research and multi-chat fanout.

New generic capabilities (background windows, dedup, session recording) belong in the generic layer. Site-specific DOM selectors and navigation belong in site skill modules.

## Runtime Flow

1. Python starts a local WebSocket server.
2. The Chrome extension connects from the logged-in browser profile.
3. `clawvision.platforms.xhs.XHSBrowser` issues DOM extraction and CDP-backed interaction commands.
4. The reasoning layer chooses a bounded execution strategy (`coverage_first` / `balanced` / `deep_focus`) using the available capability catalog.
5. `clawvision.workflows.xhs` orchestrates note collection using `lite` and `deep` extraction plans.
6. `clawvision.platforms.xhs.processor` enriches notes with OCR, image descriptions, and video transcription when the chosen plan requires it.
7. The agent writes JSON + HTML reports plus a session GIF to `task_runs/` or a custom output dir.

## Extension Ops

Generic extension operational commands live outside the XHS task layer.

- `python -m clawvision extension reload`

This path exercises the real ClawVision bridge:
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
pip install -e ".[detect]"   # optional local detection models
pip install -e ".[local-llm]" # optional local MLX backend
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
- A multi-chat input that launches visible Chrome windows for ChatGPT, Gemini, and Claude via the Python runtime
- A `clawvision://ask?question=...` deep-link entry so the installed desktop app can be opened directly from the Chrome side panel
- Placeholder views for XHS tasks, live runs, and settings

This path is intentionally separate from the Python runtime for now; treat it as
an app-shell spike, not the final packaging architecture.

The current bridge path is:

`desktop_app` -> Tauri command `start_task` -> `python -m clawvision desktop run ...`

The multi-chat bridge path is:

`desktop_app` -> Tauri command `ask_chatbots` -> `python -m clawvision chatbots ...`

The Chrome side-panel shortcut path is:

`chrome sidepanel` -> `clawvision://ask?...` -> installed `ClawVision Desktop.app` -> Tauri deep-link handler -> `ask_chatbots`

Packaging helper:

`bash scripts/build_desktop_app.sh`

- stages `desktop_app/runtime_bundle/`
- runs `npm run tauri build`
- copies the finished `.app` into `/Applications/`

### Local Config

ClawVision loads runtime settings in this order:

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
CLAWVISION_LLM_BACKEND=...           # "sonnet" (default) or "qwen-local"
CLAWVISION_WHISPER_CLI=...
CLAWVISION_WHISPER_MODELS_DIR=...
```

### Local LLM (optional)

ClawVision supports local inference via MLX as an alternative to the Anthropic API:

```bash
# Download model (~6.3GB)
modelscope download --model mlx-community/Qwen3.5-9B-MLX-4bit \
  --local_dir ~/.clawvision/weights/Qwen3.5-9B-MLX-4bit

# Switch to local backend
export CLAWVISION_LLM_BACKEND=qwen-local
```

The local backend uses **Qwen3.5-9B-MLX-4bit** via `mlx-vlm`, which is natively
multimodal (early-fusion) — the same model handles both text reasoning and
vision/screenshot understanding. Requires the `local-llm` extra or equivalent
manual installation of `mlx-lm`, `mlx-vlm`, and `modelscope`.

Backend can also be set per-instance via `MediaConfig(backend="qwen-local")` or
`VisionLLM(backend="qwen-local")`.

## Running

Primary CLI:

```bash
clawvision "露营装备"
clawvision "露营装备" --keywords "露营装备推荐,露营好物"
clawvision --user "https://www.xiaohongshu.com/user/profile/xxx"
clawvision extension reload
```

Equivalent:

```bash
python -m clawvision "露营装备"
python -m clawvision --user <user_id>
python -m clawvision extension reload
```

Watch-mode live debugging:

```bash
python -m clawvision extension watch
```

## Manual Integration Scripts

```bash
python tests/manual_xhs_research.py -t 1
python tests/manual_xhs_research.py -t 4
python tests/manual_xhs_user_analysis.py --find
python tests/manual_xhs_user_analysis.py --user <url_or_id>
python tests/manual_xhs_carousel.py
python tests/manual_local_llm.py --local-only
python tests/manual_xhs_task_workflows.py --preset topic_research
python tests/manual_xhs_task_workflows.py --preset creator_growth
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

The shared perception layer in `clawvision.perception` currently supports:

- Apple OCR on downloaded note images
- Hosted vision fallback when DOM extraction is weak
- Optional local UI detection / grounding experiments
- Local whisper.cpp video transcription

## Archive

`archive/legacy_mcp/` keeps the old screen-level MCP/server route out of the active runtime tree. It is preserved as reference only and is not part of the supported workflow anymore.
