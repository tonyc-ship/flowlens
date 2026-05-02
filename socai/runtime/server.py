"""JSON-RPC stdio server for the Socai Python runtime sidecar."""
from __future__ import annotations

import json
import sys
import traceback
from typing import Any, TextIO

from pydantic import ValidationError

from .protocol import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    JSONRPC_VERSION,
    PARSE_ERROR,
    RUNTIME_ERROR,
    JsonRpcRequest,
    RpcError,
    make_error,
    make_result,
)
from .service import RuntimeMethodError, handle_method


def write_message(message: dict[str, Any], stdout: TextIO = sys.stdout) -> None:
    """Write one newline-delimited JSON-RPC message.

    stdout is protocol-only. Runtime logs should go to stderr or log files so
    the Rust parent can parse stdout safely.
    """

    try:
        stdout.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n")
        stdout.flush()
    except BrokenPipeError:
        raise SystemExit(0)


def run_stdio(
    stdin: TextIO = sys.stdin,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """Run the newline-delimited JSON-RPC loop over stdio."""

    for raw_line in stdin:
        line = raw_line.strip()
        if not line:
            continue

        request_id: Any | None = None
        try:
            raw_request = json.loads(line)
            if not isinstance(raw_request, dict):
                write_message(make_error(None, INVALID_REQUEST, "Invalid Request"), stdout)
                continue

            request_id = raw_request.get("id")
            if (
                "params" in raw_request
                and raw_request["params"] is not None
                and not isinstance(raw_request["params"], dict)
            ):
                write_message(make_error(request_id, INVALID_PARAMS, "Params must be an object"), stdout)
                continue

            request = JsonRpcRequest.model_validate(raw_request)

            result = handle_method(request.method, request.params)
            if request.id is not None:
                write_message(make_result(request.id, result), stdout)
            if request.method == "shutdown":
                return 0
        except json.JSONDecodeError as exc:
            write_message(make_error(request_id, PARSE_ERROR, "Parse error", str(exc)), stdout)
        except ValidationError as exc:
            write_message(make_error(request_id, INVALID_REQUEST, "Invalid Request", exc.errors()), stdout)
        except RuntimeMethodError as exc:
            write_message(make_error(request_id, RUNTIME_ERROR, str(exc)), stdout)
        except RpcError as exc:
            write_message(make_error(request_id, exc.code, exc.message, exc.data), stdout)
        except Exception as exc:  # noqa: BLE001 - JSON-RPC process boundary
            traceback.print_exc(file=stderr)
            write_message(make_error(request_id, INTERNAL_ERROR, str(exc), traceback.format_exc()), stdout)

    return 0


def runtime_ready_event() -> dict[str, Any]:
    """Return a lightweight runtime-ready notification for future event wiring."""

    return {
        "jsonrpc": JSONRPC_VERSION,
        "method": "runtime.event",
        "params": {"type": "runtime_ready", "payload": {}},
    }
