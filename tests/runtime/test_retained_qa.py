import asyncio
import contextlib
import json
from pathlib import Path
from types import SimpleNamespace

from test_maintained_progress import provider
from test_memory import memory_rig, transcript
from test_memory_large_native import message, source

from duckterm import memory_sources
from duckterm.core import memory_refresh

__all__ = ["memory_rig"]

REAL_RECONCILE = memory_refresh.reconcile_reply


def test_real_checkpoint_without_reconcile_stub(memory_rig, monkeypatch):
    from duckterm import server as sm

    server, _, _, tmp = memory_rig
    transcript(tmp, "agent-claude", "Retain the owner instruction.")
    provider(monkeypatch)
    writer = sm.summarize
    calls = []

    def shaped(prompt, *, tier, claude_model=None):
        calls.append((prompt, tier, claude_model))
        if "MEMORY UPDATE:\n" in prompt:
            return writer(prompt)
        return SimpleNamespace(text='{"ops": []}', failure_reason=None)

    monkeypatch.setattr(memory_refresh, "reconcile_reply", REAL_RECONCILE)
    monkeypatch.setattr(sm, "summarize", shaped)
    asyncio.run(server._create_checkpoint("agent", server.history.session("agent"), "manual"))
    rows = server.history._conn.execute(
        "SELECT text FROM digest_items WHERE session_key='agent' AND bucket='memory_items_v1'"
    ).fetchall()
    assert rows and json.loads(rows[-1]["text"])["items"]
    assert any("MEMORY UPDATE:\n" not in p and tier == "fast" for p, tier, _ in calls)


def test_more_than_256_appends_retain_every_version(tmp_path, monkeypatch):
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path / "state"))
    p = tmp_path / "native.jsonl"
    p.write_bytes(message("first"))
    first = memory_sources.read_native("probe", source(p), retain=True)
    for i in range(257):
        with p.open("ab") as f:
            f.write(message(str(i)))
        memory_sources.read_native("probe", source(p), retain=True)
    assert memory_sources.read_native("probe", first)["version"] == first["version"]


def test_legacy_copy_is_unchanged(tmp_path, monkeypatch):
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path / "state"))
    p = tmp_path / "native.jsonl"
    p.write_bytes(message("legacy"))
    first = memory_sources.read_native("probe", source(p), retain=True)
    root = memory_sources.directory("probe")
    (root / "latest.json").unlink()
    old = root / (first["snapshot"] + ".jsonl")
    before = old.read_bytes()
    with p.open("ab") as f:
        f.write(message("new"))
    memory_sources.read_native("probe", source(p), retain=True)
    assert old.read_bytes() == before
    assert memory_sources.read_native("probe", first)["version"] == first["version"]


def test_retry_after_interrupted_index_write_preserves_old_version(tmp_path, monkeypatch):
    import os

    import pytest

    from duckterm.core.session_api import APIError

    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path / "state"))
    p = tmp_path / "native.jsonl"
    p.write_bytes(message("first"))
    first = memory_sources.read_native("probe", source(p), retain=True)
    real_replace = os.replace

    def failed_index(src, dst):
        if Path(dst).name == "latest.json":
            raise OSError("injected index interruption")
        return real_replace(src, dst)

    with p.open("ab") as f:
        f.write(message("second"))
    with monkeypatch.context() as m:
        m.setattr(os, "replace", failed_index)
        with pytest.raises(APIError):
            memory_sources.read_native("probe", source(p), retain=True)
    assert memory_sources.read_native("probe", first)["version"] == first["version"]
    write = Path.write_text

    def partial_pointer(path, text, *args, **kw):
        if path.suffix == ".prefix":
            write(path, "{")
            raise OSError("injected pointer interruption")
        return write(path, text, *args, **kw)

    with p.open("ab") as f:
        f.write(message("third"))
    with monkeypatch.context() as m:
        m.setattr(Path, "write_text", partial_pointer)
        with contextlib.suppress(APIError):
            memory_sources.read_native("probe", source(p), retain=True)
    assert memory_sources.read_native("probe", first)["version"] == first["version"]


def test_cycle_is_rejected(tmp_path, monkeypatch):
    import pytest

    from duckterm.core.session_api import APIError

    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path / "state"))
    root = memory_sources.directory("probe")
    root.mkdir(parents=True, exist_ok=True)
    a, b = "a" * 64, "b" * 64
    (root / (a + ".prefix")).write_text(json.dumps({"of": b, "bytes": 1}))
    (root / (b + ".prefix")).write_text(json.dumps({"of": a, "bytes": 1}))
    with pytest.raises(APIError):
        memory_sources.snapshot_file("probe", a)


def test_atomic_pointer_publication_failure_preserves_prior_route(tmp_path, monkeypatch):
    import os

    import pytest

    from duckterm.core.session_api import APIError

    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path / "state"))
    p = tmp_path / "native.jsonl"
    p.write_bytes(message("first"))
    first = memory_sources.read_native("probe", source(p), retain=True)
    with p.open("ab") as f:
        f.write(message("second"))
    replace = os.replace

    def fail_pointer(src, dst):
        if Path(dst).suffix == ".prefix":
            raise OSError("injected publish failure")
        return replace(src, dst)

    with monkeypatch.context() as m:
        m.setattr(os, "replace", fail_pointer)
        with pytest.raises(APIError):
            memory_sources.read_native("probe", source(p), retain=True)
    assert memory_sources.read_native("probe", first)["version"] == first["version"]
