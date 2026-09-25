"""The Harness contract: one adapter per agent, owning both halves.

  - drive   — launch/resume the agent, classify its state, read its transcript.
  - observe — where its hook config lives and how to merge/strip our entries so a
              watched session streams in. `hook_spec` is None for agents with no
              hook system (driven-only, e.g. the generic runtime).

The core never imports a concrete runtime — it loads whichever one a session
declares, via the registry in harnesses.py. The legacy alias `AgentRuntime` is
kept so existing drive-only callers don't need to change.
"""

import re
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

SessionState = Literal[
    "idle", "busy", "waiting", "terminated", "stopped", "interrupted", "archived"
]

# States that are "at rest" — the session is finished or put away, so the sweeps
# and startup reconciliation skip it. Defined once here (beside SessionState);
# both the history store and the server import it. "interrupted" is the
# involuntary sibling of "stopped": the backing terminal died (reboot, crash,
# killed tmux) rather than the user pausing it — equally resumable, but the UI
# should say what actually happened.
AT_REST_STATES: tuple[SessionState, ...] = ("terminated", "stopped", "interrupted", "archived")


@dataclass(frozen=True)
class HookSpec:
    """An agent's observe half: where its hook config lives and how to merge/strip
    Duckterm's entries. `build` and `strip` operate on the parsed JSON config so
    install/uninstall stay symmetric and idempotent.

    `global_rel` is the config path relative to the user home; `repo_rel` is
    relative to the project dir. Paths are resolved at call time (not import) so
    Path.home() is read live — tests can monkeypatch it."""

    global_rel: Path
    repo_rel: Path
    build: Callable[[dict[str, Any], str, str], dict[str, Any]]
    strip: Callable[[dict[str, Any]], dict[str, Any]]

    def path(self, *, global_scope: bool, project_dir: Path) -> Path:
        if global_scope:
            return Path.home() / self.global_rel
        return project_dir / self.repo_rel


_DIM_SPAN = re.compile(r"\x1b\[2m.*?(?:\x1b\[(?:0|22)?m|$)")
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def prompt_line_rest(screen: str, marker: str, *, ignore_dim: bool) -> str | None:
    """Text typed after the last prompt marker on screen, or None when no
    prompt line is visible. With ignore_dim, dimmed spans (placeholders) are
    dropped before comparing."""
    for raw in reversed(screen.splitlines()):
        line = _DIM_SPAN.sub("", raw) if ignore_dim else raw
        plain = _ANSI.sub("", line).replace("\xa0", " ")
        if plain.lstrip().startswith(marker):
            return plain.lstrip()[len(marker) :].strip()
    return None


def plain_screen(screen: str) -> str:
    """A screen captured with escapes, as plain text, minus the dimmed text on
    an agent's input line. That dim text is a placeholder or a suggested next
    prompt; as plain text it read like an instruction the owner had typed."""
    rows = []
    for raw in screen.splitlines():
        plain = _ANSI.sub("", raw).replace("\xa0", " ")
        if plain.lstrip().startswith(("❯", "›")):
            plain = _ANSI.sub("", _DIM_SPAN.sub("", raw)).replace("\xa0", " ")
        rows.append(plain.rstrip())
    return "\n".join(rows)


class Harness(ABC):
    name: str
    turn_end_inbox_notice = False
    # An agent's observe half; None for driven-only agents (no hook system).
    hook_spec: HookSpec | None = None

    @abstractmethod
    def __init__(self, command: str) -> None: ...

    def prompt_is_empty(self, screen: str) -> bool:
        """Whether the visible screen (ANSI escapes intact) shows the agent's
        input box with nothing typed in it. Oracle pastes an inbox reminder
        only when this is True, so an unknown prompt layout must return False."""
        return False

    @abstractmethod
    def launch_command(self, *, cwd: Path, session_key: str, initial_prompt: str) -> list[str]: ...

    @abstractmethod
    def detect_state(self, recent_output: str) -> SessionState: ...

    @abstractmethod
    def tool_in(self, recent_output: str) -> str | None: ...

    @abstractmethod
    def locate_transcript(self, *, cwd: Path, session_id: str) -> Path | None: ...

    @abstractmethod
    def read_transcript(self, *, cwd: Path, session_id: str) -> list[dict[str, str]]:
        """The session's conversation as uniform {role, text} records (including
        the agent's own responses), newest-last. Empty when unavailable. Each
        runtime reads its native format (JSONL, SQLite, …)."""

    @abstractmethod
    def restore_command(self, *, cwd: Path, session_key: str) -> list[str]: ...

    def find_resumable_id(self, *, cwd: Path, recorded: str | None) -> str | None:
        """A conversation id restore_command can actually resume: the recorded
        one if its transcript still exists, else the harness's best fallback
        (typically the newest transcript for this cwd). None — the default, for
        harnesses with no native resume — means relaunch fresh."""
        return None

    def messages(self, *, cwd: Path, session_id: str | None) -> list[dict[str, object]]:
        """Structured records for the Messages view: {id, role, blocks}, where a
        block is text / tool_use / tool_result (see claude_code.parse_messages).
        Harnesses without a structured transcript return [] — the view is
        simply unavailable for them."""
        return []


AgentRuntime = Harness
