"""Local terminal QR rendering; the caller always retains the invitation link."""

from duckterm.mobile._qrcodegen import DataTooLongError, QrCode, QrSegment

_QUIET_ZONE = 4
_MAX_BYTES = 2048
_COLORS = "\x1b[0;38;2;0;0;0;48;2;255;255;255m"
_RESET = "\x1b[0m"


def render_terminal_qr(invitation: str, *, columns: int, is_tty: bool) -> str | None:
    """Return a black-on-white QR, or None when the caller should show just its link.

    Uses UTF-8 block glyphs and true-color ANSI on an interactive terminal. Two
    vertical modules fit each character cell; four white modules surround every
    edge. Leave a spare column to avoid terminal autowrap corrupting the symbol.
    No invitation is sent anywhere, persisted, or included in error messages.
    """
    if not is_tty or columns < 30 or not invitation or len(invitation) > _MAX_BYTES:
        return None
    try:
        if len(invitation.encode("utf-8")) > _MAX_BYTES:
            return None
        # Size = 4 * version + 17, plus both quiet zones and an unused column.
        max_version = min(QrCode.MAX_VERSION, (columns - 2 * _QUIET_ZONE - 18) // 4)
        code = QrCode.encode_segments(
            QrSegment.make_segments(invitation), QrCode.Ecc.MEDIUM, maxversion=max_version
        )
    except (DataTooLongError, UnicodeEncodeError):
        return None
    size = code.get_size()
    rows = []
    # get_module() returns white outside the QR grid, including the extra bottom
    # half-cell required for an odd module count. Spaces have an explicit white
    # background; the terminal's theme/inverse-video setting cannot change it.
    for y in range(-_QUIET_ZONE, size + _QUIET_ZONE, 2):
        cells = []
        for x in range(-_QUIET_ZONE, size + _QUIET_ZONE):
            top, bottom = code.get_module(x, y), code.get_module(x, y + 1)
            cells.append("█" if top and bottom else "▀" if top else "▄" if bottom else " ")
        rows.append(_COLORS + "".join(cells) + _RESET)
    return "\n".join(rows)
