# ClawVision

Visual perception MCP server for AI agents. Provides screen-level UI understanding and precise interaction on macOS.

## What is this?

ClawVision gives AI agents (OpenClaw, Codex, Cursor, etc.) the ability to **see and interact with real screens** — not DOM, not headless browsers, but actual pixel-level visual understanding + mouse/keyboard control.

The core differentiator over vanilla LLM vision: **local CV models (YOLO, OWLv2) provide fast, precise bounding boxes** (~100ms) without API calls, while LLMs handle high-level reasoning.

First vertical use case: **Xiaohongshu (小红书) research and data collection**.

## Project Structure

```
clawvision/
├── AGENTS.md                          # This file
├── pyproject.toml                     # Dependencies and project config
├── .gitignore
├── .Codex/
│   └── settings.local.json            # MCP Server registration for Codex
├── clawvision/
│   ├── __init__.py
│   ├── server.py                      # MCP Server entry point (10 tools)
│   ├── screen.py                      # macOS screen capture + input control
│   ├── vision/
│   │   ├── __init__.py
│   │   ├── llm.py                     # Codex Vision API (high-level understanding)
│   │   ├── ocr.py                     # Text extraction (MVP: Codex API)
│   │   └── detector.py                # Local CV models (YOLO + OWLv2)
│   └── workflows/
│       ├── __init__.py
│       └── xiaohongshu.py             # Xiaohongshu-specific automation
├── tests/
│   ├── __init__.py
│   └── test_screen.py                 # Screen capture smoke test
└── weights/                           # Auto-downloaded model weights (gitignored)
```

## Architecture

```
Agent (OpenClaw / Codex / any MCP client)
    │  MCP protocol (stdio)
    ▼
ClawVision Server (server.py) — 10 tools
    │
    ├─ Screen Layer (screen.py)
    │   └─ macOS Quartz API: capture, click, type, scroll
    │
    ├─ Vision Layer (vision/)
    │   ├─ llm.py     — Codex Vision API (page analysis, action planning)
    │   ├─ detector.py — Local CV models, no API calls:
    │   │   ├─ YOLOUIDetector  — OmniParser YOLOv8, ~100ms on MPS
    │   │   ├─ OWLv2Detector   — Open-vocabulary, ~1s on MPS
    │   │   └─ HybridDetector  — Combines both
    │   └─ ocr.py     — Text extraction
    │
    └─ Workflows (workflows/)
        └─ xiaohongshu.py — Search, detail, scroll-collect
```

## MCP Tools (10 total)

### General Vision
| Tool | Description | Backend |
|------|-------------|---------|
| `capture_screen` | Screenshot full screen or app window | Quartz |
| `analyze_screen` | AI analysis of screen content | Codex Vision API |
| `find_and_click` | Find element by description and click | Codex Vision API |
| `type_text` | Type text (supports CJK) | pyautogui |
| `extract_text` | OCR text extraction | Codex Vision API |

### Local CV Detection (core differentiator)
| Tool | Description | Backend |
|------|-------------|---------|
| `detect_ui_elements` | Detect all UI elements (~100ms) | OmniParser YOLOv8 |
| `find_elements_by_query` | Find elements by text description (~1s) | OWLv2 |

### Xiaohongshu
| Tool | Description |
|------|-------------|
| `xhs_search` | Search and extract note cards |
| `xhs_note_detail` | Open and extract note details |
| `xhs_scroll_collect` | Scroll and capture multiple pages |

## Key Decisions

- **Independent MCP Server** — not tied to any agent platform; works with anything that speaks MCP
- **Screen-level control** — operates on real pixels, not DOM; bypasses anti-scraping; resilient to UI changes
- **macOS native** — uses Quartz/CGEvent APIs; requires Screen Recording + Accessibility permissions
- **Hybrid CV pipeline** — LLM for reasoning, local models for precision; avoids API costs for detection
- **Auto-download weights** — model weights download from HuggingFace on first use

## Setup

```bash
# Core (screen control + Codex Vision API)
pip install -e .

# With local CV detection models (recommended)
pip install -e ".[detect]"

# Everything including PaddleOCR
pip install -e ".[all]"
```

### macOS Permissions

Grant in **System Settings → Privacy & Security**:
- **Screen Recording** — for screenshots
- **Accessibility** — for mouse/keyboard control

### Environment

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

## Running

```bash
# As MCP server (stdio transport, used by agents)
clawvision

# Or directly
python -m clawvision.server
```

### Register with Codex

Already configured in `.Codex/settings.local.json`. Restart Codex in this project directory to activate.

## Testing

```bash
python tests/test_screen.py  # screen capture + window discovery
```

## Model Weights

Weights are auto-downloaded on first use to `~/.clawvision/weights/`:
- **OmniParser YOLOv8**: `microsoft/OmniParser-v2.0` (~50MB)
- **OWLv2**: `google/owlv2-base-patch16-ensemble` (auto via HuggingFace transformers)
