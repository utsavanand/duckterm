"""Bounded native text, exact conversation identity and privacy across awaits."""

import asyncio
import json
import threading
from pathlib import Path

import pytest
from tests.runtime.test_ducky import visible_session_fixture

from duckterm.mobile import conversation
from duckterm.persistence.history import HistoryStore
from duckterm.runtimes.claude_code import project_slug
from duckterm.server import Server


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path / "duckterm"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    history = HistoryStore(tmp_path / "state.sqlite")
    server = Server(history=history)
    visible_session_fixture(server, monkeypatch, {"a"})
    yield server, tmp_path
    server.digests.close()
    history.purge_test_sessions()
    history.close()


def source(app, runtime="claude-code", native="recorded", state="busy"):
    server, root = app
    cwd = root / "project"
    cwd.mkdir(exist_ok=True)
    server.history.record(
        {
            "_id": "start",
            "_ts": 1,
            "event_type": "SessionStart",
            "session_key": "a",
            "runtime": runtime,
            "cwd": str(cwd),
            "session_id": native,
            "test": True,
        }
    )
    server.history.set_state("a", state, now=2)
    path = (
        root / ".claude/projects" / project_slug(cwd) / f"{native}.jsonl"
        if runtime == "claude-code"
        else root / ".codex/sessions/2026/11/01" / f"rollout-2026-{native}.jsonl"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def claude(role, text, **extra):
    return {"type": role, "message": {"role": role, "content": text}, **extra}


def codex(role, text, **extra):
    return {
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": role,
            "content": [{"type": "input_text" if role == "user" else "output_text", "text": text}],
            **extra,
        },
    }


def write(path, rows):
    path.write_bytes(b"".join(json.dumps(row, ensure_ascii=False).encode() + b"\n" for row in rows))


def read(server):
    return asyncio.run(conversation.read_conversation(server, "a"))


@pytest.mark.parametrize("runtime", ["claude-code", "codex"])
@pytest.mark.parametrize("state", ["idle", "terminated"])
def test_test_session_cannot_read_or_discover_transcript(app, monkeypatch, runtime, state):
    server, root = app
    server.history.record(
        {
            "_id": "hidden-start",
            "_ts": 1,
            "event_type": "SessionStart",
            "session_key": "hidden-test",
            "runtime": runtime,
            "cwd": str(root),
            "session_id": "recorded-test-native",
            "test": True,
        }
    )
    server.history.set_state("hidden-test", state, now=2)
    monkeypatch.setattr(
        conversation,
        "runtime_for",
        lambda *args: pytest.fail("A hidden session must not discover or read a transcript"),
    )
    assert conversation.conversation_available(server, "hidden-test") is False
    with pytest.raises(conversation.ConversationNotFound, match="^session_not_found$"):
        conversation.trusted_message_source(server, "hidden-test")
    with pytest.raises(conversation.ConversationNotFound, match="^session_not_found$"):
        asyncio.run(conversation.read_conversation(server, "hidden-test"))
    assert server.history.session("hidden-test")["test"] == 1


def test_session_marked_test_during_read_discards_result(app, monkeypatch):
    path = source(app)
    write(path, [claude("assistant", "PRIVATE_TEST_REPLY")])
    server = app[0]
    entered, release = threading.Event(), threading.Event()
    original = conversation._read

    def delayed(captured):
        result = original(captured)
        entered.set()
        assert release.wait(3)
        return result

    monkeypatch.setattr(conversation, "_read", delayed)

    async def run():
        task = asyncio.create_task(conversation.read_conversation(server, "a"))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            session = server.history.session
            monkeypatch.setattr(
                server.history,
                "session",
                lambda key: {**row, "test": True} if (row := session(key)) else None,
            )
            release.set()
            with pytest.raises(conversation.ConversationNotFound, match="^session_not_found$"):
                await task
            assert conversation.conversation_available(server, "a") is False
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())


