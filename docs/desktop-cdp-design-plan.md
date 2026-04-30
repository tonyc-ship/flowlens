# SocAI Desktop + CDP Design Plan

Status: planning draft
Product name: **SocAI** — pronounced like “social AI” / “soc-ai”
Created: 2026-04-29
Inputs: current FlowLens architecture, current `desktop_app/` spike, current Chrome extension bridge, local `../browser-harness`, and local browser-control framework notes.

## 1. One-sentence direction

Build **SocAI**, a new desktop agent app for social-platform work, powered by the FlowLens runtime. SocAI should own setup, runtime orchestration, browser connection, task progress, and reports; migrate browser automation from the current Chrome-extension bridge toward a CDP-based browser controller inspired by `browser-harness`, while keeping the existing FlowLens agent/tool/XHS knowledge layers reusable.

## 2. Product goals

### Product identity and scope

- **SocAI** is the user-facing desktop app.
- **FlowLens** remains the underlying automation/runtime framework unless we later decide to rename packages.
- Product category: a social-platform agent that helps users understand, analyze, summarize, and eventually operate across social platforms.
- First vertical: Xiaohongshu / 小红书 (XHS).
- First high-value jobs: analyze posts, creators, comments, trends, content patterns, and engagement signals.
- Future candidate platforms can include other social/content networks, but the first implementation should stay XHS-focused until the full desktop + CDP loop works reliably.

### Primary goals

- Make SocAI usable by non-technical users without asking them to:
  - `pip install` dependencies manually
  - run CLI commands
  - load an unpacked Chrome extension
  - understand bridge ports, Python envs, or runtime logs
- Provide a polished desktop UI for:
  - onboarding
  - model/auth setup
  - browser/login setup
  - task entry
  - live task progress
  - screenshots/artifacts/report viewing
  - run history
  - stop/retry/debug
- Replace or reduce dependence on the Chrome extension by using CDP for browser control.
- Make XHS social analysis feel like a product workflow, not a developer automation script.
- Preserve the existing high-value FlowLens intelligence:
  - XHS-specific tools
  - XHS anti-bot knowledge
  - note/profile/comment/media extraction
  - reports and artifacts
  - local observer/perception pieces where useful

### Non-goals for the first implementation

- Do not rewrite the whole agent from scratch.
- Do not remove the extension immediately.
- Do not optimize for App Store distribution first.
- Do not make local LLM packaging the default first-run path unless it is already reliable.
- Do not build every desktop-observer feature into the first desktop product.
- Do not broaden SocAI beyond XHS until the first XHS vertical proves the full desktop + CDP experience.

## 3. Important product decision: SocAI is a new app, not the existing spike

The current `desktop_app/` is a useful spike and reference, but SocAI should be treated as a new product surface.

Recommended stance:

- Keep `desktop_app/` as legacy/reference until SocAI is real.
- Start a new desktop application path after design approval, for example:
  - `apps/socai/`, or
  - `socai_app/`
- Reuse code/concepts from the current Tauri app only when they help:
  - runtime bundle resolution
  - task spawning
  - report path discovery
  - stop-task handling
- Do not let old UI assumptions constrain the new product.

Open decision: use Tauri again, or evaluate another shell. Tauri remains a strong default because FlowLens is already Python-heavy and wants a lightweight native wrapper.

## 4. Current architecture summary

Today, a browser task works roughly like this:

```text
CLI / desktop spike / MCP host
  -> flowlens.agent.loop
  -> flowlens.tools.registry
  -> XHS / browser tools
  -> ExtensionBridge WebSocket server
  -> Chrome extension background.js
  -> content.js + content_xhs.js
  -> Chrome tabs / DOM / screenshots / XHS extraction
```

Key assets to preserve:

- `flowlens.agent.loop.run_agent`
- `flowlens.tools.build_tools`
- `flowlens.platforms.xhs.tools`
- `flowlens.platforms.xhs.processor.XHSSiteAdapter`
- `chrome_extension/content_xhs.js` XHS DOM/site adapter logic
- `flowlens.knowledge/sites/xiaohongshu.yaml`
- run artifacts under `task_runs/`

Main UX problems:

- install/setup is technical
- extension install is manual
- extension reload/version issues are confusing
- user must understand CLI task execution
- desktop UI is a thin launcher, not a full product

## 5. Browser-harness lessons to adopt

`browser-harness` proves a useful minimal CDP architecture:

```text
caller
  -> tiny helper API
  -> Unix socket
  -> long-lived daemon
  -> CDP WebSocket
  -> Chrome
```

Technical lessons worth adopting:

- Long-lived CDP daemon keeps the browser connection warm.
- Raw CDP is enough for most browser primitives.
- CDP compositor-level input is more powerful than DOM events.
- Session/target state goes stale; self-healing reattach matters.
- Screenshots should be a first-class verification primitive.
- Direct `Runtime.evaluate` is enough to inject/extract site-specific logic.
- Explicit target filtering is needed to avoid internal Chrome pages and omnibox popup targets.

Things not to copy directly:

