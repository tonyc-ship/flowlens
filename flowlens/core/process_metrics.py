"""Best-effort runtime resource snapshots for diagnostics."""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path


def _run_text(args: list[str], *, timeout: float = 2.0) -> str:
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except Exception:
        return ""
    if result.returncode != 0 and not result.stdout:
        return ""
    return result.stdout.strip()


def parse_size_to_mb(raw: str) -> float | None:
    text = str(raw or "").strip()
    if not text:
        return None
    match = re.match(r"(?i)^\s*([0-9.]+)\s*([kmgt]?)(?:i?b)?\s*$", text)
    if not match:
        return None
    value = float(match.group(1))
    unit = match.group(2).upper()
    if unit == "T":
        return round(value * 1024 * 1024, 2)
    if unit == "G":
        return round(value * 1024, 2)
    if unit == "M" or unit == "":
        return round(value, 2)
    if unit == "K":
        return round(value / 1024, 2)
    return None


def _rss_mb_for_pid(pid: int) -> float | None:
    output = _run_text(["ps", "-p", str(pid), "-o", "rss="])
    if not output:
        return None
    try:
        return round(int(output.strip()) / 1024, 2)
    except ValueError:
        return None


def _extract_command_flag(command: str, flag: str) -> str:
    prefix = f"{flag}="
    for token in command.split():
        if token.startswith(prefix):
            return token[len(prefix):].strip()
    return ""


def _chrome_process_kind(command: str) -> str:
    if "--type=renderer" in command:
        return "renderer_extension" if "--extension-process" in command else "renderer"
    if "--type=gpu-process" in command:
        return "gpu"
    if "--type=utility" in command:
        subtype = _extract_command_flag(command, "--utility-sub-type")
        if subtype:
            return f"utility:{subtype.split('.')[-1]}"
        return "utility"
    if "Google Chrome" in command:
        return "browser"
    return "other"


def _truncate_command(command: str, max_chars: int = 180) -> str:
    text = str(command or "").strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "... [truncated]"


def current_process_snapshot(pid: int | None = None) -> dict:
    target = int(pid or os.getpid())
    command = _run_text(["ps", "-p", str(target), "-o", "command="])
    return {
        "pid": target,
        "rss_mb": _rss_mb_for_pid(target),
        "command": command or "",
    }


def chrome_window_count() -> int | None:
    output = _run_text(
        ["/usr/bin/osascript", "-e", 'tell application "Google Chrome" to get count of windows'],
        timeout=1.5,
    )
    try:
        return int(output.strip())
    except Exception:
        return None


def chrome_tab_count() -> int | None:
    script = (
        'tell application "Google Chrome"\n'
        "set n to 0\n"
        "repeat with w in windows\n"
        "set n to n + (count of tabs of w)\n"
        "end repeat\n"
        "return n\n"
        "end tell"
    )
    output = _run_text(
        ["/usr/bin/osascript", "-e", script],
        timeout=1.5,
    )
    try:
        return int(output.strip())
    except Exception:
        return None


def chrome_process_snapshot() -> dict:
    output = _run_text(["ps", "-axo", "pid=,rss=,command="], timeout=2.5)
    main_count = 0
    helper_count = 0
    total_rss_mb = 0.0
    helper_rss_mb = 0.0
    main_rss_mb = 0.0
    process_counts_by_kind: dict[str, int] = {}
    processes: list[dict] = []
    largest_renderer_rss_mb = 0.0
    for line in output.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 3:
            continue
        pid_text, rss_kb, command = parts
        if "Google Chrome" not in command:
            continue
        try:
            pid = int(pid_text)
            rss_mb = int(rss_kb) / 1024
        except ValueError:
            continue
        total_rss_mb += rss_mb
        kind = _chrome_process_kind(command)
        process_counts_by_kind[kind] = process_counts_by_kind.get(kind, 0) + 1
        if "Google Chrome Helper" in command:
            helper_count += 1
            helper_rss_mb += rss_mb
        else:
            main_count += 1
            main_rss_mb += rss_mb
        renderer_client_id = _extract_command_flag(command, "--renderer-client-id")
        utility_sub_type = _extract_command_flag(command, "--utility-sub-type")
        if kind.startswith("renderer"):
            largest_renderer_rss_mb = max(largest_renderer_rss_mb, rss_mb)
        processes.append(
            {
                "pid": pid,
                "rss_mb": round(rss_mb, 2),
                "kind": kind,
                "renderer_client_id": int(renderer_client_id) if renderer_client_id.isdigit() else None,
                "utility_sub_type": utility_sub_type or "",
                "command_excerpt": _truncate_command(command),
            }
        )
    processes.sort(key=lambda item: item["rss_mb"], reverse=True)
    return {
        "window_count": chrome_window_count(),
        "tab_count": chrome_tab_count(),
        "main_process_count": main_count,
        "helper_process_count": helper_count,
        "total_process_count": main_count + helper_count,
        "main_rss_mb": round(main_rss_mb, 2),
        "helper_rss_mb": round(helper_rss_mb, 2),
        "total_rss_mb": round(total_rss_mb, 2),
        "largest_renderer_rss_mb": round(largest_renderer_rss_mb, 2),
        "process_counts_by_kind": process_counts_by_kind,
        "top_processes": processes[:8],
    }


def windowserver_snapshot() -> dict:
    pid_output = _run_text(["pgrep", "-x", "WindowServer"])
    try:
        pid = int(pid_output.splitlines()[0].strip())
    except Exception:
        return {}

    top_output = _run_text(
        [
            "top",
            "-l",
            "1",
            "-pid",
            str(pid),
            "-stats",
            "pid,command,mem,cpu,threads,ports,vsize,rprvt",
        ],
        timeout=3.0,
    )
    top_mem = None
    top_threads = None
    top_ports = None
    for line in top_output.splitlines():
        parts = line.split()
        if len(parts) >= 6 and parts[0] == str(pid):
            top_mem = parts[2]
            try:
                top_threads = int(parts[4])
            except ValueError:
                top_threads = None
            try:
                top_ports = int(parts[5])
            except ValueError:
                top_ports = None
            break

    return {
        "pid": pid,
        "rss_mb": _rss_mb_for_pid(pid),
        "footprint": top_mem or "",
        "footprint_mb": parse_size_to_mb(top_mem or ""),
        "threads": top_threads,
        "ports": top_ports,
    }


def observer_capture_loop_snapshot() -> dict:
    output = _run_text(["ps", "-axo", "pid=,rss=,command="], timeout=2.5)
    for line in output.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 3:
            continue
        pid_text, rss_kb, command = parts
        if " -m flowlens observer " not in command or "capture-loop" not in command:
            continue
        try:
            pid = int(pid_text)
            rss_mb = round(int(rss_kb) / 1024, 2)
        except ValueError:
            continue
        return {
            "pid": pid,
            "rss_mb": rss_mb,
            "command": command,
        }
    return {}


def system_resource_snapshot(*, agent_window_id: int | None = None) -> dict:
    snapshot = {
        "timestamp": datetime.now().isoformat(),
        "agent_window_id": agent_window_id,
        "current_process": current_process_snapshot(),
        "windowserver": windowserver_snapshot(),
        "chrome": chrome_process_snapshot(),
    }
    observer = observer_capture_loop_snapshot()
    if observer:
        snapshot["observer"] = observer
    return snapshot


def append_jsonl(path: str | Path, entry: dict) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
