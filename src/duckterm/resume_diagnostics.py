"""Bounded, read-only resume checks for owner-reviewed bug reports."""

import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any

from duckterm.agents.hooks_install import hook_script_path
from duckterm.harnesses import REGISTRY
from duckterm.persistence.history import HistoryStore
from duckterm.runtimes.claude_code import project_slug

LIMIT = 50


def snapshot(history: HistoryStore, selected: str | None) -> dict[str, Any]:
    """Read SQLite on its owning thread; private inputs never enter the response."""
    count = history._conn.execute(
        "SELECT count(*) FROM sessions WHERE state IN ('interrupted','stopped')"
    ).fetchone()[0]
    rows = history._conn.execute(
        "SELECT session_key,runtime,state,cwd,worktree_path,restart_json FROM sessions "
        "WHERE state IN ('interrupted','stopped') "
        "ORDER BY session_key=? DESC, updated_at DESC, session_key LIMIT ?",
        (selected or "", LIMIT),
    ).fetchall()
    inputs = []
    for row in rows:
        row = dict(row)
        try:
            control = json.loads(row.pop("restart_json") or "{}")
            binding = control.get("native_binding")
            if binding is not None:
                native_id = binding.get("native_id")
            else:
                event = history._conn.execute(
                    "SELECT json_extract(payload_json,'$.session_id') AS sid FROM events "
                    "WHERE session_key=? AND json_valid(payload_json) AND sid IS NOT NULL "
                    "ORDER BY ts DESC LIMIT 1",
                    (row["session_key"],),
                ).fetchone()
                native_id = event["sid"] if event else None
            state = (
                "missing" if native_id is None else "empty-string" if native_id == "" else "present"
            )
            if (
                native_id is not None
                and (
                    not isinstance(native_id, str)
                    or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", native_id)
                )
                and native_id != ""
            ):
                state = "invalid"
        except (ValueError, TypeError, AttributeError):
            native_id, state = None, "unknown"
        row.update(
            native_id=native_id, native_id_state=state, selected=row.pop("session_key") == selected
        )
        inputs.append(row)
    return {"total": count, "sessions": inputs}


def hook_status(runtime: str, cwd: Path | None) -> str:
    """Inspect configured hook entries, never execute hooks or print settings."""
    from duckterm.bug_reports import read_regular

    spec = REGISTRY[runtime].hook_spec
    if spec is None:
        return "not applicable"
    candidates = [Path.home() / spec.global_rel]
    if cwd:
        candidates.append(cwd / spec.repo_rel)
    found = []
    for path in dict.fromkeys(candidates):
        try:
            config = json.loads(read_regular(path, 1024 * 1024))
            if not isinstance(config, dict):
                raise ValueError("Invalid settings")
            hooks = config.get("hooks", {})
            if not isinstance(hooks, dict):
                raise ValueError("Invalid hooks")
            for entries in hooks.values():
                if not isinstance(entries, list):
                    raise ValueError("Invalid hook entries")
                for entry in entries:
                    for hook in entry.get("hooks", [entry]):
                        for field in ("command", "bash"):
                            command = hook.get(field, "")
                            if isinstance(command, str) and "duckterm" in command:
                                # Configured does not establish trust or delivery.
                                found.append(
                                    "configured"
                                    if str(hook_script_path()) in command
                                    else "stale reference"
                                )
        except FileNotFoundError:
            continue
        except (OSError, ValueError, TypeError, AttributeError):
            found.append("unknown")
    return ", ".join(sorted(set(found))) if found else "not configured"


def render(data: dict[str, Any]) -> dict[str, str]:
    """Only file metadata/settings reads here; safe to run in a worker thread."""
    lines = [
        "Read-only snapshot; does not prove hooks are trusted or working.",
        "jq present: " + ("yes" if shutil.which("jq") else "no"),
    ]
    for runtime in REGISTRY:
        if REGISTRY[runtime].hook_spec:
            lines.append(f"{runtime} global hooks: {hook_status(runtime, None)}")
    lines.append(f"Interrupted/stopped sessions: {data['total']} (showing {len(data['sessions'])})")
    for index, row in enumerate(data["sessions"], 1):
        runtime = row["runtime"] if row["runtime"] in REGISTRY else "generic"
        source = "worktree" if row["worktree_path"] else "cwd" if row["cwd"] else "missing"
        cwd = Path(row["worktree_path"] or row["cwd"]) if source != "missing" else None
        slug, exists, transcript = (
            "not applicable",
            "unknown",
            "not checked (no usable recorded ID)",
        )
        try:
            exists = "yes" if cwd and cwd.is_dir() else "no"
            if runtime == "claude-code" and cwd:
                slug = "sha256:" + hashlib.sha256(project_slug(cwd).encode()).hexdigest()[:16]
            if row["native_id_state"] == "present":
                if runtime in {"claude-code", "codex"} and cwd:
                    path = REGISTRY[runtime](runtime).locate_transcript(
                        cwd=cwd, session_id=row["native_id"]
                    )
                    transcript = "yes" if path and path.is_file() else "no"
                else:
                    transcript = "not checked (no file-per-conversation lookup)"
        except (OSError, ValueError, RuntimeError):
            transcript = "unknown (filesystem check failed)"
        label = f"Session {index}" + (" (selected)" if row["selected"] else "")
        lines.extend(
            [
                f"{label}: {runtime}; {row['state']}",
                f"  native conversation ID: {row['native_id_state']}",
                f"  directory source: {source}; exists: {exists}",
                f"  expected transcript exists: {transcript}",
                f"  Claude project slug fingerprint: {slug}",
                f"  hooks (global/project): {hook_status(runtime, cwd)}",
            ]
        )
    return {
        "id": "resume-readiness",
        "label": "Resume readiness (no conversation IDs or contents)",
        "text": "\n".join(lines),
    }
