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
_ESCAPE = re.compile(rb"\\(?:([0-3][0-7]{2})|(\\))")
_MAX_SNAPSHOT = 8 * 1024 * 1024
_MAX_QUEUED_CHUNKS = 32
_SGR = re.compile(rb"\x1b\[[0-9;:]*m")


class Snapshot(bytes):
    """A byte-compatible repaint with the grid dimensions needed to replay it."""

    cols: int
    rows: int

    def __new__(cls, data: bytes, cols: int, rows: int) -> "Snapshot":
        value = super().__new__(cls, data)
        value.cols, value.rows = cols, rows
        return value


def unescape(payload: bytes) -> bytes:
    """Decode octal output and capture-pane's doubled backslashes.

    Unknown escapes remain literal: a future tmux encoding must not terminate
    a viewer. Capture -C uses doubled backslashes, unlike %output's \134.
    """
    return _ESCAPE.sub(lambda match: bytes([int(match[1], 8)]) if match[1] else b"\\", payload)


class Decoder:
    """Consume identity, physical/joined captures, pending escapes, then live output."""

    def __init__(self) -> None:
        self.guard: bytes | None = None
        self.completed = 0
        self.screen: list[bytes] = []
        self.pending: list[bytes] = []
        self.logical: list[bytes] = []
        self.physical_text = b""
        self.logical_size = 0
        self.width = 0
        self.size = 0
        self.pane: bytes | None = None
        self.cursor_x = 0
        self.cursor_y = 0
        self.height = 0
        self.screen_lines = 0

    def accept(self, line: bytes) -> bytes | None:
        guard = _GUARD.fullmatch(line)
        if self.guard is not None:
            screen_data = (self.completed == 2 and len(self.screen) < self.screen_lines) or (
                self.completed == 3 and self.logical_size < len(self.physical_text)
            )
            if (
                not screen_data
                and guard
                and guard[2] == self.guard
                and guard[1] in (b"end", b"error")
            ):
                if guard[1] == b"error":
                    raise ValueError("tmux terminal attachment command failed")
                if self.completed == 2:
                    self.physical_text = b"".join(_SGR.sub(b"", row) for row in self.screen)
                if (
                    self.completed == 3
                    and b"".join(_SGR.sub(b"", row) for row in self.logical) != self.physical_text
                ):
                    raise ValueError("tmux joined snapshot differs from physical capture")
                self.guard = None
                self.completed += 1
                if self.completed == 5:
                    return self.snapshot()
                return None
            if self.completed == 1:
                match = re.fullmatch(rb"(%\d+) (\d+) (\d+) (\d+) (\d+) (\d+)", line)
                if match is None or self.pane is not None:
                    raise ValueError("invalid tmux pane identity")
                self.pane = match[1]
                self.cursor_x, self.cursor_y, self.width, self.height, history = map(
                    int, match.groups()[1:]
                )
                self.screen_lines = self.height + min(history, 2000)
                if (
                    self.width < 1
                    or self.height < 1
                    or self.cursor_y >= self.height
                    or self.cursor_x > self.width
                ):
                    raise ValueError("invalid tmux cursor position")
            if self.completed == 2 and len(self.screen) >= self.screen_lines:
                raise ValueError("invalid tmux snapshot length")
            if self.completed in (2, 3, 4):
                decoded = unescape(line)
                self.size += len(decoded)
                if self.size > _MAX_SNAPSHOT:
                    raise ValueError("tmux snapshot exceeds terminal buffer limit")
                if self.completed == 2:
                    self.screen.append(decoded)
                elif self.completed == 3:
                    self.logical.append(decoded)
                    self.logical_size += len(_SGR.sub(b"", decoded))
                    if (
                        self.logical_size > len(self.physical_text)
                        or len(self.logical) > self.screen_lines
                    ):
                        raise ValueError("invalid tmux joined snapshot length")
                else:
                    self.pending.append(decoded)
            return None
        if guard and guard[1] == b"begin":
            if self.completed >= 5:
                raise ValueError("unexpected tmux command reply")
            self.guard = guard[2]
            return None
        if line.startswith((b"%pause ", b"%exit")):
            raise EOFError("tmux control viewer disconnected or paused")
        if self.completed == 5 and line.startswith((b"%output ", b"%extended-output ")):
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

    def snapshot(self) -> Snapshot:
        # Replay at the captured grid width, then let the browser reflow. Keep
        # the cursor's blank line, but omit unused rows below it: replaying those
        # rows would push useful text into scrollback after a reconnect/resize.
        cursor_row = max(0, len(self.screen) - self.height) + self.cursor_y
        physical, logical = list(self.screen), list(self.logical)
        while (
            len(physical) > cursor_row + 1
            and not physical[-1].strip()
            and logical
            and not logical[-1].strip()
        ):
            physical.pop()
            logical.pop()
        up = max(0, len(physical) - 1 - cursor_row)
        # A cursor at width is tmux's pending autowrap position. When replay
        # already ends there, moving it would discard that pending wrap.
        if self.cursor_x == self.width and not up:
            position = b""
        else:
            position = (f"\x1b[{up}A".encode() if up else b"") + b"\r"
            if self.cursor_x:
                position += f"\x1b[{self.cursor_x}C".encode()
        data = b"\x1b[0m\x1b[2J\x1b[H" + b"\r\n".join(logical) + position + b"".join(self.pending)
        return Snapshot(data, self.width, self.height)


