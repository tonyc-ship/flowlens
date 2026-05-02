# Deprecated prototype diagnostics

Python CDP implementation code no longer lives under `app/prototype/`.

Use the maintained diagnostic entry points instead:

```bash
python scripts/diagnostics/chrome_cdp_discovery.py --json
python scripts/diagnostics/chrome_cdp_targets.py --json
python scripts/diagnostics/chrome_cdp_controlled_tab.py --json
python scripts/diagnostics/xhs_cdp_probe.py --json
python scripts/diagnostics/desktop_cdp_demo.py
```

Implementation modules now live in:

- `flowlens.cdp` for generic Chrome DevTools Protocol discovery, sessions, targets, pages, and controlled-tab diagnostics.
- `flowlens.platforms.xhs.cdp_diagnostics` for Xiaohongshu-specific CDP reachability/login/security diagnostics.

The Tauri app calls `flowlens.runtime` over JSON-RPC; `flowlens.runtime.service` calls those importable modules directly and does not spawn app-local prototype scripts.
