"""The observation loop: /agents-md/suggest distills the corrections users gave
agents in a folder (annotations + follow-up prompts) into proposed AGENTS.md
rules. The LLM backend is faked so the tests pin the gathering + parsing."""

import asyncio
import json
from pathlib import Path

import pytest
from tests.runtime.test_agents_md import _request

import duckterm.llm.suggest as suggest_mod
from duckterm.llm.summarizer import Summary
from duckterm.persistence.history import HistoryStore
from duckterm.server import Server


async def _suggest(port: int, token: str, directory: str) -> tuple[int, dict]:
    payload = json.dumps({"dir": directory}).encode()
    return await _request(
        port,
        b"POST /agents-md/suggest HTTP/1.1\r\nHost: x\r\n"
        b"X-Duckterm-Token: " + token.encode() + b"\r\n"
        b"Content-Type: application/json\r\n"
        b"Content-Length: " + str(len(payload)).encode() + b"\r\n\r\n" + payload,
    )


def test_suggest_gathers_corrections_and_returns_rules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = HistoryStore(tmp_path / "db.sqlite")
    workdir = tmp_path / "proj"
    workdir.mkdir()
    # A session in the folder: first prompt is the task (not a correction),
    # the second is steering. Plus one annotation.
    store.record(
        {
            "event_type": "SessionStart",
            "session_key": "s1",
            "cwd": str(workdir),
            "launched": True,
            "_ts": 1,
            "_id": "a",
        }
    )
    store.record(
        {
            "event_type": "UserPromptSubmit",
            "session_key": "s1",
            "prompt": "build the CSV exporter",
            "_ts": 2,
            "_id": "b",
        }
    )
    store.record(
        {
            "event_type": "UserPromptSubmit",
            "session_key": "s1",
            "prompt": "use rg instead of grep",
            "_ts": 3,
            "_id": "c",
        }
    )
    store.add_annotation("ann1", "s1", "// loop over items", "stop adding obvious comments", 4)
    # A session OUTSIDE the folder must not contribute.
    store.record(
        {
            "event_type": "SessionStart",
            "session_key": "other",
            "cwd": "/somewhere/else",
            "launched": True,
            "_ts": 5,
            "_id": "d",
        }
    )
    store.record(
        {
            "event_type": "UserPromptSubmit",
            "session_key": "other",
            "prompt": "first task",
            "_ts": 6,
            "_id": "e",
        }
    )
    store.record(
        {
            "event_type": "UserPromptSubmit",
            "session_key": "other",
            "prompt": "unrelated steering",
            "_ts": 7,
            "_id": "f",
        }
    )

    seen: dict = {}

    def fake_summarize(prompt: str) -> Summary:
        seen["prompt"] = prompt
        return Summary(text="- Use rg, not grep\n- Don't add obvious comments", backend="fake")

    monkeypatch.setattr(suggest_mod, "summarize", fake_summarize)

    async def scenario() -> tuple[int, dict]:
        server = Server(history=store)
        srv = await asyncio.start_server(server.handle, "127.0.0.1", 0)
        port = srv.sockets[0].getsockname()[1]
        async with srv:
            return await _suggest(port, server.token, str(workdir))

    status, body = asyncio.run(scenario())
    assert status == 200
    assert body["suggestions"] == ["- Use rg, not grep", "- Don't add obvious comments"]
    assert body["corrections_seen"] == 2  # the follow-up + the annotation
    # The LLM saw the real corrections — and NOT the task prompt or the
    # other folder's steering.
    assert "use rg instead of grep" in seen["prompt"]
    assert "stop adding obvious comments" in seen["prompt"]
    assert "build the CSV exporter" not in seen["prompt"]
    assert "unrelated steering" not in seen["prompt"]


def test_suggest_with_no_corrections_skips_the_llm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = HistoryStore(tmp_path / "db.sqlite")
    (tmp_path / "empty").mkdir()

    def boom(prompt: str) -> Summary:
        raise AssertionError("summarize must not be called with no corrections")

    monkeypatch.setattr(suggest_mod, "summarize", boom)

    async def scenario() -> tuple[int, dict]:
        server = Server(history=store)
        srv = await asyncio.start_server(server.handle, "127.0.0.1", 0)
        port = srv.sockets[0].getsockname()[1]
        async with srv:
            return await _suggest(port, server.token, str(tmp_path / "empty"))

    status, body = asyncio.run(scenario())
    assert status == 200
    assert body == {"suggestions": [], "corrections_seen": 0}
