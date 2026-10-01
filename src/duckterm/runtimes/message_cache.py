"""Bounded incremental JSONL message reads, following TokenLedger's offset pattern."""

import hashlib
import json
from collections import OrderedDict
from collections.abc import Callable, Iterable
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock

Parser = Callable[[Iterable[str], int], list[dict[str, object]]]


@dataclass
class _State:
    identity: tuple[int, int]
    stamp: tuple[int, int, int] = (0, 0, 0)
    digest: bytes = field(default_factory=lambda: hashlib.sha256().digest())
    offset: int = 0
    lines: int = 0
    records: list[dict[str, object]] = field(default_factory=list)
    trailing: list[dict[str, object]] = field(default_factory=list)
    response: bytes | None = None
    response_scope: tuple[str, str | None] | None = None


class MessageCache:
    """Keep at most eight transcripts / 128 MiB of source per runtime.

    Unchanged files require only stat. Appends reread from the last complete
    line, so a partially written JSON record is retried without duplicate IDs.
    Changed files verify the committed prefix before parsing the new suffix;
    a growing rewrite must not retain stale message contents or pin identities.
    Returned records are deeply detached, including nested tool input and blocks.
    """

    def __init__(self, parser: Parser, max_files: int = 8, max_bytes: int = 128 << 20) -> None:
        self._parser = parser
        self._max_files = max_files
        self._max_bytes = max_bytes
        self._states: OrderedDict[Path, _State] = OrderedDict()
        self._lock = RLock()

    def read(self, path: Path) -> list[dict[str, object]]:
        with self._lock:
            state = self._state(path.resolve())
            return deepcopy([*state.records, *state.trailing]) if state else []

    def response(self, path: Path, runtime: str, session_id: str | None) -> bytes:
        """Immutable JSON HTTP payload; unchanged polls never copy message trees."""
        with self._lock:
            state = self._state(path.resolve())
            if state is None:
                return b'{"messages": []}'
            scope = (runtime, session_id)
            if state.response is None or state.response_scope != scope:
                messages = []
                for record in (*state.records, *state.trailing):
                    identity = json.dumps(
                        [runtime, session_id, record], sort_keys=True, ensure_ascii=True
                    ).encode()
                    messages.append({**record, "message_key": hashlib.sha256(identity).hexdigest()})
                state.response = json.dumps({"messages": messages}).encode()
                state.response_scope = scope
            return state.response

    def _state(self, path: Path) -> _State | None:
        try:
            stat = path.stat()
            identity = (stat.st_dev, stat.st_ino)
            stamp = (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
            state = self._states.get(path)
            if state is not None and state.identity == identity and state.stamp == stamp:
                self._states.move_to_end(path)
                return state
            if state is None or state.identity != identity or stat.st_size <= state.stamp[0]:
                state = _State(identity)
            with path.open("rb") as source:
                # Size growth alone does not establish an append: transcript
                # rewrites can grow too. Hash the committed prefix in bounded
                # chunks, without reparsing JSON or retaining raw old bytes.
                digest = hashlib.sha256()
                remaining = state.offset
                while remaining:
                    chunk = source.read(min(remaining, 1024 * 1024))
                    if not chunk:
                        break
                    digest.update(chunk)
                    remaining -= len(chunk)
                if remaining or digest.digest() != state.digest:
                    state = _State(identity)
                    digest = hashlib.sha256()
                    source.seek(0)
                data = source.read(stat.st_size - state.offset)
            end = data.rfind(b"\n") + 1
            lines = data[:end].decode(errors="replace").splitlines()
            state.records.extend(self._parser(lines, state.lines))
            state.lines += len(lines)
            state.offset += end
            digest.update(data[:end])
            state.digest = digest.digest()
            # Preserve the full parser's support for a valid final record
            # without a newline, but do not commit that potentially partial line.
            state.trailing = self._parser(
                data[end:].decode(errors="replace").splitlines(), state.lines
            )
            state.stamp = stamp
            state.response = None
            state.response_scope = None
            self._states[path] = state
            self._states.move_to_end(path)
            while (
                len(self._states) > self._max_files
                or sum(item.stamp[0] for item in self._states.values()) > self._max_bytes
            ):
                self._states.popitem(last=False)
            return state
        except OSError:
            self._states.pop(path, None)
            return None


def unavailable_response(session_id: str | None) -> bytes:
    """An absent identity/file must not be disguised as another conversation."""
    return json.dumps(
        {
            "messages": [],
            "transcript": {
                "status": "not_found" if session_id else "identity_missing",
                "reason": (
                    "The recorded conversation transcript is unavailable on this machine."
                    if session_id
                    else "No conversation ID has been recorded for this session yet."
                ),
            },
        }
    ).encode()
