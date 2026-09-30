"""Bounded incremental JSONL message reads, following TokenLedger's offset pattern."""

from collections import OrderedDict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock

Parser = Callable[[Iterable[str], int], list[dict[str, object]]]


@dataclass
class _State:
    identity: tuple[int, int]
    stamp: tuple[int, int, int] = (0, 0, 0)
    offset: int = 0
    lines: int = 0
    records: list[dict[str, object]] = field(default_factory=list)
    trailing: list[dict[str, object]] = field(default_factory=list)


class MessageCache:
    """Keep at most eight transcripts / 128 MiB of source per runtime.

    Unchanged files require only stat. Appends reread from the last complete
    line, so a partially written JSON record is retried without duplicate IDs.
    Inode replacement, truncation and same-size edits reset the parser state.
    Returned record dicts are separate: the server adds session-specific keys.
    """

    def __init__(self, parser: Parser, max_files: int = 8, max_bytes: int = 128 << 20) -> None:
        self._parser = parser
        self._max_files = max_files
        self._max_bytes = max_bytes
        self._states: OrderedDict[Path, _State] = OrderedDict()
        self._lock = RLock()

    def read(self, path: Path) -> list[dict[str, object]]:
        path = path.resolve()
        with self._lock:
            try:
                stat = path.stat()
                identity = (stat.st_dev, stat.st_ino)
                stamp = (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
                state = self._states.get(path)
                if state is not None and state.identity == identity and state.stamp == stamp:
                    self._states.move_to_end(path)
                    return [dict(record) for record in (*state.records, *state.trailing)]
                if state is None or state.identity != identity or stat.st_size <= state.stamp[0]:
                    state = _State(identity)
                with path.open("rb") as source:
                    source.seek(state.offset)
                    data = source.read(stat.st_size - state.offset)
                end = data.rfind(b"\n") + 1
                lines = data[:end].decode(errors="replace").splitlines()
                state.records.extend(self._parser(lines, state.lines))
                state.lines += len(lines)
                state.offset += end
                # Preserve the full parser's support for a valid final record
                # without a newline, but do not commit that potentially partial line.
                state.trailing = self._parser(
                    data[end:].decode(errors="replace").splitlines(), state.lines
                )
                state.stamp = stamp
                self._states[path] = state
                self._states.move_to_end(path)
                while (
                    len(self._states) > self._max_files
                    or sum(item.stamp[0] for item in self._states.values()) > self._max_bytes
                ):
                    self._states.popitem(last=False)
                return [dict(record) for record in (*state.records, *state.trailing)]
            except OSError:
                self._states.pop(path, None)
                return []
