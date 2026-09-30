"""Peer artifact reads follow current sharing grants and never grant writes."""

import asyncio
import base64
import hashlib
import json
import os
import subprocess
import sys
import urllib.parse
from pathlib import Path

import pytest
from tests.runtime.test_artifacts import app as artifact_app
from tests.runtime.test_artifacts import register
from tests.runtime.test_session_api import dispatch

from duckterm.cli import build_parser
from duckterm.session_client import _save_artifact, main


@pytest.fixture
def app(tmp_path, monkeypatch):
    yield from artifact_app.__wrapped__(tmp_path, monkeypatch)


def save(server, headers, content=b"# Review", source="/gone/report.md"):
    status, body = register(
        server,
        headers,
        {
            "source_path": source,
            "title": "Review",
            "content_base64": base64.b64encode(content).decode(),
        },
    )
    assert status == 200, body
    return body["artifact"]


def listing(server, headers, folder):
    return dispatch(
        server,
        "GET",
        "/api/v1/session/artifacts?" + urllib.parse.urlencode({"folder": folder}),
        headers,
    )


def test_peer_download_is_scoped_and_read_only(app):
    server, agents, owner = app
    saved = save(server, agents["a"])
    endpoint = "/api/v1/session/artifacts/" + saved["id"]
    status, result = dispatch(server, "GET", endpoint, agents["b"])
    assert status == 200
    assert base64.b64decode(result["artifact"]["content_base64"]) == b"# Review"
    assert result["artifact"]["session_key"] == "a"
    assert dispatch(server, "GET", endpoint, {})[0] == 401
    assert dispatch(server, "GET", endpoint, owner)[0] == 401
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        assert dispatch(server, method, endpoint, agents["b"], b"{}")[0] == 404
    assert dispatch(server, "GET", endpoint, agents["b"])[1] == result
    assert register(server, agents["b"], {"session_key": "a"})[0] == 400
    server.history.set_meta("b", group="project-other")
    assert dispatch(server, "GET", endpoint, agents["b"])[0] == 404
    assert listing(server, agents["b"], "project")[0] == 403


def test_folder_listing_descendants_literals_and_private_grants(app):
    server, agents, _ = app
    saved = save(server, agents["a"])
    server.history.set_meta("a", group="project/literal%_/child")
    assert listing(server, agents["b"], "project/literal%_")[1]["artifacts"][0]["id"] == saved["id"]
    assert listing(server, agents["b"], "project/literal")[1]["artifacts"] == []
    status, result = listing(server, agents["b"], "project")
    assert status == 200 and result["truncated"] is False
    assert result["artifacts"][0]["folder"] == "project/literal%_/child"
    assert "content_base64" not in result["artifacts"][0]
    # Same physical ancestor is not permission to bypass an explicit narrower grant.
    server.history.session_api.enroll("a", {"root": "project/literal%_"})
    assert listing(server, agents["b"], "project")[1]["artifacts"] == []
    endpoint = "/api/v1/session/artifacts/" + saved["id"]
    assert dispatch(server, "GET", endpoint, agents["b"])[0] == 404


@pytest.mark.parametrize("folder", ["", "/project", "project/..", "project//x", "project/\x00"])
def test_invalid_folder_is_not_an_own_artifact_fallback(app, folder):
    server, agents, _ = app
    assert listing(server, agents["b"], folder)[0] == 400


def test_producer_stopped_moved_deleted_and_ungrouped(app):
    server, agents, owner = app
    saved = save(server, agents["a"])
    endpoint = "/api/v1/session/artifacts/" + saved["id"]
    for state in ("stopped", "archived", "terminated"):
        server.history.set_state("a", state)
        assert dispatch(server, "GET", endpoint, agents["b"])[0] == 200
        assert listing(server, agents["b"], "project")[1]["artifacts"][0]["session_state"] == state
    server.history.set_meta("a", group="elsewhere")
    assert dispatch(server, "GET", endpoint, agents["b"])[0] == 404
    assert listing(server, agents["b"], "project")[1]["artifacts"] == []
    server.history.set_meta("a", group="project")
    assert dispatch(server, "GET", endpoint, agents["b"])[0] == 200
    server.history.delete_session("a")
    assert dispatch(server, "GET", endpoint, agents["b"])[0] == 404
    assert listing(server, agents["b"], "project")[1]["artifacts"] == []
    assert dispatch(server, "GET", "/folders/project/artifacts", owner)[1]["artifacts"] == []
    own = save(server, agents["b"])
    server.history.set_meta("b", group="")
    assert dispatch(server, "GET", "/api/v1/session/artifacts/" + own["id"], agents["b"])[0] == 200
    assert listing(server, agents["b"], "project")[0] == 403
    server.history.set_state("b", "stopped")
    assert dispatch(server, "GET", "/api/v1/session/artifacts/" + own["id"], agents["b"])[0] == 401


