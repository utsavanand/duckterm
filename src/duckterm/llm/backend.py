"""One call for every DuckTerm AI feature: complete(prompt, tier=..., schema=...).

The tier names the job, not the model:
    fast   extraction, short summaries, structuring (checkpoint writer, rule
           from English, compaction, release map)
    deep   judgement over a lot of material (Oracle, checkpoint reviewer,
           Relay classifier, rule suggestions, PR review)

The owner's ai-settings pick the backend (see ai_settings). A failing API key or
the unavailable DuckTerm gateway falls back to the harness CLI, which costs a
subscription owner nothing; nothing ever falls back to a path that bills
without the owner configuring it.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from duckterm import checkpoint_settings, memory_provider
from duckterm.core.session_api import APIError
from duckterm.llm import ai_settings, api

TIERS = ("fast", "deep")
EFFORT = {"fast": "low", "deep": "medium"}
# exec ignores the owner's Codex config, so without a model it runs Codex's
# built-in default. Luna answered a one-line summary in 3 s against 5-6 s for
# the default (2026-10-29). Deep keeps the default for its judgement.
CODEX_MODELS = {"fast": "gpt-6-luna"}
_HARNESS_BINARIES = (("claude-code", "claude"), ("codex", "codex"))


@dataclass
class Reply:
    text: str
    backend: str  # "harness" | "api"
    detail: str  # "claude-code", "anthropic/claude-haiku-5-5", ...
    fallback: str = ""  # why the configured backend didn't serve this call


def harness(choice: str = "auto") -> str | None:
    """The installed harness that serves calls: the chosen one, or the first
    installed when the choice is auto."""
    for name, binary in _HARNESS_BINARIES:
        if choice in ("auto", name) and shutil.which(binary):
            return name
    return None


def ready() -> bool:
    """Whether any backend can serve a call right now, without making one."""
    if os.environ.get("DUCKTERM_SUMMARIZER") == "off":
        return False
    settings = ai_settings.load()
    if settings["backend"] == "api" and _api_key(settings) is not None:
        return True
    return harness(settings["harness"]) is not None


async def complete(
    prompt: str,
    *,
    tier: str,
    schema: dict[str, Any] | None = None,
    claude_model: str = "",
    feature: str = "",
) -> Reply:
    """claude_model pins the model when Claude Code serves the call (a
    checkpoint keeps the models captured with it); other backends use the
    tier's model. feature names the caller in the server log."""
    started = time.monotonic()
    try:
        reply = await _complete(prompt, tier, schema, claude_model)
    except APIError as exc:
        _log(feature, tier, prompt, started, f"failed: {exc}")
        raise
    served = f"{reply.backend} {reply.detail}"
    _log(
        feature,
        tier,
        prompt,
        started,
        served + (f" (fallback: {reply.fallback})" if reply.fallback else ""),
    )
    return reply


async def _complete(
    prompt: str, tier: str, schema: dict[str, Any] | None, claude_model: str
) -> Reply:
    if os.environ.get("DUCKTERM_SUMMARIZER") == "off":
        raise APIError(503, "Automatic preparation is disabled on this host")
    settings = ai_settings.load()
    fallback = ""
    if settings["backend"] == "api":
        try:
            model = ai_settings.model(settings, tier)
            text = await asyncio.to_thread(_api, settings, model, tier, prompt, schema)
            return Reply(text, "api", f"{settings['provider']}/{model}")
        except APIError as exc:
            fallback = str(exc)
    elif settings["backend"] == "duckterm":
        try:
            return await _gateway(tier, prompt, schema)
        except APIError as exc:
            fallback = str(exc)
    name = harness(settings["harness"])
    if name is None:
        raise APIError(503, fallback or "No Claude Code or Codex CLI is installed")
    if name == "codex":
        model = _codex_model(tier)
    else:
        model = claude_model
        if not model:
            with contextlib.suppress(APIError):
                chosen = checkpoint_settings.load()
                model = chosen["writer_model" if tier == "fast" else "reviewer_model"]
    text = await memory_provider.generate(
        name, model, prompt, schema, user_settings=True, effort=EFFORT[tier]
    )
    return Reply(text, "harness", f"{name}/{model}" if model else name, fallback)


def _codex_model(tier: str) -> str:
    """The tier's Codex model when the owner's account lists it, else "" for
    Codex's default. A model the account lacks would fail every call."""
    model = CODEX_MODELS.get(tier, "")
    if not model:
        return ""
    try:
        listed = json.loads((Path.home() / ".codex" / "models_cache.json").read_text())
        slugs = {m.get("slug") for m in listed.get("models", []) if isinstance(m, dict)}
    except (OSError, ValueError, AttributeError):
        return ""
    return model if model in slugs else ""


def _log(feature: str, tier: str, prompt: str, started: float, outcome: str) -> None:
    seconds = time.monotonic() - started
    size = len(prompt.encode()) / 1000
    print(
        f"[duckterm] ai {feature or '-'} {tier} {seconds:.1f}s in={size:.0f}KB {outcome}",
        file=sys.stderr,
    )


def _api_key(settings: dict[str, Any]) -> str | None:
    secret, _source = ai_settings.key(settings["provider"], ai_settings.base_url(settings))
    # A local OpenAI-compatible server (Ollama, LM Studio) needs no key.
    return secret if secret or settings["provider"] != "custom" else ""


def _api(
    settings: dict[str, Any], model: str, tier: str, prompt: str, schema: dict[str, Any] | None
) -> str:
    key = _api_key(settings)
    if key is None:
        raise APIError(401, f"No {settings['provider']} API key is saved")
    base = ai_settings.base_url(settings)
    if ai_settings.PROVIDERS[settings["provider"]][0] == "anthropic":
        return api.anthropic(
            base, key, model, memory_provider.SYSTEM_PROMPT, prompt, schema, EFFORT[tier]
        )
    return api.openai_chat(base, key, model, memory_provider.SYSTEM_PROMPT, prompt, schema)


async def _gateway(tier: str, prompt: str, schema: dict[str, Any] | None) -> Reply:
    # DuckTerm's hosted models, paid with credits. Not built yet. It needs a
    # DuckTerm account token and a credit balance; the endpoint will speak the
    # OpenAI-compatible shape so api.openai_chat can serve it.
    raise APIError(503, "DuckTerm models are not available yet")
