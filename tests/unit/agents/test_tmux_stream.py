import pytest

from duckterm.agents.tmux_stream import Decoder, unescape


def frame(decoder, number, lines=()):
    assert decoder.accept(f"%begin 123 {number} 0".encode()) is None
    for line in lines:
        assert decoder.accept(line) is None
    return decoder.accept(f"%end 123 {number} 0".encode())


def ready(screen=(b"draft",), metadata=b"%7 5 0 80 1 0", pending=()):
    decoder = Decoder()
    frame(decoder, 1)
    frame(decoder, 2, [metadata])
    frame(decoder, 3, screen)
    frame(decoder, 4, screen)
    snapshot = frame(decoder, 5, pending)
    return decoder, snapshot


def test_every_byte_and_unicode_roundtrip():
    raw = bytes(range(256)) + "🦆 café".encode() + rb"\015"
    encoded = b"".join(
        f"\\{byte:03o}".encode() if byte < 32 or byte == 92 else bytes([byte]) for byte in raw
    )
    assert unescape(encoded) == raw


@pytest.mark.parametrize("bad", [b"\\", rb"\12", rb"\400", rb"\999", rb"\x1b"])
def test_unknown_escapes_remain_literal(bad):
    assert unescape(bad) == bad


def test_ordered_snapshot_then_output_ignores_old_and_other_panes():
    decoder = Decoder()
    assert decoder.accept(b"%output %7 old") is None
    frame(decoder, 1)
    frame(decoder, 2, [b"%7 5 0 80 1 0"])
    frame(decoder, 3, [b"draft"])
    frame(decoder, 4, [b"draft"])
    snap = frame(decoder, 5)
    assert snap == b"\x1b[0m\x1b[2J\x1b[Hdraft\r\x1b[5C"
    assert decoder.accept(rb"%output %8 wrong-pane") is None
    assert decoder.accept(rb"%output %7 _NEW\015\012") == b"_NEW\r\n"
    assert decoder.accept(rb"%extended-output %7 0 : twice twice\015\012") == b"twice twice\r\n"


def test_cursor_blank_line_and_pending_escape_are_preserved():
    decoder, snap = ready((b"line", b"", b""), b"%7 0 1 80 3 0", [rb"\033["])
    assert snap == b"\x1b[0m\x1b[2J\x1b[Hline\r\n\r\x1b["
    assert decoder.accept(b"%output %7 31mred") == b"31mred"


def test_protocol_looking_capture_text_is_data():
    _, snap = ready((b"%output %7 text", b"%end 999 999 0"), b"%7 0 0 80 2 0")
    assert b"%output %7 text\r\n%end 999 999 0" in snap
    assert snap.endswith(b"\x1b[1A\r")


@pytest.mark.parametrize("line", [b"%pause %7", b"%exit"])
def test_pause_and_disconnect_end_viewer(line):
    decoder, _ = ready()
    with pytest.raises(EOFError):
        decoder.accept(line)


def test_command_error_is_not_live_stream():
    decoder = Decoder()
    decoder.accept(b"%begin 123 1 0")
    with pytest.raises(ValueError, match="command failed"):
        decoder.accept(b"%error 123 1 0")


def test_exact_guard_text_in_snapshot_cannot_end_the_frame():
    _, snap = ready((b"%end 123 3 0",), b"%7 0 0 80 1 0")
    assert b"%end 123 3 0" in snap


def test_capture_backslash_and_hyperlink_terminator():
    assert unescape(rb"C:\\work\\file") == rb"C:\work\file"
    assert unescape(rb"\033]8;;https://example.test\033\\") == b"\x1b]8;;https://example.test\x1b\\"


