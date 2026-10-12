import json
from dataclasses import replace

import pytest

from duckterm.runtimes import native_history
from duckterm.runtimes.opencode_protocol import NativeClient, OpenCodeError
from duckterm.runtimes.opencode_snapshot import lifecycle


def message(identity="msg_one", text="answer"):
    return {"id": identity, "role": "assistant", "blocks": [{"type": "text", "text": text}]}


def test_cached_history_rejects_late_writes_without_changing_message_keys_or_content_fence(
    tmp_path,
):
    source = native_history.NativeSnapshot(
        str(tmp_path / "messages.json"), "gen", "local", "store", "ses_one"
    )
    assert native_history.read(source)["history_status"] == "unavailable"
    assert native_history.persist(source, [message()], 1000, 10)
    first = native_history.read(source)
    version = (tmp_path / "messages.json").stat().st_mtime_ns
    assert native_history.persist(source, [message()], 2000, 100)
    assert (tmp_path / "messages.json").stat().st_mtime_ns == version
    assert not native_history.persist(source, [message(text="old streaming chunk")], 1500, 50)
    assert native_history.read(source) == first
    assert native_history.persist(source, [message(text="revised")], 3000, 101)
    assert (
        native_history.read(source)["messages"][0]["message_key"]
        != first["messages"][0]["message_key"]
    )


def test_cache_scope_and_symlink_cannot_rebind_native_history(tmp_path):
    source = native_history.NativeSnapshot(
        str(tmp_path / "messages.json"), "gen", "local", "store", "ses_one"
    )
    native_history.persist(source, [message()], 1, 1)
    for changed in [
        replace(source, generation="other"),
        replace(source, store="other"),
        replace(source, destination="remote"),
        replace(source, session_id="ses_other"),
    ]:
        with pytest.raises(OpenCodeError, match="another conversation"):
            native_history.read(changed)
    link = tmp_path / "alias.json"
    link.symlink_to(source.path)
    with pytest.raises(OSError):
        native_history.read(replace(source, path=str(link)))


def raw(identity):
    return {"info": {"id": identity, "sessionID": "ses_one", "role": "user"}, "parts": []}


def test_paginated_native_history_is_chronological_and_scoped(monkeypatch):
    calls = []
    pages = [([raw("msg_new")], "cursor/+?"), ([raw("msg_old")], None)]

    def request(self, method, path, body=None, **kwargs):
        assert method == "GET" and kwargs == {"with_cursor": True}
        calls.append(path)
        return pages.pop(0)

    monkeypatch.setattr(NativeClient, "request", request)
    client = NativeClient(1234, "fixture-only", "/fixture")
    assert [row["id"] for row in client.messages("ses_one")] == ["msg_old", "msg_new"]
    assert calls[1] == "/session/ses_one/message?limit=100&before=cursor%2F%2B%3F"


@pytest.mark.parametrize("mode", ["repeated-cursor", "duplicate-message", "foreign-session"])
def test_ambiguous_or_incomplete_pagination_is_not_a_complete_snapshot(monkeypatch, mode):
    first = raw("msg_one")
    second = raw("msg_two")
    cursor = None
    if mode == "repeated-cursor":
        cursor = "same"
    elif mode == "duplicate-message":
        second = first
    else:
        second["info"]["sessionID"] = "ses_foreign"
    pages = [([first], "same"), ([second], cursor)]
    monkeypatch.setattr(NativeClient, "request", lambda *args, **kwargs: pages.pop(0))
    with pytest.raises(OpenCodeError):
        NativeClient(1234, "fixture-only", "/fixture").messages("ses_one")


def test_idle_requires_latest_parent_completion_and_no_parent_request():
    rows = [
        {"id": "msg_user", "role": "user", "blocks": []},
        {
            "id": "msg_answer",
            "role": "assistant",
            "parent_id": "msg_user",
            "complete": True,
            "finish": "stop",
            "blocks": [],
        },
    ]
    assert lifecycle("ses_one", {}, {}, [], [], rows) == "idle"
    assert lifecycle("ses_one", {}, {}, [{"sessionID": "ses_child"}], [], rows) == "idle"
    assert lifecycle("ses_one", {}, {}, [{"sessionID": "ses_one"}], [], rows) == "waiting"
    assert lifecycle("ses_one", {}, {"ses_one": {"type": "busy"}}, [], [], rows) == "busy"
    rows.append({"id": "msg_next", "role": "user", "blocks": []})
    assert lifecycle("ses_one", {}, {}, [], [], rows) == "unknown"
    rows.pop()
    rows[-1]["blocks"] = [{"type": "tool_use", "status": "running"}]
    assert lifecycle("ses_one", {}, {}, [], [], rows) == "unknown"
    rows[-1]["blocks"] = []
    rows[-1]["error"] = True
    assert lifecycle("ses_one", {}, {}, [], [], rows) == "unknown"


def test_corrupt_cached_scope_never_becomes_an_empty_successful_conversation(tmp_path):
    source = native_history.NativeSnapshot(
        str(tmp_path / "messages.json"), "gen", "local", "store", "ses_one"
    )
    (tmp_path / "messages.json").write_text(json.dumps({"messages": []}))
    with pytest.raises(OpenCodeError):
        native_history.read(source)
