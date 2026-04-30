# SocAI Prototype

SocAI is a new desktop-app prototype for social-platform agent tasks. The first vertical is Xiaohongshu/XHS, but the first prototype does **not** implement XHS product functions yet.

## Prototype decisions

- App path: `apps/socai/`
- Desktop shell: Tauri, added later after CDP is proven
- Browser target: the user's existing Google Chrome profile on macOS
- Permission path: Chrome inspect / remote-debugging permission flow
- CDP client: `cdp-use`, starting in Session 2
- Controlled target: a newly created and clearly marked SocAI Chrome tab
- No fallback browser modes
- No LLM, MCP, Chrome extension backend, managed profile, or full XHS tools in the first proof

## Session plan

1. **Session 1 — prototype scaffold + Chrome discovery**
   - Create this prototype folder.
   - Add a Chrome discovery script.
   - Verify whether the existing Chrome profile exposes a CDP endpoint or needs setup.

2. **Session 2 — CDP attach + target listing**
   - Add `cdp-use`.
   - Connect to Chrome CDP.
   - Call `Target.getTargets`.

3. **Session 3 — controlled tab + primitives**
   - Create a new SocAI tab.
   - Mark its title.
   - Add navigate, evaluate, screenshot, scroll/click/key basics.

4. **Session 4 — XHS technical proof**
   - Open Xiaohongshu in the controlled tab.
   - Capture screenshot.
   - Prove SocAI can operate the page.

5. **Session 5 — minimal Tauri shell**
   - Add the UI after CDP/XHS proof works from scripts.

6. **Session 6 — demo bundle + checklist**
   - Save screenshots, timing, diagnostics, and manual demo steps.

## Session 1 command

Run from the repository root:

```bash
python3 apps/socai/prototype/chrome_discovery.py
```

Machine-readable output:

```bash
python3 apps/socai/prototype/chrome_discovery.py --json
```

For local tests, `SOCAI_CHROME_USER_DATA_DIR` can point at a custom Chrome user-data root. Set `SOCAI_CHROME_USER_DATA_DIR_ONLY=1` to skip default profile paths.

If Chrome CDP is not available, open the inspect permission page:

```bash
python3 apps/socai/prototype/chrome_discovery.py --open-inspect
```

Expected result is one of:

- `cdp_available` — a local Chrome CDP endpoint was discovered.
- `setup_required` — SocAI needs the user to open/approve Chrome inspect remote debugging and re-run discovery.

The script only discovers the endpoint. It does not attach to Chrome or control pages yet.

## Session 2 command

Run from the repository root with the prototype dependency supplied by `uv`:

```bash
uv run --no-project --with cdp-use==1.4.5 --python 3.11 \
  python apps/socai/prototype/cdp_targets.py
```

Machine-readable output:

```bash
uv run --no-project --with cdp-use==1.4.5 --python 3.11 \
  python apps/socai/prototype/cdp_targets.py --json
```

If Chrome shows an **Allow remote debugging?** dialog, click **Allow** while the command is still running. In the current Chrome permission flow, one dialog can appear per connection attempt during the prototype.

Expected result:

- `connected` — SocAI connected to Chrome CDP and called `Target.getTargets`.
- `setup_required` or `connection_failed` — open/approve `chrome://inspect/#remote-debugging` and retry.

## Session 3 command

Create a marked SocAI-controlled tab and exercise the minimal browser primitives:

```bash
uv run --no-project --with cdp-use==1.4.5 --python 3.11 \
  python apps/socai/prototype/cdp_controlled_tab.py
```

Machine-readable output:

```bash
uv run --no-project --with cdp-use==1.4.5 --python 3.11 \
  python apps/socai/prototype/cdp_controlled_tab.py --json
```

Expected result:

- `controlled_tab_ready` — SocAI created a new tab, marked its title with `🟢 SocAI`, and verified navigate/evaluate/click/type/key/scroll/screenshot primitives.

## Session 4 command

Open Xiaohongshu in a SocAI-controlled tab and prove basic operation:

```bash
uv run --no-project --with cdp-use==1.4.5 --python 3.11 \
  python apps/socai/prototype/cdp_xhs_probe.py
```

Machine-readable output:

```bash
uv run --no-project --with cdp-use==1.4.5 --python 3.11 \
  python apps/socai/prototype/cdp_xhs_probe.py --json
```

Expected result:

- `xhs_probe_ready` — SocAI opened a Xiaohongshu URL in the controlled tab, captured screenshots, scrolled the page, and read basic runtime state.
- `xhs_probe_inconclusive` — the script ran but could not confirm the landed URL/screenshot diagnostics.

## Session 5 command

Build and open the minimal Tauri shell:

```bash
cd apps/socai
npm install
npm run build
npm run tauri build -- --bundles app
open "src-tauri/target/release/bundle/macos/SocAI Prototype.app"
```

Smoke-tested UI actions:

- `Connect Chrome` → expected status `connect_chrome — cdp_available`.
- `Create Controlled Tab` → expected status `controlled_tab — controlled_tab_ready` and a marked Chrome tab.

Known Session 5 follow-up:

- `Open XHS Probe` is wired to the Python XHS proof script, but the packaged-app UI path needs one more reliability pass around Chrome's per-connection **Allow remote debugging?** dialog / XHS login timing before it is marked complete. The script-level XHS proof remains verified via the Session 4 command above.