def test_scope_filter_runs_before_limit_and_ignores_stale_membership(app):
    server, agents, _ = app
    own = save(server, agents["b"])
    private = save(server, agents["a"])
    server.history.set_meta("a", group="project/private")
    server.history.session_api.enroll("a", {"root": "project/private"})
    server.history._conn.execute(
        "UPDATE artifacts SET updated_at = 9999999999999 WHERE id = ?", (private["id"],)
    )
    assert [
        a["id"] for a in server.history.artifacts.list_folder("project", 1, shared_root="project")
    ] == [own["id"]]
    server.history._conn.execute(
        "UPDATE session_api_members SET folder = 'stale' WHERE session_key = 'a'"
    )
    assert listing(server, agents["b"], "project")[1]["artifacts"][0]["id"] == own["id"]
    assert (
        dispatch(server, "GET", "/api/v1/session/artifacts/" + private["id"], agents["b"])[0] == 404
    )


def test_orphaned_snapshots_are_not_visible_to_peers(app):
    server, agents, owner = app
    saved = save(server, agents["a"])
    # Match the folder route's JOIN behavior even for a legacy dangling row.
    server.history._conn.execute("DELETE FROM sessions WHERE session_key = 'a'")
    assert server.history.artifacts.list("a")[0]["id"] == saved["id"]
    assert listing(server, agents["b"], "project")[1]["artifacts"] == []
    assert dispatch(server, "GET", "/folders/project/artifacts", owner)[1]["artifacts"] == []
    assert (
        dispatch(server, "GET", "/api/v1/session/artifacts/" + saved["id"], agents["b"])[0] == 404
    )


def test_real_cli_peer_list_and_binary_download_without_owner_token(app, tmp_path):
    server, agents, _ = app
    payload = b"\x00\xff\r\n" * (600 * 1024)
    saved = save(server, agents["a"], payload, "/missing/source.pdf")
    output = tmp_path / "download.pdf"

    async def run():
        listener = await asyncio.start_server(server.handle, "127.0.0.1", 0)
        port = listener.sockets[0].getsockname()[1]
        server.history.session_api.set_url(f"http://127.0.0.1:{port}")
        env = dict(os.environ, DUCKTERM_SESSION_KEY="b", DUCKTERM_INTERNAL="", TMPDIR=str(tmp_path))
        env.pop("DUCKTERM_SESSION_TOKEN_FILE", None)

        async def cli(*args):
            return await asyncio.to_thread(
                subprocess.run,
                [
                    sys.executable,
                    "-c",
                    "from duckterm.cli import main; raise SystemExit(main())",
                    "session",
                    *args,
                ],
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
            )

        async with listener:
            listed = await cli("artifacts", "--folder", "project")
            assert listed.returncode == 0, listed.stderr
            assert json.loads(listed.stdout)["artifacts"][0]["id"] == saved["id"]
            downloaded = await cli("artifact", "get", saved["id"], "--output", str(output))
            assert downloaded.returncode == 0, downloaded.stderr
            result = json.loads(downloaded.stdout)
            assert (
                result["saved_path"] == str(output) and "content_base64" not in result["artifact"]
            )
            assert "untrusted" in result["instructions"]
            assert output.read_bytes() == payload and output.stat().st_mode & 0o777 == 0o600
            retry = await cli("artifact", "get", saved["id"], "--output", str(output))
            assert retry.returncode == 1 and output.read_bytes() == payload
            default = await cli("artifact", "get", saved["id"])
            assert default.returncode == 0, default.stderr
            downloaded_path = Path(json.loads(default.stdout)["saved_path"])
            assert downloaded_path.is_relative_to(tmp_path)
            assert downloaded_path.read_bytes() == payload
            assert downloaded_path != Path(saved["source_path"])

    asyncio.run(run())


def test_download_rejects_corruption_and_symlink_destinations(tmp_path):
    content = b"saved bytes"
    result = {
        "artifact": {
            "source_path": "/source.txt",
            "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
            "content_base64": base64.b64encode(content).decode(),
        }
    }
    output = tmp_path / "copy"
    broken = {"artifact": {**result["artifact"], "sha256": "wrong"}}
    with pytest.raises(ValueError, match="checksum"):
        _save_artifact(broken, output)
    assert not output.exists()
    target = tmp_path / "existing"
    target.write_bytes(b"preserve")
    output.symlink_to(target)
    with pytest.raises(FileExistsError):
        _save_artifact(result, output)
    assert target.read_bytes() == b"preserve" and output.is_symlink()
    args = build_parser().parse_args(["session", "artifact", "get", "a" * 32])
    assert args.file == Path("get") and args.artifact_id == "a" * 32


def test_download_without_id_never_registers_a_file_named_get(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "get").write_text("This is not an upload request")
    monkeypatch.setattr(
        "duckterm.session_client.client_credentials", lambda: ("http://127.0.0.1:1", "test")
    )
    monkeypatch.setattr(
        "duckterm.session_client.registration", lambda *args: pytest.fail("unexpected upload")
    )
    assert main(build_parser().parse_args(["session", "artifact", "get"])) == 1
    assert "get <32-character ID>" in capsys.readouterr().err
    assert (tmp_path / "get").read_text() == "This is not an upload request"
