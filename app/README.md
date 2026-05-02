# Socai Desktop App

`app/` is the active Socai desktop application. It is a Tauri 2 app with a Vite/TypeScript frontend, a Rust native shell, and a long-lived Python `socai.runtime` sidecar.

## Architecture

```text
Tauri WebView frontend (Vite + TypeScript)
        ↓ invoke/events
Rust Tauri shell
        ↓ newline-delimited JSON-RPC over stdio
Python socai.runtime sidecar
        ↓
socai.cdp browser backend / task runtime
        ↓
User's existing Google Chrome profile
```

The app currently uses Chrome's remote-debugging / Chrome DevTools Protocol path to create a clearly marked `🟢 Socai` Chrome tab. Generic CDP discovery, target-listing, controlled-tab, and page primitives live in `socai.cdp`; Xiaohongshu CDP probe logic lives in `socai.platforms.xhs.cdp_diagnostics`. `app/prototype/` is deprecated and points to maintained wrappers under `scripts/diagnostics/`.

## Frontend

- Vite 6
- TypeScript
- Plain DOM rendering in `src/main.ts`
- Styles in `src/styles.css`
- Tauri API calls via `@tauri-apps/api/core`

## Native shell

- Tauri 2
- Rust commands in `src-tauri/src/lib.rs`
- Product name: `Socai`
- Bundle identifier: `com.tonycship.socai`

Rust owns native desktop concerns: app window lifecycle, opening Chrome's setup page, starting/stopping the Python sidecar, and converting local screenshot artifacts into data URLs for the WebView.

## Python runtime

The desktop runtime entry point is:

```bash
python -m socai.runtime --transport stdio
```

The sidecar speaks newline-delimited JSON-RPC over stdio, with Pydantic models validating the Python runtime protocol boundary. In development the Tauri shell launches it from the source tree. Packaged builds are prepared to prefer a bundled runtime at `Socai.app/Contents/Resources/socai-runtime/bin/python3` when that runtime exists.

Manual sidecar smoke test from the repository root:

```bash
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"health","params":{}}' \
  '{"jsonrpc":"2.0","id":2,"method":"shutdown","params":{}}' \
  | .venv/bin/python -m socai.runtime --transport stdio
```

## Chrome connection flow

1. Socai opens `chrome://inspect/#remote-debugging` in Google Chrome.
2. The user approves/enables Chrome remote debugging.
3. Socai discovers the local CDP endpoint for the existing Chrome profile.
4. Socai creates a clearly marked `🟢 Socai` tab.
5. Browser diagnostics and task runs operate in that controlled tab.

## Development

From `app/`:

```bash
pnpm install
pnpm exec tauri dev
```

The Vite dev server runs on port `1421`, as configured in `vite.config.ts` and `src-tauri/tauri.conf.json`.

Useful runtime environment variables:

```bash
SOCAI_PYTHON=/path/to/python3              # Python used for source-tree sidecar
SOCAI_DESKTOP_RUNTIME_PYTHON=/path/python  # Explicit sidecar Python override
SOCAI_DESKTOP_USE_UV_FOR_CDP=0             # Do not use uv for CDP diagnostic scripts
SOCAI_REPO_ROOT=/path/to/socai             # Override repository root for sidecar scripts
```

## Build and install locally

Preferred path from the repository root:

```bash
bash scripts/build_app.sh
```

This builds the Tauri `.app` bundle and installs it at:

```text
/Applications/Socai.app
```

For manual packaging from `app/`:

```bash
pnpm install --frozen-lockfile
pnpm run build
pnpm exec tauri build --bundles app
open "src-tauri/target/release/bundle/macos/Socai.app"
```

## Production packaging direction

Real-user builds should ship as a signed and notarized macOS app/DMG with no dependency on a cloned repo, user-installed Python, or `uv`.

Target production shape:

```text
Socai.app
  Contents/MacOS/Socai                 # Tauri/Rust binary
  Contents/Resources/frontend          # Built WebView assets
  Contents/Resources/socai-runtime/    # Bundled Python runtime + Socai package
```

Large optional local models should be downloaded into Application Support rather than bundled into the base app.
