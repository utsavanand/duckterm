"""Session checkpoints: a record of *what was done* in a session so far.

A checkpoint captures both:
  - mechanical data — the prompts given to the agent, files changed, tool-use
    counts, and git state (branch + working-tree status), read out-of-band from
    the stored events and the worktree;
  - semantic data — a short "what was done" summary (LLM-written when a
    summarizer is configured, mechanical fallback otherwise).

It is a read-only record, not a restore point — we use git for code state. The
record is stored in the DB; the human-readable markdown is written under
DUCKTERM_HOME/checkpoints/<session_key>/ (a single stable root, NOT inside the
session's worktree — a worktree is deleted with the session, taking its logs
with it), and the row stores a path RELATIVE to that root so it survives moving
DUCKTERM_HOME or restoring on another machine.

Ordering matters for durability: build the record (build_checkpoint, no file
I/O), insert the DB row (the source of truth), THEN write the markdown as a
derived artifact. A crash between the row and the file leaves markdown_path NULL,
never an orphan file with no row.

Modeled on uv-suite's watchtower checkpoint service.
"""

import subprocess
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from duckterm.helpers import paths
from duckterm.llm.summarizer import summarize

Event = dict[str, Any]


@dataclass
class Checkpoint:
    id: str
    session_key: str
    label: str
    summary: str
    record: dict[str, Any]
    # The rendered markdown text; the caller writes it AFTER inserting the row
    # (so a crash can't orphan a file). markdown_path is set by the writer to a
    # path relative to DUCKTERM_HOME/checkpoints/.
    markdown: str
    markdown_path: str | None = None
    created_at: int = field(default=0)


def _extract(events: list[Event]) -> dict[str, Any]:
    """Pull prompts, commands run, changed files, and tool counts from a
    session's events."""
    prompts: list[str] = []
    commands: list[str] = []
    files: Counter[str] = Counter()
    tools: Counter[str] = Counter()
    # PreToolUse and PostToolUse both fire for one Bash call; count the command
    # once (on PreToolUse) so a single run isn't recorded twice.
    for e in events:
        etype = e.get("event_type")
        if etype == "UserPromptSubmit":
            text = str(e.get("prompt") or e.get("tool_input", {}).get("prompt") or "").strip()
            if text:
                prompts.append(text)
        if etype in ("PreToolUse", "PostToolUse"):
            tool = e.get("tool_name")
            if tool:
                tools[str(tool)] += 1
            tool_input = e.get("tool_input") or {}
            path = tool_input.get("file_path")
            if path:
                files[str(path)] += 1
            if etype == "PreToolUse" and tool == "Bash":
                cmd = str(tool_input.get("command") or "").strip()
                if cmd:
                    commands.append(cmd)
    return {
        # Every human prompt, in order, untruncated — the checkpoint is a record.
        "prompts": prompts,
        # Every terminal command the agent ran, in order.
        "commands": commands,
        "files": [{"path": p, "edits": n} for p, n in files.most_common()],
        "tools": [{"tool": t, "count": n} for t, n in tools.most_common()],
        "event_count": len(events),
    }


def _git_state(cwd: Path) -> dict[str, Any]:
    if not (cwd / ".git").exists():
        return {"git": False, "path": str(cwd)}

    def git(*args: str) -> str:
        r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else ""

    return {
        "git": True,
        "repo": cwd.name,
        "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
        "dirty_files": [ln[3:] for ln in git("status", "--porcelain").splitlines()],
        "last_commit": git("log", "-1", "--oneline"),
    }


def build_checkpoint(
    *,
    session_key: str,
    label: str,
    cwd: Path,
    events: list[Event],
    intention: str,
    now_ms: int,
    since_ms: int = 0,
    transcript: list[dict[str, str]] | None = None,
) -> Checkpoint:
    """Capture a checkpoint over `events`. If `since_ms` is set (the previous
    checkpoint's time), the record also breaks out the work done *since then* so
    the summary describes the delta, not the whole session again. `transcript`
    is the agent's own conversation (role/text incl. its responses) when its
    runtime can read one — it sharpens the summary and is recorded for handoff."""
    activity = _extract(events)
    git = _git_state(cwd)
    new_events = [e for e in events if int(e.get("_ts", 0)) > since_ms] if since_ms else events
    new_activity = _extract(new_events) if since_ms else activity
    record = {
        "intention": intention,
        **activity,
        **git,
        "created_at": now_ms,
        "since_ms": since_ms,
        "turns": len(transcript or []),
        "new_since_last": {
            "prompts": new_activity["prompts"],
            "commands": new_activity["commands"],
            "files": new_activity["files"],
            "tools": new_activity["tools"],
            "event_count": new_activity["event_count"],
        },
    }
    summary = _summarize(intention, new_activity if since_ms else activity, git, transcript)
    markdown = _render_markdown(label, record, summary)
    return Checkpoint(
        id=uuid.uuid4().hex,
        session_key=session_key,
        label=label,
        summary=summary,
        record=record,
        markdown=markdown,
        markdown_path=None,  # set by write_markdown() after the row is inserted
        created_at=now_ms,
    )


