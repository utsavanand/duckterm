"""An old reference without a records digest is fenced on the raw copy it reads."""

import asyncio
import json

import pytest
from test_memory import memory_rig as _memory_rig
from test_memory import transcript
from test_memory_preparation import typed_provider

from duckterm import memory_sources
from duckterm.core.session_api import APIError
from duckterm.memory import Memory

memory_rig = _memory_rig


@pytest.fixture(params=["claude-code", "codex"])
def idle_legacy(memory_rig, monkeypatch, request):
    server, _, _, tmp = memory_rig
    runtime = request.param
    native = "qa-idle-" + runtime
    server.history.set_harness_identity(
        "agent",
        runtime,
        None,
        None,
        control={"native_binding": {"runtime": runtime, "native_id": native, "generation": "qa"}},
    )
    path = transcript(tmp, native, "Retain glacier originals.", runtime)
    typed_provider(monkeypatch, "Retain glacier originals.")

    async def prepare():
        first = await server._create_checkpoint("agent", server.history.session("agent"), "legacy")
        old_ref = dict(first["record"]["memory_source"])
        old_ref.pop("records_sha256")
        root = memory_sources.directory("agent")
        raw = root / (old_ref["snapshot"] + ".jsonl")
        raw.write_bytes(path.read_bytes())

        # Represent a persisted checkpoint from the raw-copy format.
        def legacy(value):
            if isinstance(value, dict):
                return {k: legacy(v) for k, v in value.items() if k != "records_sha256"}
            if isinstance(value, list):
                return [legacy(v) for v in value]
            return value

        with server.history._conn:
            server.history._conn.execute(
                "UPDATE checkpoints SET record_json=? WHERE id=?",
                (json.dumps(legacy(first["record"])), first["id"]),
            )
        (root / (old_ref["snapshot"] + ".records")).unlink()
        second = await server._create_checkpoint(
            "agent", server.history.session("agent"), "unchanged"
        )
        new_ref = second["record"]["memory_source"]
        assert new_ref["snapshot"] == old_ref["snapshot"]
        assert new_ref["records_sha256"]
        path.unlink()
        server.memory = Memory(server)
        return server, old_ref, new_ref, raw, first["id"]

    return asyncio.run(prepare())


def test_idle_resave_preserves_old_checkpoint_evidence(idle_legacy):
    server, old_ref, new_ref, raw, checkpoint_id = idle_legacy
    for ref in (old_ref, new_ref):
        assert (
            server.memory._read_native("agent", ref)["records"][0]["text"]
            == "Retain glacier originals."
        )
    result = asyncio.run(server.memory.operate("agent", "view", {"checkpoint": [checkpoint_id]}))
    assert "Retain glacier originals." in json.dumps(result)


@pytest.mark.parametrize("damage", ["rewrite", "delete", "symlink"])
def test_legacy_raw_change_invalidates_warm_cache(idle_legacy, damage):
    server, old_ref, new_ref, raw, _ = idle_legacy
    server.memory._read_native("agent", old_ref)
    if damage == "rewrite":
        raw.write_bytes(raw.read_bytes().replace(b"glacier", b"volcano"))
    elif damage == "delete":
        raw.unlink()
    else:
        other = raw.with_suffix(".outside")
        other.write_bytes(raw.read_bytes())
        raw.unlink()
        raw.symlink_to(other)
    with pytest.raises(APIError):
        server.memory._read_native("agent", old_ref)
    assert (
        server.memory._read_native("agent", new_ref)["records"][0]["text"]
        == "Retain glacier originals."
    )
