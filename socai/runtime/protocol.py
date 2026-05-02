"""Typed JSON-RPC protocol models for the Socai Python runtime sidecar.

Pydantic is intentionally used at this process boundary because Rust/Tauri sends
untyped JSON into the Python runtime. Internal Socai domain models can remain as
standard dataclasses where that is already the local convention.
"""
from __future__ import annotations

from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field

JSONRPC_VERSION = "2.0"

# JSON-RPC 2.0 standard error codes plus the app/runtime execution code we use
# for expected method failures.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
RUNTIME_ERROR = -32000

RpcId: TypeAlias = int | str | None


class JsonRpcRequest(BaseModel):
    """Validated JSON-RPC request accepted by the sidecar."""

    model_config = ConfigDict(extra="ignore")

    jsonrpc: Literal["2.0"]
    method: str = Field(min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)
    id: RpcId = None


class JsonRpcErrorObject(BaseModel):
    """JSON-RPC error payload."""

    code: int
    message: str
    data: Any | None = None

    def to_wire(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        if payload.get("data") is None:
            payload.pop("data", None)
        return payload


class JsonRpcSuccessResponse(BaseModel):
    """JSON-RPC success response."""

    jsonrpc: Literal["2.0"] = JSONRPC_VERSION
    id: RpcId
    result: Any

    def to_wire(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class JsonRpcErrorResponse(BaseModel):
    """JSON-RPC error response."""

    jsonrpc: Literal["2.0"] = JSONRPC_VERSION
    id: RpcId
    error: JsonRpcErrorObject

    def to_wire(self) -> dict[str, Any]:
        return {"jsonrpc": self.jsonrpc, "id": self.id, "error": self.error.to_wire()}


class RuntimeEvent(BaseModel):
    """Runtime notification payload reserved for task/browser events.

    We are not streaming events heavily yet, but defining this model now keeps
    the sidecar protocol ready for future task progress, screenshot, artifact,
    reasoning, and browser-status notifications.
    """

    type: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)


class RpcError(Exception):
    """Error that should be serialized as a JSON-RPC error response."""

    def __init__(self, code: int, message: str, data: Any | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


def make_result(request_id: Any, result: Any) -> dict[str, Any]:
    return JsonRpcSuccessResponse(id=request_id, result=result).to_wire()


def make_error(request_id: Any, code: int, message: str, data: Any | None = None) -> dict[str, Any]:
    return JsonRpcErrorResponse(
        id=request_id,
        error=JsonRpcErrorObject(code=code, message=message, data=data),
    ).to_wire()
