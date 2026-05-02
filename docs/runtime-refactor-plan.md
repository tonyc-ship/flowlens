# FlowLens Runtime + CDP Refactor Plan

Status: active refactor plan — Phases 1-5 mostly implemented  
Created: 2026-05-01  
Branch: `desktop-runtime-refactor`  
Scope: restructure FlowLens runtime and CDP code so the desktop app can grow from CDP diagnostics into the full agent product without keeping Python browser logic inside `app/prototype/`.

Progress so far:

- `flowlens.runtime` is the renamed Python sidecar package.
- Runtime JSON-RPC request/response/error models are typed with Pydantic in `flowlens.runtime.protocol`.
- Generic CDP discovery, connect/retry, target-listing, page/session, and controlled-tab diagnostic code now lives under top-level `flowlens.cdp`.
- XHS CDP probe logic now lives under `flowlens.platforms.xhs.cdp_diagnostics`.
- Current app runtime actions call importable Python functions directly through the sidecar.
- `app/prototype/` has been reduced to a deprecation README pointing to `scripts/diagnostics/`.

## 1. Summary

The current desktop app has the right top-level process shape:

```text
Tauri WebView UI
  -> Rust/Tauri native shell
  -> Python flowlens.runtime sidecar
  -> Chrome CDP
```

The original CDP browser-control logic lived in `app/prototype/*.py` and was invoked through subprocesses from the sidecar. That was useful while proving the CDP path, but the runtime now calls importable package modules directly and the app-local prototype files have been replaced by maintained diagnostic wrappers under `scripts/diagnostics/`.

This refactor should move implementation code into importable FlowLens Python modules, keep `app/` focused on the desktop shell/UI, and turn the sidecar into the single app-facing Python runtime that can later run full FlowLens agent tasks.

## 2. Goals

- Remove meaningful Python implementation logic from `app/prototype/`.
- Keep `app/` focused on:
  - frontend UI
  - Tauri/Rust native shell
  - sidecar process supervision
  - app packaging
- Move generic CDP functionality into top-level `flowlens.cdp`.
- Move Xiaohongshu-specific CDP diagnostics into `flowlens.platforms.xhs`.
- Clean up migrated code as it moves: reduce duplicate CLI/runtime paths, tighten names/types, remove obsolete session/prototype wording, and separate implementation from wrappers.
- Make `flowlens.runtime` call Python functions directly, not subprocess scripts.
- Keep manual developer/support diagnostics as thin wrappers under `scripts/diagnostics/`.
- Preserve all currently working desktop app flows:
  - app health
  - Connect Chrome
  - List Targets
  - Create Controlled Tab
  - Open XHS Probe
  - Capture Test Screenshot
  - onboarding XHS connection test
- Prepare the sidecar for future agent task execution through `flowlens.agent`, `flowlens.tools`, and `flowlens.platforms`.

## 3. Non-goals for this refactor

- Do not implement the full research-agent UI yet.
- Do not package a bundled Python runtime yet.
- Do not rewrite the existing `flowlens.agent` loop.
- Do not switch to FastAPI or a localhost HTTP server.
- Do not remove the Chrome extension architecture from the broader repo.
- Do not change the user-facing Chrome permission/onboarding flow unless needed for this migration.

## 4. Current state

### 4.1 Process architecture

```text
FlowLens.app
  -> Rust/Tauri process (`app/src-tauri/src/lib.rs`)
    -> Python sidecar (`python -m flowlens.runtime --transport stdio`)
      -> importable FlowLens Python modules (`flowlens.cdp`, `flowlens.platforms.xhs`)
        -> Chrome CDP WebSocket
```

The sidecar exists and now calls importable modules directly for every current app action:

```text
flowlens.runtime.service.run_action(...)
  -> flowlens.cdp.discovery / flowlens.cdp.targets / flowlens.cdp.diagnostics
  -> flowlens.platforms.xhs.cdp_diagnostics
```

`app/prototype/*.py` files are no longer runtime implementation dependencies.

### 4.2 Current CDP diagnostics

`app/prototype/` now contains only a deprecation README. Maintained diagnostic entry points live under `scripts/diagnostics/`:

```text
scripts/diagnostics/chrome_cdp_discovery.py
scripts/diagnostics/chrome_cdp_targets.py
scripts/diagnostics/chrome_cdp_controlled_tab.py
scripts/diagnostics/xhs_cdp_probe.py
scripts/diagnostics/desktop_cdp_demo.py
```

`app/prototype/` is no longer the right home because:

