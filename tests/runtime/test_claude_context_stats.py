"""Claude transcript stats are parsed off the event loop and never outlive their binding."""

import asyncio
import json
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from duckterm.runtimes import claude_code
from duckterm.server import Server


def assistant(tokens: int, model: str = "claude-opus-5-5") -> str:
    return json.dumps(
        {"type": "assistant", "message": {"model": model, "usage": {"input_tokens": tokens}}}
    )


@pytest.fixture
def fleet(tmp_path, monkeypatch):
    """Two bound Claude sessions with real transcripts under a private home."""
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    rows, binding, paths = {}, {}, {}
    for key in ("a", "b"):
        cwd = tmp_path / key
        cwd.mkdir()
        sid = f"sid-{key}"
        path = home / ".claude" / "projects" / claude_code.project_slug(cwd) / f"{sid}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(assistant(1000 if key == "a" else 2000) + "\n")
        rows[key] = {"session_key": key, "runtime": "claude-code", "cwd": str(cwd)}
        binding[key] = sid
        paths[key] = path
    server = SimpleNamespace(
        _context_cache={},
        history=SimpleNamespace(
            session_id_for=lambda key: binding.get(key),
            session=lambda key: dict(rows[key]) if key in rows else None,
        ),
    )
    return SimpleNamespace(server=server, rows=rows, binding=binding, paths=paths)


def stats(fleet, key):
    return Server._transcript_stats_for(fleet.server, dict(fleet.rows[key]))


def test_reads_overlap_off_loop_while_the_loop_keeps_running(fleet, monkeypatch):
    real = claude_code.transcript_stats
    both_reading = threading.Barrier(2, timeout=5)
    loop_thread = threading.get_ident()

    def slow(path):
        assert threading.get_ident() != loop_thread
        both_reading.wait()  # Serial on-loop parsing could never reach this together.
        time.sleep(0.2)
        return real(path)

    monkeypatch.setattr(claude_code, "transcript_stats", slow)

    async def main():
        ticks = 0

        async def heartbeat():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        beat = asyncio.create_task(heartbeat())
        try:
            results = await asyncio.gather(stats(fleet, "a"), stats(fleet, "b"))
        finally:
            beat.cancel()
        return results, ticks

    (a, b), ticks = asyncio.run(main())
    assert a == {"context_tokens": 1000, "model": "claude-opus-5-5"}
    assert b == {"context_tokens": 2000, "model": "claude-opus-5-5"}
    assert ticks >= 10  # about 20 expected during the 0.2 s read


def test_unchanged_transcript_is_served_from_cache_and_an_append_reparses(fleet, monkeypatch):
    calls = []
    real = claude_code.transcript_stats
    monkeypatch.setattr(claude_code, "transcript_stats", lambda p: calls.append(p) or real(p))
    assert asyncio.run(stats(fleet, "a"))["context_tokens"] == 1000
    assert asyncio.run(stats(fleet, "a"))["context_tokens"] == 1000
    assert len(calls) == 1
    path = fleet.paths["a"]
    with path.open("a") as stream:
        stream.write(assistant(3000) + "\n")
    later = path.stat().st_mtime + 5
    os.utime(path, (later, later))
    assert asyncio.run(stats(fleet, "a"))["context_tokens"] == 3000
    assert len(calls) == 2


@pytest.mark.parametrize("change", ["rebound", "cwd", "worktree", "runtime", "deleted", "replaced"])
def test_result_is_discarded_when_binding_or_file_changes_during_read(fleet, monkeypatch, change):
    real = claude_code.transcript_stats
    path = fleet.paths["a"]

    def racing(p):
        value = real(p)
        if change == "rebound":
            fleet.binding["a"] = "sid-other"
        elif change == "cwd":
            fleet.rows["a"]["cwd"] = "/elsewhere"
        elif change == "worktree":
            fleet.rows["a"]["worktree_path"] = "/elsewhere"
        elif change == "runtime":
            fleet.rows["a"]["runtime"] = "codex"
        elif change == "deleted":
            del fleet.rows["a"]
        else:
            replacement = path.with_suffix(".new")
            replacement.write_text(assistant(9999) + "\n")
            replacement.replace(path)
        return value

    original = dict(fleet.rows["a"])
    monkeypatch.setattr(claude_code, "transcript_stats", racing)
    assert asyncio.run(Server._transcript_stats_for(fleet.server, original)) == {}
    assert fleet.server._context_cache == {}
