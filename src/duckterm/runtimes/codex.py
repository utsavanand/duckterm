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
from pathlib import Path

from duckterm.agents.hooks_install import claude_style_build, claude_style_strip
from duckterm.runtimes.base import Harness, HookSpec, SessionState

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

_ROLLOUT_ID = re.compile(
    r"-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$"
)


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

    def detect_state(self, recent_output: str) -> SessionState:
        for line in reversed(recent_output.splitlines()):
            if _WAITING.search(line):
                return "waiting"
            if _WORKING.search(line):
                return "busy"
        # No working/waiting marker in the window: treat as idle (output settled).
        return "idle"

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
        if path is None:
            path = self.latest_transcript(cwd=cwd)
        return parse_codex_messages(path) if path else []

    def latest_transcript(self, *, cwd: Path) -> Path | None:
        """The newest rollout whose session_meta records this cwd. Sessions
        launched in-process never report Codex's session_id, so locating by id
        fails — but the rollout's first line names the cwd it ran in."""
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
        if recorded and self.locate_transcript(cwd=cwd, session_id=recorded):
            return recorded
        # In-process launches never report Codex's session_id, but the newest
        # rollout whose session_meta names this cwd carries the id in its
        # filename (rollout-<timestamp>-<uuid>.jsonl).
        latest = self.latest_transcript(cwd=cwd)
        if latest is None:
            return None
        m = _ROLLOUT_ID.search(latest.name)
        return m.group(1) if m else None


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
    records: list[dict[str, object]] = []
    for i, line in enumerate(path.read_text(errors="replace").splitlines()):
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
