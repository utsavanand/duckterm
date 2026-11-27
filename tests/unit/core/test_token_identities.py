"""Identity snapshots remain exact when read from a worker connection."""

import asyncio
import json
import sqlite3
from types import SimpleNamespace

from duckterm.server import Server


def test_worker_snapshot_keeps_mapping_and_reflects_retention_and_metadata(tmp_path):
    path = tmp_path / "identities.sqlite"
    writer = sqlite3.connect(path)
    try:
        writer.executescript(
            """
            CREATE TABLE sessions(session_key TEXT, runtime TEXT, name TEXT, grp TEXT, test INT);
            CREATE TABLE events(session_key TEXT, payload_json TEXT);
            INSERT INTO sessions VALUES ('probe', 'codex', '', 'first', 1);
        """
        )
        writer.executemany(
            "INSERT INTO events VALUES (?,?)",
            [("probe", json.dumps({"session_id": sid})) for sid in ["native", "native", None]],
        )
        writer.commit()
        server = object.__new__(Server)
        # The writer belongs to this thread; the worker must open its own reader.
        server.history = SimpleNamespace(path=path, _conn=writer)  # type: ignore[assignment]

        async def read():
            return await asyncio.to_thread(server._token_identities)

        result = asyncio.run(read())
        assert len(result) == 1
        assert set(result[0].pop("native_ids")) == {"probe", "native"}
        assert result[0] == dict(
            key="probe", runtime="codex", name="probe", folder="first", test=True
        )
        writer.execute("DELETE FROM events")
        writer.execute("UPDATE sessions SET name='Renamed', grp='second'")
        writer.commit()
        result = asyncio.run(read())
        assert result == [
            dict(
                key="probe",
                runtime="codex",
                name="Renamed",
                folder="second",
                test=True,
                native_ids=["probe"],
            )
        ]
    finally:
        writer.close()
