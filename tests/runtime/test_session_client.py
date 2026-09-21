"""A real child process can use its own credential immediately after launch."""

import asyncio
import json
import shlex
import sys
from pathlib import Path

import pytest

from duckterm.cli import build_parser
from duckterm.core.eventbus import EventBus
from duckterm.core.orchestrator import Orchestrator
from duckterm.helpers.session_credentials import client_credentials, credential_path
from duckterm.persistence.history import HistoryStore
from duckterm.runtimes.generic import GenericRuntime
from duckterm.server import Server


def test_existing_session_client_reads_backfilled_credentials(tmp_path: Path, monkeypatch) -> None:
    store = HistoryStore(tmp_path / "db.sqlite")
    store.record(
        {"_id": "start", "_ts": 1, "session_key": "existing", "event_type": "SessionStart"}
    )
    store.session_api.set_url("http://127.0.0.1:4398")
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path))
    monkeypatch.setenv("DUCKTERM_SESSION_KEY", "existing")
    monkeypatch.delenv("DUCKTERM_SESSION_TOKEN_FILE", raising=False)
    monkeypatch.delenv("DUCKTERM_INTERNAL", raising=False)
    url, token = client_credentials()
    assert url == "http://127.0.0.1:4398"
    assert store.session_api.authenticate({"authorization": "Bearer " + token}) == "existing"
    assert (
        credential_path("existing", store.session_api.credential_dir).stat().st_mode & 0o777
        == 0o600
    )
    monkeypatch.setenv("DUCKTERM_INTERNAL", "1")
    with pytest.raises(ValueError, match="internal helper"):
        client_credentials()
    store.close()


def test_child_can_read_its_card_before_first_hook(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("duckterm.core.orchestrator.tmux.has_tmux", lambda: False)
    store = HistoryStore(tmp_path / "db.sqlite")
    server = Server(history=store)
    bus = EventBus(sink=store.record)
    orch = Orchestrator(bus, history=store)
    result_file = tmp_path / "child-card.json"
    script = tmp_path / "agent.py"
    script.write_text(
        "import json, urllib.request\n"
        "from pathlib import Path\n"
        "from duckterm.helpers.session_credentials import client_credentials\n"
        "url, token = client_credentials()\n"
        "request = urllib.request.Request(url + '/api/v1/session/self', "
        "headers={'Authorization': 'Bearer ' + token})\n"
        "with urllib.request.urlopen(request) as response:\n"
        f"    Path({str(result_file)!r}).write_text(response.read().decode())\n"
    )

    async def scenario() -> None:
        listener = await asyncio.start_server(server.handle, "127.0.0.1", 0)
        port = listener.sockets[0].getsockname()[1]
        store.session_api.set_url(f"http://127.0.0.1:{port}")
        async with listener:
            await orch.launch(
                runtime=GenericRuntime(shlex.join([sys.executable, str(script)])),
                cwd=str(tmp_path),
                session_key="fresh",
                env={"DUCKTERM_INTERNAL": ""},
            )
            for _ in range(100):
                if result_file.exists():
                    break
                await asyncio.sleep(0.05)
            assert result_file.exists(), orch.get("fresh").output_tail()
            assert json.loads(result_file.read_text())["session_id"] == "fresh"
            supervisor = orch.get("fresh")
            assert supervisor is not None and supervisor._task is not None
            await asyncio.wait_for(supervisor._task, 5)

    asyncio.run(scenario())
    store.close()


def test_cli_commands_accept_multiline_reply_file() -> None:
    args = build_parser().parse_args(["session", "reply", "q-123", "--file", "answer.txt"])
    assert (args.session_action, args.request_id, args.file) == ("reply", "q-123", "answer.txt")
