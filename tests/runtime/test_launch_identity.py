"""Conversation identity is durable before spawn and independent of optional hooks."""

import asyncio
import json
import sqlite3
import sys
import uuid

import pytest
from tests.runtime.test_session_api import dispatch

from duckterm.core.eventbus import EventBus
from duckterm.core.orchestrator import Orchestrator, SessionSupervisor
from duckterm.harness_switch import accept_hook
from duckterm.harnesses import runtime_for
from duckterm.persistence.history import HistoryStore
from duckterm.runtimes.launch_identity import assign
from duckterm.server import Server


@pytest.fixture
def history(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path / "home"))
    store = HistoryStore(tmp_path / "history.sqlite")
    yield store
    store.purge_test_sessions()
    store.close()


def seed(history, key="old", native_id="old-id", cwd="/tmp"):
    history.record(
        {
            "_id": str(uuid.uuid4()),
            "_ts": 1,
            "event_type": "SessionStart",
            "session_key": key,
            "session_id": native_id,
            "runtime": "claude-code",
            "cwd": cwd,
            "launched": True,
            "test": True,
        }
    )


@pytest.mark.parametrize("runtime", ["claude-code", "copilot"])
@pytest.mark.parametrize("tmux", [False, True])
def test_identity_is_committed_before_spawn_without_hooks(
    history, tmp_path, monkeypatch, runtime, tmux
):
    monkeypatch.setattr("duckterm.core.orchestrator.tmux.has_tmux", lambda: tmux)
    calls = []

    async def spawn(supervisor, argv):
        native_id = argv[argv.index("--session-id") + 1]
        assert uuid.UUID(native_id).version == 4
        # A second connection proves this is committed, not merely staged.
        with sqlite3.connect(tmp_path / "history.sqlite") as reader:
            row = reader.execute(
                "SELECT restart_json FROM sessions WHERE session_key='new'"
            ).fetchone()
            binding = json.loads(row[0])["native_binding"]
            assert (binding["native_id"], binding["source"]) == (native_id, "assigned")
        assert history.session_id_for("new") == native_id
        assert history.native_identity("new")["status"] == "recorded"
        assert supervisor._env["DUCKTERM_HARNESS_GENERATION"]
        calls.append(argv)

    monkeypatch.setattr(SessionSupervisor, "_start_tmux" if tmux else "_start_pty", spawn)
    orchestrator = Orchestrator(EventBus(sink=history.record), history=history)
    asyncio.run(
        orchestrator.launch(
            runtime=runtime_for(runtime, "/bin/echo"),
            cwd=str(tmp_path),
            session_key="new",
            prompt="hello",
            test=True,
        )
    )
    assert len(calls) == 1
    assigned = history.session_id_for("new")
    history._conn.execute("DELETE FROM events WHERE session_key='new'")
    history._conn.commit()
    assert history.session_id_for("new") == assigned
    reopened = HistoryStore(tmp_path / "history.sqlite")
    assert reopened.native_identity("new") == {
        "native_id": assigned,
        "source": "assigned",
        "status": "recorded",
    }
    reopened.close()


def test_persistence_failure_rolls_back_identity_and_prevents_spawn(history, tmp_path, monkeypatch):
    history._conn.execute(
        "CREATE TEMP TRIGGER reject_identity BEFORE UPDATE OF restart_json ON sessions "
        "BEGIN SELECT RAISE(ABORT,'synthetic disk failure'); END"
    )
    calls = []

    async def spawn(*args):
        calls.append(args)

    monkeypatch.setattr(SessionSupervisor, "_start_tmux", spawn)
    monkeypatch.setattr(SessionSupervisor, "_start_pty", spawn)
    orchestrator = Orchestrator(EventBus(sink=history.record), history=history)
    with pytest.raises(ValueError, match="not started"):
        asyncio.run(
            orchestrator.launch(
                runtime=runtime_for("claude-code", "/bin/echo"),
                cwd=str(tmp_path),
                session_key="failed",
                test=True,
            )
        )
    assert calls == []
    assert history.session("failed") is None
    assert history.events_for("failed") == []
    assert orchestrator.get("failed") is None
    assert orchestrator.bus.recent() == []


