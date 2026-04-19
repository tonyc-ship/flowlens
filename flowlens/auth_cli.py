"""Authentication helpers for hosted LLM providers.

Provides both a programmatic argparse interface and an interactive menu
(``flowlens auth`` with no subcommand) that guides users through credential
setup with arrow-key navigation.
"""

from __future__ import annotations

import argparse
import getpass
import subprocess
import sys

from .core.auth import (
    CLOUD_PROVIDERS,
    METHOD_API_KEY,
    METHOD_OAUTH,
    PROVIDER_ANTHROPIC,
    PROVIDER_OPENAI,
    PROVIDERS,
    available_provider_statuses,
    clear_auth_secret,
    provider_config,
    save_auth_secret,
)


# ---------------------------------------------------------------------------
# Tiny terminal menu (arrow keys, no external dependency)
# ---------------------------------------------------------------------------

def _read_key() -> str:
    """Read a single keypress, returning special names for arrow keys / enter."""
    import tty
    import termios

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\r" or ch == "\n":
            return "enter"
        if ch == "\x1b":
            seq = sys.stdin.read(2)
            if seq == "[A":
                return "up"
            if seq == "[B":
                return "down"
            return "esc"
        if ch == "\x03":
            raise KeyboardInterrupt
        if ch == "\x04":
            return "esc"
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _pick(prompt: str, options: list[str], *, show_status: str = "") -> int | None:
    """Show an arrow-key menu. Returns the selected index, or None on ESC/q."""
    cursor = 0
    while True:
        # Clear and redraw
        sys.stderr.write("\033[2J\033[H")  # clear screen, cursor to top
        if show_status:
            sys.stderr.write(show_status + "\n")
        sys.stderr.write(f"\n{prompt}\n\n")
        for i, opt in enumerate(options):
            marker = "❯ " if i == cursor else "  "
            sys.stderr.write(f"  {marker}{opt}\n")
        sys.stderr.write("\n  (↑/↓ to move, Enter to select, q to quit)\n")
        sys.stderr.flush()

        key = _read_key()
        if key == "up":
            cursor = (cursor - 1) % len(options)
        elif key == "down":
            cursor = (cursor + 1) % len(options)
        elif key == "enter":
            return cursor
        elif key in ("esc", "q"):
            return None


def _clear_screen() -> None:
    sys.stderr.write("\033[2J\033[H")
    sys.stderr.flush()


# ---------------------------------------------------------------------------
# Status rendering
# ---------------------------------------------------------------------------

def _status_banner() -> str:
    lines: list[str] = ["  Current credentials:\n"]
    for s in available_provider_statuses():
        config = provider_config(s.provider)
        name = config.display_name if config else s.provider
        parts: list[str] = []
        if s.api_key_available:
            parts.append(f"API Key ✓ ({s.api_key_source})")
        if s.oauth_available or s.oauth_logged_in:
            src = s.oauth_source or ("logged in" if s.oauth_logged_in else "")
            parts.append(f"OAuth ✓ ({src})" if src else "OAuth ✓")
        status = ", ".join(parts) if parts else "not configured"
        lines.append(f"  {name}: {status}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Interactive flow
# ---------------------------------------------------------------------------

def _prompt_api_key(provider_label: str) -> str | None:
    _clear_screen()
    sys.stderr.write(f"\n  Paste your {provider_label} API key below.\n")
    sys.stderr.write("  (input is hidden)\n\n")
    sys.stderr.flush()
    try:
        key = getpass.getpass(f"  {provider_label} API Key: ")
    except (EOFError, KeyboardInterrupt):
        return None
    return key.strip() or None


def _run_codex_oauth() -> bool:
    import shutil

    _clear_screen()
    codex_path = shutil.which("codex")
    if not codex_path:
        sys.stderr.write(
            "\n  `codex` CLI not found.\n\n"
            "  Install it first:\n"
            "    npm install -g @openai/codex\n\n"
            "  Or use 'Set OpenAI API Key' instead.\n"
        )
        sys.stderr.flush()
        return False

    sys.stderr.write("\n  Starting Codex OAuth device login...\n\n")
    sys.stderr.flush()
    # Run codex with full stdio inheritance so it can interact with the terminal
    code = subprocess.call(
        [codex_path, "login", "--device-auth"],
        stdin=sys.stdin,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )
    return code == 0


def _interactive() -> int:
    """Main interactive auth menu."""
    if not sys.stdin.isatty():
        sys.stderr.write("Interactive mode requires a terminal. Use subcommands instead.\n")
        return 1

    # Menu entries built dynamically from the provider registry so new
    # providers show up without touching this function.
    api_key_entries: list[tuple[str, str]] = [
        (PROVIDERS[name].display_name, name) for name in CLOUD_PROVIDERS
    ]

    while True:
        banner = _status_banner()
        options: list[str] = [f"Set {label} API Key" for label, _ in api_key_entries]
        options.append("Login with OpenAI OAuth (opens browser, requires codex CLI)")
        options.append("Clear a credential")
        options.append("Exit")

        choice = _pick("What would you like to do?", options, show_status=banner)

        if choice is None or choice == len(options) - 1:
            _clear_screen()
            return 0

        if choice < len(api_key_entries):
            label, provider = api_key_entries[choice]
            key = _prompt_api_key(label)
            if key:
                save_auth_secret(provider, METHOD_API_KEY, key)
                _clear_screen()
                sys.stderr.write(f"\n  ✓ {label} API Key saved.\n")
                sys.stderr.flush()
                _wait_enter()

        elif choice == len(api_key_entries):
            ok = _run_codex_oauth()
            _clear_screen()
            if ok:
                sys.stderr.write("\n  ✓ OpenAI OAuth login succeeded.\n")
            else:
                sys.stderr.write("\n  ✗ OpenAI OAuth login failed. Is `codex` installed?\n")
            sys.stderr.flush()
            _wait_enter()

        elif choice == len(api_key_entries) + 1:
            creds: list[tuple[str, str, str] | str] = [
                (f"{PROVIDERS[name].display_name} API Key", name, METHOD_API_KEY)
                for name in CLOUD_PROVIDERS
            ]
            creds.append(("OpenAI OAuth token", PROVIDER_OPENAI, METHOD_OAUTH))
            creds.append("Back")
            labels = [c[0] if isinstance(c, tuple) else c for c in creds]
            pick = _pick("Which credential to clear?", labels)
            if pick is not None and pick < len(creds) - 1:
                entry = creds[pick]
                assert isinstance(entry, tuple)
                clear_auth_secret(entry[1], entry[2])
                _clear_screen()
                sys.stderr.write(f"\n  ✓ {entry[0]} cleared.\n")
                sys.stderr.flush()
                _wait_enter()


def _wait_enter() -> None:
    sys.stderr.write("\n  Press Enter to continue...")
    sys.stderr.flush()
    try:
        input()
    except (EOFError, KeyboardInterrupt):
        pass


# ---------------------------------------------------------------------------
# Argparse CLI (kept for scripting / CI usage)
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="flowlens auth",
        description="Inspect or store FlowLens hosted-model credentials.",
    )
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("status", help="Show discovered Anthropic / OpenAI credentials.")

    provider_choices = list(CLOUD_PROVIDERS)

    login = subparsers.add_parser("login", help="Open the official provider login flow when available.")
    login.add_argument("provider", choices=provider_choices)
    login.add_argument("method", choices=[METHOD_API_KEY, METHOD_OAUTH])

    set_parser = subparsers.add_parser("set", help="Persist a credential into ~/.flowlens/auth.json.")
    set_parser.add_argument("provider", choices=provider_choices)
    set_parser.add_argument("method", choices=[METHOD_API_KEY, METHOD_OAUTH])
    set_parser.add_argument(
        "--value",
        default="",
        help="Credential value. If omitted, FlowLens reads from stdin.",
    )

    clear_parser = subparsers.add_parser("clear", help="Remove a stored credential from ~/.flowlens/auth.json.")
    clear_parser.add_argument("provider", choices=provider_choices)
    clear_parser.add_argument("method", choices=[METHOD_API_KEY, METHOD_OAUTH])

    return parser


