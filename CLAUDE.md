# ClawVision

Visual perception + browser automation for AI agents. Chrome Extension handles DOM extraction and browser actions; Python agent handles LLM reasoning, Vision API, and report generation.

First vertical use case: **Xiaohongshu (小红书) research and data collection**.

## Project Structure

```
clawvision/
├── CLAUDE.md                          # This file
├── pyproject.toml                     # Dependencies and project config
├── .gitignore
├── .claude/
│   └── settings.local.json            # MCP Server registration for Claude Code
│
├── chrome_extension/                  # Chrome Extension (MV3)
│   ├── manifest.json                  # Extension config (permissions, content scripts)
│   ├── background.js                  # WebSocket client, CDP screenshots, command routing
│   ├── content.js                     # DOM extraction, card clicks, state detection
│   ├── popup.html                     # Extension popup UI
│   └── popup.js                       # Popup logic (connect/disconnect)
│
├── clawvision/
│   ├── __init__.py
│   ├── server.py                      # MCP Server entry point (10 tools)
│   ├── screen.py                      # macOS screen capture + input control (Quartz)
│   │
│   ├── agent/                         # Chrome Extension bridge + research agent
│   │   ├── __init__.py
│   │   ├── __main__.py                # CLI: python -m clawvision.agent "topic"
│   │   ├── bridge.py                  # WebSocket server (Python ↔ Extension)
│   │   └── xhs_agent.py              # XHS research agent (search → extract → report)
│   │
│   ├── skills/                        # Site-specific state machines
│   │   ├── __init__.py
│   │   ├── base.py                    # Base SiteSkill class
│   │   └── xiaohongshu_skill.py       # XHS skill (5 states, no pixel code)
│   │
│   └── vision/                        # Vision capabilities
│       ├── __init__.py
│       ├── grounding.py               # Unified grounding (UI-TARS MLX, Claude, ollama)
│       ├── llm.py                     # Claude Vision API wrapper
│       ├── detector.py                # Local CV models (YOLO + OWLv2)
│       └── ocr.py                     # Text extraction
│
├── tests/
│   ├── __init__.py
│   ├── test_extension_agent.py        # 5-level test: connection → search → note → full research
│   ├── test_screen.py                 # Screen capture smoke test
│   └── test_state_machine.py          # Skill state machine tests
│
└── weights/                           # Auto-downloaded model weights (gitignored)
```

## Architecture

```
Python Agent (xhs_agent.py)
    │  LLM decisions, Vision API, report generation
    │
    │  WebSocket (bridge.py ↔ background.js)
    │
Chrome Extension (MV3)
    ├─ background.js — WebSocket client, CDP screenshots, tab management
    ├─ content.js   — DOM extraction, card clicks, state detection, comments
    └─ manifest.json — Permissions: tabs, scripting, debugger, alarms

Vision Layer (available for fallback)
    ├─ grounding.py  — UI-TARS MLX (89% accuracy, ~7s) / Claude Vision
    ├─ detector.py   — YOLO (~100ms) + OWLv2 (~1s) local detection
    └─ llm.py        — Claude Vision API for image understanding

MCP Server (server.py) — 10 tools for external agents
    └─ screen.py — macOS Quartz capture + input control
```

### Data Flow (XHS Research)

```
1. Agent generates search keywords (Claude Text)
2. Extension navigates to XHS search URL
3. Content script extracts cards from DOM
4. Agent picks best notes (Claude Text)
5. For each note:
   a. Content script clicks card → opens overlay
   b. CDP captures screenshot (chrome.debugger)
   c. Content script extracts DOM content + comments
   d. If DOM fails → Vision fallback (screenshot → Claude Vision)
   e. Agent downloads image URLs → Claude Vision describes them
6. Agent synthesizes findings → generates HTML report with screenshots
```

## Key Technical Decisions

- **Chrome Extension over Accessibility** — no screen focus needed, user can keep using computer
- **CDP screenshots** — `chrome.debugger` + `Page.captureScreenshot` (not `captureVisibleTab` which crashes MV3 service workers)
- **DOM-first, Vision-fallback** — DOM extraction is fast and reliable; Vision API for when DOM fails or for image understanding
- **WebSocket bridge** — Python WebSocket server ↔ Extension background.js client; auto-reconnect + keepalive
- **MV3 keepalive** — chrome.alarms (30s) + content script long-lived port + WebSocket pings (10s)
- **XHS SPA handling** — click cover image (not `<a>` tag) for React modal overlay; wait for async DOM render

## Setup

```bash
# Install Python dependencies
pip install -e .

# With local CV detection models (optional, for vision fallback)
pip install -e ".[detect]"
```

### Chrome Extension

1. Open `chrome://extensions/`
2. Enable "Developer mode"
3. Click "Load unpacked" → select `chrome_extension/` directory
4. Grant permissions when prompted

### Environment

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

## Running

### XHS Research Agent (primary use case)

```bash
# Run research on a topic
python -m clawvision.agent "2025春季露营装备趋势"

# With custom keywords
python -m clawvision.agent "咖啡拉花" --keywords "咖啡拉花教程,拉花技巧入门"
```

The agent starts a WebSocket server, connects to the Chrome Extension, and runs the research flow autonomously. Reports are saved to `research_output/`.

### MCP Server (for external agents)

```bash
clawvision
# or: python -m clawvision.server
```

## Testing

```bash
# Full research test (requires Chrome + extension + XHS login)
python tests/test_extension_agent.py -t 4   # camping research
python tests/test_extension_agent.py -t 5   # coffee latte art

# Individual steps
python tests/test_extension_agent.py -t 1   # connection + screenshot
python tests/test_extension_agent.py -t 2   # search + card extraction
python tests/test_extension_agent.py -t 3   # note content extraction

# Screen capture smoke test
python tests/test_screen.py
```

## Model Weights

Weights are auto-downloaded on first use to `~/.clawvision/weights/`:
- **UI-TARS-1.5-7B-6bit (MLX)**: Best local grounding, 89% accuracy, ~7-8s/query
- **OmniParser YOLOv8**: `microsoft/OmniParser-v2.0` (~50MB, ~100ms)
- **OWLv2**: `google/owlv2-base-patch16-ensemble` (auto via HuggingFace)