def test_claude_only_plain_turns_and_nullable_epoch_timestamps(app):
    path = source(app)
    write(
        path,
        [
            claude("user", "Question", timestamp="2026-11-01T00:00:00Z"),
            claude("user", "PRIVATE_SYSTEM", isMeta=True),
            claude("user", "PRIVATE_NOTIFICATION", promptSource="system"),
            claude("user", "<local-command-stdout>PRIVATE_COMMAND"),
            claude(
                "assistant",
                [
                    {"type": "thinking", "thinking": "PRIVATE_THOUGHT", "text": "PRIVATE_THOUGHT"},
                    {"type": "tool_use", "input": {"token": "PRIVATE_TOOL"}},
                    {"type": "text", "text": "Answer\nsecond line"},
                ],
                timestamp="invalid",
            ),
            claude("user", [{"type": "tool_result", "content": "PRIVATE_RESULT"}]),
        ],
    )
    result = read(app[0])
    assert [(m["role"], m["text"]) for m in result["messages"]] == [
        ("user", "Question"),
        ("assistant", "Answer\nsecond line"),
    ]
    assert result["messages"][0]["at"] == 1793491200000
    assert result["messages"][1]["at"] is None
    assert result["truncated"] is False
    assert "PRIVATE" not in json.dumps(result)
    assert all(set(m) == {"id", "role", "text", "at"} for m in result["messages"])


def test_codex_does_not_duplicate_event_messages_or_expose_tools_analysis(app):
    path = source(app, "codex")
    write(
        path,
        [
            {"type": "session_meta", "payload": {"id": "recorded"}},
            {"type": "event_msg", "payload": {"type": "user_message", "message": "DUPLICATE"}},
            codex("user", "Question"),
            codex("user", "<environment_context>PRIVATE_CONTEXT"),
            codex("assistant", "PRIVATE_ANALYSIS", channel="analysis"),
            {
                "type": "response_item",
                "payload": {"type": "function_call_output", "output": "PRIVATE_TOOL"},
            },
            codex("assistant", "Answer"),
        ],
    )
    result = read(app[0])
    assert [m["text"] for m in result["messages"]] == ["Question", "Answer"]
    assert "PRIVATE" not in json.dumps(result) and "DUPLICATE" not in json.dumps(result)


@pytest.mark.parametrize("state", ["stopped", "terminated", "interrupted"])
def test_ended_session_can_read_exact_trusted_history(app, state):
    path = source(app, state=state)
    write(path, [claude("assistant", "Saved reply")])
    assert read(app[0])["messages"][0]["text"] == "Saved reply"


@pytest.mark.parametrize("runtime", ["generic", "copilot", "opencode"])
def test_unsupported_adapter_is_unavailable_instead_of_fabricated(app, runtime):
    source(app, runtime)
    with pytest.raises(conversation.ConversationUnavailable, match="source_unavailable"):
        read(app[0])


def test_missing_identity_never_uses_a_peers_latest_transcript(app):
    path = source(app)
    write(path, [claude("assistant", "PEER_REPLY")])
    app[0].history._conn.execute(
        "UPDATE sessions SET restart_json = ? WHERE session_key='a'",
        (
            json.dumps(
                {
                    "native_binding": {
                        "runtime": "claude-code",
                        "native_id": None,
                        "generation": "new",
                    }
                }
            ),
        ),
    )
    with pytest.raises(conversation.ConversationUnavailable):
        read(app[0])


def test_missing_file_and_unknown_session_have_typed_safe_errors(app):
    with pytest.raises(conversation.ConversationNotFound):
        read(app[0])
    source(app)
    with pytest.raises(conversation.ConversationUnavailable) as caught:
        read(app[0])
    assert str(caught.value) == "source_unavailable"


def test_damaged_identity_metadata_is_typed_unavailable(app):
    source(app)
    app[0].history._conn.execute("UPDATE sessions SET restart_json='invalid' WHERE session_key='a'")
    assert conversation.conversation_available(app[0], "a") is False
    with pytest.raises(conversation.ConversationUnavailable, match="^source_unavailable$"):
        read(app[0])


