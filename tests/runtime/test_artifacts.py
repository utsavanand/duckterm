"""Artifact snapshots survive source removal and remain owned by one session."""

import base64
import json
from pathlib import Path

import pytest
from tests.runtime.test_session_api import dispatch

from duckterm.cli import build_parser
from duckterm.persistence import artifacts
from duckterm.persistence.history import HistoryStore
from duckterm.server import Server


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path))
    history = HistoryStore(tmp_path / "db.sqlite")
    agents = {}
    for key in ("a", "b"):
        history.record(
            {"_id": key, "_ts": 1, "event_type": "SessionStart", "session_key": key, "test": True}
        )
        history.set_meta(key, group="project", name=f"Agent {key}")
        result = history.session_api.enroll(key, {"root": "project"})
        agents[key] = {"authorization": "Bearer " + result["token"]}
    server = Server(history=history)
    yield server, agents, {"x-duckterm-token": server.token}
    history.purge_test_sessions()
    history.close()


def register(server, agent, req):
    return dispatch(server, "POST", "/api/v1/session/artifacts", agent, json.dumps(req).encode())


def test_register_replace_restart_and_remove_source(app, tmp_path):
    server, agents, owner = app
    file = tmp_path / "mockup.html"
    file.write_text("<h1>Original</h1>")
    req = artifacts.registration(file, "Mockup")
    status, body = register(server, agents["a"], req)
    assert status == 200
    saved = body["artifact"]
    endpoint = f"/sessions/a/artifacts/{saved['id']}"
    assert "content_base64" not in saved
    assert register(server, agents["a"], req) == (status, body)
    file.write_text("<h1>Revised</h1>")
    replacement = register(server, agents["a"], artifacts.registration(file, "Final mockup"))[1][
        "artifact"
    ]
    assert replacement["id"] == saved["id"]
    assert replacement["created_at"] == saved["created_at"]
    assert replacement["sha256"] != saved["sha256"]
    file.unlink()
    reopened = HistoryStore(tmp_path / "db.sqlite")
    try:
        copy = reopened.artifacts.get("a", saved["id"])
        assert base64.b64decode(copy["content_base64"]) == b"<h1>Revised</h1>"
    finally:
        reopened.close()
    assert dispatch(server, "GET", "/sessions/a/artifacts", owner)[1]["artifacts"] == [replacement]
    assert dispatch(server, "GET", endpoint, owner)[1]["artifact"]["media_type"] == "text/html"
    assert dispatch(server, "DELETE", endpoint, owner) == (200, {"removed": True})
    assert dispatch(server, "GET", endpoint, owner)[0] == 404


def test_auth_provenance_and_session_cleanup(app, tmp_path):
    server, agents, owner = app
    file = tmp_path / "report.md"
    file.write_text("# Private output")
    req = artifacts.registration(file)
    saved = register(server, agents["a"], req)[1]["artifact"]
    assert register(server, agents["b"], {**req, "session_key": "a"})[0] == 400
    assert dispatch(server, "GET", "/api/v1/session/artifacts", agents["b"])[1] == {"artifacts": []}
    endpoint = f"/sessions/a/artifacts/{saved['id']}"
    assert dispatch(server, "GET", endpoint, {})[0] == 401
    assert dispatch(server, "GET", endpoint, agents["b"])[0] == 403
    assert dispatch(server, "GET", endpoint.replace("/a/", "/b/"), owner)[0] == 404
    assert dispatch(server, "DELETE", endpoint.replace("/a/", "/b/"), owner)[0] == 200
    assert dispatch(server, "GET", endpoint, owner)[0] == 200
    server.history.delete_session("a")
    assert server.history.artifacts.list("a") == []
    saved_b = register(server, agents["b"], req)[1]["artifact"]
    server.history.purge_test_sessions()
    with pytest.raises(artifacts.ArtifactError):
        server.history.artifacts.get("b", saved_b["id"])


