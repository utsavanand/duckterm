import pytest

from duckterm.agents.tmux_stream import Decoder, unescape


def frame(decoder, number, lines=()):
    assert decoder.accept(f"%begin 123 {number} 0".encode()) is None
    for line in lines:
        assert decoder.accept(line) is None
    return decoder.accept(f"%end 123 {number} 0".encode())


def ready(screen=(b"draft",), metadata=b"%7 5 0 1 0", pending=()):
    decoder = Decoder()
    frame(decoder, 1)
    frame(decoder, 2, [metadata])
    frame(decoder, 3, screen)
    snapshot = frame(decoder, 4, pending)
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
    frame(decoder, 2, [b"%7 5 0 1 0"])
    frame(decoder, 3, [b"draft"])
    snap = frame(decoder, 4)
    assert snap == b"\x1b[2J\x1b[Hdraft\r\x1b[5C"
    assert decoder.accept(rb"%output %8 wrong-pane") is None
    assert decoder.accept(rb"%output %7 _NEW\015\012") == b"_NEW\r\n"
    assert decoder.accept(rb"%extended-output %7 0 : twice twice\015\012") == b"twice twice\r\n"


def test_cursor_blank_line_and_pending_escape_are_preserved():
    decoder, snap = ready((b"line", b"", b""), b"%7 0 1 3 0", [rb"\033["])
    assert snap == b"\x1b[2J\x1b[Hline\r\n\r\x1b["
    assert decoder.accept(b"%output %7 31mred") == b"31mred"


def test_protocol_looking_capture_text_is_data():
    _, snap = ready((b"%output %7 text", b"%end 999 999 0"), b"%7 0 0 2 0")
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
    _, snap = ready((b"%end 123 3 0",), b"%7 0 0 1 0")
    assert b"%end 123 3 0" in snap


def test_capture_backslash_and_hyperlink_terminator():
    assert unescape(rb"C:\\work\\file") == rb"C:\work\file"
    assert unescape(rb"\033]8;;https://example.test\033\\") == b"\x1b]8;;https://example.test\x1b\\"
