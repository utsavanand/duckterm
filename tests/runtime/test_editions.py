"""The edition foundation describes existing features without activating restrictions."""

import asyncio
import json

from test_memory import memory_rig
from test_session_api import Writer, dispatch

from duckterm.editions import CODE, CURRENT_EDITION, FULL, LITE

__all__ = ["memory_rig"]


def status(server, method, headers, body=b""):
    writer = Writer()

    async def run():
        await server._dispatch(method, "/edition", asyncio.StreamReader(), writer, headers, body)

    asyncio.run(run())
    return int(writer.data.split(b"\r\n", 1)[0].split()[1])


def test_editions_keep_the_agreed_core_and_separate_the_full_additions():
    assert LITE.features == {
        "folders",
        "workstreams",
        "harnesses",
        "terminal",
        "shell",
        "messages",
        "forks",
        "session_status",
    }
    assert LITE.features < CODE.features < FULL.features
    assert FULL.features - CODE.features == {"oracle", "remote_workspaces", "artifacts"}
    for feature in ("explorer", "inbox", "folder_chat", "checkpoints", "connectors"):
        assert CODE.includes(feature)
        assert not LITE.includes(feature)
    for feature in ("checks", "pr_review", "ducky", "unknown"):
        assert not FULL.includes(feature)
    assert CURRENT_EDITION is FULL


def test_description_cannot_mutate_the_shared_feature_set():
    description = FULL.description()
    features = description["features"]
    assert isinstance(features, list)
    features.clear()
    assert FULL.includes("terminal")
    assert FULL.description()["features"] == sorted(FULL.features)


def test_edition_route_is_owner_read_only_and_preserves_data(memory_rig):
    server, agent, _, _ = memory_rig
    owner = {"x-duckterm-token": server.token}
    assert dispatch(server, "GET", "/edition", {})[0] == 401
    assert dispatch(server, "GET", "/edition", agent)[0] == 403
    assert status(server, "GET", {**owner, "origin": "https://evil.test"}) == 403
    before_changes = server.history._conn.total_changes
    before_schema = server.history._conn.execute("PRAGMA user_version").fetchone()[0]
    assert dispatch(server, "GET", "/edition", owner) == (200, FULL.description())
    assert status(server, "POST", owner, json.dumps({"id": "lite"}).encode()) == 404
    assert dispatch(server, "GET", "/edition", owner) == (200, FULL.description())
    assert server.history._conn.total_changes == before_changes
    assert server.history._conn.execute("PRAGMA user_version").fetchone()[0] == before_schema
