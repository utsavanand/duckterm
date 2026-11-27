"""Real request stages, owner isolation and persisted timing without extra inference."""

import asyncio
import json

import pytest
from tests.runtime.test_session_api import dispatch
from tests.runtime.test_support import _server

from duckterm.core import chat_progress
from duckterm.llm.summarizer import Summary


def test_support_progress_tracks_real_work_and_survives_reload(tmp_path, monkeypatch):
    history, server = _server(tmp_path, monkeypatch)
    key = "a" * 32
    calls = []

    def answer(prompt, **kwargs):
        calls.append(prompt)
        assert server.chat_progress.read(key)["stages"][-1]["stage"] == "answer"
        return Summary(text="Check your installation.", backend="test")

    monkeypatch.setattr("duckterm.server.summarize", answer)
    headers = {"x-duckterm-token": server.token, "x-duckterm-chat-progress": key}
    status, result = dispatch(server, "POST", "/support/ask", headers, b'{"question":"Why?"}')
    assert status == 200
    assert len(calls) == 1
    stages = [s["stage"] for s in result["chat_progress"]["stages"]]
    assert stages == ["starting", "installation", "guide", "answer"]
    assert result["chat_progress"]["state"] == "finished"
    saved = dispatch(server, "GET", "/support/chat", headers)[1]["messages"][0]
    assert saved["progress"] == result["exchange"]["progress"]
    assert saved["progress"]["elapsed_ms"] >= 0
    assert dispatch(server, "GET", "/chat-progress/" + key, {})[0] == 401
    assert (
        dispatch(server, "GET", "/chat-progress/" + key, {"authorization": "Bearer bogus"})[0]
        == 403
    )
    assert dispatch(server, "GET", "/chat-progress/" + key, headers)[1]["state"] == "finished"
    assert "Why?" not in json.dumps(server.chat_progress.read(key))
    # A poll and a reused progress ID must never start another model call.
    assert dispatch(server, "POST", "/support/ask", headers, b'{"question":"Why?"}')[0] == 400
    assert len(calls) == 1
    history.close()


def test_failure_stops_progress_and_legacy_requests_stay_compatible(tmp_path, monkeypatch):
    history, server = _server(tmp_path, monkeypatch)
    owner = {"x-duckterm-token": server.token}
    monkeypatch.setattr(
        "duckterm.server.summarize", lambda *a, **kw: Summary(text="", backend="none")
    )
    status, result = dispatch(
        server,
        "POST",
        "/support/ask",
        {**owner, "x-duckterm-chat-progress": "b" * 32},
        b'{"question":"Why?"}',
    )
    assert status == 502
    assert result["chat_progress"]["state"] == "failed"
    assert dispatch(server, "GET", "/support/chat", owner)[1]["messages"] == []
    status, result = dispatch(server, "POST", "/support/ask", owner, b'{"question":"Why?"}')
    assert status == 502 and "chat_progress" not in result
    history.close()


def test_concurrent_requests_and_child_tasks_do_not_mix_stages():
    store = chat_progress.Store()

    async def run():
        ready = asyncio.Event()

        async def one(key, stage):
            with store.track(key):
                chat_progress.mark(stage)
                ready.set()
                await asyncio.sleep(0)
                await asyncio.to_thread(lambda: chat_progress.attach({}))
                assert [s["stage"] for s in store.read(key)["stages"]] == ["starting", stage]

        await asyncio.gather(one("a" * 32, "diff"), one("b" * 32, "installation"))

    asyncio.run(run())
    assert chat_progress.response(200, {}) == {}


def test_cache_is_bounded_expires_and_cancellation_finishes(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(chat_progress.time, "monotonic", lambda: now[0])
    store = chat_progress.Store(capacity=1, ttl=10)
    with store.track("a" * 32), store.track("b" * 32):
        assert len(store.rows) == 1
        assert chat_progress.response(200, {})["chat_progress"]["state"] == "finished"
    now[0] += 11
    assert store.read("a" * 32) is None
    with pytest.raises(asyncio.CancelledError), store.track("c" * 32):
        raise asyncio.CancelledError
    assert store.read("c" * 32)["state"] == "failed"
    with pytest.raises(ValueError), store.track("not-an-id"):
        pass
