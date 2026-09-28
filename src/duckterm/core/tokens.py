"""Token usage across Claude Code and Codex transcripts, for the control tower.

Both agents write their own transcripts; duckterm only reads them. Totals are
kept per file and per UTC day, and each rescan reads only the bytes appended
since the last one, so a refresh after the first scan costs little.

Claude Code writes one transcript line per content block of an assistant
reply, and every line repeats the reply's usage. Counting lines doubled the
total, so usage is counted once per (message id, request id).

Codex records a running total (`total_token_usage`) on each `token_count`
event; the usage of an event is its total minus the previous one.
"""

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

FIELDS = ("input", "cache_read", "cache_write", "output")


@dataclass
class _FileState:
    offset: int = 0
    days: dict[str, dict[str, int]] = field(default_factory=dict)
    seen: set[tuple[str, str]] = field(default_factory=set)
    models: dict[tuple[str, str], dict[str, int]] = field(default_factory=dict)
    native_id: str = ""
    model: str = ""
    codex_prev: dict[str, int] = field(default_factory=dict)


def _day(ts: object) -> str | None:
    if not isinstance(ts, str) or len(ts) < 10:
        return None
    try:
        stamp = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            return None
        return stamp.astimezone(UTC).date().isoformat()
    except ValueError:
        return None


def _add(state: _FileState, day: str, usage: dict[str, int], model: str = "") -> None:
    bucket = state.days.setdefault(day, dict.fromkeys(FIELDS, 0))
    detail = state.models.setdefault((day, model), dict.fromkeys(FIELDS, 0))
    for k in FIELDS:
        value = max(0, usage.get(k, 0))
        bucket[k] += value
        detail[k] += value


def _claude_line(state: _FileState, obj: dict[str, object]) -> None:
    if isinstance(obj.get("sessionId"), str):
        state.native_id = str(obj["sessionId"])
    if obj.get("type") != "assistant":
        return
    message = obj.get("message")
    if not isinstance(message, dict):
        return
    usage = message.get("usage")
    day = _day(obj.get("timestamp"))
    if not isinstance(usage, dict) or day is None:
        return
    key = (str(message.get("id") or ""), str(obj.get("requestId") or ""))
    if key != ("", "") and key in state.seen:
        return
    state.seen.add(key)
    _add(
        state,
        day,
        {
            "input": int(usage.get("input_tokens") or 0),
            "cache_read": int(usage.get("cache_read_input_tokens") or 0),
            "cache_write": int(usage.get("cache_creation_input_tokens") or 0),
            "output": int(usage.get("output_tokens") or 0),
        },
        model=str(message["model"]) if isinstance(message.get("model"), str) else "",
    )


def _codex_line(state: _FileState, obj: dict[str, object]) -> None:
    payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else obj
    if not isinstance(payload, dict):
        return
    if obj.get("type") == "session_meta" and isinstance(payload.get("id"), str):
        state.native_id = payload["id"]
    if obj.get("type") == "turn_context":
        state.model = str(payload["model"]) if isinstance(payload.get("model"), str) else ""
    if payload.get("type") != "token_count":
        return
    info = payload.get("info")
    total = info.get("total_token_usage") if isinstance(info, dict) else None
    day = _day(obj.get("timestamp"))
    if not isinstance(total, dict) or day is None:
        return
    # Codex's input_tokens includes the cached part.
    now = {
        "cache_read": int(total.get("cached_input_tokens") or 0),
        "input": int(total.get("input_tokens") or 0) - int(total.get("cached_input_tokens") or 0),
        "cache_write": int(total.get("cache_write_input_tokens") or 0),
        "output": int(total.get("output_tokens") or 0),
    }
    delta = {k: now[k] - state.codex_prev.get(k, 0) for k in FIELDS}
    state.codex_prev = now
    _add(state, day, delta, model=state.model)


