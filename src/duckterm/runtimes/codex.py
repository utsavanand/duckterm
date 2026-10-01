"""The Codex runtime. State is detected from terminal output (coarser than
Claude's hook-driven state). Transcripts come from Codex's rollout JSONL files
(~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl): flat {role, text} records for
the summarizer, structured {id, role, blocks} records for the Messages view.

This adapter exists to prove the boundary: a second real runtime drops in by
implementing the same contract, with zero changes to the core.
"""

import contextlib
import json
import re
import shlex
from collections.abc import Iterable
from pathlib import Path

from duckterm.agents.hooks_install import claude_style_build, claude_style_strip
from duckterm.runtimes.base import (
    Harness,
    HookSpec,
    SessionState,
    plain_screen,
    prompt_line_rest,
)
from duckterm.runtimes.message_cache import MessageCache, unavailable_response

# Codex prints a spinner/working line while busy and a prompt glyph when idle.
# "esc to interrupt" appears on every interruptible-active line (including
# "Reviewing approval request (5m …)" — codex's approval machinery running is
# WORK, not waiting-on-you).
_WORKING = re.compile(
    r"(working|thinking|running|reviewing|applying patch|esc to interrupt)", re.IGNORECASE
)
# Codex's real approval prompt says "Would you like to run the following
# command?" and ends with "Press enter to confirm" — neither matched the old
# pattern, so the output detector never saw codex enter waiting, its cached
# state diverged from the hook-driven DB state, and a terminal-approved long
# command stayed "waiting" for its whole run.
# UI prompt markers only. Bare "allow"/"approve" are gone: they matched code
# ON SCREEN (a diff containing `allowfullscreen` voted a busy session into
# "waiting").
_WAITING = re.compile(
    r"(\(y/n\)|continue\?|would you like to|press enter to confirm|do you want to proceed)",
    re.IGNORECASE,
)
# The owner's queued question above the input box: "? 1 question" (a timer
# like "· 7s" comes and goes) over "shift + ← to answer".
_QUEUED_QUESTION = re.compile(r"^\s*\? \d+ questions?\b|shift \+ ← to answer", re.MULTILINE)

_SESSION_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


class CodexRuntime(Harness):
    name = "codex"
    # Codex's config is repo-local-unreliable upstream (openai/codex#17532), but
    # the file shape is identical to Claude's, so it reuses the same build/strip.
    hook_spec = HookSpec(
        global_rel=Path(".codex") / "hooks.json",
        repo_rel=Path(".codex") / "hooks.json",
        build=claude_style_build,
        strip=claude_style_strip,
    )

    def __init__(self, command: str = "codex") -> None:
        self._argv = shlex.split(command)

    def launch_command(self, *, cwd: Path, session_key: str, initial_prompt: str) -> list[str]:
        argv = list(self._argv)
        if initial_prompt:
            argv += [initial_prompt]
        return argv

    # With approvals_reviewer = "auto_review", Codex fires PermissionRequest for
    # every gated command and its reviewer approves nearly all of them: 1,597
    # requests in 5 days on the owner's machine, typically done in ~20 s.
    auto_approves_requests = True
    # Its PreToolUse carries questions[{title, options: [str]}]; the tool
    # returns at once. An answer arrives as a prompt starting "> <title>";
    # any other submitted prompt discards the queued question (0.155.1).
    owner_prompt = ("PreToolUse", "request_user_input_async")
    owner_prompt_blocks = False

    def approval_prompt_visible(self, screen: str) -> bool:
        # Codex 0.155's prompt: "Would you like to run the following command?"
        # (or "...make the following edits?") over "› 1. Yes, proceed (y)".
        text = plain_screen(screen)
        return "Would you like to" in text and "Yes, proceed (y)" in text

    def approval_keys(self, decision: str) -> bytes | None:
        return b"y" if decision == "approve" else b"\x1b"

    def prompt_is_empty(self, screen: str) -> bool:
        # Codex shows "›" plus a dimmed placeholder ("Ask Codex to do anything")
        # when empty; typed text renders undimmed. A queued question for the
        # owner ("? 1 question" over "shift + ← to answer") sits above an
        # empty box, and anything submitted there discards it, so the box
        # doesn't count as free.
        if _QUEUED_QUESTION.search(plain_screen(screen)):
            return False
        return prompt_line_rest(screen, "›", ignore_dim=True) == ""

    def detect_state(self, recent_output: str) -> SessionState | None:
        for line in reversed(recent_output.splitlines()):
            if _WAITING.search(line):
                return "waiting"
            if _WORKING.search(line):
                return "busy"
        # Unrecognized output is not evidence that the turn ended.
        return None

    def tool_in(self, recent_output: str) -> str | None:
        return None

    def locate_transcript(self, *, cwd: Path, session_id: str) -> Path | None:
        # Codex writes one rollout-*.jsonl per session under a date hierarchy,
        # with the session_id (a UUID) in the filename.
        root = Path.home() / ".codex" / "sessions"
        if not root.exists():
            return None
        matches = sorted(root.glob(f"**/rollout-*-{session_id}.jsonl"))
        return matches[-1] if matches else None

    def read_transcript(self, *, cwd: Path, session_id: str) -> list[dict[str, str]]:
        path = self.locate_transcript(cwd=cwd, session_id=session_id)
        return parse_codex_transcript(path) if path else []

    def messages(self, *, cwd: Path, session_id: str | None) -> list[dict[str, object]]:
        path = self.locate_transcript(cwd=cwd, session_id=session_id) if session_id else None
        return _MESSAGE_CACHE.read(path) if path else []

    def messages_response(self, *, cwd: Path, session_id: str | None) -> bytes:
        path = self.locate_transcript(cwd=cwd, session_id=session_id) if session_id else None
        return (
            _MESSAGE_CACHE.response(path, self.name, session_id)
            if path
            else unavailable_response(session_id)
        )

    def latest_transcript(self, *, cwd: Path) -> Path | None:
        """Newest rollout for directory-level discovery, never session identity."""
        root = Path.home() / ".codex" / "sessions"
        if not root.exists():
            return None
        for path in sorted(root.glob("**/rollout-*.jsonl"), reverse=True):
            if _rollout_cwd(path) == str(cwd):
                return path
        return None

    def restore_command(self, *, cwd: Path, session_key: str) -> list[str]:
        # `codex resume <uuid>` continues the recorded conversation (rollout).
        # Global [OPTIONS] are accepted before the subcommand, so extra flags in
        # the configured command survive.
        return [*self._argv, "resume", session_key]

    def find_resumable_id(self, *, cwd: Path, recorded: str | None) -> str | None:
        # Cwd is not conversation identity: multiple agents can share a repo.
        # Session-key-bound hooks persist the native ID in history; never
        # substitute a newer rollout when that identity is missing or stale.
        if (
            recorded
            and _SESSION_ID.fullmatch(recorded)
            and self.locate_transcript(cwd=cwd, session_id=recorded)
        ):
            return recorded
        return None

    def model_arguments(self, model: str) -> list[str]:
        return ["-c", "model=" + json.dumps(model)]

    def can_resume_unambiguously(self, *, cwd: Path, recorded: str | None) -> bool:
        return self.find_resumable_id(cwd=cwd, recorded=recorded) is not None


