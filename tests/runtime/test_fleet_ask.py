"""POST /fleet/ask: digests of RUNNING sessions through the summarizer backend.
DUCKTERM_SUMMARIZER_CMD=cat echoes the prompt back as the "answer", so these
tests can assert exactly what reached the LLM."""

import asyncio
import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from duckterm.helpers import security
from duckterm.persistence.history import HistoryStore
from duckterm.runtimes.generic import GenericRuntime
from duckterm.server import Server


def _ask(port: int, payload: dict[str, object]) -> tuple[int, dict[str, object]]:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/fleet/ask",
        data=json.dumps(payload).encode(),
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


def test_fleet_ask_digests_running_sessions_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DUCKTERM_SUMMARIZER_CMD", "cat")

    async def scenario() -> tuple[int, dict[str, object]]:
        server = Server(history=HistoryStore(tmp_path / "db.sqlite"))
        orch = server.orchestrator
        await orch.launch(
            runtime=GenericRuntime("sh -c 'echo pondering; sleep 5'"),
            cwd=str(tmp_path),
            session_key="fleet-live",
            name="refactor-auth",
        )
        stopped = await orch.launch(
            runtime=GenericRuntime("sh -c 'sleep 5'"),
            cwd=str(tmp_path),
            session_key="fleet-stopped",
            name="old-work",
        )
        sup = orch.get("fleet-live")
        assert sup is not None
        for _ in range(80):  # the tmux pipe can lag the spawn by ~a second
            if "pondering" in "".join(sup.output_tail()):
                break
            await asyncio.sleep(0.05)
        await orch.stop(stopped)
        srv = await asyncio.start_server(server.handle, "127.0.0.1", 0)
        port = srv.sockets[0].getsockname()[1]
        async with srv:
            out = await asyncio.to_thread(_ask, port, {"question": "what is refactor-auth doing?"})
        await orch.stop("fleet-live")
        return out

    status, body = asyncio.run(scenario())
    assert status == 200
    answer = str(body["answer"])
    # The digest reached the backend: session name, its terminal output, and
    # the question itself all appear in the echoed prompt.
    assert "refactor-auth" in answer
    assert "pondering" in answer
    assert "what is refactor-auth doing?" in answer
    # Running-only: the stopped session is not offered to the LLM.
    assert "old-work" not in answer
    assert body["sessions"] == ["fleet-live"]


def test_fleet_ask_requires_a_question_and_reports_missing_backend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DUCKTERM_SUMMARIZER_CMD", raising=False)
    monkeypatch.setenv("DUCKTERM_SUMMARIZER", "off")

    async def scenario() -> list[tuple[int, dict[str, object]]]:
        server = Server(history=HistoryStore(tmp_path / "db.sqlite"))
        await server.orchestrator.launch(
            runtime=GenericRuntime("sh -c 'sleep 5'"),
            cwd=str(tmp_path),
            session_key="fleet-x",
        )
        srv = await asyncio.start_server(server.handle, "127.0.0.1", 0)
        port = srv.sockets[0].getsockname()[1]
        async with srv:
            out = [
                await asyncio.to_thread(_ask, port, {"question": ""}),
                await asyncio.to_thread(_ask, port, {"question": "anyone stuck?"}),
            ]
        await server.orchestrator.stop("fleet-x")
        return out

    (empty_status, empty_body), (off_status, off_body) = asyncio.run(scenario())
    assert empty_status == 400
    assert "question" in str(empty_body["error"])
    # Fail loudly when no backend exists instead of returning an empty answer.
    assert off_status == 502
    assert "summarizer" in str(off_body["error"])


def test_fleet_ask_with_no_sessions_answers_without_an_llm_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Backend off: if the endpoint tried to call it, this would 502.
    monkeypatch.setenv("DUCKTERM_SUMMARIZER", "off")
    monkeypatch.delenv("DUCKTERM_SUMMARIZER_CMD", raising=False)

    async def scenario() -> tuple[int, dict[str, object]]:
        server = Server(history=HistoryStore(tmp_path / "db.sqlite"))
        srv = await asyncio.start_server(server.handle, "127.0.0.1", 0)
        port = srv.sockets[0].getsockname()[1]
        async with srv:
            return await asyncio.to_thread(_ask, port, {"question": "anything running?"})

    status, body = asyncio.run(scenario())
    assert status == 200
    assert body["answer"] == "No sessions are running right now."


def test_headless_launch_persists_the_name_for_the_digest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The SessionStart event carries the name to live dashboards, but the
    fleet digest reads the ROW — a headless launch must persist it too."""
    monkeypatch.setenv("DUCKTERM_SUMMARIZER", "off")

    def _launch(port: int) -> None:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/sessions/launch",
            data=json.dumps(
                {
                    "command": "sh -c 'sleep 3'",
                    "cwd": str(tmp_path),
                    "name": "digest-me",
                    "session_key": "fleet-named",
                    "in_terminal": False,
                    "test": True,
                }
            ).encode(),
            headers={
                "Content-Type": "application/json",
                "X-Duckterm-Token": security.load_or_create_token(),
            },
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10).read()

    async def scenario() -> str | None:
        server = Server(history=HistoryStore(tmp_path / "db.sqlite"))
        srv = await asyncio.start_server(server.handle, "127.0.0.1", 0)
        port = srv.sockets[0].getsockname()[1]
        async with srv:
            await asyncio.to_thread(_launch, port)
        row = server.history.session("fleet-named")
        await server.orchestrator.stop("fleet-named")
        return str(row["name"]) if row and row.get("name") else None

    assert asyncio.run(scenario()) == "digest-me"