def test_observed_identity_survives_retention_without_a_schema_migration(history, tmp_path):
    seed(history, "observed")
    seed(history, "pending")
    seed(history, "missing", None)
    history.set_restart_control(
        "pending",
        {"native_binding": {"runtime": "claude-code", "native_id": None, "generation": "new"}},
    )
    reopened = HistoryStore(tmp_path / "history.sqlite")
    try:
        assert reopened._conn.execute("PRAGMA user_version").fetchone()[0] == 12
        assert reopened.native_identity("observed") == {
            "native_id": "old-id",
            "source": "observed",
            "status": "recorded",
        }
        assert reopened.native_identity("pending")["status"] == "pending"
        assert reopened.session_id_for("pending") is None
        assert reopened.native_identity("missing")["status"] == "missing"
        assert reopened.events_for("observed") == []
        assert reopened.session_id_for("observed") == "old-id"
    finally:
        reopened.close()


def test_existing_legacy_events_remain_readable_and_old_observations_do_not_rebind(history):
    seed(history, native_id="legacy-id")
    history.set_restart_control("old", {})
    assert history.session_id_for("old") == "legacy-id"
    seed(history, native_id="latest-id")
    event = dict(history.events_for("old")[-1])
    history.record({**event, "_id": str(uuid.uuid4()), "_ts": 0, "session_id": "stale-id"})
    assert history.session_id_for("old") == "latest-id"


@pytest.mark.parametrize("runtime", ["claude-code", "copilot"])
def test_assigned_current_generation_mismatch_is_contested_not_rebound(
    history, tmp_path, monkeypatch, runtime
):
    server = Server(history=history)
    seed(history)
    history._conn.execute("UPDATE sessions SET runtime=? WHERE session_key='old'", (runtime,))
    history.set_restart_control(
        "old",
        {
            "native_binding": {
                "runtime": runtime,
                "native_id": "assigned",
                "source": "assigned",
                "generation": "current",
                "retired_ids": ["retired"],
            }
        },
    )
    base = {
        "event_type": "SessionStart",
        "runtime": runtime,
        "session_key": "old",
        "launch_generation": "current",
    }
    assert not accept_hook(
        server, {**base, "native_id": "bad", "session_id": "retired", "launch_generation": "stale"}
    )
    assert history.session_id_for("old") == "assigned"
    assert not accept_hook(server, {**base, "session_id": "different"})
    assert history.native_identity("old") == {
        "native_id": None,
        "source": "assigned",
        "status": "contested",
    }
    assert history.restart_control("old")["native_binding"]["native_id"] == "assigned"
    assert not accept_hook(server, {**base, "session_id": "assigned"})
    history.set_state("old", "stopped")
    monkeypatch.setattr(
        server.orchestrator, "launch", lambda **kwargs: pytest.fail("contested resume spawned")
    )
    assert (
        dispatch(server, "POST", "/sessions/old/resume", {"x-duckterm-token": server.token}, b"{}")[
            0
        ]
        == 409
    )
    server.digests.close()


@pytest.mark.parametrize("contested", [False, True])
def test_launch_rejects_a_uuid_already_owned_by_another_card(
    history, tmp_path, monkeypatch, contested
):
    native_id = str(uuid.uuid4())
    seed(history, native_id=native_id)
    if contested:
        history.set_restart_control(
            "old",
            {
                "native_binding": {
                    "runtime": "claude-code",
                    "native_id": native_id,
                    "source": "assigned",
                    "contested": True,
                }
            },
        )
    calls = []

    async def spawn(*args):
        calls.append(args)

    monkeypatch.setattr(SessionSupervisor, "_start_tmux", spawn)
    monkeypatch.setattr(SessionSupervisor, "_start_pty", spawn)
    orchestrator = Orchestrator(EventBus(sink=history.record), history=history)
    with pytest.raises(ValueError, match="not started"):
        asyncio.run(
            orchestrator.launch(
                runtime=runtime_for("claude-code", "/bin/echo --session-id " + native_id),
                cwd=str(tmp_path),
                session_key="duplicate",
                test=True,
            )
        )
    assert calls == []
    assert history.session("duplicate") is None
    assert history.session_id_for("old") == (None if contested else native_id)


@pytest.mark.parametrize(
    "args",
    [
        ["--resume", "old"],
        ["--resume=old"],
        ["-c"],
        ["--resume", "old", "--fork-session"],
        ["--no-session-persistence"],
    ],
)
def test_existing_conversation_modes_do_not_receive_a_new_identity(args):
    argv = ["claude", *args]
    assert assign(argv) == (argv, None)


def test_explicit_uuid_is_preserved_and_duplicate_or_invalid_flags_fail():
    native_id = str(uuid.uuid4())
    argv = ["claude", "--session-id", native_id]
    assert assign(argv) == (argv, native_id)
    assert assign(["copilot", "--session-id=" + native_id])[1] == native_id
    assert assign(["claude", "--session-id", native_id.upper()])[1] == native_id
    for args in (
        ["--session-id"],
        ["--session-id", "bad"],
        ["--session-id", native_id, "--session-id=" + native_id],
    ):
        with pytest.raises(ValueError):
            assign(["claude", *args])
    assert not runtime_for("codex", "codex").session_id_assignable