def _rollout_cwd(path: Path) -> str | None:
    """The cwd a rollout ran in, from its session_meta first line."""
    try:
        with path.open(errors="replace") as f:
            first = f.readline()
        obj = json.loads(first)
    except (OSError, json.JSONDecodeError):
        return None
    if obj.get("type") != "session_meta":
        return None
    payload = obj.get("payload") or {}
    cwd = payload.get("cwd")
    return str(cwd) if cwd else None


def parse_codex_transcript(path: Path) -> list[dict[str, str]]:
    """Read {role, text} records from a Codex rollout JSONL. Messages are
    `response_item` lines whose payload is {type:"message", role, content:[…]};
    each content block carries text under "text". Skips unreadable lines."""
    records: list[dict[str, str]] = []
    for line in path.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("type") != "response_item":
            continue
        payload = obj.get("payload") or {}
        if payload.get("type") != "message":
            continue
        role = payload.get("role")
        text = _codex_text(payload.get("content"))
        if role and text:
            records.append({"role": str(role), "text": text})
    return records


def parse_codex_messages(path: Path) -> list[dict[str, object]]:
    """Structured records for the Messages view, same shape as claude_code.
    parse_messages: {id, role, blocks} with text / tool_use / tool_result
    blocks. From a rollout, response_item payloads map as:
      message (role user/assistant) -> a text block
      function_call                 -> an assistant tool_use block
      function_call_output          -> an assistant tool_result block
    Skipped: developer-role messages (injected instructions), reasoning items,
    and user turns that are machine context (<environment_context>,
    <user_instructions>) — rendering those as "you" would be wrong and would
    anchor the latest-reply view on a machine-generated turn."""
    return _parse_message_lines(path.read_text(errors="replace").splitlines(), 0)


def _parse_message_lines(lines: Iterable[str], start: int) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for i, line in enumerate(lines, start):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("type") != "response_item":
            continue
        payload = obj.get("payload") or {}
        ptype = payload.get("type")
        if ptype == "message":
            role = payload.get("role")
            if role not in ("user", "assistant"):
                continue
            text = _codex_text(payload.get("content"))
            if role == "user" and text.lstrip().startswith(
                ("<environment_context>", "<user_instructions>")
            ):
                continue
            if text:
                records.append(
                    {"id": i, "role": str(role), "blocks": [{"type": "text", "text": text}]}
                )
        elif ptype == "function_call":
            args: object = payload.get("arguments")
            if isinstance(args, str):
                # Keep the raw string if it isn't JSON — still renderable.
                with contextlib.suppress(json.JSONDecodeError):
                    args = json.loads(args)
            block = {"type": "tool_use", "name": payload.get("name") or "tool", "input": args}
            records.append({"id": i, "role": "assistant", "blocks": [block]})
        elif ptype == "function_call_output":
            out = payload.get("output")
            text = out if isinstance(out, str) else json.dumps(out) if out is not None else ""
            if text:
                block = {"type": "tool_result", "text": text}
                records.append({"id": i, "role": "assistant", "blocks": [block]})
    return records


def _codex_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            str(b["text"])
            for b in content
            if isinstance(b, dict) and isinstance(b.get("text"), str)
        ]
        return "\n".join(parts)
    return ""


_MESSAGE_CACHE = MessageCache(_parse_message_lines)
