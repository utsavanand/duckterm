"""The in-dashboard file editor: read/write confined to home + tempdir (same
guard as AGENTS.md), 600 on fresh dotfiles, refuses directories and huge
files. This endpoint exists so secrets can be typed into a .env without
passing through an agent's conversation."""

import asyncio
import json
import stat
import tempfile
from pathlib import Path

from duckterm.persistence.history import HistoryStore
from duckterm.server import Server


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


def _server(tmp_path: Path) -> Server:
    return Server(history=HistoryStore(tmp_path / "db.sqlite"))


def _read(server: Server, path: str) -> tuple[int, dict]:
    w = _W()
    asyncio.run(server._read_file(w, f"?path={path}"))  # type: ignore[arg-type]
    return _status_and_body(w)


def _write(server: Server, req: dict) -> tuple[int, dict]:
    w = _W()
    asyncio.run(server._write_file(w, json.dumps(req).encode()))  # type: ignore[arg-type]
    return _status_and_body(w)


def test_write_then_read_roundtrip_and_dotfile_mode(tmp_path: Path) -> None:
    server = _server(tmp_path)
    target = tempfile.mkdtemp() + "/.env.porkbun"

    status, body = _write(server, {"path": target, "text": "API_KEY=pk_123\n"})
    assert (status, body["written"]) == (200, True)
    assert stat.S_IMODE(Path(target).stat().st_mode) == 0o600  # fresh dotfile locked down

    status, body = _read(server, target)
    assert status == 200
    assert body["text"] == "API_KEY=pk_123\n"
    assert body["exists"] is True


def test_paths_outside_home_and_tmp_are_refused(tmp_path: Path) -> None:
    server = _server(tmp_path)
    status, body = _write(server, {"path": "/etc/duckterm-evil", "text": "x"})
    assert status == 403
    assert not Path("/etc/duckterm-evil").exists()
    status, _ = _read(server, "/etc/passwd")
    assert status == 403


def test_directories_and_missing_fields_are_rejected(tmp_path: Path) -> None:
    server = _server(tmp_path)
    d = tempfile.mkdtemp()
    assert _write(server, {"path": d, "text": "x"})[0] == 400  # a directory
    assert _write(server, {"path": "", "text": "x"})[0] == 400
    assert _write(server, {"path": d + "/f"})[0] == 400  # no text
    status, body = _read(server, d + "/missing.env")
    assert (status, body["exists"], body["text"]) == (200, False, "")