- The “agent edits helpers.py mid-task” philosophy is good for a coding-agent harness, not for a user-facing product.
- The CLI-only UX is not the target UX.
- The “attach to existing Chrome after chrome://inspect Allow” path is acceptable for SocAI because reusing the user’s real social-platform login state is more important than avoiding the one-time permission ceremony. SocAI should wrap this flow with clear UI guidance instead of exposing it as a developer setup task.

## 5.1. Browser-harness `helpers.py` model vs SocAI model

Your understanding of browser-harness is correct: it gives a coding agent a small Python helper library, pre-imports that library into `browser-harness -c`, and lets the agent write arbitrary Python code that calls those helpers. If a primitive is missing, the coding agent can edit `helpers.py` itself and then use the new helper immediately.

That is an excellent fit for Claude Code / Codex-style agents because the agent already has a code execution environment and the user expects it to edit files. It is not the right default product model for SocAI because SocAI is a consumer desktop app handling a real logged-in social profile. Letting the model freely write and execute Python, mutate the shipped helper library, or call unrestricted raw CDP would make consent, safety, reproducibility, support, and packaging much harder.

SocAI should copy the **technical substrate** of browser-harness, not the full coding-agent interaction model:

- Copy/adapt:
  - CDP connection to the user’s real Chrome profile.
  - Long-lived connection/session management.
  - Raw CDP primitives internally.
  - Screenshots as verification checkpoints.
  - Stale target/session recovery.
  - Ability to inject site-specific JavaScript adapters.
- Do not copy as the default UX:
  - Arbitrary model-written Python execution.
  - Runtime edits to `helpers.py` / shipped product code.
  - Unbounded raw CDP exposed directly to the user-facing agent.

Instead, SocAI should use audited tools and skills:

```text
LLM planner
  -> fixed SocAI/FlowLens tool schemas
  -> XHS-specific macros and extractors
  -> CDP browser kernel
  -> injected XHS JavaScript adapter
  -> structured artifacts + report
```

The model can still be flexible by composing tools, choosing parameters, reading run state, and using site-specific skills. New low-level browser capabilities or site skills should be added through a developer/review path, not by arbitrary runtime mutation in the consumer app.

A future advanced/dev mode could allow sandboxed codegen experiments, but that is outside the initial happy path.

## 6. Browser strategy

Decision: SocAI should **default to the user’s existing Chrome profile**.

The first product vertical is XHS, and XHS may restrict or heavily complicate multiple simultaneous logged-in devices/sessions. A separate managed SocAI Chrome profile could force the user to log in again, potentially displacing or breaking their normal browser login. That is worse than asking the user to approve Chrome’s remote-debugging / inspect permission once.

Therefore, SocAI should reuse the user’s default Chrome profile as much as possible and attach through CDP, following the browser-harness-style permission flow.

### Mode A — attach to existing Chrome via CDP (recommended default)

SocAI connects to the user’s already-running Chrome profile via CDP.

```text
SocAI Desktop
  -> detect running Chrome / default user profile
  -> guide user through chrome://inspect/#remote-debugging if needed
  -> connect to existing Chrome DevTools endpoint
  -> create a SocAI-controlled tab in the existing Chrome profile/window
  -> reuse the user’s existing XHS login/session/cookies
```

Pros:

- Reuses the user’s existing XHS login state.
- Avoids a second XHS login that may conflict with Xiaohongshu device/session restrictions.
- Avoids copying, importing, or overwriting profile data.
- Keeps the user’s social-platform identity exactly where it already lives.
- Mirrors the proven browser-harness flow.
- No Chrome extension install.

Cons:

- Requires a one-time `chrome://inspect/#remote-debugging` permission/Allow flow on some machines.
- The permission flow must be explained carefully because it grants powerful browser access.
- SocAI must avoid interfering with the user’s active browsing.
- Google/Chrome CDP restrictions may change.

Required mitigations:

- Make the permission flow explicit and user-consented.
- Be honest that CDP permission is broad: technically SocAI can inspect/control Chrome targets in that profile, even though the product will scope itself to the SocAI-controlled tab.
- Create a new SocAI-controlled tab in the existing Chrome profile/window instead of taking over the current foreground tab.
- Pin all automation to that tab.
- Visibly mark the controlled tab. Desired: put it in a Chrome tab group named “SocAI” if feasible. If Chrome tab grouping is not reliably exposed over CDP, use a clear title prefix such as “🟢 SocAI” for the prototype.
- Clearly show when SocAI is connected and which window it controls.
- Provide a disconnect/stop control.
- Never copy or mutate Chrome profile files directly.

### No fallback for prototype / v1 happy path

For the SocAI prototype and v1 happy path, do **not** spend product/engineering effort on fallback browser modes.

Out of scope:

- Managed SocAI Chrome/Chromium profile.
- Existing Chrome extension backend as a user-facing fallback.
- Remote/browser-cloud mode.

The SocAI product path should be exactly:

```text
existing user Chrome profile -> Chrome inspect/remote-debugging permission -> CDP attach -> SocAI-controlled tab -> XHS task
```