class TokenLedger:
    def __init__(self, claude_root: Path, codex_root: Path) -> None:
        self.roots = {"claude-code": claude_root, "codex": codex_root}
        self._files: dict[Path, _FileState] = {}

    def _paths(self, agent: str) -> list[Path]:
        root = self.roots[agent]
        if agent == "claude-code":
            # Sub-agent transcripts live in <session>/subagents/.
            return sorted(root.glob("**/*.jsonl"))
        return sorted(root.glob("**/rollout-*.jsonl"))

    def _scan(self, agent: str, path: Path, since_mtime: float) -> _FileState | None:
        try:
            stat = path.stat()
        except OSError:
            return None
        state = self._files.get(path)
        if state is None:
            if stat.st_mtime < since_mtime:
                return None
            state = self._files[path] = _FileState()
        if stat.st_size < state.offset:  # rewritten from scratch
            state = self._files[path] = _FileState()
        if stat.st_size == state.offset:
            return state
        handle_line = _claude_line if agent == "claude-code" else _codex_line
        with path.open("rb") as fh:
            fh.seek(state.offset)
            data = fh.read(stat.st_size - state.offset)
        # Leave a trailing partial line for the next scan.
        end = data.rfind(b"\n") + 1
        for raw in data[:end].splitlines():
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                try:
                    handle_line(state, obj)
                except (TypeError, ValueError, OverflowError):
                    continue  # malformed usage must not break the whole page
        state.offset += end
        return state

    def totals(self, days: int, now: float) -> dict[str, dict[str, int]]:
        """Usage per agent over the last `days` UTC days, today included."""
        cutoff_day = datetime.fromtimestamp(now - (days - 1) * 86400, UTC).strftime("%Y-%m-%d")
        out: dict[str, dict[str, int]] = {}
        for agent in self.roots:
            sums = dict.fromkeys(FIELDS, 0)
            for path in self._paths(agent):
                state = self._scan(agent, path, since_mtime=now - days * 86400)
                if state is None:
                    continue
                for day, bucket in state.days.items():
                    if day >= cutoff_day:
                        for k in FIELDS:
                            sums[k] += bucket[k]
            out[agent] = sums
        return out

    def analytics(
        self,
        days: int | None,
        now: float,
        identities: list[dict[str, Any]],
        *,
        agent: str = "",
        folder: str = "",
        session: str = "",
    ) -> dict[str, Any]:
        """Exact model IDs per usage event; never infer a model from the harness.

        Identity mapping uses recorded native IDs only, never directory recency.
        Ambiguous/unmapped transcripts remain Outside DuckTerm; test IDs are excluded.
        Folder/name metadata describes the session's current placement.
        """
        today = datetime.fromtimestamp(now, UTC).date().isoformat()
        cutoff = (
            _day(datetime.fromtimestamp(now - (days - 1) * 86400, UTC).isoformat())
            if days
            else None
        )
        mapping: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for entry in identities:
            for native in entry["native_ids"]:
                mapping.setdefault((entry["runtime"], native), []).append(entry)
        rows: list[dict[str, Any]] = []
        earliest: str | None = None
        options: dict[str, dict[str, str]] = {"agents": {}, "folders": {}, "sessions": {}}
        for harness in self.roots:
            for path in self._paths(harness):
                state = self._scan(harness, path, since_mtime=0)
                if state is None:
                    continue
                # Claude files use the native session UUID as the filename; helper
                # transcripts carry the parent sessionId in their records.
                native = state.native_id or (path.stem if harness == "claude-code" else "")
                matches = mapping.get((harness, native), [])
                if any(i.get("test") for i in matches):
                    continue
                identity = matches[0] if len(matches) == 1 else None
                key = identity["key"] if identity else "outside"
                name = identity["name"] if identity else "Outside DuckTerm"
                group = (identity["folder"] or "ungrouped") if identity else "outside"
                group_name = (identity["folder"] or "Ungrouped") if identity else "Outside DuckTerm"
                options["agents"][harness] = {"codex": "Codex", "claude-code": "Claude Code"}[
                    harness
                ]
                options["folders"][group] = group_name
                options["sessions"][key] = name
                for (day, model), usage in state.models.items():
                    if day > today or not any(usage.values()):
                        continue
                    earliest = min(earliest, day) if earliest else day
                    if (cutoff and day < cutoff) or (agent and agent != harness):
                        continue
                    if (folder and folder != group) or (session and session != key):
                        continue
                    rows.append(
                        dict(
                            day=day,
                            model=model or None,
                            agent=harness,
                            session=key,
                            session_name=name,
                            folder=group,
                            folder_name=group_name,
                            **usage,
                        )
                    )
        return dict(
            version=1,
            timezone="UTC",
            days=days,
            today=today,
            earliest_day=earliest,
            rows=rows,
            options={
                kind: [dict(value=k, label=v) for k, v in sorted(values.items())]
                for kind, values in options.items()
            },
        )
