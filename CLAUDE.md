# ClawVision

Visual perception + browser automation for AI agents. Chrome Extension handles DOM extraction and browser actions; Python agent handles LLM reasoning, Vision API, and report generation.

First vertical use case: **Xiaohongshu (小红书) research and data collection**.

## Development Principles

### Never report untested code

Every change must be tested and verified before presenting to the user. No exceptions. No "needs re-test" or "not yet integrated". If it's not tested, it's not done.

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

### Self-unblock with Accessibility tools

When blocked by something that needs manual browser/UI interaction (reload Chrome extension, click dialogs, navigate chrome:// pages, approve permissions), use macOS Accessibility APIs (screen.py, pyautogui, AppleScript) to do it instead of asking the user. This also serves as self-hosting validation of ClawVision's own capabilities.

### Autonomous long-horizon work

Do as much as possible autonomously — verify each step, then present the final result. Don't stop to ask the user for simple operational steps. Auto-open browsers/websites as needed.

### Use Claude Vision to verify screenshots

During testing, use Claude Vision to inspect screenshots and verify correctness, not just check for non-empty data.

### No pixel-heuristic CV

Prefer semantic understanding over pixel math. Don't use pixel-level heuristics for UI understanding.

### Strategic architecture

The project's goal is **robust agentic browser automation**, not a single-site scraper. Architecture is layered:
1. **Generic Agent Infrastructure** (bridge.py, media.py) — WebSocket, CDP, screenshots, background windows, LLM/OCR/Whisper. Platform-independent.
2. **Site Skills** (xhs/, future: douyin/, taobao/, etc.) — Site-specific DOM extraction, navigation patterns, entity models. Each site is a "skill" integrated one by one.
3. **Task Agents** (research.py, user_analysis.py) — High-level orchestration using site skills.

New generic capabilities (background windows, dedup, DOM-first pattern) belong in the generic layer. Site-specific DOM selectors and navigation belong in site skill modules.

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
│   ├── agent/                         # 3-layer agent architecture
│   │   ├── __init__.py                # Top-level exports
│   │   ├── __main__.py                # CLI: python -m clawvision.agent "topic"
│   │   ├── bridge.py                  # Generic WebSocket + CDP (platform-independent)
│   │   ├── media.py                   # Generic LLM/OCR/Whisper/Vision (platform-independent)
│   │   │
│   │   └── xhs/                       # Xiaohongshu platform module
│   │       ├── __init__.py
│   │       ├── entities.py            # Structured entity models (Note, Author, etc.)
│   │       ├── browser.py             # XHS DOM extraction + CDP anti-bot clicks
│   │       ├── research.py            # Topic research agent
│   │       └── user_analysis.py       # User/creator analysis agent
│   │
│   └── vision/                        # Vision capabilities
│       ├── __init__.py
│       ├── grounding.py               # Unified grounding (UI-TARS MLX, Claude, ollama)
│       ├── llm.py                     # Claude Vision API wrapper
│       ├── apple_ocr.py               # macOS native OCR (Vision.framework)
│       ├── transcriber.py             # whisper.cpp video transcription
│       ├── detector.py                # Local CV models (YOLO + OWLv2)
│       └── ocr.py                     # Text extraction
│
├── tests/
│   ├── __init__.py
│   ├── test_extension_agent.py        # Research agent tests (5 levels)
│   ├── test_user_analyzer.py          # User analysis tests
│   └── test_screen.py                 # Screen capture smoke test
│
└── weights/                           # Auto-downloaded model weights (gitignored)
```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Task Agents (xhs/research.py, xhs/user_analysis.py)       │
│  LLM decisions, flow orchestration, report generation       │
├─────────────────────────────────────────────────────────────┤
│  Platform Browser (xhs/browser.py)                          │
│  XHS-specific DOM extraction, CDP anti-bot clicks,          │
│  SPA navigation patterns                                    │
├─────────────────────────────────────────────────────────────┤
│  Entity Models (xhs/entities.py)                            │
│  NoteEntity, AuthorEntity, NoteCard, SearchResult           │
│  Completeness tracking, structured extraction targets       │
├──────────────────────┬──────────────────────────────────────┤
│  Generic Bridge      │  Generic Media                       │
│  (bridge.py)         │  (media.py)                          │
│  WebSocket, CDP,     │  Claude API, Apple OCR,              │
│  navigation, JS exec │  Whisper, image download             │
├──────────────────────┴──────────────────────────────────────┤
│  Chrome Extension (MV3)                                     │
│  background.js — WebSocket client, CDP, tab management      │
│  content.js   — DOM extraction, card clicks, state detect   │
└─────────────────────────────────────────────────────────────┘
```

### Entity Model

The agent uses structured entities to ensure thorough extraction:

- **NoteEntity** — the primary content unit (title, content, images[], video, comments[], engagement, author info). Each note has a `completeness_score` tracking how thoroughly it's been extracted.
- **AuthorEntity** — creator profile (bio, stats, all note cards, detailed top notes, content strategy analysis).
- **NoteCard** — lightweight preview from search/profile grids (for ranking before opening).
- **SearchResult** — search page state (query, filter, cards).

### Data Flow (XHS Research)

```
1. Agent generates search keywords (Claude Text)
2. XHSBrowser navigates to XHS search URL
3. Content script extracts NoteCards from DOM
4. Agent picks best notes (Claude Text)
5. For each note → populate NoteEntity:
   a. Click card via CDP (anti-bot) → opens overlay
   b. DOM extraction: title, content, author, engagement
   c. Screenshot via CDP (chrome.debugger)
   d. Download each image → Apple OCR + Vision API
   e. If video → Whisper transcription + Vision on poster
   f. Scroll + extract comments (deduped)
   g. Check completeness_score before moving on
6. Agent synthesizes findings → generates HTML report
```

## Key Technical Decisions

- **Chrome Extension over Accessibility** — no screen focus needed, user can keep using computer
- **CDP screenshots** — `chrome.debugger` + `Page.captureScreenshot` (not `captureVisibleTab` which crashes MV3 service workers)
- **CDP real mouse clicks** — `Input.dispatchMouseEvent` for anti-bot avoidance (indistinguishable from human clicks)
- **DOM-first, Vision-fallback** — DOM extraction is fast and reliable; Vision API for when DOM fails or for image understanding
- **WebSocket bridge** — Python WebSocket server ↔ Extension background.js client; auto-reconnect + keepalive
- **MV3 keepalive** — chrome.alarms (30s) + content script long-lived port + WebSocket pings (10s)
- **XHS SPA handling** — click cover image (not `<a>` tag) for React modal overlay; wait for async DOM render
- **Entity-driven extraction** — structured models ensure thorough data collection, not just shallow scraping

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

# User analysis
python -m clawvision.agent --user "https://www.xiaohongshu.com/user/profile/xxx"
```

The agent starts a WebSocket server, connects to the Chrome Extension, and runs the research flow autonomously. Reports are saved to `research_output/` or `user_analysis/`.

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

# User analysis
python tests/test_user_analyzer.py

# Screen capture smoke test
python tests/test_screen.py
```

## Model Weights

Weights are auto-downloaded on first use to `~/.clawvision/weights/`:
- **UI-TARS-1.5-7B-6bit (MLX)**: Best local grounding, 89% accuracy, ~7-8s/query
- **OmniParser YOLOv8**: `microsoft/OmniParser-v2.0` (~50MB, ~100ms)
- **OWLv2**: `google/owlv2-base-patch16-ensemble` (auto via HuggingFace)
