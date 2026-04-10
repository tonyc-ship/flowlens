"""Credential discovery and model selection for hosted LLM providers."""

from __future__ import annotations

import json
import os
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .runtime import load_runtime_env


PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_OPENAI = "openai"
METHOD_API_KEY = "api_key"
METHOD_OAUTH = "oauth"

FLOWLENS_DIR = Path.home() / ".flowlens"
FLOWLENS_AUTH_FILE = FLOWLENS_DIR / "auth.json"
CODEX_AUTH_FILE = Path.home() / ".codex" / "auth.json"
CODEX_CONFIG_FILE = Path.home() / ".codex" / "config.toml"

DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-6"
DEFAULT_OPENAI_MODEL = "gpt-5.4"


@dataclass(frozen=True)
class ResolvedCredential:
    provider: str
    method: str
    secret: str
    source: str


@dataclass(frozen=True)
class ProviderStatus:
    provider: str
    api_key_available: bool
    api_key_source: str = ""
    oauth_available: bool = False
    oauth_source: str = ""
    oauth_logged_in: bool = False
    oauth_login_hint: str = ""

    @property
    def available(self) -> bool:
        return self.api_key_available or self.oauth_available


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _flowlens_auth_config() -> dict:
    if not FLOWLENS_AUTH_FILE.exists():
        return {}
    data = _read_json(FLOWLENS_AUTH_FILE)
    return data if isinstance(data, dict) else {}


def save_auth_secret(provider: str, method: str, secret: str) -> Path:
    FLOWLENS_DIR.mkdir(parents=True, exist_ok=True)
    data = _flowlens_auth_config()
    provider_block = dict(data.get(provider) or {})
    key = "api_key" if method == METHOD_API_KEY else "auth_token"
    provider_block[key] = secret.strip()
    data[provider] = provider_block
    FLOWLENS_AUTH_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    try:
        os.chmod(FLOWLENS_AUTH_FILE, 0o600)
    except OSError:
        pass
    return FLOWLENS_AUTH_FILE


def clear_auth_secret(provider: str, method: str) -> Path | None:
    if not FLOWLENS_AUTH_FILE.exists():
        return None
    data = _flowlens_auth_config()
    provider_block = dict(data.get(provider) or {})
    key = "api_key" if method == METHOD_API_KEY else "auth_token"
    provider_block.pop(key, None)
    if provider_block:
        data[provider] = provider_block
    else:
        data.pop(provider, None)
    FLOWLENS_AUTH_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    try:
        os.chmod(FLOWLENS_AUTH_FILE, 0o600)
    except OSError:
        pass
    return FLOWLENS_AUTH_FILE


def _config_secret(provider: str, key: str) -> str:
    block = _flowlens_auth_config().get(provider) or {}
    value = str(block.get(key) or "").strip()
    return value


def _codex_auth() -> dict:
    if not CODEX_AUTH_FILE.exists():
        return {}
    data = _read_json(CODEX_AUTH_FILE)
    return data if isinstance(data, dict) else {}


def _codex_access_token() -> str:
    tokens = _codex_auth().get("tokens") or {}
    if not isinstance(tokens, dict):
        return ""
    return str(tokens.get("access_token") or "").strip()


def _codex_api_key() -> str:
    return str(_codex_auth().get("OPENAI_API_KEY") or "").strip()


def _run_json_command(argv: list[str]) -> dict:
    try:
        result = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return {}
    output = (result.stdout or result.stderr or "").strip()
    if not output:
        return {}
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def claude_auth_status() -> dict:
    return _run_json_command(["claude", "auth", "status"])


def codex_login_status() -> dict:
    output = _run_json_command(["codex", "login", "status"])
    return output if isinstance(output, dict) else {}


