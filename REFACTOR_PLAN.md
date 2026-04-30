# SocAI Agent Architecture Refactor Plan

**Created:** 2026-04-05
**Goal:** Transform SocAI from hardcoded site-specific workflows into a generic Computer Use Agent (CUA) framework with an LLM-driven agent loop.

## Problem Statement

Current architecture: Python code is the controller. LLM is only used for perception (OCR, vision verification). All "see → decide → act" logic is hardcoded `if/else` in `workflows/xhs/research.py` (927 lines) and `platforms/xhs/browser.py` (718 lines).

Target architecture: LLM is the controller. Python provides generic tools. Site-specific knowledge is loaded from structured files, not baked into code.

## Architecture Overview

```
┌─────────────────────────────────────────────────────┐
│                    Agent Loop                        │
│  while True:                                         │
│    response = LLM(messages, tools, knowledge)        │
│    if no tool_use: break                             │
│    for call in response.tool_calls:                  │
│      result = tools[call.name].execute(call.input)   │
│      messages.append(tool_result(result))             │
└─────────────────────────────────────────────────────┘
         │                        │
    ┌────▼────┐            ┌─────▼──────┐
    │  Tools   │            │ Knowledge  │
    │ (generic)│            │(per-site)  │
    └────┬────┘            └─────┬──────┘
         │                        │
  ┌──────┼──────┐         site knowledge
  │      │      │         YAML files
browser desktop vision    loaded into
 tools   tools  tools     system prompt
```

## Step 1: Agent Loop + Generic Browser Tools

### 1.1 Agent Loop (`socai/agent/loop.py`)

Core `while True` loop that:
1. Builds system prompt from tool descriptions + site knowledge
2. Calls Anthropic API with `tools=` parameter
3. Executes tool calls, collects results
4. Appends results to messages, loops back
5. Stops when LLM returns text without tool_use

Key design decisions:
- Uses Anthropic native tool_use API (not custom JSON parsing)
- System prompt assembled from: base instructions + tool.prompt() + knowledge
- Screenshots saved to task run directory automatically
- Reasoning log recorded from LLM responses

### 1.2 Tool Interface (`socai/agent/tool.py`)

Minimal interface:
```python
class Tool(ABC):
    name: str
    description: str
    parameters: dict  # JSON Schema for Anthropic API
    
    async def execute(self, params: dict, context: ToolContext) -> str:
        """Returns text result for LLM"""
```

### 1.3 Browser Tools (`socai/agent/tools/browser.py`)

Wrap `core/bridge.py` primitives as agent tools:

| Tool | Bridge Method | Purpose |
|------|---------------|---------|
| `navigate` | `bridge.navigate()` | Go to URL |
| `go_back` | `bridge.go_back()` | Browser back |
| `screenshot` | `bridge.save_screenshot()` | Capture + return to LLM as image |
| `click` | `bridge.click_at()` | Click at viewport coordinates |
| `scroll` | `bridge.scroll_page()` | Scroll page |
| `type_text` | `bridge.type_text()` | Type at cursor |
| `press_key` | `bridge.press_key()` | Keyboard key |
| `read_page` | `bridge.run_js()` | Extract visible text + links from DOM |
| `run_javascript` | `bridge.run_js()` | Run arbitrary JS |
| `extract_page_data` | `bridge.send_command()` | Use extension's XHS extractors when on XHS |

### 1.4 Vision Tools (`socai/agent/tools/vision.py`)

| Tool | Underlying | Purpose |
|------|-----------|---------|
| `analyze_screenshot` | `VisionLLM` | Describe what's visible on screen |
| `ocr_screenshot` | `AppleOCR` | Extract text from screenshot |

### 1.5 Knowledge Tools (`socai/agent/tools/knowledge.py`)

| Tool | Purpose |
|------|---------|
| `get_site_knowledge` | Load knowledge for current URL's site |
| `save_observation` | Record new site behavior/pattern |

## Step 2: Migrate XHS Knowledge to Structured Files

### 2.1 Knowledge Directory Structure

```
socai/knowledge/sites/
├── xiaohongshu.yaml      # All XHS knowledge in one file
└── _template.yaml        # Template for new sites
```

### 2.2 Knowledge Content (extracted from code)

From `platforms/xhs/browser.py` (94 CSS selectors, navigation logic, anti-bot rules):
- Page types and URL patterns
- Navigation rules (how to open notes, search, profiles)
- Anti-bot signals and mitigation
- DOM extraction strategies

From `platforms/xhs/entities.py` (entity definitions):
- Note entity fields and their meanings
- Comment structure
- Author profile structure

From `platforms/xhs/capabilities.py` (operation catalog):
- Available operations with cost/latency
- Extraction plan templates

### 2.3 Knowledge Loading

Knowledge is loaded into the system prompt when the agent navigates to a known site. The `read_page` tool also returns relevant knowledge hints.

## Step 3: Verification (Future)

- Run existing XHS research task through new agent loop
- Run a novel XHS task type to test generalization
- Compare output quality with old hardcoded workflow
- Capture screenshots and GIFs throughout

## Migration Strategy

- **Preserve** all existing code in `platforms/`, `workflows/`, `reasoning/` untouched
- **Add** new `agent/` module alongside existing code
- **New CLI entry**: `socai agent "task description"` runs the new agent loop
- Old `socai "topic"` still works via the existing workflow code
- Gradual migration: once agent loop proves capable, deprecate old workflows

## File Changes Summary

### New Files
- `socai/agent/__init__.py`
- `socai/agent/loop.py` — Core agent loop
- `socai/agent/tool.py` — Tool base class + context
- `socai/agent/tools/__init__.py`
- `socai/agent/tools/browser.py` — Browser tools wrapping bridge.py
- `socai/agent/tools/vision.py` — Vision/OCR tools
- `socai/agent/tools/knowledge.py` — Knowledge loading tools
- `socai/knowledge/__init__.py`
- `socai/knowledge/loader.py` — Knowledge file loading
- `socai/knowledge/sites/xiaohongshu.yaml` — XHS knowledge
- `socai/knowledge/sites/_template.yaml` — Template

### Modified Files
- `socai/cli.py` — Add `agent` subcommand
- `CLAUDE.md` — Update architecture docs

### Unchanged Files
- All existing `platforms/`, `workflows/`, `reasoning/` code preserved as-is
