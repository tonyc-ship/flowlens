# ClawVision

ClawVision is currently maintained as a Xiaohongshu research / analysis agent built on:

- Python orchestration in `clawvision.agent.xhs`
- A Chrome Extension in `chrome_extension/`
- Shared media / vision utilities in `clawvision.agent.media` and `clawvision.vision`

The old screen-level MCP route has been archived under `archive/legacy_mcp/`.

## Current Architecture

```
clawvision/
├── chrome_extension/                 # MV3 extension: websocket, CDP, DOM extraction
├── clawvision/
│   ├── cli.py                        # Primary CLI entry
│   ├── __main__.py                   # `python -m clawvision`
│   ├── server.py                     # Archived-route compatibility stub
│   ├── runtime.py                    # Local env / path discovery
│   ├── agent/
│   │   ├── bridge.py                 # WebSocket bridge to the extension
│   │   ├── media.py                  # Anthropic, OCR, transcription helpers
│   │   └── xhs/
│   │       ├── browser.py            # XHS-specific browser actions / DOM extraction
│   │       ├── entities.py           # Note / author / card schemas
│   │       ├── processor.py          # OCR / vision / transcript enrichment
│   │       ├── research.py           # Topic research flow
│   │       └── user_analysis.py      # Creator profile analysis flow
│   └── vision/
│       ├── llm.py                    # Vision API wrapper
│       ├── apple_ocr.py              # macOS native OCR
│       ├── detector.py               # Optional local UI detection
│       ├── grounding.py              # Optional local grounding backends
│       ├── ocr.py                    # OCR helpers
│       └── transcriber.py            # whisper.cpp integration
├── tests/
│   ├── manual_xhs_research.py        # Manual integration script
│   ├── manual_xhs_user_analysis.py   # Manual integration script
│   └── manual_xhs_carousel.py        # Manual media pipeline script
└── archive/
    └── legacy_mcp/                   # Archived screen-level MCP route
```

## Runtime Flow

1. Python starts a local WebSocket server.
2. The Chrome extension connects from the logged-in browser profile.
3. `XHSBrowser` issues DOM extraction and CDP-backed interaction commands.
4. `research.py` / `user_analysis.py` orchestrate note collection.
5. `processor.py` enriches notes with OCR, image descriptions, and video transcription.
6. The agent writes JSON + HTML reports to `research_output/`, `user_analysis/`, or a custom output dir.

## Setup

### Install

```bash
pip install -e .
pip install -e ".[detect]"   # optional local detection models
```

### Chrome Extension

1. Open `chrome://extensions/`
2. Enable Developer Mode
3. Load `chrome_extension/` as an unpacked extension
4. Open the extension popup and connect it to the agent port when running scripts

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
CLAWVISION_WHISPER_CLI=...
CLAWVISION_WHISPER_MODELS_DIR=...
```

## Running

Primary CLI:

```bash
clawvision "露营装备"
clawvision "露营装备" --keywords "露营装备推荐,露营好物"
clawvision --user "https://www.xiaohongshu.com/user/profile/xxx"
```

Equivalent:

```bash
python -m clawvision "露营装备"
python -m clawvision --user <user_id>
```

## Manual Integration Scripts

```bash
python tests/manual_xhs_research.py -t 1
python tests/manual_xhs_research.py -t 4
python tests/manual_xhs_user_analysis.py --find
python tests/manual_xhs_user_analysis.py --user <url_or_id>
python tests/manual_xhs_carousel.py
```

These are manual scripts for live-browser validation, not stable unit tests.

## Debugging Rule

When implementation or tests hit a page-state bug and the DOM behavior is unclear:

1. Capture a screenshot first.
2. Inspect the screenshot with the available LLM vision capability.
3. Use that visual diagnosis to confirm what the page is actually showing before changing selectors, state detection, or action logic.

Do not guess page state from code alone when a screenshot can disambiguate the issue quickly.

## Vision Status

The active product path is still DOM-first browser automation for XHS.

The `clawvision.vision` modules remain available for:

- Apple OCR on downloaded note images
- Anthropic Vision fallback when DOM extraction is weak
- Optional local UI detection / grounding experiments
- Local whisper.cpp video transcription

They are intentionally kept as shared utilities so future workflows can use more vision without reviving the archived screen-level MCP route.

## Archive

`archive/legacy_mcp/` keeps the old screen-level MCP/server route out of the active runtime tree. It is preserved as reference only and is not part of the supported workflow anymore.
