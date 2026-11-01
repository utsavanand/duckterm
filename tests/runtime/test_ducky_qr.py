"""Local terminal QR geometry, upstream vectors and bounded link fallback."""

import base64
import hashlib
import json

import pytest

from duckterm.mobile import qr


def pairing_link(url: str = "https://192.168.50.20:4430") -> str:
    # Synthetic and never usable as a grant. Match the actual invitation shape.
    payload = {
        "version": 1,
        "host_id": "a" * 32,
        "invite": "TEST_ONLY_" + "b" * 33,
        "expires_at": 1800000600,
        "url": url,
        "certificate_sha256": "c" * 64,
    }
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return "ducky://pair?invite=" + encoded


def pixels(rendered: str) -> list[list[bool]]:
    """Reconstruct what the terminal displays, including its quiet zone."""
    result = []
    colors = "\x1b[0;38;2;0;0;0;48;2;255;255;255m"
    reset = "\x1b[0m"
    cells = {" ": (False, False), "▀": (True, False), "▄": (False, True), "█": (True, True)}
    for line in rendered.splitlines():
        assert line.startswith(colors), "black/white must not depend on the terminal theme"
        assert line.endswith(reset), "QR colors must not leak into later terminal output"
        body = line[len(colors) : -len(reset)]
        assert body and set(body) <= cells.keys()
        result.extend([[cells[cell][half] for cell in body] for half in (0, 1)])
    assert len({len(row) for row in result}) == 1
    return result


@pytest.mark.parametrize(
    ("text", "size", "digest"),
    [
        ("HELLO WORLD", 21, "e5395d7a0fb74b1e1b0b752cbb72ee155a5ad7b4cbd2023fe17384aa002230b1"),
        (
            "ducky://pair?invite=TEST_ONLY_known_vector",
            29,
            "d15c75fa25c609d3dda99d00cfa3ccb9db22aeefb539c38a342352cbaa985a85",
        ),
    ],
)
def test_terminal_pixels_match_pinned_upstream_vectors(text, size, digest):
    # Generated independently from unmodified Nayuki v1.8.0, medium ECC with
    # automatic mask/boost. Comparing the rendered pixels catches inverted or
    # transposed half-blocks as well as changes to the vendored encoder.
    rendered = qr.render_terminal_qr(text, columns=160, is_tty=True)
    assert rendered is not None
    image = pixels(rendered)
    data = bytes(image[y + 4][x + 4] for y in range(size) for x in range(size))
    assert hashlib.sha256(data).hexdigest() == digest
    assert len(image[0]) == size + 8


@pytest.mark.parametrize(
    "url",
    ["https://192.168.50.20:4430", "https://[fd12:3456:789a:bcde:1234:5678:9abc:def0]:65535"],
)
def test_full_invitation_has_solid_quiet_zone_and_never_wraps(url):
    invitation = pairing_link(url)
    assert 350 <= len(invitation) <= 550
    assert qr.render_terminal_qr(invitation, columns=80, is_tty=True) is None
    rendered = qr.render_terminal_qr(invitation, columns=160, is_tty=True)
    assert rendered is not None
    image = pixels(rendered)
    width = len(image[0])
    assert width < 160
    assert len(image) == width + 1  # final unused half-cell is also white
    assert not any(any(row) for row in image[:4] + image[-5:])
    assert all(not any(row[:4] + row[-4:]) for row in image)
    assert any(any(row) for row in image[4:-5])
    assert qr.render_terminal_qr(invitation, columns=width, is_tty=True) is None
    assert qr.render_terminal_qr(invitation, columns=width + 1, is_tty=True) == rendered
    assert invitation not in rendered


def test_redirected_or_too_narrow_output_never_encodes(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("nonterminal output should immediately use the invitation link")

    monkeypatch.setattr(qr.QrCode, "encode_segments", unexpected)
    assert qr.render_terminal_qr(pairing_link(), columns=160, is_tty=False) is None
    for columns in (-1, 0, 20, 29):
        assert qr.render_terminal_qr(pairing_link(), columns=columns, is_tty=True) is None


@pytest.mark.parametrize("text", ["", "x" * 2049, "é" * 1025, "\ud800"])
def test_unrepresentable_payload_falls_back_without_error_or_secret_output(text, capsys):
    assert qr.render_terminal_qr(text, columns=160, is_tty=True) is None
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


def test_one_module_version_preserves_all_four_quiet_edges():
    rendered = qr.render_terminal_qr("1", columns=30, is_tty=True)
    assert rendered is not None
    image = pixels(rendered)
    assert len(image) == 30
    assert len(image[0]) == 29
    assert not any(any(row) for row in image[:4] + image[-5:])
    assert all(not any(row[:4] + row[-4:]) for row in image)
