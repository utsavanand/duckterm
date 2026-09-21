"""POST /paste-image: a clipboard image becomes a file under
DUCKTERM_HOME/pastes and the response carries its path — the terminal types
that path so claude/codex attach it (the iTerm paste-an-image experience)."""

import asyncio
import json
from pathlib import Path

import pytest

from duckterm.persistence.history import HistoryStore
from duckterm.server import Server

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64  # header + padding is enough to store


class _W:
    def __init__(self) -> None:
        self.data = b""

    def write(self, b: bytes) -> None:
        self.data += b

    async def drain(self) -> None:
        pass


def _status_and_body(w: _W) -> tuple[int, dict]:
    head, body = w.data.split(b"\r\n\r\n", 1)
    return int(head.split()[1]), json.loads(body)


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / "duckterm-home"
    monkeypatch.setenv("DUCKTERM_HOME", str(h))
    return h


def test_paste_image_saves_under_home_and_returns_path(home: Path, tmp_path: Path) -> None:
    server = Server(history=HistoryStore(tmp_path / "db.sqlite"))
    w = _W()
    asyncio.run(server._paste_image(w, {"content-type": "image/png"}, PNG))
    status, body = _status_and_body(w)
    assert status == 200
    saved = Path(body["path"])
    assert saved.is_relative_to(home / "pastes")  # confined, name not user-controlled
    assert saved.suffix == ".png"
    assert saved.read_bytes() == PNG


def test_paste_image_rejects_empty_and_oversized(home: Path, tmp_path: Path) -> None:
    server = Server(history=HistoryStore(tmp_path / "db.sqlite"))
    w = _W()
    asyncio.run(server._paste_image(w, {}, b""))
    assert _status_and_body(w)[0] == 400
    w = _W()
    asyncio.run(server._paste_image(w, {}, b"x" * (server._PASTE_MAX_BYTES + 1)))
    assert _status_and_body(w)[0] == 413
    assert not (home / "pastes").exists() or not list((home / "pastes").iterdir())
