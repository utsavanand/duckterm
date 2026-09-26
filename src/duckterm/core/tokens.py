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

FIELDS = ("input", "cache_read", "cache_write", "output")


@dataclass
class _FileState:
    offset: int = 0
    days: dict[str, dict[str, int]] = field(default_factory=dict)
    seen: set[tuple[str, str]] = field(default_factory=set)
    codex_prev: dict[str, int] = field(default_factory=dict)


def _day(ts: object) -> str | None:
    if not isinstance(ts, str) or len(ts) < 10:
        return None
    return ts[:10]


def _add(state: _FileState, day: str, usage: dict[str, int]) -> None:
    bucket = state.days.setdefault(day, dict.fromkeys(FIELDS, 0))
    for k in FIELDS:
        bucket[k] += max(0, usage.get(k, 0))


def _claude_line(state: _FileState, obj: dict[str, object]) -> None:
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
    )


def _codex_line(state: _FileState, obj: dict[str, object]) -> None:
    payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else obj
    if not isinstance(payload, dict) or payload.get("type") != "token_count":
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
    _add(state, day, delta)


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
            if b"usage" not in raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                handle_line(state, obj)
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
