"""An ordered tmux snapshot followed by live bytes, for one terminal viewer.

Control-mode replies and %output share tmux's ordered queue. File-tail bytes do
not: a capture can include bytes still in flight to the spool. Never mix them.
The existing pipe remains available for state detection and persistent logs.
"""

import asyncio
import contextlib
import re
import sys
from collections.abc import AsyncGenerator

from duckterm.agents import tmux

_GUARD = re.compile(rb"%(begin|end|error) (\d+ \d+ \d+)\Z")
_OCTAL = re.compile(rb"\\([0-3][0-7]{2})")
_MAX_SNAPSHOT = 8 * 1024 * 1024


def unescape(payload: bytes) -> bytes:
    """Decode tmux's byte escapes, preserving UTF-8 and literal backslashes."""
    parts: list[bytes] = []
    position = 0
    for match in _OCTAL.finditer(payload):
        plain = payload[position : match.start()]
        if b"\\" in plain:
            raise ValueError("invalid tmux control escape")
        parts.extend((plain, bytes([int(match[1], 8)])))
        position = match.end()
    plain = payload[position:]
    if b"\\" in plain:
        raise ValueError("invalid tmux control escape")
    parts.append(plain)
    return b"".join(parts)


class Decoder:
    """Consume attach, pane identity, screen, pending escapes, then live output."""

    def __init__(self) -> None:
        self.guard: bytes | None = None
        self.completed = 0
        self.screen: list[bytes] = []
        self.pending: list[bytes] = []
        self.size = 0
        self.pane: bytes | None = None
        self.cursor_x = 0
        self.cursor_y = 0
        self.height = 0
        self.screen_lines = 0

    def accept(self, line: bytes) -> bytes | None:
        guard = _GUARD.fullmatch(line)
        if self.guard is not None:
            screen_data = self.completed == 2 and len(self.screen) < self.screen_lines
            if (
                not screen_data
                and guard
                and guard[2] == self.guard
                and guard[1] in (b"end", b"error")
            ):
                if guard[1] == b"error":
                    raise ValueError("tmux terminal attachment command failed")
                self.guard = None
                self.completed += 1
                if self.completed == 4:
                    return self.snapshot()
                return None
            if self.completed == 1:
                match = re.fullmatch(rb"(%\d+) (\d+) (\d+) (\d+) (\d+)", line)
                if match is None or self.pane is not None:
                    raise ValueError("invalid tmux pane identity")
                self.pane = match[1]
                self.cursor_x, self.cursor_y, self.height, history = map(int, match.groups()[1:])
                self.screen_lines = self.height + min(history, 2000)
                if self.height < 1 or self.cursor_y >= self.height:
                    raise ValueError("invalid tmux cursor position")
            if self.completed == 2 and len(self.screen) >= self.screen_lines:
                raise ValueError("invalid tmux snapshot length")
            if self.completed in (2, 3):
                decoded = unescape(line)
                self.size += len(decoded)
                if self.size > _MAX_SNAPSHOT:
                    raise ValueError("tmux snapshot exceeds terminal buffer limit")
                (self.screen if self.completed == 2 else self.pending).append(decoded)
            return None
        if guard and guard[1] == b"begin":
            if self.completed >= 4:
                raise ValueError("unexpected tmux command reply")
            self.guard = guard[2]
            return None
        if line.startswith((b"%pause ", b"%exit")):
            raise EOFError("tmux control viewer disconnected or paused")
        if self.completed == 4 and line.startswith((b"%output ", b"%extended-output ")):
            if line.startswith(b"%extended-output "):
                header, separator, payload = line.partition(b" : ")
                fields = header.split(b" ")
                if not separator or len(fields) < 3:
                    raise ValueError("invalid extended tmux output frame")
            else:
                fields = line.split(b" ", 2)
                if len(fields) != 3:
                    raise ValueError("invalid tmux output frame")
                payload = fields[2]
            if fields[1] == self.pane:
                return unescape(payload)
        return None

    def snapshot(self) -> bytes:
        # Keep the cursor's blank line: stripping it would join the next live
        # line onto the preceding snapshot line. Restore column/row for TUIs
        # whose cursor is not at the end of the last visible text.
        cursor_row = max(0, len(self.screen) - self.height) + self.cursor_y
        while len(self.screen) > cursor_row + 1 and not self.screen[-1].strip():
            self.screen.pop()
        up = max(0, len(self.screen) - 1 - cursor_row)
        position = (f"\x1b[{up}A".encode() if up else b"") + b"\r"
        if self.cursor_x:
            position += f"\x1b[{self.cursor_x}C".encode()
        return b"\x1b[2J\x1b[H" + b"\r\n".join(self.screen) + position + b"".join(self.pending)


async def stream(target: str) -> AsyncGenerator[bytes, None]:
    """Close only this viewer on failure; the browser reconnects with a new snapshot.

    ignore-size leaves pane sizing unchanged. The private command channel only
    captures output; read-only would also block send-keys in tmux 3.7.
    pause-after bounds tmux's backlog for a stalled viewer; %pause terminates
    this feed rather than silently leaving the browser attached to paused output.
    """
    proc = await asyncio.create_subprocess_exec(
        *tmux.client_command(
            "-C",
            "attach-session",
            "-f",
            "ignore-size,pause-after=5",
            "-t",
            target,
            ";",
            "display-message",
            "-p",
            "-t",
            target,
            "#{pane_id} #{cursor_x} #{cursor_y} #{pane_height} #{history_size}",
            ";",
            "capture-pane",
            "-p",
            "-e",
            "-C",
            "-t",
            target,
            "-S",
            "-2000",
            ";",
            "capture-pane",
            "-p",
            "-P",
            "-C",
            "-t",
            target,
        ),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        limit=256 * 1024,
    )
    assert proc.stdout is not None
    decoder = Decoder()
    try:
        while True:
            # A command failure must not leave the browser waiting forever for
            # its first frame. Idle *live* terminals have no read timeout.
            if decoder.completed < 4:
                line = await asyncio.wait_for(proc.stdout.readline(), 10)
            else:
                line = await proc.stdout.readline()
            if not line:
                return
            if not line.endswith(b"\n"):
                raise ValueError("truncated tmux control frame")
            chunk = decoder.accept(line[:-1])
            if chunk:
                yield chunk
    except EOFError:
        return
    except (ValueError, TimeoutError) as exc:
        # No fallback to the file tail: that would reintroduce the overlap.
        print(
            f"[duckterm] ordered terminal stream ended: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return
    finally:
        if proc.stdin is not None:
            proc.stdin.close()
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), 2)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
