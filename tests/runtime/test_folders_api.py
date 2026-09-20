"""Folder PATCH: rename the leaf, re-parent, or both; omitted fields keep
their current value. Grouped sessions follow the rename."""

import asyncio
import json
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


def _server() -> Server:
    return Server(history=HistoryStore(Path(tempfile.mkdtemp()) / "db.sqlite"))


def _patch(server: Server, folder: str, req: dict) -> tuple[int, dict]:
    w = _W()
    asyncio.run(server._move_folder(w, folder, json.dumps(req).encode()))  # type: ignore[arg-type]
    return _status_and_body(w)


def test_rename_leaf_keeps_parent_and_moves_sessions() -> None:
    server = _server()
    server.history.create_folder("billing/orders")
    server.bus.publish({"event_type": "SessionStart", "session_key": "s1"})
    server.history.set_meta("s1", group="billing/orders")

    status, body = _patch(server, "billing/orders", {"name": "invoices"})
    assert (status, body["to"]) == (200, "billing/invoices")
    assert "billing/invoices" in server.history.folders()
    assert "billing/orders" not in server.history.folders()
    session = server.history.session("s1")
    assert session is not None and session["grp"] == "billing/invoices"


def test_rename_and_reparent_together() -> None:
    server = _server()
    server.history.create_folder("billing/orders")
    status, body = _patch(server, "billing/orders", {"parent": "", "name": "archive"})
    assert (status, body["to"]) == (200, "archive")
    assert server.history.folders() == ["archive"]


def test_reparent_without_name_keeps_leaf() -> None:
    server = _server()
    server.history.create_folder("orders")
    server.history.create_folder("billing")
    status, body = _patch(server, "orders", {"parent": "billing"})
    assert (status, body["to"]) == (200, "billing/orders")


def test_rejects_slash_in_name() -> None:
    server = _server()
    server.history.create_folder("orders")
    status, body = _patch(server, "orders", {"name": "a/b"})
    assert status == 400
    assert "'/'" in body["error"]


def test_noop_when_nothing_changes() -> None:
    server = _server()
    server.history.create_folder("orders")
    status, body = _patch(server, "orders", {"name": "orders"})
    assert (status, body["to"]) == (200, "orders")
    assert server.history.folders() == ["orders"]