If this path does not work reliably enough, that is a core product risk to solve directly rather than hiding behind fallback UX.

## 6.1. Prototype principle

The prototype should be built from the ground up and should **not** try to integrate every existing FlowLens skill/tool/workflow.

Prototype goal:

```text
Prove SocAI can connect to the user’s existing Chrome profile through CDP, after explicit inspect permission, and operate a Xiaohongshu session already logged in there.
```

The prototype does not need:

- full FlowLens agent integration
- full XHS skill integration
- all current `xhs_*` tools
- MCP support
- local LLM support
- packaging/signing polish
- extension compatibility

Recommended prototype scope:

1. A new minimal SocAI desktop shell.
2. A guided “Connect Chrome” flow.
3. A CDP connection to existing Chrome after inspect permission.
4. Creation of a SocAI-controlled Chrome tab in the existing profile/window.
5. Navigation to Xiaohongshu.
6. Proof that SocAI can operate the page:
   - read current URL/title
   - capture screenshot
   - perform a simple click/scroll/key action in the controlled tab
   - optionally run a tiny JavaScript probe only to prove page access, not to implement product extraction
7. A simple UI showing connection status and screenshot.

No LLM and no XHS product functions are required for the first proof. Once CDP + existing XHS session control is proven, add a fixed audited tool layer, then later the LLM planner.

## 7. Target architecture

```text
SocAI Desktop
  ├─ UI shell
  │   ├─ onboarding
  │   ├─ browser/login manager
  │   ├─ task composer
  │   ├─ live run monitor
  │   ├─ report/artifact viewer
  │   └─ settings/auth/model management
  │
  ├─ Runtime supervisor
  │   ├─ starts/stops Python runtime service
  │   ├─ starts/stops CDP daemon
  │   ├─ connects/disconnects existing Chrome via CDP
  │   ├─ streams logs/events to UI
  │   └─ manages run history
  │
  ├─ FlowLens Python runtime
  │   ├─ agent loop
  │   ├─ unified tool registry
  │   ├─ XHS tools
  │   ├─ perception/media
  │   └─ report/artifact generation
  │
  └─ Browser backend abstraction
      └─ CDP backend, new SocAI happy path
          ├─ CDP connection/session manager
          ├─ target/window/profile manager
          ├─ browser primitives
          └─ JS adapter injection
```

## 8. Browser backend abstraction

The key implementation boundary should be a browser backend interface that hides whether we use the extension or CDP.

Candidate interface:

```python
class BrowserBackend:
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def wait_for_connection(self, timeout: float = 120) -> None: ...

    async def create_background_window(self, url: str, focused: bool = False) -> dict: ...
    async def close_window(self, window_id: int) -> dict: ...
    def tab(self, tab_id: int, *, window_id: int | None = None) -> "BrowserBackend": ...

    async def navigate(self, url: str, wait_ms: int = 5000) -> dict: ...
    async def go_back(self, wait_ms: int = 1500) -> dict: ...
    async def get_tab_info(self) -> dict: ...

    async def capture_screenshot(self) -> str: ...
    async def save_screenshot(self, path: str | Path) -> str: ...

    async def click_at(self, x: int, y: int) -> dict: ...
    async def type_text(self, text: str) -> dict: ...
    async def press_key(self, key: str, **kwargs) -> dict: ...
    async def scroll_page(self, pixels: int = 600) -> dict: ...

    async def run_js(self, code: str) -> dict: ...
    async def send_command(self, command: str, params: dict | None = None) -> dict: ...
```

Current `ExtensionBridge` can be adapted to this interface. New `CDPBridge` should implement it.

## 9. CDP backend design

### Core responsibilities

- Start/connect to CDP endpoint.
- Maintain browser-level WebSocket connection.
- Manage page targets and attached sessions.
- Create/activate/close tabs/windows.
- Dispatch mouse/keyboard/wheel events.
- Capture screenshots.
- Evaluate JavaScript.
- Listen for key CDP events:
  - page load
  - dialog opened/closed
  - target attached/detached
  - network events if enabled
- Recover from stale sessions.

### Implementation options

Option 1: vendor/adapt browser-harness style code using `cdp-use`.

- Pros: proven locally, small, fast to spike.
- Cons: FlowLens needs async integration, structured logging, app lifecycle, tests.

Option 2: implement direct WebSocket CDP client in FlowLens.

- Pros: no extra abstraction, full control.
- Cons: more work, must handle request IDs, sessions, events, reconnects.

Option 3: use Playwright only for CDP plumbing.

- Pros: stable high-level browser lifecycle.
- Cons: heavier dependency, may fight FlowLens’s raw-CDP needs, adds its own model of pages/contexts.

Prototype decision: use `cdp-use`, matching browser-harness’s thin CDP client approach. We can replace it later only if the prototype exposes a concrete reason.

## 10. XHS over CDP

The XHS tool surface should stay stable. The implementation changes below it.

Current extension path:

```text
XHSSiteAdapter
  -> ext_bridge.send_command("extract_note_content")
  -> background.js
  -> content.js
  -> window.FlowLensXhs.extractNoteContentCommand()
```

Future CDP path:

```text
XHSSiteAdapter
  -> cdp_bridge.send_command("extract_note_content")
  -> ensure XHS adapter JS injected
  -> Runtime.evaluate("window.FlowLensXhs.extractNoteContentCommand(params)")
```

Recommended work:

- Extract reusable JS assets from the extension:
  - common helpers
  - XHS adapter
- Make them injectable without relying on Chrome extension APIs.
- Provide a Python command mapping:
  - `extract_search_cards` -> `FlowLensXhs.extractSearchCards()`
  - `submit_search_query` -> `FlowLensXhs.submitSearchQuery(keyword)`
  - `click_note_by_id` -> `FlowLensXhs.clickNoteById(note_id)`
  - `extract_note_content` -> `FlowLensXhs.extractNoteContentCommand(params)`
  - etc.

Potential improvement from browser-harness XHS skill:

- Add optional Pinia store extraction for richer note/video data:
  - `window.__INITIAL_STATE__.note.noteDetailMap`
  - video `masterUrl`
  - image list
  - comments if present
- Do this carefully. For anti-bot safety, still prefer human-like search and card clicks for navigation.

## 11. SocAI UX plan

### First-run onboarding

Screens:

1. Welcome
   - What SocAI does for social-platform work
   - Privacy explanation
   - Local/browser/model choices

2. Runtime check
   - bundled runtime OK
   - Python/service OK
   - model/auth configured or needs setup

3. Browser setup
   - recommended: connect to the user’s existing Chrome profile
   - guide the user through `chrome://inspect/#remote-debugging` if needed
   - create a SocAI-controlled Chrome tab in the same profile/window
   - test connection
   - show status: connected, controlled tab, XHS session maybe unknown/verified

4. Model setup
   - cloud Sonnet / OpenAI / Kimi / Qwen
   - local model optional

5. Ready screen
   - suggested first tasks

### Main app screens

- Task composer
  - natural-language prompt
  - site/task presets
  - model selector
  - browser connection/profile status

- Live run monitor
  - current step
  - reasoning summary
  - current browser screenshot
  - tool timeline
  - stop/pause button

- Results viewer
  - report markdown/html preview
  - screenshots
  - extracted notes
  - artifacts/files
  - open run folder

- History
  - previous runs
  - filters by site/task/date/status

- Settings
  - API keys/auth
  - browser connection status
  - Chrome inspect/permission diagnostics
  - data directory
  - model backend
  - advanced CDP/extension options

## 12. Packaging/runtime plan

Questions to settle:

- Bundle Python runtime or use embedded standalone binary?
- Use `uv` managed venv in app support directory?
- Package heavy optional dependencies separately?
- How to update runtime after app install?

Recommended initial path:

- Keep Python runtime bundled similarly to current packaging, but make it explicit and robust.
- Default cloud model path should have small dependency footprint.
- Local MLX models remain optional downloads managed by the app.
- Desktop app owns an app data directory:

```text
~/Library/Application Support/SocAI/
  runtime/
  task_runs/
  logs/
  browser_connection.json
  config.json
```

## 13. Phased implementation plan

### Phase 0 — decisions and design lock

Deliverable: agreed design choices before code.

Tasks:

- [x] Decide desktop shell: Tauri v2 for prototype.
- [x] Decide new app path: `apps/socai/`.
- [x] Decide browser default: existing user Chrome profile via CDP / inspect permission flow.
- [x] No fallback browser modes in prototype/v1 happy path.
- [x] Decide CDP client implementation for prototype: `cdp-use`, matching browser-harness’s thin CDP client approach.
- [x] Prototype is ground-up; do not reuse current `desktop_app/` UI/runtime structure.
- [x] Defer packaging strategy for Python runtime; not required for prototype proof.

Acceptance criteria:

- Written decisions appended to this document.
- No major open architecture ambiguity for Phase 1.

### Phase 1 — CDP spike outside the product UI

Deliverable: command-line/internal proof that FlowLens can control the user’s existing Chrome profile via CDP without the extension.

Tasks:

- [ ] Detect an existing running Chrome profile / DevTools endpoint.
- [ ] If needed, guide through the `chrome://inspect/#remote-debugging` permission flow.
- [ ] Connect to CDP endpoint.
- [ ] Create a SocAI-controlled Chrome tab/page target in the existing profile/window.
- [ ] Implement basic primitives:
  - [ ] navigate
  - [ ] get tab info
  - [ ] screenshot
  - [ ] click
  - [ ] type text
  - [ ] press key
  - [ ] scroll
  - [ ] run JavaScript
- [ ] Save screenshots into a run directory.
- [ ] Handle stale session by reattaching.
- [ ] Write a small verification script.

Acceptance criteria:

- Script opens a SocAI-controlled tab in the user’s existing Chrome profile/window.
- Script opens XHS/explore in that window.
- Script captures screenshot.
- Script can run JS and return `document.title` / current URL.
- No extension loaded/required.

