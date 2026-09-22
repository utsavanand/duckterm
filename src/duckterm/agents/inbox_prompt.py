"""Fail-closed readiness checks for a small set of interactive agent prompts."""

import re
import subprocess
from dataclasses import dataclass

from duckterm.agents import tmux

_SGR = re.compile(r"\x1b\[[0-9;]*m")
_BUSY = re.compile(r"esc to interrupt|press enter to confirm|would you like to|\(y/n\)", re.I)


@dataclass(frozen=True)
class Prompt:
    command: str
    x: int
    y: int
    screen: str


def empty_prompt(runtime: str, command: str, x: int, y: int, screen: str) -> bool:
    """A cursor at column 2 alone is not proof: users can move into a draft.

    Codex renders its placeholder dim; typed text is not dim. Unknown layouts,
    multiline drafts, attachment chips, menus and shells are deliberately refused.
    """
    if (runtime, command) not in {("codex", "codex"), ("claude-code", "claude")}:
        return False
    lines = screen.splitlines()
    if not 0 <= y < len(lines) or x != 2:
        return False
    row = lines[y]
    plain = _SGR.sub("", row).rstrip()
    # Require a real TUI footer below the input, not a glyph in command output.
    below = _SGR.sub("", "\n".join(lines[y + 1 :])).strip()
    footer = next((line.strip() for line in below.splitlines() if line.strip()), "")
    if _BUSY.search(_SGR.sub("", "\n".join(lines[max(0, y - 4) :]))):
        return False
    if re.search(r"\[(?:image|attachment|pasted)", screen, re.I):
        return False
    if runtime == "codex":
        if not re.search(r"(?m)^\s*(?:gpt-|o[134]-|\? for shortcuts|\d+% context)", footer):
            return False
        if plain == "›":
            return True
        # Exact styled placeholder observed in Codex; unfamiliar versions defer.
        return bool(
            re.fullmatch(
                r"(?:\x1b\[[0-9;]*m)*›(?:\x1b\[[0-9;]*m)* "
                r"\x1b\[2mAsk Codex to do anything(?:\x1b\[[0-9;]*m)*\s*",
                row,
            )
        )
    return plain == "❯" and bool(re.match(r"[─━]{3,}", footer))


def probe(target: str, runtime: str) -> Prompt | None:
    try:
        result = subprocess.run(
            [
                "tmux",
                "-L",
                tmux.socket_name(),
                "display-message",
                "-p",
                "-t",
                target,
                "#{pane_current_command}|#{cursor_x}|#{cursor_y}|#{pane_in_mode}|#{session_attached}",
            ],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode:
            return None
        command, x, y, mode, attached = result.stdout.strip().split("|")
        if mode != "0" or attached != "0":
            return None
        capture = subprocess.run(
            ["tmux", "-L", tmux.socket_name(), "capture-pane", "-p", "-e", "-t", target],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if capture.returncode or not empty_prompt(runtime, command, int(x), int(y), capture.stdout):
            return None
        return Prompt(command, int(x), int(y), capture.stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
