"""Generic Chrome extension operational commands for FlowLens."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
from datetime import datetime
import json
from pathlib import Path
import time

from .core.bridge import ExtensionBridge
from .core.reporting import markdown_styles, render_markdown_block


@dataclass
class ExtensionOperationResult:
    operation: str
    success: bool
    port: int
    started_at: str
    finished_at: str
    duration_s: float
    logs: list[dict] = field(default_factory=list)
    output_dir: str = ""
    error: str = ""


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _safe_slug(text: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in text).strip("_").lower()


def _write_report(result: ExtensionOperationResult, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "report.json").write_text(
        json.dumps(asdict(result), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    summary_md = "\n".join([
        f"# Extension Operation: {result.operation}",
        "",
        f"- Success: `{result.success}`",
        f"- Port: `{result.port}`",
        f"- Started: `{result.started_at}`",
        f"- Finished: `{result.finished_at}`",
        f"- Duration: `{result.duration_s:.2f}s`",
        f"- Error: `{result.error or 'none'}`",
    ])
    logs_md = "\n".join(
        f"- `{entry['ts']}` `{entry['action']}` {entry['detail']}".rstrip()
        for entry in result.logs
    ) or "- No logs captured"

    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>FlowLens Extension Operation Report</title>
  <style>
    body{{font-family:ui-sans-serif,system-ui,-apple-system,sans-serif;margin:24px;background:#f7f7f5;color:#1f2937}}
    .wrap{{max-width:960px;margin:0 auto}}
    .card{{background:white;border:1px solid #e5e7eb;border-radius:12px;padding:20px;margin:0 0 16px;box-shadow:0 2px 10px rgba(15,23,42,0.04)}}
    h1{{margin:0 0 16px}}
    h2{{margin:0 0 12px;font-size:18px}}
    {markdown_styles()}
  </style>
</head>
<body>
  <div class="wrap">
    <h1>FlowLens Extension Operation Report</h1>
    <div class="card">
      <h2>Summary</h2>
      {render_markdown_block(summary_md)}
    </div>
    <div class="card">
      <h2>Bridge Logs</h2>
      {render_markdown_block(logs_md)}
    </div>
  </div>
</body>
</html>
"""
    (output_dir / "report.html").write_text(html, encoding="utf-8")


async def run_extension_reload(
    *,
    port: int = 8765,
    timeout: float = 30,
    output_dir: str | Path | None = None,
) -> ExtensionOperationResult:
    """Reload the Chrome extension through the live bridge.

    This is the preferred path because it tests the actual FlowLens runtime:
    Python bridge -> background service worker -> chrome.runtime.reload().
    """
    out_dir = Path(output_dir or Path("task_runs") / f"{_safe_slug('extension_reload')}_{_timestamp()}")
    started_at = datetime.now().isoformat(timespec="seconds")
    t0 = time.perf_counter()
    logs: list[dict] = []

    def on_log(action: str, detail: str = "") -> None:
        logs.append({
            "ts": datetime.now().isoformat(timespec="seconds"),
            "action": action,
            "detail": detail,
        })

    bridge = ExtensionBridge(port=port)
    bridge.on_log(on_log)

    success = False
    error = ""
    try:
        await bridge.start()
        await bridge.wait_for_connection(timeout=timeout)
        await bridge.reload_extension()
        success = True
    except Exception as exc:
        error = str(exc)
    finally:
        try:
            await bridge.stop()
        except Exception:
            pass

    finished_at = datetime.now().isoformat(timespec="seconds")
    result = ExtensionOperationResult(
        operation="reload",
        success=success,
        port=port,
        started_at=started_at,
        finished_at=finished_at,
        duration_s=time.perf_counter() - t0,
        logs=logs,
        output_dir=str(out_dir),
        error=error,
    )
    if output_dir is not None:
        _write_report(result, out_dir)
    return result


def run_extension_reload_sync(
    *,
    port: int = 8765,
    timeout: float = 30,
    output_dir: str | Path | None = None,
) -> ExtensionOperationResult:
    return asyncio.run(run_extension_reload(port=port, timeout=timeout, output_dir=output_dir))
