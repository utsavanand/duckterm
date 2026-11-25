"""Bounded incremental JSONL message reads, following TokenLedger's offset pattern."""

import hashlib
import json
import time
from collections import OrderedDict
from collections.abc import Callable, Iterable
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock

Parser = Callable[[Iterable[str], int], list[dict[str, object]]]
# Fast writes can share even nanosecond-valued mtime/ctime on Linux. Only
# trust stat after checking the bytes beyond this filesystem timestamp window.
STAMP_WINDOW_NS = 2_000_000_000
READ_CHUNK = 1024 * 1024


@dataclass
class _State:
    identity: tuple[int, int]
    stamp: tuple[int, int, int] = (0, 0, 0)
    digest: bytes = field(default_factory=lambda: hashlib.sha256().digest())
    content_digest: bytes = b""
    verified_at: int = 0
    offset: int = 0
    lines: int = 0
    records: list[dict[str, object]] = field(default_factory=list)
    trailing: list[dict[str, object]] = field(default_factory=list)
    response: bytes | None = None
    response_scope: tuple[str, str | None] | None = None


class MessageCache:
    """Keep at most eight transcripts / 128 MiB of source per runtime.

    Settled unchanged files require only stat. Recent files verify their bytes
    until a read occurs beyond the timestamp collision window. Appends parse
    from the last complete line, so partial records retry without duplicate IDs.
    Changed files verify the committed prefix before parsing the new suffix;
    a growing rewrite must not retain stale message contents or pin identities.
    Returned records are deeply detached, including nested tool input and blocks.
    Raw input is buffered by chunk plus the longest unfinished line, rather than
    the whole transcript. Parsed records and complete responses remain in memory.
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

    def _make_room(self, path: Path, size: int) -> None:
        """Free unrelated LRU records before allocating the incoming transcript."""
        other_bytes = sum(item.stamp[0] for key, item in self._states.items() if key != path)
        other_files = len(self._states) - int(path in self._states)
        for key in tuple(self._states):
            if other_files + 1 <= self._max_files and other_bytes + size <= self._max_bytes:
                break
            if key != path:
                other_bytes -= self._states[key].stamp[0]
                other_files -= 1
                del self._states[key]

    def _state(self, path: Path) -> _State | None:
        try:
            stat = path.stat()
            identity = (stat.st_dev, stat.st_ino)
            stamp = (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
            state = self._states.get(path)
            if (
                state is not None
                and state.identity == identity
                and state.stamp == stamp
                and state.verified_at > max(stamp[1:]) + STAMP_WINDOW_NS
            ):
                self._states.move_to_end(path)
                return state
            if state is None or state.identity != identity or stat.st_size < state.stamp[0]:
                self._states.pop(path, None)
                state = _State(identity)
            self._make_room(path, stat.st_size)
            verified_at = time.time_ns()
            with path.open("rb") as source:
                # Size growth alone does not establish an append: transcript
                # rewrites can grow too. Hash the committed prefix in bounded
                # chunks, without reparsing JSON or retaining raw old bytes.
                digest = hashlib.sha256()
                remaining = state.offset
                while remaining:
                    chunk = source.read(min(remaining, READ_CHUNK))
                    if not chunk:
                        break
                    digest.update(chunk)
                    remaining -= len(chunk)
                if remaining or digest.digest() != state.digest:
                    self._states.pop(path, None)
                    state = _State(identity)
                    digest = hashlib.sha256()
                    source.seek(0)
                content_digest = digest.copy()
                remaining = stat.st_size - state.offset
                tail = bytearray()
                records: list[dict[str, object]] = []
                offset, line_count = state.offset, state.lines
                while remaining:
                    chunk = source.read(min(remaining, READ_CHUNK))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    content_digest.update(chunk)
                    end = chunk.rfind(b"\n") + 1
                    if end:
                        digest.update(tail)
                        digest.update(chunk[:end])
                        offset += len(tail) + end
                        tail.extend(chunk[:end])
                        # Keep splitlines' Unicode/CR semantics: annotations
                        # use its line indices, including blanks and bad JSON.
                        lines = tail.decode(errors="replace").splitlines()
                        records.extend(self._parser(lines, line_count))
                        line_count += len(lines)
                        tail.clear()
                    tail.extend(chunk[end:])
            state.verified_at = verified_at
            if content_digest.digest() == state.content_digest:
                state.stamp = stamp
                self._states.move_to_end(path)
                return state
            state.records.extend(records)
            state.lines = line_count
            state.offset = offset
            state.digest = digest.digest()
            state.content_digest = content_digest.digest()
            # Preserve the full parser's support for a valid final record
            # without a newline, but do not commit that potentially partial line.
            state.trailing = self._parser(tail.decode(errors="replace").splitlines(), state.lines)
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