### Phase 2 — browser backend abstraction

Deliverable: FlowLens tools can target CDP through a browser backend interface while preserving existing extension code for legacy paths.

Tasks:

- [ ] Define backend protocol/interface.
- [ ] Implement initial `CDPBridge` for SocAI happy path.
- [ ] Do not integrate `ExtensionBridge` into SocAI prototype/v1 UX.
- [ ] Update browser tools to depend on the interface, not extension-specific types where possible.
- [ ] Preserve existing extension behavior.
- [ ] Add internal backend selection config for development:
  - `FLOWLENS_BROWSER_BACKEND=cdp` for SocAI happy path

Acceptance criteria:

- Existing extension-backed tests still pass.
- Basic browser tools work against CDP backend.
- No XHS parity required yet.

### Phase 3 — injectable XHS adapter

Deliverable: current XHS JS adapter can run without being a Chrome extension content script.

Tasks:

- [ ] Split extension JS into reusable injection assets:
  - common helpers
  - XHS adapter
  - extension-only overlay/watch code remains separate
- [ ] Remove or guard extension-only calls like `chrome.runtime.sendMessage`.
- [ ] Implement `CDPBridge.send_command(command, params)` command mapping.
- [ ] Inject adapter on XHS pages when needed.
- [ ] Implement CDP commands:
  - [ ] `detect_state`
  - [ ] `get_search_page_state`
  - [ ] `submit_search_query`
  - [ ] `extract_search_cards`
  - [ ] `click_search_tab`
  - [ ] `click_note_by_id`
  - [ ] `click_card`
  - [ ] `extract_note_content`
  - [ ] `extract_comments`
  - [ ] `scroll_note`
  - [ ] `close_note`
  - [ ] `extract_profile_info`
  - [ ] `extract_profile_notes`
  - [ ] `collect_carousel_images`

Acceptance criteria:

- `xhs_search_notes` works through CDP.
- `xhs_read_note(level="lite")` works through CDP.
- Screenshots and site result artifacts are saved.

### Phase 4 — XHS parity and improvements

Deliverable: CDP backend can complete representative XHS research tasks well enough for the SocAI happy path.

Tasks:

- [ ] Run fixed XHS task prompts through CDP and compare against current known-good outputs where useful.
- [ ] Compare:
  - success rate
  - run duration
  - note count
  - content completeness
  - screenshots
  - anti-bot states
- [ ] Add optional Pinia store extraction for note/video details.
- [ ] Add CDP network logging for video/media request diagnosis.
- [ ] Preserve anti-bot behavior: card clicks, modal close, low navigation pressure.
- [ ] Generate visual reports for parity runs.

Acceptance criteria:

- CDP can run `xhs_topic_scan` successfully.
- CDP report includes note screenshots and structured note summaries.
- No intentional changes to extension legacy path during this phase.

### Phase 5 — SocAI desktop shell

Deliverable: new SocAI app skeleton that can start and monitor a FlowLens run.

Tasks:

- [ ] Create new app path.
- [ ] Implement basic UI shell:
  - [ ] onboarding placeholder
  - [ ] task composer
  - [ ] run monitor
  - [ ] result viewer placeholder
  - [ ] settings placeholder
- [ ] Implement runtime supervisor:
  - [ ] launch FlowLens runtime
  - [ ] stream logs/events
  - [ ] stop task
  - [ ] locate report/artifacts
- [ ] Implement app data directory layout.
- [ ] Add browser backend setting to UI/dev config.

Acceptance criteria:

- User can start a task from SocAI.
- User can see running status.
- User can stop a task.
- User can open/view generated report.

### Phase 6 — browser onboarding in SocAI

Deliverable: existing Chrome profile connection works from the UI.

Tasks:

- [ ] Add “Connect Chrome” onboarding flow.
- [ ] Detect whether Chrome is running.
- [ ] Detect whether a DevTools endpoint is available.
- [ ] If unavailable, open and guide the user through `chrome://inspect/#remote-debugging`.
- [ ] Explain clearly what permission is being granted and why SocAI needs it.
- [ ] After approval, connect to the existing Chrome profile.
- [ ] Create a SocAI-controlled Chrome tab in the existing profile/window.
- [ ] Show connection status and controlled-tab status.
- [ ] Provide XHS session/check screen using the existing profile.
- [ ] Persist connection diagnostics, not profile data.
- [ ] Do not implement managed-profile fallback.

Acceptance criteria:

- Fresh user can connect SocAI to their existing Chrome profile from UI.
- User can approve the inspect/remote-debugging flow with clear guidance.
- SocAI creates and controls a marked Chrome tab without taking over the current foreground tab.
- FlowLens can run an XHS task using the existing profile/session without extension install.

### Phase 7 — polished run UX

Deliverable: user can understand what the agent is doing while it runs.

Tasks:

- [ ] Add structured run event stream from Python to desktop.
- [ ] Show current phase/tool/action.
- [ ] Show latest screenshot.
- [ ] Show compact reasoning/log timeline.
- [ ] Show artifacts as they appear.
- [ ] Add report preview after completion.
- [ ] Add retry/run-again.