- `app/` should not contain core Python runtime implementation.
- subprocess execution causes cold starts and path/package assumptions.
- stdout JSON parsing is brittle.
- the future agent needs persistent runtime/session state, not one-off script runs.
- CDP code should be reusable by CLI, MCP, desktop runtime, and tests.

## 5. Target code layout

```text
app/
  src/                              # Vite/TypeScript UI
  src-tauri/                        # Rust/Tauri native shell

flowlens/
  runtime/                          # App-facing Python sidecar
    __main__.py
    server.py                       # JSON-RPC stdio loop
    protocol.py                     # Pydantic request/response/event schemas
    service.py                      # method dispatch
    task_manager.py                 # future run/cancel/status support
    artifacts.py                    # runtime artifact dirs
    commands/
      __init__.py
      browser.py                    # connect/list/create controlled tab commands
      diagnostics.py                # app-facing diagnostics
      agent.py                      # future run_task/cancel_task

  cdp/                              # Generic Chrome DevTools Protocol backend
    __init__.py
    discovery.py                    # DevToolsActivePort + /json/version discovery
    client.py                       # CDP client/connect/retry wrapper
    session.py                      # ChromeSessionManager / browser session
    page.py                         # ControlledPage primitives
    targets.py                      # target listing/filtering
    errors.py                       # typed CDP/runtime errors

  platforms/
    xhs/
      cdp_diagnostics.py            # XHS reachability/login/security diagnostic
      processor.py
      tools.py

scripts/
  diagnostics/
    chrome_cdp_discovery.py         # thin wrapper around flowlens.cdp.discovery
    chrome_cdp_targets.py           # thin wrapper around flowlens.cdp.targets
    chrome_cdp_controlled_tab.py    # thin wrapper around flowlens.cdp.diagnostics
    xhs_cdp_probe.py                # thin wrapper around flowlens.platforms.xhs.cdp_diagnostics
```

## 6. Ownership rules

### 6.1 `app/`

`app/` owns desktop shell concerns only:

- UI rendering and app views
- Tauri commands/invokes
- Rust sidecar supervisor
- packaging config
- native app paths/resources
- opening Chrome setup pages
- reading artifact files for WebView display

`app/` should not own:

- generic CDP control logic
- XHS page-state detection
- FlowLens agent execution
- LLM calls
- platform-specific extraction logic

### 6.2 `flowlens.runtime`

`runtime` is the app-facing Python sidecar package. It owns:

- JSON-RPC request handling
- method dispatch
- runtime state
- task IDs and cancellation later
- event streaming later
- app-oriented orchestration
- artifact directory coordination

It should delegate implementation to:

- `flowlens.cdp` for browser/CDP primitives
- `flowlens.platforms.xhs` for XHS-specific behavior
- `flowlens.agent` / `flowlens.tools` for future full agent tasks

### 6.3 `flowlens.cdp`

`flowlens.cdp` owns generic browser-control primitives:

- Chrome CDP discovery
- WebSocket connect/retry
- target listing
- controlled tab creation
- navigation
- JavaScript evaluation
- screenshots
- clicks/typing/keys/scrolling
- connection health checks
- typed CDP errors

It must not contain XHS-specific strings/selectors/states.

### 6.4 `flowlens.platforms.xhs`

XHS owns:

- XHS login prompt detection
- XHS security verification detection
- XHS page-state probing
- XHS connection diagnostic
- future XHS search/note/profile workflows

### 6.5 `scripts/diagnostics`

Diagnostic scripts are developer/support entry points only. They should be thin wrappers around FlowLens package functions, not implementation homes.

### 6.6 Cleanup rules during migration

This refactor should clean code as it moves, not preserve the old script shape verbatim.

Required cleanup:

- Remove obsolete `prototype`, `Session N`, and demo-session wording from moved modules.
- Replace script-style globals/argparse coupling with importable config objects and functions.
- Keep CLI wrappers thin; parsing/printing belongs in wrappers, browser behavior belongs in `flowlens.cdp` or platform modules.
- Use typed return/config models at boundaries where useful, but avoid a repo-wide dataclass-to-Pydantic conversion.
- Keep stdout protocol-only in `flowlens.runtime`; logs go to stderr or log files.
- Avoid subprocess calls between Python modules once code is importable.
- Keep platform-specific strings/selectors out of `flowlens.cdp`.
- Keep Rust/Tauri unaware of CDP/XHS implementation details beyond runtime method names and artifact paths.
- Delete dead compatibility code after each phase once a replacement path is verified.

Do not do cleanup that changes product behavior without a testable reason. If behavior changes, capture it as an explicit migration step with validation.

## 7. Migration phases

### Phase 1 — Create `flowlens.cdp` and move generic discovery/connect code

Move/import logic from:

```text
app/prototype/chrome_discovery.py
app/prototype/cdp_connect.py
app/prototype/cdp_targets.py
```

Into:

```text
flowlens/cdp/discovery.py
flowlens/cdp/client.py
flowlens/cdp/targets.py
flowlens/cdp/errors.py
```

Expected public functions/classes:

```python
discover_chrome_cdp() -> dict
open_chrome_inspect_page() -> None
connect_cdp_with_retry(browser_ws_url: str, ...) -> CDPClient
list_targets(...) -> dict
```

Compatibility:

- Keep old `app/prototype/*.py` files as temporary wrappers or move wrappers to `scripts/diagnostics/` in the same phase.
- No Tauri behavior change yet.

Validation:

```bash
python -m py_compile flowlens/cdp/*.py
python scripts/diagnostics/chrome_cdp_discovery.py --json
python scripts/diagnostics/chrome_cdp_targets.py --json
```

### Phase 2 — Move controlled-tab primitives into `flowlens.cdp`

Move `FlowLensCDPPage` and helper logic from:

```text
app/prototype/cdp_controlled_tab.py
```

Into:

```text
flowlens/cdp/page.py
flowlens/cdp/session.py
```

Expected public shape:

```python
class ChromeCDPSession:
    async def connect(...)
    async def list_targets(...)
    async def create_controlled_tab(...)
    async def close(...)

class ControlledPage:
    async def navigate(...)
    async def evaluate_js(...)
    async def mark_title(...)
    async def capture_screenshot(...)
    async def click(...)
    async def type_text(...)
    async def press_key(...)
    async def scroll(...)
```

Validation:

```bash
python scripts/diagnostics/chrome_cdp_controlled_tab.py --json
```

Expected status:

```text
controlled_tab_ready
```

### Phase 3 — Move XHS probe into `flowlens.platforms.xhs`

Move XHS-specific logic from:

```text
app/prototype/cdp_xhs_probe.py
```

Into:

```text
flowlens/platforms/xhs/cdp_diagnostics.py
```

Expected public function:

```python
async def run_xhs_cdp_probe(config: XHSCdpProbeConfig) -> dict:
    ...
```

Validation:

```bash
python scripts/diagnostics/xhs_cdp_probe.py --json
```

Expected statuses:

```text
xhs_probe_ready
xhs_login_required
xhs_security_verification
xhs_probe_inconclusive
setup_required
```

### Phase 4 — Update sidecar to call Python functions directly

Replace subprocess delegation in:

```text
flowlens/runtime/service.py
```

Current:

```python
subprocess.run([... app/prototype/*.py --json ...])
```

Target:

```python
from flowlens.cdp.discovery import discover_chrome_cdp
from flowlens.cdp.targets import list_chrome_targets
from flowlens.cdp.diagnostics import run_controlled_tab_diagnostic
from flowlens.platforms.xhs.cdp_diagnostics import run_xhs_cdp_probe
```

This phase removes the app dependency on `app/prototype/` for runtime behavior.

Validation:

```bash
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"health","params":{}}' \
  '{"jsonrpc":"2.0","id":2,"method":"connect_chrome","params":{}}' \
  '{"jsonrpc":"2.0","id":3,"method":"shutdown","params":{}}' \
  | .venv/bin/python -m flowlens.runtime --transport stdio
```

Then validate through installed app:

- `Connect Chrome` -> `cdp_available`
- `Create Controlled Tab` -> `controlled_tab_ready`
- `Open XHS Probe` -> one valid XHS status + screenshots

### Phase 5 — Rename/remove `app/prototype/`

Once the sidecar and diagnostic scripts no longer depend on `app/prototype/`, remove it or replace it with a short README pointing to `scripts/diagnostics/`.

Preferred final state:

```text
app/prototype/                 # deleted
scripts/diagnostics/*.py       # thin wrappers
```

If deletion is too disruptive during migration, use:

```text
app/prototype/README.md        # deprecated location notice
```

No implementation code should remain there.

### Phase 6 — Prepare for agent integration

Before wiring the full agent, add runtime infrastructure:

```text
flowlens/runtime/task_manager.py
flowlens/runtime/events.py
flowlens/runtime/artifacts.py
flowlens/runtime/protocol.py
```

Required methods:

```text
run_task
cancel_task
get_task_status
list_task_artifacts
open_report
export_diagnostics
```

Required events:

```text
runtime_ready
browser_status
task_started
status
reasoning
tool_call
tool_result
screenshot
artifact
error
task_done
task_cancelled
```

Initial rule:

```text
Only one active browser task at a time.
```

Then wire `run_task` to existing FlowLens systems:

```python
flowlens.agent.loop.run_agent
flowlens.tools.registry.build_tools
flowlens.knowledge.loader
flowlens.platforms.xhs
```

## 8. Dependency changes

`pydantic>=2.7` should be a direct core dependency because `flowlens.runtime.protocol` validates the JSON-RPC boundary between Rust/Tauri and Python. This keeps runtime request/response/event payloads typed without forcing the whole codebase to migrate away from dataclasses.

`cdp-use==1.4.5` is now a direct root dependency because `flowlens.cdp` is part of the active desktop runtime path. `app/requirements.txt` is retained only as a legacy note for old app-local diagnostics.

Development command:

```bash
uv sync --extra dev
```

Production packaging must include the root runtime dependencies, including `pydantic` and `cdp-use`, in the bundled Python runtime. Eventually remove or fully deprecate:

```text
app/requirements.txt
```

## 9. Testing strategy

### Unit/static checks

```bash
python -m py_compile flowlens/cdp/*.py flowlens/runtime/*.py flowlens/platforms/xhs/*.py
pnpm --dir app run build
cargo check --manifest-path app/src-tauri/Cargo.toml
```

### Sidecar protocol checks

```bash
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"health","params":{}}' \
  '{"jsonrpc":"2.0","id":2,"method":"connect_chrome","params":{}}' \
  '{"jsonrpc":"2.0","id":3,"method":"shutdown","params":{}}' \
  | .venv/bin/python -m flowlens.runtime --transport stdio
```

### Live browser diagnostics

Run with Chrome remote debugging approved:

```bash
python scripts/diagnostics/chrome_cdp_discovery.py --json
python scripts/diagnostics/chrome_cdp_targets.py --json
python scripts/diagnostics/chrome_cdp_controlled_tab.py --json
python scripts/diagnostics/xhs_cdp_probe.py --json
```

### Desktop app smoke test

After Tauri changes:

```bash
bash scripts/build_app.sh
open /Applications/FlowLens.app
```

Verify in installed app:

- app launches as `FlowLens`
- `Runtime ready` appears
- backend shows `Tauri + FlowLens Python runtime`
- `Connect Chrome` returns `cdp_available` or a clear `setup_required`
- `Create Controlled Tab` creates a marked `🟢 FlowLens` tab
- `Open XHS Probe` returns a valid XHS diagnostic status and displays screenshots when available

Because this touches `app/`, every completed implementation phase must rebuild and reinstall the desktop app with:

```bash
bash scripts/build_app.sh
```

## 10. Acceptance criteria

This refactor is complete when:

- `app/prototype/` no longer contains runtime implementation code.
- `flowlens.runtime` does not call `subprocess.run(... app/prototype/*.py ...)` for current app actions.
- Generic CDP code is importable from `flowlens.cdp`.
- XHS-specific diagnostic code is importable from `flowlens.platforms.xhs.cdp_diagnostics`.
- Manual diagnostics live under `scripts/diagnostics/` and are thin wrappers.
- Existing desktop UI buttons still work.
- Installed `/Applications/FlowLens.app` is rebuilt and smoke-tested.
- Documentation reflects the new code layout.

## 11. Open decisions

1. CDP dependency name/version:
   - keep `cdp-use==1.4.5`, or replace with a smaller direct WebSocket CDP client?
2. Persistent Chrome session timing:
   - keep per-action CDP connections for now, or introduce `ChromeSessionManager` immediately?
3. Diagnostic UI visibility:
   - keep diagnostics on the main post-onboarding screen, or move them behind a developer/support panel?
4. Event framing:
   - keep newline-delimited JSON-RPC for now, or move to `Content-Length` framing before streaming lots of task events?
5. Agent integration boundary:
   - should desktop runtime invoke `run_agent` directly, or create an app-specific `DesktopAgentRunner` adapter first?

## 12. Current implementation checkpoint

Implemented in this branch so far:

1. Added `flowlens/cdp/`.
2. Moved Chrome discovery, CDP connect helpers, target listing, page/session primitives, and controlled-tab diagnostics there.
3. Moved XHS CDP probe diagnostics to `flowlens.platforms.xhs.cdp_diagnostics`.
4. Added `scripts/diagnostics/chrome_cdp_discovery.py`, `chrome_cdp_targets.py`, `chrome_cdp_controlled_tab.py`, `xhs_cdp_probe.py`, and `desktop_cdp_demo.py` wrappers.
5. Updated `flowlens.runtime.service` so all current app actions call package functions directly.
6. Replaced `app/prototype/*.py` with `app/prototype/README.md` pointing to the maintained diagnostics.
7. Ran sidecar, diagnostic, and installed-app smoke tests.
