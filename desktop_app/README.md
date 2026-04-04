# FlowLens Desktop

Minimal Tauri 2.x desktop shell for FlowLens.

This spike intentionally keeps the UI small:

- Overview page
- Task launcher placeholder
- Live run placeholder
- Settings placeholder
- A simple Rust `app_health` command invoked from the frontend

## Run

```bash
npm install
npm run tauri dev
```

## Build

```bash
npm run build
cargo check --manifest-path src-tauri/Cargo.toml
```