def _render_status() -> int:
    for status in available_provider_statuses():
        print(f"[{status.provider}]")
        print(f"api_key_available={str(status.api_key_available).lower()}")
        if status.api_key_source:
            print(f"api_key_source={status.api_key_source}")
        print(f"oauth_available={str(status.oauth_available).lower()}")
        if status.oauth_source:
            print(f"oauth_source={status.oauth_source}")
        print(f"oauth_logged_in={str(status.oauth_logged_in).lower()}")
        if status.oauth_login_hint:
            print(f"oauth_login_hint={status.oauth_login_hint}")
        print("")
    return 0


def _run_login(provider: str, method: str) -> int:
    if provider == PROVIDER_OPENAI and method == METHOD_OAUTH:
        return subprocess.call(["codex", "login", "--device-auth"])
    if provider == PROVIDER_OPENAI and method == METHOD_API_KEY:
        print("Use `printenv OPENAI_API_KEY | codex login --with-api-key` or `flowlens auth set openai api_key`.")
        return 0
    if provider == PROVIDER_ANTHROPIC and method == METHOD_OAUTH:
        return subprocess.call(["claude", "auth", "login"])

    # All other providers (including Chinese vendors) only support api_key.
    config = provider_config(provider)
    if config is not None:
        hint = config.env_var_hint or f"{provider.upper()}_API_KEY"
        print(
            f"{config.display_name} only supports API keys. "
            f"Use `flowlens auth set {provider} api_key` or set ${hint}."
        )
        return 0
    print(f"Unknown provider: {provider}")
    return 2


def _set_secret(provider: str, method: str, value: str) -> int:
    secret = value.strip() if value else sys.stdin.read().strip()
    if not secret:
        raise SystemExit("Missing credential value. Pass --value or pipe it through stdin.")
    path = save_auth_secret(provider, method, secret)
    print(f"saved={path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    raw = list(argv or [])

    # No subcommand → interactive menu
    if not raw or (len(raw) == 1 and raw[0] in ("-h", "--help")):
        if not raw:
            return _interactive()

    parser = build_parser()
    args = parser.parse_args(raw)

    if args.command is None:
        return _interactive()
    if args.command == "status":
        return _render_status()
    if args.command == "login":
        return _run_login(args.provider, args.method)
    if args.command == "set":
        return _set_secret(args.provider, args.method, args.value)
    if args.command == "clear":
        path = clear_auth_secret(args.provider, args.method)
        print(f"cleared={path or 'none'}")
        return 0

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
