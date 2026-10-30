"""Which backend serves DuckTerm's own AI features, and with which models.

Backends:
    harness    the owner's installed claude/codex CLI and its existing login
               (the default; no key, no extra cost on a subscription)
    api        the owner's own API key, called directly (faster, billed per token)
    duckterm   DuckTerm's hosted models, paid with credits (not available yet)

Keys live in the Keychain (connector secret store), never in this file. An
exported ANTHROPIC_API_KEY / OPENAI_API_KEY / OPENROUTER_API_KEY wins over the
stored key, the way the provider CLIs and SDKs resolve them.

A key only goes to the host it was saved for. Named providers always use their
own address, and a custom key is stored with the URL it was verified against,
so changing the settings file alone can't send a key somewhere else.
"""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import urlparse

from duckterm.core.session_api import APIError
from duckterm.helpers import paths
from duckterm.helpers.private_files import private_read, private_write

DEFAULTS: dict[str, Any] = {
    "backend": "harness",
    "harness": "auto",
    "provider": "anthropic",
    "base_url": "",
    "fast_model": "",
    "deep_model": "",
}
BACKENDS = {"harness", "api", "duckterm"}
HARNESSES = {"auto", "claude-code", "codex"}
# provider -> (wire format, default base URL, env var holding a key)
PROVIDERS: dict[str, tuple[str, str, str]] = {
    "anthropic": ("anthropic", "https://api.anthropic.com/v1", "ANTHROPIC_API_KEY"),
    "openai": ("openai", "https://api.openai.com/v1", "OPENAI_API_KEY"),
    "openrouter": ("openai", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    # Any OpenAI-compatible endpoint: Ollama, LM Studio, Groq, a company gateway.
    "custom": ("openai", "", ""),
}
# Tested defaults. Other providers have no default: their model ids are
# whatever the owner's account lists, so the owner picks them.
DEFAULT_MODELS = {"anthropic": {"fast": "claude-haiku-5-5", "deep": "claude-sonnet-5-5"}}


def _path() -> Any:
    return paths.home() / "ai-settings.json"


def validated(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - set(DEFAULTS):
        raise APIError(400, "Unknown AI setting")
    result = {**DEFAULTS, **value}
    if any(not isinstance(v, str) for v in result.values()):
        raise APIError(400, "AI settings are strings")
    if result["backend"] not in BACKENDS:
        raise APIError(400, "Choose harness, api or duckterm as the AI backend")
    if result["harness"] not in HARNESSES:
        raise APIError(400, "Choose auto, claude-code or codex as the harness")
    if result["provider"] not in PROVIDERS:
        raise APIError(400, "Choose anthropic, openai, openrouter or custom as the provider")
    if result["base_url"] and urlparse(result["base_url"]).scheme not in ("http", "https"):
        raise APIError(400, "The base URL must start with http:// or https://")
    if result["provider"] == "custom" and not result["base_url"]:
        raise APIError(400, "A custom provider needs its OpenAI-compatible base URL")
    if result["provider"] != "custom":
        result["base_url"] = ""
    if (
        result["backend"] == "api"
        and result["provider"] not in DEFAULT_MODELS
        and not (result["fast_model"] and result["deep_model"])
    ):
        raise APIError(400, "Choose a fast and a deep model for this provider")
    return result


def load() -> dict[str, Any]:
    text = private_read(_path())
    if text is None:
        return dict(DEFAULTS)
    try:
        return validated(json.loads(text))
    except (ValueError, APIError) as exc:
        raise APIError(409, "AI settings could not be read; save valid settings again") from exc


def save(value: Any) -> dict[str, Any]:
    try:
        current = load()
    except APIError:
        current = dict(DEFAULTS)
    if not isinstance(value, dict):
        raise APIError(400, "Unknown AI setting")
    updated = validated({**current, **value})
    private_write(_path(), json.dumps(updated))
    return updated


def base_url(settings: dict[str, Any]) -> str:
    if settings["provider"] != "custom":
        return PROVIDERS[settings["provider"]][1]
    return str(settings["base_url"]).rstrip("/")


def model(settings: dict[str, Any], tier: str) -> str:
    return settings[f"{tier}_model"] or DEFAULT_MODELS.get(settings["provider"], {}).get(tier, "")


def key(provider: str, base: str = "") -> tuple[str | None, str]:
    """The key and where it came from: "env", "stored" or "" when none. A
    custom key counts only for the base URL it was verified against."""
    from duckterm import connectors

    env = PROVIDERS[provider][2]
    if env and os.environ.get(env):
        return os.environ[env], "env"
    stored = connectors.load_secret(f"ai-{provider}")
    if stored and provider == "custom":
        try:
            bound = json.loads(stored)
            stored = bound["key"] if bound["base_url"] == base.rstrip("/") else None
        except (ValueError, KeyError, TypeError):
            stored = None
    return (stored, "stored") if stored else (None, "")


def save_key(provider: str, secret: str, base: str = "") -> None:
    from duckterm import connectors

    if provider == "custom":
        secret = json.dumps({"key": secret, "base_url": base.rstrip("/")})
    connectors.save_secret(f"ai-{provider}", secret)


def delete_key(provider: str) -> None:
    from duckterm import connectors

    connectors.delete_secret(f"ai-{provider}")