def _summarize(
    intention: str,
    activity: dict[str, Any],
    git: dict[str, Any],
    transcript: list[dict[str, str]] | None = None,
) -> str:
    facts = (
        f"{activity['event_count']} events; "
        f"{len(activity['files'])} files changed; "
        f"tools: {', '.join(f'{t['count']}x {t['tool']}' for t in activity['tools'][:5]) or 'none'}"
    )
    if git.get("git"):
        facts += f"; on {git['repo']}@{git['branch']}"
    prompt = (
        "Summarize what was done in this coding session in 2-3 sentences.\n\n"
        f"Intent: {intention or '(none)'}\n"
        f"Prompts given:\n" + "\n".join(f"- {p}" for p in activity["prompts"]) + "\n\n"
        f"Activity: {facts}"
    )
    # When we have the agent's own conversation, give the summarizer the tail of
    # it (including the agent's responses) — far better signal than counts alone.
    convo = _transcript_excerpt(transcript)
    if convo:
        prompt += f"\n\nConversation (most recent):\n{convo}"
    result = summarize(prompt)
    return result.text or f"{intention or 'Session'} — {facts}."


def _transcript_excerpt(transcript: list[dict[str, str]] | None, budget: int = 4000) -> str:
    """The tail of the conversation as `role: text` lines, capped at ~budget
    chars (most recent kept) so a long session doesn't blow the prompt."""
    if not transcript:
        return ""
    lines = [f"{r.get('role', '?')}: {r.get('text', '').strip()}" for r in transcript]
    out: list[str] = []
    used = 0
    for line in reversed(lines):
        if used + len(line) > budget:
            break
        out.append(line)
        used += len(line) + 1
    return "\n".join(reversed(out))


def _render_markdown(label: str, record: dict[str, Any], summary: str) -> str:
    """Render the checkpoint as markdown TEXT. Pure — no file I/O; the caller
    writes it (via write_markdown) only after the DB row is committed."""

    def bullets(items: list[str]) -> list[str]:
        return items if items else ["(none)"]

    delta = record.get("new_since_last") or {}
    since_lines: list[str] = []
    if record.get("since_ms"):
        since_lines = [
            "## Since the last checkpoint",
            f"{delta.get('event_count', 0)} events, "
            f"{len(delta.get('files', []))} files changed.",
            "",
            "### New prompts",
            *bullets([f"- {p}" for p in delta.get("prompts", [])]),
            "",
            "### New commands",
            *bullets([f"- `{c}`" for c in delta.get("commands", [])]),
            "",
        ]

    lines = [
        f"# Checkpoint: {label}",
        "",
        f"_{summary}_",
        "",
        "## Intent",
        record.get("intention") or "(none recorded)",
        "",
        *since_lines,
        "## All prompts this session",
        *bullets([f"- {p}" for p in record["prompts"]]),
        "",
        "## Commands run",
        *bullets([f"- `{c}`" for c in record["commands"]]),
        "",
        "## Files changed",
        *bullets([f"- {f['path']} ({f['edits']}x)" for f in record["files"]]),
        "",
        "## Tools",
        *bullets([f"- {t['count']}x {t['tool']}" for t in record["tools"]]),
        "",
        "## Git",
    ]
    if record.get("git"):
        lines += [
            f"- repo: {record['repo']}",
            f"- branch: {record['branch']}",
            f"- last commit: {record.get('last_commit') or '(none)'}",
            f"- uncommitted files: {len(record.get('dirty_files', []))}",
        ]
    else:
        lines.append(f"- not a git repo ({record.get('path')})")
    return "\n".join(lines) + "\n"


def _checkpoints_root() -> Path:
    return paths.home() / "checkpoints"


def write_markdown(session_key: str, now_ms: int, text: str) -> str | None:
    """Write a checkpoint's markdown under DUCKTERM_HOME/checkpoints/<key>/ and
    return its path RELATIVE to that root (stored in the row so it survives a
    moved home). Called AFTER the DB row is inserted; returns None on write
    failure so the caller leaves markdown_path NULL rather than orphaning a row.
    Also refreshes latest.md for the session."""
    dest = _checkpoints_root() / session_key
    try:
        dest.mkdir(parents=True, exist_ok=True)
        name = f"checkpoint-{now_ms}.md"
        (dest / name).write_text(text)
        (dest / "latest.md").write_text(text)
    except OSError:
        return None
    return f"{session_key}/{name}"


def markdown_dir(session_key: str) -> Path:
    """Where a session's checkpoint markdown lives — for the delete cascade."""
    return _checkpoints_root() / session_key


def load_record(markdown_path: str) -> str:
    """Read a checkpoint's markdown. `markdown_path` is relative to the
    checkpoints root; legacy absolute paths (from before the stable-root move)
    are still honored so old rows keep rendering."""
    p = Path(markdown_path)
    if not p.is_absolute():
        p = _checkpoints_root() / markdown_path
    return p.read_text() if p.is_file() else ""