@pytest.mark.parametrize("change", ["delete", "identity", "runtime", "cwd", "generation"])
def test_session_provenance_is_rechecked_after_file_work(app, monkeypatch, change):
    path = source(app)
    write(path, [claude("assistant", "OLD_IDENTITY_REPLY")])
    server = app[0]
    original = conversation._read
    entered, release = threading.Event(), threading.Event()

    def delayed(captured):
        result = original(captured)
        entered.set()
        assert release.wait(3)
        return result

    monkeypatch.setattr(conversation, "_read", delayed)

    async def run():
        task = asyncio.create_task(conversation.read_conversation(server, "a"))
        while not entered.is_set():
            await asyncio.sleep(0.001)
        if change == "delete":
            server.history.delete_session("a")
        elif change == "cwd":
            server.history._conn.execute(
                "UPDATE sessions SET cwd='elsewhere' WHERE session_key='a'"
            )
        elif change == "runtime":
            server.history._conn.execute(
                "UPDATE sessions SET runtime='codex' WHERE session_key='a'"
            )
        else:
            native = "new" if change == "identity" else "recorded"
            control = {
                "native_binding": {
                    "runtime": "claude-code",
                    "native_id": native,
                    "generation": "new",
                }
            }
            server.history._conn.execute(
                "UPDATE sessions SET restart_json=? WHERE session_key='a'", (json.dumps(control),)
            )
        release.set()
        with pytest.raises(conversation.ConversationUnavailable):
            await task

    asyncio.run(run())


def test_last_hundred_messages_have_stable_ids_after_append(app):
    path = source(app)
    write(path, [claude("assistant", f"Reply {i}") for i in range(110)])
    before = read(app[0])
    assert len(before["messages"]) == 100 and before["truncated"] is True
    assert before["messages"][0]["text"] == "Reply 10"
    with path.open("ab") as output:
        output.write(json.dumps(claude("assistant", "Reply 110")).encode() + b"\n")
    after = read(app[0])
    assert [m["id"] for m in before["messages"][1:]] == [m["id"] for m in after["messages"][:-1]]


def test_output_byte_limit_and_utf8_are_bounded(app):
    path = source(app)
    write(path, [claude("assistant", '🙂"\n' * 50000)])
    result = read(app[0])
    encoded = json.dumps(result["messages"]).encode()
    assert len(encoded) <= conversation.MAX_BYTES
    assert result["truncated"] is True and result["messages"]
    assert result["messages"][0]["text"].startswith('🙂"\n')


def test_huge_transcript_uses_only_bounded_tail_and_skips_partial_records(app, monkeypatch):
    path = source(app)
    with path.open("wb") as output:
        output.write(b"not-a-complete-record" * 100000)
        output.write(b"\n")
        output.write(json.dumps(claude("assistant", "Recent answer")).encode() + b"\n")
    calls = []
    original = conversation.os.read

    def bounded(fd, size):
        calls.append(size)
        assert size <= conversation.MAX_READ_BYTES
        return original(fd, size)

    monkeypatch.setattr(conversation.os, "read", bounded)
    monkeypatch.setattr(Path, "read_text", lambda *a, **k: pytest.fail("No whole transcript reads"))
    result = read(app[0])
    assert calls == [conversation.MAX_READ_BYTES]
    assert result["truncated"] and [m["text"] for m in result["messages"]] == ["Recent answer"]


def test_symlink_source_is_refused(app):
    path = source(app)
    other = path.parent / "other.txt"
    other.write_text(json.dumps(claude("assistant", "ARBITRARY_FILE")))
    path.symlink_to(other)
    with pytest.raises(conversation.ConversationUnavailable):
        read(app[0])


def test_recorded_identity_eligibility_is_cheap_but_read_checks_file(app, monkeypatch):
    assert conversation.conversation_available(app[0], "a") is False
    source(app)
    monkeypatch.setattr(
        conversation, "runtime_for", lambda *a: pytest.fail("No file lookup for metadata")
    )
    assert conversation.conversation_available(app[0], "a") is True


def test_partial_write_and_non_message_json_are_ignored(app):
    path = source(app)
    write(path, [[], None, claude("assistant", "Complete reply")])
    with path.open("ab") as output:
        output.write(b'{"type":"assistant","message":')
    result = read(app[0])
    assert result["truncated"] is True
    assert [row["text"] for row in result["messages"]] == ["Complete reply"]