def discover_credential(
    provider: str,
    method: str,
    *,
    allow_fallback: bool = True,
) -> ResolvedCredential | None:
    load_runtime_env()
    provider = str(provider or "").strip().lower()
    method = str(method or "").strip().lower()

    env_sources: list[tuple[str, str]] = []
    config_sources: list[tuple[str, str]] = []
    file_sources: list[tuple[str, str]] = []

    if provider == PROVIDER_ANTHROPIC and method == METHOD_API_KEY:
        env_sources.append(("ANTHROPIC_API_KEY", os.environ.get("ANTHROPIC_API_KEY", "")))
        config_sources.append(("flowlens auth file", _config_secret(provider, "api_key")))
    elif provider == PROVIDER_ANTHROPIC and method == METHOD_OAUTH:
        env_sources.append(("ANTHROPIC_AUTH_TOKEN", os.environ.get("ANTHROPIC_AUTH_TOKEN", "")))
        config_sources.append(("flowlens auth file", _config_secret(provider, "auth_token")))
    elif provider == PROVIDER_OPENAI and method == METHOD_API_KEY:
        env_sources.append(("OPENAI_API_KEY", os.environ.get("OPENAI_API_KEY", "")))
        config_sources.append(("flowlens auth file", _config_secret(provider, "api_key")))
        file_sources.append((str(CODEX_AUTH_FILE), _codex_api_key()))
    elif provider == PROVIDER_OPENAI and method == METHOD_OAUTH:
        env_sources.append(("OPENAI_AUTH_TOKEN", os.environ.get("OPENAI_AUTH_TOKEN", "")))
        config_sources.append(("flowlens auth file", _config_secret(provider, "auth_token")))
        file_sources.append((str(CODEX_AUTH_FILE), _codex_access_token()))

    for source, value in [*env_sources, *config_sources, *file_sources]:
        secret = str(value or "").strip()
        if secret:
            return ResolvedCredential(provider=provider, method=method, secret=secret, source=source)

    if allow_fallback and method == METHOD_OAUTH and provider == PROVIDER_OPENAI:
        api_key = discover_credential(provider, METHOD_API_KEY, allow_fallback=False)
        if api_key:
            return api_key
    return None


def provider_status(provider: str) -> ProviderStatus:
    provider = str(provider or "").strip().lower()

    if provider == PROVIDER_ANTHROPIC:
        api = discover_credential(provider, METHOD_API_KEY, allow_fallback=False)
        oauth = discover_credential(provider, METHOD_OAUTH, allow_fallback=False)
        status = claude_auth_status()
        logged_in = bool(status.get("loggedIn"))
        return ProviderStatus(
            provider=provider,
            api_key_available=api is not None,
            api_key_source=api.source if api else "",
            oauth_available=oauth is not None,
            oauth_source=oauth.source if oauth else "",
            oauth_logged_in=logged_in,
            oauth_login_hint="claude auth login" if not logged_in else "",
        )

    api = discover_credential(PROVIDER_OPENAI, METHOD_API_KEY, allow_fallback=False)
    oauth = discover_credential(PROVIDER_OPENAI, METHOD_OAUTH, allow_fallback=False)
    codex_state = codex_login_status()
    logged_in = bool(codex_state.get("logged_in") or oauth is not None)
    return ProviderStatus(
        provider=PROVIDER_OPENAI,
        api_key_available=api is not None,
        api_key_source=api.source if api else "",
        oauth_available=oauth is not None,
        oauth_source=oauth.source if oauth else "",
        oauth_logged_in=logged_in,
        oauth_login_hint="codex login --device-auth" if not logged_in else "",
    )


def available_provider_statuses() -> list[ProviderStatus]:
    return [
        provider_status(PROVIDER_ANTHROPIC),
        provider_status(PROVIDER_OPENAI),
    ]


def preferred_provider() -> str | None:
    load_runtime_env()
    explicit = str(os.environ.get("FLOWLENS_MODEL_PROVIDER", "")).strip().lower()
    if explicit in {PROVIDER_ANTHROPIC, PROVIDER_OPENAI}:
        return explicit
    block = _flowlens_auth_config().get("defaults") or {}
    configured = str(block.get("provider") or "").strip().lower()
    if configured in {PROVIDER_ANTHROPIC, PROVIDER_OPENAI}:
        return configured
    return None


