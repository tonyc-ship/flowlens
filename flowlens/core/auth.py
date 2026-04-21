"""Credential discovery and model selection for hosted LLM providers.

Providers are described by :class:`ProviderConfig` entries in ``PROVIDERS``.
Adding a new hosted model vendor usually means adding one entry there plus a
matching backend class — no other file in this module needs to change.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .runtime import load_runtime_env


# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------

PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_OPENAI = "openai"
PROVIDER_KIMI = "kimi"
PROVIDER_QWEN = "qwen"

METHOD_API_KEY = "api_key"
METHOD_OAUTH = "oauth"

# API styles supported by the agent backends.
API_STYLE_ANTHROPIC = "anthropic"            # Anthropic Messages API
API_STYLE_OPENAI_RESPONSES = "openai_responses"  # OpenAI Responses API
API_STYLE_OPENAI_COMPAT = "openai_compat"    # Standard OpenAI /v1/chat/completions

FLOWLENS_DIR = Path.home() / ".flowlens"
FLOWLENS_AUTH_FILE = FLOWLENS_DIR / "auth.json"
CODEX_AUTH_FILE = Path.home() / ".codex" / "auth.json"

DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-6"
DEFAULT_OPENAI_MODEL = "gpt-5.4"
DEFAULT_KIMI_MODEL = "kimi-k2.5"                    # Kimi K2.5: native multimodal (text + image + video)
DEFAULT_QWEN_MODEL = "qwen3.6-plus"                # Qwen3.6-Plus: current hosted Qwen default (multimodal)


@dataclass(frozen=True)
class ProviderConfig:
    """Static metadata for a hosted LLM provider."""

    name: str
    display_name: str
    api_style: str
    api_key_env: tuple[str, ...]                  # env var names checked in order
    auth_token_env: tuple[str, ...] = ()          # OAuth / bearer env vars
    base_url: str | None = None                   # None = provider SDK default
    default_model: str = ""
    model_prefixes: tuple[str, ...] = ()          # used by resolve_model_provider
    supports_oauth: bool = False

    @property
    def env_var_hint(self) -> str:
        """Primary env var name (for CLI hints)."""
        return self.api_key_env[0] if self.api_key_env else ""


PROVIDERS: dict[str, ProviderConfig] = {
    PROVIDER_ANTHROPIC: ProviderConfig(
        name=PROVIDER_ANTHROPIC,
        display_name="Anthropic",
        api_style=API_STYLE_ANTHROPIC,
        api_key_env=("ANTHROPIC_API_KEY",),
        auth_token_env=("ANTHROPIC_AUTH_TOKEN",),
        base_url=None,
        default_model=DEFAULT_ANTHROPIC_MODEL,
        model_prefixes=("claude-", "sonnet", "opus", "haiku"),
        supports_oauth=True,
    ),
    PROVIDER_OPENAI: ProviderConfig(
        name=PROVIDER_OPENAI,
        display_name="OpenAI",
        api_style=API_STYLE_OPENAI_RESPONSES,
        api_key_env=("OPENAI_API_KEY",),
        auth_token_env=("OPENAI_AUTH_TOKEN",),
        base_url=None,
        default_model=DEFAULT_OPENAI_MODEL,
        model_prefixes=("gpt-", "gpt5", "o1", "o3", "o4"),
        supports_oauth=True,
    ),
    PROVIDER_KIMI: ProviderConfig(
        name=PROVIDER_KIMI,
        display_name="Kimi",
        api_style=API_STYLE_OPENAI_COMPAT,
        # MOONSHOT_API_KEY is the vendor-official name; KIMI_API_KEY is a friendly alias.
        api_key_env=("MOONSHOT_API_KEY", "KIMI_API_KEY"),
        base_url="https://api.moonshot.cn/v1",
        default_model=DEFAULT_KIMI_MODEL,
        model_prefixes=("kimi-", "moonshot-"),
    ),
    PROVIDER_QWEN: ProviderConfig(
        name=PROVIDER_QWEN,
        display_name="Qwen",
        api_style=API_STYLE_OPENAI_COMPAT,
        api_key_env=("DASHSCOPE_API_KEY", "QWEN_API_KEY"),
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        default_model=DEFAULT_QWEN_MODEL,
        # Cloud Qwen uses lowercase prefixes; local MLX models use capitalized
        # "Qwen..." and are handled as provider="local" before this lookup runs.
        model_prefixes=(
            "qwen-max", "qwen-plus", "qwen-turbo", "qwen-long",
            "qwen-coder",
            "qwen3.", "qwen3-", "qwen2.5-", "qwen2-", "qwq-", "qvq-",
        ),
    ),
}

# Providers recognised as "cloud" for status / fallback purposes.
CLOUD_PROVIDERS: tuple[str, ...] = (
    PROVIDER_ANTHROPIC,
    PROVIDER_OPENAI,
    PROVIDER_KIMI,
    PROVIDER_QWEN,
)


def provider_config(provider: str) -> ProviderConfig | None:
    return PROVIDERS.get(str(provider or "").strip().lower())


# ---------------------------------------------------------------------------
# Credential dataclasses
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# flowlens/auth.json storage helpers
# ---------------------------------------------------------------------------


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


def _write_flowlens_auth_config(data: dict) -> Path:
    FLOWLENS_DIR.mkdir(parents=True, exist_ok=True)
    FLOWLENS_AUTH_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    try:
        os.chmod(FLOWLENS_AUTH_FILE, 0o600)
    except OSError:
        pass
    return FLOWLENS_AUTH_FILE


def save_auth_secret(provider: str, method: str, secret: str) -> Path:
    data = _flowlens_auth_config()
    provider_block = dict(data.get(provider) or {})
    key = "api_key" if method == METHOD_API_KEY else "auth_token"
    provider_block[key] = secret.strip()
    data[provider] = provider_block
    return _write_flowlens_auth_config(data)


def save_default_model(
    provider: str,
    model: str,
    *,
    make_provider_default: bool = True,
) -> Path:
    provider = str(provider or "").strip().lower()
    if provider_config(provider) is None:
        raise ValueError(f"Unknown provider: {provider}")
    model = str(model or "").strip()
    if not model:
        raise ValueError("Missing model name")

    data = _flowlens_auth_config()
    defaults = dict(data.get("defaults") or {})
    defaults[f"{provider}_model"] = model
    if make_provider_default:
        defaults["provider"] = provider
    data["defaults"] = defaults
    return _write_flowlens_auth_config(data)


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
    return _write_flowlens_auth_config(data)


def _config_secret(provider: str, key: str) -> str:
    block = _flowlens_auth_config().get(provider) or {}
    value = str(block.get(key) or "").strip()
    return value


# ---------------------------------------------------------------------------
# External tool integrations (Codex for OpenAI OAuth)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Credential discovery
# ---------------------------------------------------------------------------


def discover_credential(
    provider: str,
    method: str,
    *,
    allow_fallback: bool = True,
) -> ResolvedCredential | None:
    load_runtime_env()
    provider = str(provider or "").strip().lower()
    method = str(method or "").strip().lower()

    config = provider_config(provider)
    if config is None:
        return None

    env_sources: list[tuple[str, str]] = []
    config_sources: list[tuple[str, str]] = []
    file_sources: list[tuple[str, str]] = []
    flowlens_auth_source = str(FLOWLENS_AUTH_FILE.expanduser())

    if method == METHOD_API_KEY:
        for env_name in config.api_key_env:
            env_sources.append((env_name, os.environ.get(env_name, "")))
        config_sources.append((flowlens_auth_source, _config_secret(provider, "api_key")))
        if provider == PROVIDER_OPENAI:
            file_sources.append((str(CODEX_AUTH_FILE), _codex_api_key()))
    elif method == METHOD_OAUTH:
        if not config.supports_oauth:
            return None
        for env_name in config.auth_token_env:
            env_sources.append((env_name, os.environ.get(env_name, "")))
        config_sources.append((flowlens_auth_source, _config_secret(provider, "auth_token")))
        if provider == PROVIDER_OPENAI:
            file_sources.append((str(CODEX_AUTH_FILE), _codex_access_token()))
    else:
        return None

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
    config = provider_config(provider)
    if config is None:
        return ProviderStatus(provider=provider, api_key_available=False)

    api = discover_credential(provider, METHOD_API_KEY, allow_fallback=False)
    oauth = (
        discover_credential(provider, METHOD_OAUTH, allow_fallback=False)
        if config.supports_oauth
        else None
    )

    logged_in = False
    login_hint = ""
    if provider == PROVIDER_ANTHROPIC:
        status = claude_auth_status()
        logged_in = bool(status.get("loggedIn"))
        if not logged_in:
            login_hint = "claude auth login"
    elif provider == PROVIDER_OPENAI:
        codex_state = codex_login_status()
        logged_in = bool(codex_state.get("logged_in") or oauth is not None)
        if not logged_in:
            login_hint = "codex login --device-auth"

    return ProviderStatus(
        provider=provider,
        api_key_available=api is not None,
        api_key_source=api.source if api else "",
        oauth_available=oauth is not None,
        oauth_source=oauth.source if oauth else "",
        oauth_logged_in=logged_in,
        oauth_login_hint=login_hint,
    )


def available_provider_statuses() -> list[ProviderStatus]:
    return [provider_status(name) for name in CLOUD_PROVIDERS]


# ---------------------------------------------------------------------------
# Provider / model resolution
# ---------------------------------------------------------------------------


def preferred_provider() -> str | None:
    block = _flowlens_auth_config().get("defaults") or {}
    configured = str(block.get("provider") or "").strip().lower()
    if configured in CLOUD_PROVIDERS:
        return configured
    return None


def preferred_auth_method() -> str | None:
    block = _flowlens_auth_config().get("defaults") or {}
    configured = str(block.get("method") or "").strip().lower()
    if configured in {METHOD_API_KEY, METHOD_OAUTH}:
        return configured
    return None


def default_model_for_provider(provider: str) -> str:
    provider = str(provider or "").strip().lower()
    config = provider_config(provider)
    if config is None:
        return DEFAULT_ANTHROPIC_MODEL

    defaults_block = _flowlens_auth_config().get("defaults") or {}
    configured = str(defaults_block.get(f"{provider}_model") or "").strip()
    if configured:
        return configured

    return config.default_model


def resolve_model_provider(model: str | None) -> str:
    """Return the provider name for a model identifier.

    Returns "local" for MLX / UI-TARS models, a provider key from ``PROVIDERS``
    for hosted models, and falls back to the preferred / first available cloud
    provider when the model name carries no prefix hint.
    """
    normalized = str(model or "").strip()

    # Local MLX models — checked first so lowercase `qwen-...` DashScope models
    # don't collide with capitalized `Qwen...` local aliases.
    if normalized == "qwen-local" or normalized == "ui-tars-local":
        return "local"
    if normalized.startswith("Qwen") or normalized.startswith("UI-TARS"):
        return "local"

    lowered = normalized.lower()
    for pkey, pconfig in PROVIDERS.items():
        for prefix in pconfig.model_prefixes:
            if lowered.startswith(prefix):
                return pkey

    # qwen-vl-* models (e.g. qwen-vl-max-2025-08-13) are not in PROVIDER_QWEN
    # prefixes. Route them to PROVIDER_OPENAI so they use OPENAI_API_KEY /
    # OPENAI_BASE_URL which Auto-Redbook-Skills maps from MIDSCENE_MODEL_* vars.
    if lowered.startswith("qwen") and normalized != "qwen-local" and not normalized.startswith("Qwen"):
        return PROVIDER_OPENAI

    preferred = preferred_provider()
    if preferred:
        return preferred
    for name in CLOUD_PROVIDERS:
        if provider_status(name).available:
            return name
    return PROVIDER_ANTHROPIC


def default_cloud_model(*, provider: str | None = None) -> str:
    chosen_provider = provider or preferred_provider()
    if chosen_provider and chosen_provider in PROVIDERS:
        return default_model_for_provider(chosen_provider)
    for name in CLOUD_PROVIDERS:
        if provider_status(name).available:
            return default_model_for_provider(name)
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
