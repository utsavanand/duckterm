from pathlib import Path
from types import SimpleNamespace

from duckterm.core.eventbus import EventBus
from duckterm.core.saved_progress import transcript_fence
from duckterm.persistence import opencode_launch as journal
from duckterm.persistence.history import HistoryStore, derive_state
from duckterm.runtimes import native_history
from duckterm.server import Server


def test_api_backed_messages_use_recorded_binding_and_never_terminal_fallback(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path))
    history = HistoryStore(tmp_path / "db.sqlite")
    server = SimpleNamespace(history=history)
    EventBus(sink=history.record).publish(
        {
            "event_type": "SessionStart",
            "session_key": "fixture",
            "runtime": "opencode",
            "cwd": str(tmp_path),
            "test": True,
        }
    )
    try:
        state = journal.Launch("gen", "local", "store", str(tmp_path.resolve()), 123)
        journal.begin(history._conn, "fixture", state)
        journal.claim_create(history._conn, "fixture", "gen")
        journal.bind_created(
            history._conn, "fixture", "gen", {"id": "ses_fixture", "directory": state.directory}
        )
        source = Server._message_source(server, "fixture")
        Path(source.path).parent.mkdir(parents=True)
        records = [
            {
                "id": "msg_native",
                "role": "assistant",
                "blocks": [{"type": "text", "text": "verified native answer"}],
            }
        ]
        native_history.persist(source, records, 1000, 1)
        assert Server._read_messages(source)[0]["blocks"][0]["text"] == "verified native answer"
        assert transcript_fence(source)["source_kind"] == "native-api-cache"
        assert Server._progress_transcript(source, None) == [
            {"role": "assistant", "text": "verified native answer"}
        ]
        control = history.restart_control("fixture")
        control["native_binding"]["generation"] = "replacement"
        history.set_restart_control("fixture", control)
        assert Server._message_source(server, "fixture") is None
        sup = SimpleNamespace(runtime=SimpleNamespace(name="opencode"))
        assert Server._progress_transcript(None, sup) == []
    finally:
        history.delete_session("fixture")
        history.close()


def test_unavailable_native_state_is_neither_idle_nor_a_dead_terminal():
    event = {"event_type": "NativeStatus", "runtime": "opencode", "native_state": "unknown"}
    assert derive_state(event, "idle") == "unknown"
    assert derive_state(event, "stopped") == "stopped"
    assert derive_state({**event, "native_state": "idle"}, "unknown") == "idle"
