"""JSON-RPC stdio entry point for the Socai desktop runtime sidecar."""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from typing import Any

from .service import RuntimeMethodError, handle_method

JSONRPC_VERSION = "2.0"


def make_result(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}


def make_error(request_id: Any, code: int, message: str, data: Any | None = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "error": error}


def write_message(message: dict[str, Any]) -> None:
    try:
        sys.stdout.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n")
        sys.stdout.flush()
    except BrokenPipeError:
        raise SystemExit(0)


def run_stdio() -> int:
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue

        request_id: Any | None = None
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                write_message(make_error(None, -32600, "Invalid Request"))
                continue

            request_id = request.get("id")
            method = request.get("method")
            params = request.get("params") or {}

            if request.get("jsonrpc") != JSONRPC_VERSION or not isinstance(method, str):
                write_message(make_error(request_id, -32600, "Invalid Request"))
                continue
            if not isinstance(params, dict):
                write_message(make_error(request_id, -32602, "Params must be an object"))
                continue

            result = handle_method(method, params)
            if request_id is not None:
                write_message(make_result(request_id, result))
            if method == "shutdown":
                return 0
        except json.JSONDecodeError as exc:
            write_message(make_error(request_id, -32700, "Parse error", str(exc)))
        except RuntimeMethodError as exc:
            write_message(make_error(request_id, -32000, str(exc)))
        except Exception as exc:  # noqa: BLE001 - JSON-RPC process boundary
            traceback.print_exc(file=sys.stderr)
            write_message(make_error(request_id, -32603, str(exc), traceback.format_exc()))

    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--transport",
        choices=("stdio",),
        default="stdio",
        help="Runtime transport. The desktop app currently uses newline-delimited JSON-RPC over stdio.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    parse_args(argv or sys.argv[1:])
    return run_stdio()


if __name__ == "__main__":
    raise SystemExit(main())
