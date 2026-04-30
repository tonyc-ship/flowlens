# SocAI Prototype Session Log

## 2026-04-29 — Session 1: scaffold + Chrome discovery

Completed:

- Created `apps/socai/` prototype folder.
- Added `apps/socai/README.md` with scope, decisions, and session plan.
- Added `apps/socai/prototype/chrome_discovery.py`.
- Discovery script checks the existing Chrome user-data root for `DevToolsActivePort` and reports either `cdp_available` or `setup_required`.

Verification commands run from repo root:

```bash
python3 -m py_compile apps/socai/prototype/chrome_discovery.py
python3 apps/socai/prototype/chrome_discovery.py
python3 apps/socai/prototype/chrome_discovery.py --json
SOCAI_CHROME_USER_DATA_DIR="$(mktemp -d)" SOCAI_CHROME_USER_DATA_DIR_ONLY=1 \
  python3 apps/socai/prototype/chrome_discovery.py --json
```

Observed result on this machine:

- Real Chrome profile discovery reported `cdp_available` on port `9222`.
- Isolated empty-profile test reported `setup_required`.

Next session:

- Add `cdp-use`.
- Connect to the discovered browser WebSocket.
- Call `Target.getTargets`.

## 2026-04-30 — Session 2: CDP attach + target listing

Completed:

- Added `apps/socai/requirements.txt` with `cdp-use==1.4.5`.
- Added `apps/socai/prototype/cdp_targets.py`.
- Script reuses Chrome discovery, connects to the browser WebSocket with `cdp-use`, and calls `Target.getTargets`.
- Documented the Chrome **Allow remote debugging?** dialog behavior: Chrome may show one Allow dialog per connection attempt in the prototype.

Verification commands run from repo root:

```bash
python3 -m py_compile apps/socai/prototype/chrome_discovery.py apps/socai/prototype/cdp_targets.py
uv run --no-project --with cdp-use==1.4.5 --python 3.11 \
  python apps/socai/prototype/cdp_targets.py --json
```

Observed result on this machine:

- Chrome remote debugging page showed server running at `127.0.0.1:9222`.
- During live attach, the Chrome permission dialog appeared and needed **Allow** clicked while the command was running.
- After approval, `cdp_targets.py --json` reported `connected`.
- `Target.getTargets` returned 49 total Chrome targets and 21 visible page targets.

Next session:

- Create a new SocAI-controlled tab.
- Attach to it.
- Mark the title with `🟢 SocAI`.
- Add minimal navigation/evaluate/screenshot primitives.

## 2026-04-30 — Session 3: controlled tab + primitives

Completed:

- Added `apps/socai/prototype/cdp_controlled_tab.py`.
- Script connects through CDP, creates a new Chrome tab, attaches to it, and marks the title with `🟢 SocAI`.
- Added internal prototype primitives:
  - `navigate(url)`
  - `evaluate_js(code)`
  - `capture_screenshot(path)`
  - `click(x, y)`
  - `type_text(text)`
  - `press_key(key)`
  - `scroll(delta_y)`
- The script uses a local `data:` test page and does not open XHS yet.

Verification commands run from repo root:

```bash
python3 -m py_compile apps/socai/prototype/chrome_discovery.py apps/socai/prototype/cdp_targets.py apps/socai/prototype/cdp_controlled_tab.py
uv run --no-project --with cdp-use==1.4.5 --python 3.11 \
  python apps/socai/prototype/cdp_controlled_tab.py --json
```

Observed result on this machine:

- Status: `controlled_tab_ready`.
- Marked tab title: `🟢 SocAI — SocAI Primitive Test`.
- Primitive checks all returned true: navigate, evaluate_js, click, type_text, press_key, scroll, capture_screenshot.
- Screenshot saved under the system temp directory, e.g. `/tmp/socai/...` or macOS temp equivalent.

Next session:

- Navigate the controlled tab to Xiaohongshu.
- Capture screenshot.
- Scroll/operate the XHS page.
- Read URL/title/runtime access.

## 2026-04-30 — Session 4: XHS technical proof

Completed:

