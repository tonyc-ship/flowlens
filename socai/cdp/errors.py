"""Shared CDP error and diagnostic helpers."""
from __future__ import annotations

CDP_USE_VERSION = "1.4.5"


def exception_message(exc: BaseException) -> str:
    """Return a stable human-readable exception message."""

    message = str(exc).strip()
    if message:
        return message
    return exc.__class__.__name__


def add_chrome_permission_hint(message: str, inspect_url: str) -> str:
    """Append Chrome remote-debugging permission guidance for handshake-like errors."""

    lower = message.lower()
    if not any(token in lower for token in ("handshake", "timeout", "allow", "403")):
        return message
    return (
        f"{message}\nOpen {inspect_url}, approve Chrome remote-debugging/inspect permission, then retry. "
        "Chrome may show one Allow dialog per connection attempt."
    )


def cdp_use_install_help(command_hint: str = "python scripts/diagnostics/chrome_cdp_targets.py") -> str:
    """Return install guidance for the cdp-use dependency.

    ``command_hint`` may be either a full command (``python ...``) or a script
    path retained from older diagnostics.
    """

    uv_command = command_hint if command_hint.startswith("python ") else f"python {command_hint}"
    return "\n".join(
        [
            "Missing Python package: cdp-use",
            "Install Socai runtime dependencies:",
            "  uv sync",
            "Or run the diagnostic with uv without changing the project environment:",
            f"  uv run --no-project --with cdp-use=={CDP_USE_VERSION} --python 3.11 {uv_command}",
        ]
    )


class CDPConnectionError(RuntimeError):
    """Raised when a CDP connection or command fails."""


class CDPDependencyError(RuntimeError):
    """Raised when an optional CDP dependency is missing."""