def test_invalid_content_limits_and_no_server_file_read(app, tmp_path, monkeypatch):
    server, agents, _ = app
    file = tmp_path / "report.md"
    file.write_text("# Content")
    req = artifacts.registration(file)
    for patch in [
        {"content_base64": "!"},
        {"source_path": "../private"},
        {"title": ""},
        {"content_base64": base64.b64encode(b"\xff").decode()},
    ]:
        assert register(server, agents["a"], {**req, **patch})[0] == 400
    # The server stores client-supplied bytes; source_path is only metadata.
    req["source_path"] = "/nonexistent/remote-host/report.md"
    assert register(server, agents["a"], req)[0] == 200
    monkeypatch.setattr(artifacts, "MAX_SESSION_FILES", 1)
    assert register(server, agents["a"], {**req, "source_path": "/another/report.md"})[0] == 413
    monkeypatch.setattr(artifacts, "MAX_SESSION_BYTES", 1)
    assert (
        register(
            server,
            agents["a"],
            {**req, "content_base64": base64.b64encode(b"larger content").decode()},
        )[0]
        == 413
    )
    assert server.history.artifacts.list("a")[0]["size"] == len(b"# Content")
    assert (
        register(server, agents["a"], {**req, "content_base64": "A" * (8 * 1024 * 1024)})[0] == 413
    )
    link = tmp_path / "link.md"
    link.symlink_to(file)
    with pytest.raises(OSError):
        artifacts.registration(link)
    with pytest.raises((OSError, artifacts.ArtifactError)):
        artifacts.registration(tmp_path)


def test_cli_parser_and_automatic_registration_instructions():
    from duckterm.helpers.session_instructions import GUIDE

    args = build_parser().parse_args(["session", "artifact", "/tmp/chart.png", "--title", "Chart"])
    assert (args.session_action, args.file, args.title) == (
        "artifact",
        Path("/tmp/chart.png"),
        "Chart",
    )
    assert build_parser().parse_args(["session", "artifacts"]).session_action == "artifacts"
    assert "automatically register" in GUIDE
    assert "duckterm session artifact" in GUIDE


def test_real_cli_upload_and_backup_restore(app, tmp_path, monkeypatch):
    import asyncio
    import os
    import subprocess
    import sys
    import tarfile

    from duckterm.persistence import backup

    server, _, _ = app
    file = tmp_path / "large-report.md"
    file.write_text("# Report\n" + "x" * (2 * 1024 * 1024))
    for variable in ("CLAUDE_CONFIG_DIR", "CODEX_HOME"):
        monkeypatch.setenv(variable, str(tmp_path / variable))

    async def scenario():
        listener = await asyncio.start_server(server.handle, "127.0.0.1", 0)
        port = listener.sockets[0].getsockname()[1]
        server.history.session_api.set_url(f"http://127.0.0.1:{port}")
        env = dict(os.environ, DUCKTERM_SESSION_KEY="a", DUCKTERM_INTERNAL="")
        env.pop("DUCKTERM_SESSION_TOKEN_FILE", None)
        async with listener:
            result = await asyncio.to_thread(
                subprocess.run,
                [
                    sys.executable,
                    "-c",
                    "from duckterm.cli import main; raise SystemExit(main())",
                    "session",
                    "artifact",
                    str(file),
                ],
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
            )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)["artifact"]

    saved = asyncio.run(scenario())
    assert saved["size"] == file.stat().st_size
    archive = Path(backup.create())
    restored_db = tmp_path / "restored" / "db.sqlite"
    restored_db.parent.mkdir()
    with tarfile.open(archive) as bundle:
        snapshot = bundle.extractfile("duckterm/db.sqlite")
        assert snapshot is not None
        restored_db.write_bytes(snapshot.read())
    restored = HistoryStore(restored_db)
    try:
        copy = restored.artifacts.get("a", saved["id"])
        assert base64.b64decode(copy["content_base64"]) == file.read_bytes()
    finally:
        restored.purge_test_sessions()
        restored.close()
