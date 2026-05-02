"""Socai Python runtime sidecar.

The Tauri desktop app talks to this package as a long-lived Python sidecar in
both development and packaged builds. The sidecar owns Socai's Python-first
browser/agent logic while Tauri owns the native window and packaging layer.
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