Acceptance criteria:

- A non-technical user can tell whether the task is progressing, blocked, completed, or failed.
- User can inspect outputs without opening Finder/terminal.

### Phase 8 — packaging and install experience

Deliverable: a distributable macOS app build.

Tasks:

- [ ] Package SocAI app.
- [ ] Bundle or bootstrap Python runtime.
- [ ] Bundle minimal required dependencies for cloud-backed XHS tasks.
- [ ] Add first-run config migration.
- [ ] Add runtime health checks.
- [ ] Add auto-update strategy proposal.
- [ ] Document installation and reset/uninstall.

Acceptance criteria:

- Fresh machine install path is documented and tested.
- App can run a basic XHS task after onboarding.
- No manual `pip install` or CLI invocation needed by the user.

### Phase 9 — migration/deprecation strategy

Deliverable: clear path from extension-first FlowLens to SocAI/CDP-first FlowLens.

Tasks:

- [ ] Keep extension backend code outside SocAI prototype/v1 UX.
- [ ] Add diagnostics for the CDP happy path.
- [ ] Update README and docs.
- [ ] Later, decide when extension becomes optional/legacy for non-SocAI paths.
- [ ] Remove extension assumptions from agent prompts where inappropriate.
- [ ] Preserve MCP support if useful, backed by same browser backend abstraction.

Acceptance criteria:

- Existing CLI/MCP users are not broken.
- New users are directed to SocAI.
- Extension install is no longer part of primary onboarding.

## 14. Testing strategy

Every phase should include tests appropriate to its layer.

### Unit tests

- backend interface contract
- CDP request/response handling
- target filtering
- JS command mapping
- XHS entity normalization
- desktop runtime path resolution

### Integration tests

- existing Chrome detection/connect
- inspect-permission flow diagnostics
- controlled-tab creation and marking
- navigation/screenshot/run_js
- XHS adapter injection
- `xhs_search_notes` through CDP
- `xhs_read_note` through CDP

### Live parity tests

Run the same task on extension and CDP backends:

```text
在小红书上调研露营装备，阅读3篇相关笔记，总结内容套路和高互动点
```

Compare:

- completion status
- total duration
- number of notes extracted
- screenshot validity
- report usefulness
- anti-bot failures

### Desktop packaging tests

- fresh install
- first run
- existing Chrome connection
- inspect permission guidance
- task run
- stop task
- reopen app and view history

## 15. Risks and mitigations

### Risk: CDP setup becomes another technical hurdle

Mitigation:

- Make existing-Chrome connection a guided product onboarding flow, not a terminal setup task.
- Open `chrome://inspect/#remote-debugging` for the user when needed.
- Explain the permission in plain language.
- Poll/retry automatically after the user approves.

### Risk: User is uncomfortable granting Chrome inspect/remote-debugging access

Mitigation:

- Be explicit that this grants SocAI powerful access to the Chrome profile, not just a single webpage.
- Require user consent.
- Show connection status and a clear disconnect/stop button.
- Create a marked controlled tab and avoid the user’s active tabs.
- Do not design fallback modes; focus on making the existing-Chrome happy path clear and trustworthy.

### Risk: Existing Chrome profile control interferes with user browsing

Mitigation:

- Create a marked SocAI automation tab in the same Chrome profile/window.
- Pin automation to that tab.
- Never use the active foreground tab as the target unless the user explicitly chooses it.
- Close or release the SocAI-controlled tab at task end.

### Risk: Chrome CDP restrictions change

Mitigation:

- Track Chrome remote-debugging policy changes.
- Track whether the happy path remains viable.
- If the happy path fails, revisit the product premise explicitly rather than adding hidden fallback complexity.

### Risk: XHS anti-bot worsens under CDP

Mitigation:

- Preserve human-like site behavior.
- Avoid direct detail navigation by default.
- Use visible card clicks and modal close.
- Detect anti-bot states explicitly.

### Risk: Packaging Python/local ML is too heavy

Mitigation:

- Make cloud-backed path first.
- Make local models optional downloads.
- Keep runtime modular.

### Risk: Desktop app scope explodes

Mitigation:

- First product target is XHS social analysis/research only.
- WeChat/observer can come later.
- Keep phase acceptance criteria narrow.

## 16. Decisions and deferred questions

Prototype decisions already made:

1. SocAI prototype uses Tauri.
2. New app path is `apps/socai/`.
3. Browser path is existing user Chrome profile via CDP and Chrome inspect/remote-debugging permission.
4. No fallback browser modes in prototype/v1 happy path.
5. CDP client for prototype is `cdp-use`, matching browser-harness’s thin CDP client approach.
6. Prototype is ground-up and does not reuse current `desktop_app/` UI/runtime structure.
7. No LLM, MCP, extension, managed profile, or full XHS tool integration is required for the first proof.
8. Prototype proof target: connect to existing Chrome, create/mark controlled tab, open XHS, operate page, capture screenshot, show result.

Deferred until after prototype proof:

1. Exact production copy/UX for Chrome inspect permission.
2. How much production SocAI should expose browser internals vs hide them.
3. Whether SocAI should support MCP server mode.
4. Python runtime packaging strategy.
5. API key storage strategy.
6. Full XHS social-analysis task design.
7. Whether/when extension backend becomes legacy for non-SocAI paths.

## 17. Implementation procedure

Execute in small sessions. Each session should finish with tested artifacts before moving on.

General rules:

- Keep prototype code under `apps/socai/`.
- Do not modify or depend on the old `desktop_app/`.
- Do not integrate the LLM, MCP, extension backend, managed browser, or full XHS tools in the first prototype.
- Use `cdp-use` for the CDP client.
- Target macOS + Google Chrome + existing default profile only.
- Use a marked SocAI-controlled tab, not the user’s active tab.
- After every task, record what was tested and what command/manual step verifies it.

Recommended session breakdown:

1. **Session 1 — prototype scaffold + Chrome discovery**
   - Create `apps/socai/`.
   - Add prototype README.
   - Add a Python Chrome discovery script.
   - Verify it reports CDP available vs setup required.

2. **Session 2 — CDP attach + target listing**
   - Add `cdp-use` dependency for prototype/runtime.
   - Connect to existing Chrome CDP endpoint.
   - Call `Target.getTargets` and print page targets.
   - Verify connection after Chrome inspect permission is granted.

3. **Session 3 — controlled tab + primitives**
   - Create a new SocAI-controlled tab.
   - Mark the title with `🟢 SocAI`.
   - Implement navigate, JS evaluate, screenshot, scroll/key/click basics.
   - Verify with a screenshot and `document.title`.

4. **Session 4 — XHS technical proof**
   - Navigate controlled tab to XHS.
   - Capture screenshot.
   - Scroll or otherwise operate the page.
   - Read URL/title/runtime access.
   - Optionally navigate to a supplied XHS profile URL.

5. **Session 5 — minimal Tauri shell**
   - Create the SocAI Tauri app in `apps/socai/`.
   - Add buttons for Connect Chrome, Create Controlled Tab, Open XHS, Capture Screenshot.
   - Wire buttons to the Python prototype logic.

6. **Session 6 — demo bundle + checklist**
   - Save screenshots, timing, diagnostics, and operation results to a run folder.
   - Add a manual demo checklist.
   - Decide whether prototype proves enough to move to audited tools.

## 18. Current implementation status

Completed:

1. **Session 1** — created `apps/socai/`, added a prototype README, and added Chrome CDP discovery.
2. **Session 2** — added `cdp-use`, connected to Chrome CDP, and called `Target.getTargets`.
3. **Session 3** — created a marked SocAI-controlled tab and verified browser primitives.
4. **Session 4** — opened Xiaohongshu in the controlled tab, captured screenshots, scrolled, and read runtime state.
5. **Session 5** — created the minimal Tauri shell, built the packaged app, and verified Connect Chrome + Create Controlled Tab from the UI.
6. **Session 5 follow-up** — added CDP connection retry logic; XHS probe now works end-to-end from the packaged app UI.

Next task:

```text
Session 6 — demo bundle + manual checklist.
```

## 19. Prototype task breakdown

This is the concrete small-task backlog for the ground-up SocAI prototype. Each task should be independently testable before moving to the next.

### Milestone A — CDP attach proof, no app UI

Goal: prove we can attach to the user’s existing Chrome and operate a controlled tab.

A1. **Create prototype folder**

- [x] Create `apps/socai/`.
- [x] Add a minimal README explaining prototype scope.
- [x] Do not modify existing `desktop_app/`.

Acceptance:

- Prototype path exists and is clearly separate from the old desktop spike.

A2. **Chrome discovery script**

- [x] Implement a minimal script that checks known Chrome profile locations for `DevToolsActivePort`.
- [x] Print whether Chrome CDP is available.
- [x] If unavailable, print/open `chrome://inspect/#remote-debugging` instructions.

Acceptance:

- Running the script tells us either “CDP available” or “permission/setup required.”

A3. **CDP WebSocket connection**

- [x] Resolve browser WebSocket URL from `DevToolsActivePort` or `/json/version`.
- [x] Connect to Chrome over CDP.
- [x] Call `Target.getTargets`.

Acceptance:

- Script prints current page targets from the existing Chrome profile. Verified with `cdp_targets.py --json` after approving Chrome's remote-debugging dialog.

A4. **Create controlled tab**

- [x] Use CDP to create a new tab/page target.
- [x] Attach to that target.
- [x] Mark the tab title with `🟢 SocAI`.
- [x] Investigate tab grouping only as a non-blocking note; do not depend on it for prototype.

Acceptance:

- User can see a newly created SocAI-marked tab in their existing Chrome. Verified with `cdp_controlled_tab.py --json` and marked title `🟢 SocAI — SocAI Primitive Test`.

A5. **Browser primitives**