- Added `apps/socai/prototype/cdp_xhs_probe.py`.
- Script creates a SocAI-controlled tab and navigates to `https://www.xiaohongshu.com/explore` by default.
- Captures screenshots before and after scroll.
- Reads basic runtime state: URL, title, ready state, body text length, scroll position, and simple login/security indicators.
- Does not implement XHS extraction or product functions.

Verification commands run from repo root:

```bash
python3 -m py_compile apps/socai/prototype/chrome_discovery.py apps/socai/prototype/cdp_targets.py apps/socai/prototype/cdp_controlled_tab.py apps/socai/prototype/cdp_xhs_probe.py
uv run --no-project --with cdp-use==1.4.5 --python 3.11 \
  python apps/socai/prototype/cdp_xhs_probe.py --json
```

Observed result on this machine:

- Status: `xhs_probe_ready`.
- Landed on a `xiaohongshu.com` URL in the controlled tab.
- Captured before/after screenshots showing the Xiaohongshu feed.
- Scrolled the page to `scrollY=650`.
- Runtime state was readable from the page.
- A login-related prompt/toast appeared, but it did not block the technical proof that SocAI can open, see, and operate XHS through CDP.

Next session:

- Start Session 5 only if we want the minimal Tauri shell.
- Add buttons for Connect Chrome, Create Controlled Tab, Open XHS, and Capture Screenshot.

## 2026-04-30 — Session 5: minimal Tauri shell

Completed:

- Added a new Tauri app under `apps/socai/` without reusing `desktop_app/`.
- Added TypeScript UI, CSS, Tauri config, Rust command handlers, and icons.
- UI actions added:
  - Connect Chrome
  - List Targets
  - Create Controlled Tab
  - Open XHS Probe
  - Capture Test Screenshot
- Rust commands call the prototype Python scripts and return stdout/stderr/JSON/screenshots to the UI.
- Built a macOS `.app` bundle at `apps/socai/src-tauri/target/release/bundle/macos/SocAI Prototype.app`.

Verification commands run from repo root:

```bash
cd apps/socai && npm install
cd apps/socai && npm run build
cd apps/socai/src-tauri && cargo check
cd apps/socai && npm run tauri build -- --bundles app
open "apps/socai/src-tauri/target/release/bundle/macos/SocAI Prototype.app"
```

Observed result on this machine:

- The packaged SocAI app opens successfully.
- Health/status UI renders.
- `Connect Chrome` button returned `connect_chrome — cdp_available`.
- `Create Controlled Tab` button returned `controlled_tab — controlled_tab_ready` and created a marked Chrome tab.
- The direct script-level XHS proof remains verified from Session 4.
- The packaged-app `Open XHS Probe` button is wired, but automated smoke testing still saw transient failure around Chrome's per-connection remote-debugging Allow dialog / XHS login timing. Do not mark UI-level XHS proof complete until this is fixed and re-tested.

Next session:

- Make the packaged-app XHS button reliable, or split the UI into explicit “Attach/Allow” and “Run XHS” stages so the user can approve Chrome permission before the XHS command starts.

## 2026-04-30 — Session 5 follow-up: CDP retry + UI XHS proof

Completed:

- Added `apps/socai/prototype/cdp_connect.py`: shared CDP connection helper with retry logic (4 attempts, 12s each, 1s pause between).
- Updated `cdp_targets.py`, `cdp_controlled_tab.py`, `cdp_xhs_probe.py` to use `connect_cdp_with_retry` instead of raw `CDPClient.start()`.
- Rebuilt the packaged SocAI app.
- The `Open XHS Probe` button now works end-to-end from the packaged app UI.

Verification:

- Clicked `Open XHS Probe` in the packaged SocAI app.
- Chrome showed one or more Allow dialogs; clicking Allow during the retry window allowed the connection to succeed.
- App showed `xhs_probe — xhs_probe_ready` with Exit: 0.
- Screenshots section showed 2 artifacts with XHS feed content.
- JSON section showed full diagnostics including landed URL, title `🟢 SocAI — XHS — 小红书 - 你的生活兴趣社区`, scrollY=650.

All Milestone A, B, and C tasks are now verified.

Next session:

- Session 6: demo bundle + manual checklist.