def test_buffered_burst_does_not_evict_a_ready_consumer(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from duckterm.agents import tmux_stream

    async def scenario():
        stdout = asyncio.StreamReader()
        frames = []
        for number, rows in enumerate([[], [b"%7 0 0 80 1 0"], [b"READY"], [b"READY"], []], 1):
            frames.extend(
                [f"%begin 123 {number} 0".encode(), *rows, f"%end 123 {number} 0".encode()]
            )
        # Simulate a scheduled reader finding a full pipe after CPU contention.
        # The consumer is ready throughout; this is not a stalled browser.
        expected = b"".join(f"R{i:06d}\n".encode() for i in range(100)) + b"DONE\n"
        for line in expected.splitlines():
            frames.append(b"%output %7 " + line + rb"\012")
        stdout.feed_data(b"\n".join(frames) + b"\n")
        stdout.feed_eof()

        async def communicate():
            return b"", b""

        async def create(*args, **kwargs):
            return SimpleNamespace(stdout=stdout, stdin=None, returncode=0, communicate=communicate)

        monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
        chunks = [chunk async for chunk in tmux_stream.stream("rd_test")]
        assert chunks and b"READY" in chunks[0]
        assert b"".join(chunks[1:]) == expected

    asyncio.run(scenario())


def test_joined_prose_and_capture_dimensions_survive_snapshot():
    decoder = Decoder()
    frame(decoder, 1)
    frame(decoder, 2, [b"%7 0 2 5 3 0"])
    frame(decoder, 3, [b"abcde", b"fghi", b""])
    frame(decoder, 4, [b"abcdefghi", b""])
    snapshot = frame(decoder, 5)
    assert snapshot.cols == 5 and snapshot.rows == 3
    assert b"abcdefghi\r\n" in snapshot
    assert b"abcde\r\nfghi" not in snapshot
    assert snapshot.endswith(b"\r\n\r")


def test_exact_guard_in_joined_capture_is_still_data():
    decoder = Decoder()
    frame(decoder, 1)
    frame(decoder, 2, [b"%7 0 0 80 1 0"])
    frame(decoder, 3, [b"%end 123 4 0"])
    frame(decoder, 4, [b"%end 123 4 0"])
    assert b"%end 123 4 0" in frame(decoder, 5)


def test_changed_joined_capture_is_rejected_instead_of_replaying_wrong_cursor():
    decoder = Decoder()
    frame(decoder, 1)
    frame(decoder, 2, [b"%7 0 0 80 1 0"])
    frame(decoder, 3, [b"draft"])
    with pytest.raises(ValueError, match="differs"):
        frame(decoder, 4, [b"other"])


def test_snapshot_at_right_margin_retains_pending_wrap():
    decoder = Decoder()
    frame(decoder, 1)
    frame(decoder, 2, [b"%7 5 0 5 2 0"])
    frame(decoder, 3, [b"12345", b""])
    frame(decoder, 4, [b"12345", b""])
    snapshot = frame(decoder, 5)
    assert snapshot.endswith(b"12345")
    assert decoder.accept(b"%output %7 6") == b"6"


def test_buffered_burst_survives_websocket_task_scheduling(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from duckterm.agents import tmux_stream

    async def scenario():
        stdout = asyncio.StreamReader()
        frames = []
        for number, rows in enumerate([[], [b"%7 0 0 80 1 0"], [b"READY"], [b"READY"], []], 1):
            frames.extend(
                [f"%begin 123 {number} 0".encode(), *rows, f"%end 123 {number} 0".encode()]
            )
        expected = b"".join(f"R{i:06d}\n".encode() for i in range(1024))
        for line in expected.splitlines():
            frames.append(b"%output %7 " + line + rb"\012")
        stdout.feed_data(b"\n".join(frames) + b"\n")
        stdout.feed_eof()

        async def communicate():
            return b"", b""

        async def create(*args, **kwargs):
            return SimpleNamespace(stdout=stdout, stdin=None, returncode=0, communicate=communicate)

        monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
        feed = tmux_stream.stream("rd_test")
        incoming = asyncio.get_running_loop().create_future()
        chunks = []
        try:
            while True:
                # Match the terminal WebSocket's pending outgoing/incoming tasks.
                # A network drain that is ready adds no artificial sleep here.
                outgoing = asyncio.ensure_future(anext(feed))
                await asyncio.wait({incoming, outgoing}, return_when=asyncio.FIRST_COMPLETED)
                try:
                    chunks.append(outgoing.result())
                except StopAsyncIteration:
                    break
        finally:
            incoming.cancel()
            await feed.aclose()
        assert isinstance(chunks[0], tmux_stream.Snapshot)
        assert b"READY" in chunks[0]
        assert b"".join(chunks[1:]) == expected

    asyncio.run(scenario())


def test_stalled_viewer_still_closes_its_tmux_reader(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from duckterm.agents import tmux_stream

    async def scenario():
        stdout = asyncio.StreamReader()
        frames = []
        for number, rows in enumerate([[], [b"%7 0 0 80 1 0"], [b"READY"], [b"READY"], []], 1):
            frames.extend(
                [f"%begin 123 {number} 0".encode(), *rows, f"%end 123 {number} 0".encode()]
            )
        stdout.feed_data(b"\n".join(frames) + b"\n")
        cleaned = asyncio.Event()
        terminated = []

        async def communicate():
            cleaned.set()
            return b"", b""

        async def create(*args, **kwargs):
            return SimpleNamespace(
                stdout=stdout,
                stdin=None,
                returncode=None,
                communicate=communicate,
                terminate=lambda: terminated.append(True),
            )

        monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
        feed = tmux_stream.stream("rd_test")
        try:
            assert isinstance(await anext(feed), tmux_stream.Snapshot)
            stdout.feed_data((b"%output %7 LIVE\n") * 1000)
            # No EOF and no further reads by the viewer: the independent drain
            # must close its tmux attachment rather than block or grow forever.
            await asyncio.wait_for(cleaned.wait(), 1)
            assert terminated == [True]
            assert [chunk async for chunk in feed] == []
        finally:
            await feed.aclose()

    asyncio.run(scenario())
