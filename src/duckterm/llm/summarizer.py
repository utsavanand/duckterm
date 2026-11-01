"""The synchronous entry to DuckTerm's AI backends for prompt-in, text-out
features. Backends, in priority order:

    DUCKTERM_SUMMARIZER_CMD   a shell command fed the prompt on stdin
                                (e.g. "claude -p --bare")
    DUCKTERM_SUMMARIZER_URL   POST {prompt} -> {text}
    llm.backend                 the owner's AI settings: harness CLI login
                                (default), API key, or DuckTerm's gateway
                                (disable with DUCKTERM_SUMMARIZER=off)
    copilot -p                  last resort when no claude/codex is installed

Every call names a tier (see llm.backend). Callers fall back to a mechanical,
fact-only summary when no backend answers.
"""

import asyncio
import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass

from duckterm import memory_provider
from duckterm.core.session_api import APIError


@dataclass
class Summary:
    text: str
    backend: str
    failure_reason: str | None = None


def summarize(prompt: str, *, tier: str, claude_model: str | None = None) -> Summary:
    """claude_model pins the model when Claude Code serves the call (e.g.
    "sonnet" for a classifier measured on Sonnet)."""
    # Explicit config always wins. DUCKTERM_SUMMARIZER=off only disables the
    # automatic backends (so e.g. tests don't shell out to a real agent), not
    # a backend the user set on purpose.
    cmd = os.environ.get("DUCKTERM_SUMMARIZER_CMD")
    if cmd:
        return _cli_summary(cmd, prompt)
    url = os.environ.get("DUCKTERM_SUMMARIZER_URL")
    if url:
        return _http_summary(url, prompt)
    if os.environ.get("DUCKTERM_SUMMARIZER") == "off":
        return Summary(text="", backend="none", failure_reason="disabled")
    from duckterm.llm import backend

    try:
        if not backend.ready():
            if shutil.which("copilot"):
                return _cli_summary("copilot -p", prompt)
            return Summary(text="", backend="none", failure_reason="no_provider")
        reply = asyncio.run(backend.complete(prompt, tier=tier, claude_model=claude_model or ""))
    except memory_provider.BatchTimeout:
        return Summary(text="", backend="none", failure_reason="provider_timeout")
    except APIError:
        return Summary(text="", backend="none", failure_reason="provider_failed")
    return Summary(text=reply.text.strip(), backend="cli" if reply.backend == "harness" else "api")


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
