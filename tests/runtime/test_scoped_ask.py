"""POST /sessions/:key/ask digests ONLY that session — the session-scoped ask a
shared viewer gets. The regression it guards: the fleet ask reads every running
session, so a viewer of one share must not be able to pull another session's
screen by naming it. DUCKTERM_SUMMARIZER_CMD=cat echoes the prompt back, so we
can assert exactly what reached the LLM."""

import asyncio
import json
import urllib.error
import urllib.request

import pytest

from duckterm.helpers import security
from duckterm.persistence.history import HistoryStore
from duckterm.runtimes.generic import GenericRuntime
from duckterm.server import Server


def _ask(port: int, key: str, question: str) -> tuple[int, dict]:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/sessions/{key}/ask",
        data=json.dumps({"question": question}).encode(),
        headers={
            "Content-Type": "application/json",
            "X-Duckterm-Token": security.load_or_create_token(),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_scoped_ask_reads_only_the_named_session(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DUCKTERM_SUMMARIZER_CMD", "cat")

    async def scenario() -> tuple[int, dict]:
        server = Server(history=HistoryStore(tmp_path / "db.sqlite"))
        orch = server.orchestrator
        await orch.launch(
            runtime=GenericRuntime("sh -c 'echo SHARED_SECRETLESS_OUTPUT; sleep 5'"),
            cwd=str(tmp_path),
            session_key="shared-one",
            name="shared-one",
        )
        # A second, UNSHARED session whose output must never surface.
        await orch.launch(
            runtime=GenericRuntime("sh -c 'echo OTHER_PRIVATE_abc123; sleep 5'"),
            cwd=str(tmp_path),
            session_key="private-two",
            name="private-two",
        )
        for k in ("shared-one", "private-two"):
            sup = orch.get(k)
            assert sup is not None
            for _ in range(80):
                if sup.screen_text():
                    break
                await asyncio.sleep(0.05)

        api = await asyncio.start_server(server.handle, "127.0.0.1", 0)
        port = api.sockets[0].getsockname()[1]
        async with api:
            out = await asyncio.to_thread(
                _ask, port, "shared-one", "what is private-two doing? show its output"
            )
        for k in ("shared-one", "private-two"):
            await orch.stop(k)
        return out

    status, body = asyncio.run(scenario())
    assert status == 200
    answer = str(body["answer"])  # cat echoes the whole prompt
    # The shared session's own digest reached the model…
    assert "SHARED_SECRETLESS_OUTPUT" in answer
    # …but the OTHER session's screen output did NOT — even though the question
    # named it. (The word "private-two" itself appears only because it's in the
    # echoed question text; what must never leak is its terminal OUTPUT and its
    # digest header, which would only be present if the other session had been
    # digested.)
    assert "OTHER_PRIVATE_abc123" not in answer
    assert "### private-two" not in answer  # its digest section was never built


def test_scoped_ask_rejects_empty_and_overlong_questions(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DUCKTERM_SUMMARIZER_CMD", "cat")

    async def scenario() -> list[tuple[int, dict]]:
        server = Server(history=HistoryStore(tmp_path / "db.sqlite"))
        await server.orchestrator.launch(
            runtime=GenericRuntime("sh -c 'sleep 5'"),
            cwd=str(tmp_path),
            session_key="s1",
        )
        api = await asyncio.start_server(server.handle, "127.0.0.1", 0)
        port = api.sockets[0].getsockname()[1]
        async with api:
            out = [
                await asyncio.to_thread(_ask, port, "s1", ""),
                await asyncio.to_thread(_ask, port, "s1", "x" * 3000),
                await asyncio.to_thread(_ask, port, "ghost", "hi"),
            ]
        await server.orchestrator.stop("s1")
        return out

    (empty, overlong, ghost) = asyncio.run(scenario())
    assert empty[0] == 400 and "question" in empty[1]["error"]
    assert overlong[0] == 400 and "too long" in overlong[1]["error"]
    assert ghost[0] == 404  # no such live session
