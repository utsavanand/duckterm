"""Turn a session's transcript (or its raw PTY log) into a one-paragraph
outcome summary. Backends, in priority order:

    DUCKTERM_SUMMARIZER_CMD   a shell command fed the prompt on stdin
                                (e.g. "claude -p --bare")
    DUCKTERM_SUMMARIZER_URL   POST {prompt} -> {text}
    auto-detected CLI agent     if claude/codex is on PATH, use it
                                (disable with DUCKTERM_SUMMARIZER=off)
    mechanical fallback         no LLM, a fact-only summary

Auto-detection means good LLM summaries work out of the box for anyone who has
an agent installed — they don't have to set an env var. The mechanical fallback
still works with zero configuration and zero API key.
"""

import asyncio
import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass


@dataclass
class Summary:
    text: str
    backend: str
    failure_reason: str | None = None


# CLI agents we'll auto-use for summaries, in preference order. Each entry is
# the command run with the prompt piped on stdin (non-interactive, plain text).
# Bring-your-own agents: set DUCKTERM_SUMMARIZER_CMD to your agent's own
# non-interactive command (e.g. "aider --message-file -").
_AUTO_AGENTS = [
    ("claude", "claude -p"),
    ("codex", "codex exec -"),
    ("copilot", "copilot -p"),
]


def summarize(prompt: str, *, claude_model: str | None = None) -> Summary:
    """claude_model picks the model when the auto-detected backend is Claude
    Code (e.g. "sonnet" for a classifier that must be accurate but needn't be
    the user's default, often slower, model). Explicit backends are used as
    configured."""
    # Explicit config always wins. DUCKTERM_SUMMARIZER=off only disables the
    # auto-detect fallback (so e.g. tests don't shell out to a real agent), not
    # a backend the user set on purpose.
    cmd = os.environ.get("DUCKTERM_SUMMARIZER_CMD")
    if cmd:
        return _cli_summary(cmd, prompt)
    url = os.environ.get("DUCKTERM_SUMMARIZER_URL")
    if url:
        return _http_summary(url, prompt)
    if os.environ.get("DUCKTERM_SUMMARIZER") == "off":
        return Summary(text="", backend="none", failure_reason="disabled")
    auto = _auto_command()
    if auto:
        if auto == "claude -p":
            return _isolated_summary(claude_model or "", prompt)
        return _cli_summary(auto, prompt)
    return Summary(text="", backend="none", failure_reason="no_provider")


def _auto_command() -> str | None:
    """The first installed CLI agent we can summarize with, or None."""
    for binary, command in _AUTO_AGENTS:
        if shutil.which(binary):
            return command
    return None


def _isolated_summary(model: str, prompt: str) -> Summary:
    # Reuse the bounded memory runner: no project instructions, agent tools,
    # session enrollment, or shell child left behind when the call times out.
    # Provider preference and the selected/default model stay unchanged.
    from duckterm import memory_provider
    from duckterm.core.session_api import APIError

    try:
        text = asyncio.run(
            memory_provider.generate("claude-code", model, prompt, user_settings=True)
        )
    except memory_provider.BatchTimeout:
        return Summary(text="", backend="none", failure_reason="provider_timeout")
    except APIError:
        return Summary(text="", backend="none", failure_reason="provider_failed")
    return Summary(text=text.strip(), backend="cli")


def _cli_summary(cmd: str, prompt: str) -> Summary:
    # DUCKTERM_INTERNAL marks this as a Duckterm-spawned agent so its hooks
    # no-op — otherwise summarizing via `claude -p` would report a phantom
    # session back into Duckterm on every checkpoint.
    env = {**os.environ, "DUCKTERM_INTERNAL": "1"}
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return Summary(text="", backend="none", failure_reason="provider_timeout")
    except OSError:
        return Summary(text="", backend="none", failure_reason="provider_failed")
    if result.returncode != 0:
        return Summary(text="", backend="none", failure_reason="provider_failed")
    return Summary(text=result.stdout.strip(), backend="cli")


def _http_summary(url: str, prompt: str) -> Summary:
    req = urllib.request.Request(
        url,
        data=json.dumps({"prompt": prompt}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        body = urllib.request.urlopen(req, timeout=60).read()
        text = str(json.loads(body).get("text", "")).strip()
    except urllib.error.URLError as exc:
        reason = "provider_timeout" if isinstance(exc.reason, TimeoutError) else "provider_failed"
        return Summary(text="", backend="none", failure_reason=reason)
    except TimeoutError:
        return Summary(text="", backend="none", failure_reason="provider_timeout")
    except (OSError, json.JSONDecodeError):
        return Summary(text="", backend="none", failure_reason="provider_failed")
    return Summary(text=text, backend="http")


def build_prompt(intention: str, transcript: str, events_summary: str) -> str:
    return (
        "Summarize this AI coding session in 2-3 sentences: what was the intent, "
        "what was done, and the outcome.\n\n"
        f"Intent: {intention or '(none recorded)'}\n\n"
        f"Activity: {events_summary}\n\n"
        f"Transcript (may be empty):\n{transcript[:4000]}"
    )


def mechanical_summary(intention: str, events_summary: str) -> str:
    """The no-LLM fallback: state the intent and the raw activity counts."""
    intent = intention or "no stated intent"
    return f"Intent: {intent}. {events_summary}"