- [x] Implement minimal functions:
  - [x] `navigate(url)`
  - [x] `evaluate_js(code)`
  - [x] `capture_screenshot(path)`
  - [x] `click(x, y)`
  - [x] `type_text(text)`
  - [x] `press_key(key)`
- [x] Keep these internal; do not expose raw arbitrary code to an LLM.

Acceptance:

- Script navigates the marked tab, reads `document.title`, and saves a screenshot. Verified on the local SocAI primitive test page with all primitive checks returning true.

### Milestone B — XHS session proof, no LLM

Goal: prove the controlled tab can use the user’s real XHS session.

B1. **Open XHS in controlled tab**

- [x] Navigate controlled tab to `https://www.xiaohongshu.com/explore`.
- [x] Wait for basic page readiness.
- [x] Capture screenshot.

Acceptance:

- Screenshot shows Xiaohongshu page in the controlled tab. Verified with `cdp_xhs_probe.py --json`; screenshots show the XHS feed.

B2. **Operate XHS page**

- [x] Prove SocAI can interact with the controlled XHS tab using CDP:
  - [x] capture screenshot
  - [x] scroll the page
  - [x] read URL/title after interaction
  - [x] optionally run a tiny JavaScript probe to prove page runtime access

Acceptance:

- Script proves we can see and operate the XHS page from the existing Chrome profile. Verified on the XHS feed; a login-related prompt/toast appeared but did not block page operation.

B3. **Optional profile/page proof**

- [ ] If user provides a profile URL, navigate to it.
- [ ] Capture screenshot and read basic URL/title/runtime access.

Acceptance:

- Script can show that SocAI can access a real XHS profile/page from the existing Chrome session.

### Milestone C — minimal SocAI desktop shell

Goal: wrap the proven CDP/XHS proof in a desktop UI.

C1. **Create new Tauri shell**

- [x] Create a new SocAI app path.
- [x] Minimal window with title/logo placeholder.
- [x] No old desktop app UI reuse unless intentionally copied.

Acceptance:

- SocAI Tauri shell builds and opens as `/apps/socai/src-tauri/target/release/bundle/macos/SocAI Prototype.app`.

C2. **Connect Chrome button**

- [x] Add “Connect Chrome” UI.
- [x] Tauri command calls prototype Chrome discovery/connect code.
- [x] Show connected/error/setup-required state.

Acceptance:

- User can click a button and see Chrome connection status. Verified in the packaged app with status `connect_chrome — cdp_available`.

C3. **Inspect permission guidance**

- [x] UI explains Chrome remote-debugging permission and tells the user to click Allow while the action is running.
- [ ] Add setup-required action to open `chrome://inspect/#remote-debugging` directly from UI.
- [ ] UI polls/retries after user approves.

Acceptance:

- Partial: guidance is visible in the app; direct inspect-page open/polling remains a future UI polish task.

C4. **Open controlled SocAI tab**

- [x] UI button creates/marks a SocAI-controlled tab.
- [x] UI displays controlled-tab command status and JSON; screenshot artifacts are rendered when available.

Acceptance:

- User sees the controlled tab in Chrome and its metadata in SocAI. Verified in the packaged app with status `controlled_tab — controlled_tab_ready`.

C5. **Open XHS + screenshot**

- [x] UI button is wired to the XHS probe command.
- [x] Underlying Python XHS probe captures screenshots when run directly.
- [x] Added CDP connection retry logic (`cdp_connect.py`) so the user has multiple chances to click Allow.
- [x] Packaged-app UI XHS probe returns `xhs_probe_ready` and displays 2 screenshot artifacts.

Acceptance:

- SocAI displays screenshots from the controlled XHS tab. Verified in the packaged app.

C6. **Operate XHS proof**

- [x] UI button runs the XHS CDP operation.
- [x] Python XHS proof opens, scrolls, screenshots, and reads URL/title/runtime state.
- [x] Packaged-app UI returns diagnostics including scrollY, readyState, landed URL, login/security indicators.

Acceptance:

- SocAI proves from the UI that it can operate the XHS tab. Verified in the packaged app with JSON diagnostics and screenshot artifacts.

### Milestone D — prototype report

Goal: turn the proof into a demo artifact.

D1. **Write prototype run bundle**

- [ ] Save screenshot(s), extracted JSON, timing, and connection diagnostics under a run directory.

Acceptance:

- Each prototype run leaves a small reproducible artifact bundle.

D2. **Manual demo checklist**

- [ ] Document exact demo steps:
  1. Open SocAI.
  2. Connect Chrome.
  3. Approve inspect permission if needed.
  4. Create marked tab.
  5. Open XHS.
  6. Capture screenshot.
  7. Operate the page and show the result.

Acceptance:

- Another developer can run the prototype demo from the checklist.

### What comes after the prototype

Only after Milestones A-D pass:

- [ ] Decide which prototype code becomes the SocAI CDP kernel.
- [ ] Decide whether to reuse current `content_xhs.js` by making it injectable.
- [ ] Add fixed audited SocAI/FlowLens tools on top of CDP.
- [ ] Add the LLM planner.
- [ ] Add XHS social-analysis report generation.
