"""Primary CLI for Socai."""

from __future__ import annotations

import sys

from .auth_cli import main as auth_main
from .run_cli import main as run_main
from .xhs_cli import main as xhs_main


def _lazy_import(module_path: str, attr: str):
    """Import a CLI main function lazily, with a friendly error on missing deps."""
    def wrapper(argv):
        try:
            mod = __import__(module_path, fromlist=[attr])
        except ImportError as exc:
            print(f"\n错误: 缺少依赖 — {exc}\n")
            print("该功能需要额外依赖，请运行:")
            print("  pip install -e '.[all]'\n")
            return 1
        return getattr(mod, attr)(argv)
    return wrapper

extension_main = _lazy_import("socai.extension_cli", "main")
debug_main = _lazy_import("socai.debug_cli", "main")
observer_main = _lazy_import("socai.observer.cli", "main")


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "auth":
        raise SystemExit(auth_main(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "xhs":
        raise SystemExit(xhs_main(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "extension":
        raise SystemExit(extension_main(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "debug":
        raise SystemExit(debug_main(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "observer":
        raise SystemExit(observer_main(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "run":
        raise SystemExit(run_main(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "agent":
        raise SystemExit(run_main(sys.argv[2:]))
    if len(sys.argv) > 2 and sys.argv[1] == "desktop" and sys.argv[2] == "run":
        raise SystemExit(run_main(sys.argv[3:]))
    if len(sys.argv) > 1 and sys.argv[1] == "desktop":
        raise SystemExit(run_main(sys.argv[2:]))
    # Default: free-form prompt → unified run loop
    raise SystemExit(run_main(sys.argv[1:]))