def preferred_auth_method() -> str | None:
    load_runtime_env()
    explicit = str(os.environ.get("FLOWLENS_AUTH_METHOD", "")).strip().lower()
    if explicit in {METHOD_API_KEY, METHOD_OAUTH}:
        return explicit
    block = _flowlens_auth_config().get("defaults") or {}
    configured = str(block.get("method") or "").strip().lower()
    if configured in {METHOD_API_KEY, METHOD_OAUTH}:
        return configured
    return None


def default_model_for_provider(provider: str) -> str:
    load_runtime_env()
    provider = str(provider or "").strip().lower()
    config = _flowlens_auth_config().get("defaults") or {}

    if provider == PROVIDER_OPENAI:
        explicit = str(os.environ.get("FLOWLENS_OPENAI_MODEL", "") or os.environ.get("OPENAI_MODEL", "")).strip()
        if explicit:
            return explicit
        configured = str(config.get("openai_model") or "").strip()
        if configured:
            return configured
        if CODEX_CONFIG_FILE.exists():
            try:
                parsed = tomllib.loads(CODEX_CONFIG_FILE.read_text(encoding="utf-8"))
            except Exception:
                parsed = {}
            model = str(parsed.get("model") or "").strip()
            if model:
                return model
        return DEFAULT_OPENAI_MODEL

    explicit = str(os.environ.get("FLOWLENS_ANTHROPIC_MODEL", "")).strip()
    if explicit:
        return explicit
    configured = str(config.get("anthropic_model") or "").strip()
    if configured:
        return configured
    return DEFAULT_ANTHROPIC_MODEL


def resolve_model_provider(model: str | None) -> str:
    normalized = str(model or "").strip()
    if normalized == "qwen-local" or normalized.startswith("Qwen"):
        return "local"
    if normalized == "ui-tars-local" or normalized.startswith("UI-TARS"):
        return "local"
    lowered = normalized.lower()
    if lowered.startswith(("gpt-", "o1", "o3", "o4")) or lowered.startswith("gpt5") or lowered.startswith("gpt-5"):
        return PROVIDER_OPENAI
    if lowered.startswith(("claude-", "sonnet", "opus", "haiku")):
        return PROVIDER_ANTHROPIC
    preferred = preferred_provider()
    if preferred:
        return preferred
    if provider_status(PROVIDER_ANTHROPIC).available:
        return PROVIDER_ANTHROPIC
    if provider_status(PROVIDER_OPENAI).available:
        return PROVIDER_OPENAI
    return PROVIDER_ANTHROPIC


def default_cloud_model(*, provider: str | None = None) -> str:
    chosen_provider = provider or preferred_provider()
    if chosen_provider in {PROVIDER_ANTHROPIC, PROVIDER_OPENAI}:
        return default_model_for_provider(chosen_provider)
    anth = provider_status(PROVIDER_ANTHROPIC)
    if anth.available:
        return default_model_for_provider(PROVIDER_ANTHROPIC)
    openai = provider_status(PROVIDER_OPENAI)
    if openai.available:
        return default_model_for_provider(PROVIDER_OPENAI)
    return default_model_for_provider(PROVIDER_ANTHROPIC)


def resolve_provider_auth(
    provider: str,
    *,
    preferred_method_name: str | None = None,
) -> ResolvedCredential | None:
    method = preferred_method_name or preferred_auth_method()
    methods = [method] if method in {METHOD_API_KEY, METHOD_OAUTH} else []
    methods.extend([METHOD_API_KEY, METHOD_OAUTH])

    seen: set[str] = set()
    for item in methods:
        if item in seen:
            continue
        seen.add(item)
        credential = discover_credential(provider, item, allow_fallback=False)
        if credential is not None:
            return credential
    return None