async def stream(target: str) -> AsyncGenerator[bytes, None]:
    """Close only this viewer on failure; the browser reconnects with a new snapshot.

    ignore-size leaves pane sizing unchanged. The private command channel only
    captures output; read-only would also block send-keys in tmux 3.7.
    pause-after bounds tmux's backlog for a stalled viewer; %pause terminates
    this feed rather than silently leaving the browser attached to paused output.
    """
    pane_target = tmux.exact_target(target, pane=True)
    target = tmux.exact_target(target)
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
            pane_target,
            "#{pane_id} #{cursor_x} #{cursor_y} #{pane_width} #{pane_height} #{history_size}",
            ";",
            "capture-pane",
            "-p",
            "-e",
            "-N",
            "-T",
            "-C",
            "-t",
            pane_target,
            "-S",
            "-2000",
            ";",
            "capture-pane",
            "-p",
            "-e",
            "-J",
            "-C",
            "-t",
            pane_target,
            "-S",
            "-2000",
            ";",
            "capture-pane",
            "-p",
            "-P",
            "-C",
            "-t",
            pane_target,
        ),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        # -J can put a whole wrapped paragraph on one control-protocol line.
        # Allow its escaped representation; decoded captures remain bounded.
        limit=4 * _MAX_SNAPSHOT,
    )
    queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=_MAX_QUEUED_CHUNKS)

    async def drain() -> None:
        # Read independently of WebSocket writes and resize requests. A slow
        # browser must never leave a live tmux client with nobody draining it.
        assert proc.stdout is not None
        decoder = Decoder()
        try:
            while True:
                if decoder.completed < 5:
                    line = await asyncio.wait_for(proc.stdout.readline(), 10)
                else:
                    line = await proc.stdout.readline()
                if not line:
                    break
                if not line.endswith(b"\n"):
                    raise ValueError("truncated tmux control frame")
                chunk = decoder.accept(line[:-1])
                if chunk:
                    queue.put_nowait(chunk)
                    # Buffered readline() may complete without yielding. Let a
                    # ready consumer drain before judging it a stalled viewer.
                    await asyncio.sleep(0)
        except (EOFError, asyncio.QueueFull):
            # Close only this viewer. A fresh connection gets a new snapshot;
            # never drop arbitrary output and continue a corrupted screen.
            pass
        except (ValueError, TimeoutError) as exc:
            print(
                f"[duckterm] ordered terminal stream ended: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
        finally:
            if proc.stdin is not None:
                proc.stdin.close()
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    proc.terminate()
            try:
                # communicate drains remaining pipe bytes; wait() alone can
                # hang after exit when asyncio has paused a full stdout pipe.
                await asyncio.wait_for(proc.communicate(), 2)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                await proc.communicate()
            if queue.full():
                while not queue.empty():
                    queue.get_nowait()
            queue.put_nowait(None)

    reader = asyncio.create_task(drain())
    try:
        while True:
            chunk = await queue.get()
            if chunk is None:
                break
            yield chunk
    finally:
        if not reader.done():
            reader.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reader
