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
    # Claude Code aliases for the harness tiers. Separate from the checkpoint
    # summary writer, so changing that writer never moves every fast-tier
    # feature onto a costlier model (#350 x #364).
    "harness_fast_model": "haiku",
    "harness_deep_model": "sonnet",
}
HARNESS_MODELS = {"haiku", "sonnet", "opus"}
BACKENDS = {"harness", "api", "duckterm"}
LOOPBACK = {"localhost", "127.0.0.1", "::1"}
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
    if {result["harness_fast_model"], result["harness_deep_model"]} - HARNESS_MODELS:
        raise APIError(400, "Choose haiku, sonnet or opus for each harness model")
    if result["provider"] not in PROVIDERS:
        raise APIError(400, "Choose anthropic, openai, openrouter or custom as the provider")
    if result["provider"] != "custom":
        result["base_url"] = ""
    elif not result["base_url"]:
        raise APIError(400, "A custom provider needs its OpenAI-compatible base URL")
    else:
        check_custom_url(result["base_url"])
    if (
        result["backend"] == "api"
        and result["provider"] not in DEFAULT_MODELS
        and not (result["fast_model"] and result["deep_model"])
    ):
        raise APIError(400, "Choose a fast and a deep model for this provider")
    return result


def check_custom_url(url: str) -> None:
    """The key and every prompt go to this URL, so plain http is only allowed
    to this Mac (Ollama, LM Studio)."""
    parsed = urlparse(url)
    if parsed.scheme == "https" and parsed.hostname:
        return
    if parsed.scheme == "http" and parsed.hostname in LOOPBACK:
        return
    raise APIError(400, "The base URL must be https://, or http:// on localhost")


def check_named_url(provider: str, url: Any) -> None:
    """Named providers only use their own address; a base URL sent with one is
    a mistake or a redirect attempt, so it is refused rather than ignored."""
    if provider != "custom" and url:
        raise APIError(
            400, f"{provider} always uses its own address; a base URL is only for custom"
        )


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
    check_named_url(value.get("provider", current["provider"]), value.get("base_url"))
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


def env_keys() -> set[str]:
    return {env for _, _, env in PROVIDERS.values() if env}


def delete_key(provider: str) -> None:
    from duckterm import connectors

    connectors.delete_secret(f"ai-{provider}")