def test_hook_cannot_forge_assignment(history):
    server = Server(history=history)
    seed(history)
    status, _ = dispatch(
        server,
        "POST",
        "/events",
        {"x-duckterm-token": server.token},
        json.dumps(
            {
                "event_type": "SessionStart",
                "runtime": "claude-code",
                "session_key": "old",
                "_assigned_native_id": "forged",
                "session_id": "observed",
            }
        ).encode(),
    )
    assert status == 200
    assert history.native_identity("old")["source"] == "observed"
    assert history.session_id_for("old") == "observed"
    server.digests.close()


def test_real_no_hook_child_observes_durable_identity(history, tmp_path, monkeypatch):
    script = tmp_path / "no_hooks.py"
    script.write_text(
        "import json,sqlite3,sys\n"
        "sid=sys.argv[sys.argv.index('--session-id')+1]\n"
        "with sqlite3.connect(sys.argv[1]) as db:\n"
        " query=\"SELECT restart_json FROM sessions WHERE session_key='child'\"\n"
        " row=db.execute(query).fetchone()\n"
        " assert json.loads(row[0])['native_binding']['native_id']==sid\n"
        "print('durable-before-child:'+sid,flush=True)\n"
    )
    monkeypatch.setattr("duckterm.core.orchestrator.tmux.has_tmux", lambda: False)
    orchestrator = Orchestrator(EventBus(sink=history.record), history=history)

    async def run():
        await orchestrator.launch(
            runtime=runtime_for(
                "claude-code", f"{sys.executable} {script} {tmp_path / 'history.sqlite'}"
            ),
            cwd=str(tmp_path),
            session_key="child",
            test=True,
        )
        supervisor = orchestrator.get("child")
        await asyncio.wait_for(supervisor._task, 5)
        assert supervisor._proc.returncode == 0
        assert "durable-before-child:" + history.session_id_for("child") in "".join(
            supervisor._output
        )

    asyncio.run(run())
    assert history.native_identity("child")["source"] == "assigned"
    assert history.session("child")["state"] == "terminated"


def test_switch_generation_barrier_and_restore_preserve_assigned_source(history):
    from duckterm.harness_switch import restore

    server = Server(history=history)
    seed(history)
    prior = {
        "runtime": "claude-code",
        "native_id": "original",
        "source": "assigned",
        "generation": "prior",
    }
    history.set_restart_control("old", {"native_binding": prior})
    history.set_harness_identity(
        "old",
        "copilot",
        None,
        None,
        control={
            "previous_conversation": {
                "runtime": "claude-code",
                "native_id": "original",
                "native_binding": prior,
            },
            "native_binding": {"runtime": "copilot", "native_id": None, "generation": "next"},
        },
    )
    assert history.native_identity("old")["status"] == "pending"
    assert history.session_id_for("old") is None
    restore(server, "old")
    assert history.native_identity("old") == {
        "native_id": "original",
        "source": "assigned",
        "status": "recorded",
    }
    assert history.session("old")["state"] == "stopped"
    assert history.restart_control("old")["native_binding"]["generation"] != "prior"
    server.digests.close()


def test_fresh_resume_of_unidentified_copilot_accepts_its_new_generation(
    history, tmp_path, monkeypatch
):
    (tmp_path / "copilot").symlink_to("/bin/echo")
    monkeypatch.setenv("PATH", str(tmp_path), prepend=":")
    server = Server(history=history)
    seed(history, native_id=None, cwd=str(tmp_path))
    history._conn.execute(
        "UPDATE sessions SET runtime='copilot', command='/bin/echo', state='interrupted' "
        "WHERE session_key='old'"
    )
    history._conn.commit()
    monkeypatch.setattr("duckterm.core.orchestrator.tmux.has_tmux", lambda: False)

    async def spawn(supervisor, argv):
        generation = supervisor._env["DUCKTERM_HARNESS_GENERATION"]
        assert generation
        assert accept_hook(
            server,
            {
                "event_type": "SessionStart",
                "runtime": "copilot",
                "session_key": "old",
                "session_id": argv[argv.index("--session-id") + 1],
                "launch_generation": generation,
            },
        )

    monkeypatch.setattr(SessionSupervisor, "_start_pty", spawn)
    try:
        assert asyncio.run(server._resume_session("old"))[0] == 200
        assert history.native_identity("old")["source"] == "assigned"
    finally:
        server.digests.close()
