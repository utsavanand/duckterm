"""Manual backup API remains responsive and reports retained archives precisely."""

import asyncio
import json
import subprocess
import tarfile
import threading
from pathlib import Path

import pytest
from tests.runtime.test_session_api import Writer, dispatch

from duckterm.core.backup_jobs import BackupJobs
from duckterm.persistence import backup
from duckterm.persistence.history import HistoryStore
from duckterm.server import Server


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    root = tmp_path / "duckterm"
    root.mkdir()
    monkeypatch.setenv("DUCKTERM_HOME", str(root))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    history = HistoryStore(root / "db.sqlite")
    server = Server(history=history)
    yield server, {"x-duckterm-token": server.token}, root
    history.close()


async def request(server, owner, method, body=None, path="/backup"):
    writer = Writer()
    await server._dispatch(
        method, path, asyncio.StreamReader(), writer, owner, json.dumps(body or {}).encode()
    )
    headers, data = writer.data.split(b"\r\n\r\n", 1)
    return int(headers.split()[1]), json.loads(data)


def test_slow_connector_probe_does_not_block_backup_settings(scenario, monkeypatch):
    from duckterm import connectors

    server, owner, _ = scenario
    started = threading.Event()
    release = threading.Event()

    def slow_status():
        started.set()
        release.wait(2)
        return []

    monkeypatch.setattr(connectors, "list_status", slow_status)

    async def run():
        probe = asyncio.create_task(request(server, owner, "GET", path="/connectors"))
        try:
            assert await asyncio.to_thread(started.wait, 1)
            status, body = await request(server, owner, "GET")
            assert status == 200 and "destination" in body
            assert not probe.done(), "Connector probes blocked the dashboard event loop"
        finally:
            release.set()
            assert await probe == (200, {"connectors": []})

    asyncio.run(run())


def test_auth_validation_and_remembered_destination(scenario, tmp_path):
    server, owner, root = scenario
    assert dispatch(server, "GET", "/backup", {})[0] == 401
    for method in ("GET", "PUT", "POST"):
        assert dispatch(server, method, "/backup", {"authorization": "Bearer agent"})[0] == 403
    assert dispatch(server, "POST", "/backup", owner)[0] == 400
    for destination in ("", "gs://", "https://example.org", "gs://bucket/*"):
        assert (
            dispatch(
                server, "PUT", "/backup", owner, json.dumps({"destination": destination}).encode()
            )[0]
            == 400
        )
    assert dispatch(server, "PUT", "/backup", owner, b"[]")[0] == 400
    assert dispatch(server, "PUT", "/backup", owner, b"\xff")[0] == 400
    assert dispatch(server, "PUT", "/backup", owner, b"x" * 8193)[0] == 413
    destination = str(tmp_path / "archives")
    status, result = dispatch(
        server, "PUT", "/backup", owner, json.dumps({"destination": destination}).encode()
    )
    assert status == 200
    assert result == {"destination": destination, "job": None}
    state = root / "backup-state.json"
    assert state.stat().st_mode & 0o777 == 0o600
    assert BackupJobs(state).snapshot() == result


def test_real_archive_and_status_survive_restart(scenario, tmp_path):
    server, owner, root = scenario

    async def run():
        status, started = await request(
            server, owner, "POST", {"destination": str(tmp_path / "archives")}
        )
        assert status == 202 and started["job"]["status"] == "running"
        await server._backup_jobs.task
        status, done = await request(server, owner, "GET")
        assert status == 200 and done["job"]["status"] == "succeeded"
        archive_path = Path(done["job"]["archive_path"])
        assert archive_path.stat().st_mode & 0o777 == 0o600
        with tarfile.open(archive_path) as archive:
            assert "duckterm/db.sqlite" in archive.getnames()
        assert BackupJobs(root / "backup-state.json").snapshot() == done

    asyncio.run(run())


def test_running_job_does_not_block_dashboard_and_rejects_overlap(scenario, monkeypatch, tmp_path):
    server, owner, _ = scenario
    release = threading.Event()
    calls = []

    def slow(destination):
        calls.append(destination)
        assert release.wait(5)
        return str(tmp_path / "done.tar.gz")

    monkeypatch.setattr(backup, "create", slow)

    async def run():
        try:
            assert (await request(server, owner, "POST", {"destination": str(tmp_path)}))[0] == 202
            status, state = await asyncio.wait_for(request(server, owner, "GET"), 1)
            assert status == 200 and state["job"]["status"] == "running"
            assert (await asyncio.wait_for(request(server, owner, "GET", path="/sessions"), 1))[
                0
            ] == 200
            status, duplicate = await request(server, owner, "POST", {"destination": "/different"})
            assert status == 409 and duplicate["destination"] == str(tmp_path)
        finally:
            release.set()
            await server._backup_jobs.task
        assert calls == [str(tmp_path)]

    asyncio.run(run())


def test_failed_gcs_upload_reports_real_retained_archive(scenario, monkeypatch):
    server, owner, root = scenario
    monkeypatch.setattr(backup.shutil, "which", lambda _: "/test/gcloud")

    def fail(argv, **kwargs):
        raise subprocess.CalledProcessError(1, argv)

    monkeypatch.setattr(backup.subprocess, "run", fail)

    async def run():
        assert (await request(server, owner, "POST", {"destination": "gs://test-bucket/prefix"}))[
            0
        ] == 202
        await server._backup_jobs.task
        job = (await request(server, owner, "GET"))[1]["job"]
        assert job["status"] == "failed"
        path = Path(job["archive_path"])
        assert path.parent == root / "backups"
        assert str(path) in job["error"]
        with tarfile.open(path) as archive:
            assert "duckterm/db.sqlite" in archive.getnames()

    asyncio.run(run())


def test_no_overwrite_and_interrupted_job_does_not_restart(scenario, tmp_path, monkeypatch):
    server, owner, root = scenario
    existing = tmp_path / "existing.tar.gz"
    existing.write_text("preserve")

    async def run():
        assert (await request(server, owner, "POST", {"destination": str(existing)}))[0] == 202
        await server._backup_jobs.task
        job = (await request(server, owner, "GET"))[1]["job"]
        assert job["status"] == "failed"
        assert "Refusing to overwrite" in job["error"]
        assert existing.read_text() == "preserve"

    asyncio.run(run())
    state = root / "backup-state.json"
    persisted = json.loads(state.read_text())
    persisted["job"]["status"] = "running"
    state.write_text(json.dumps(persisted))
    monkeypatch.setattr(backup, "create", lambda *_: pytest.fail("must not restart a backup"))
    restored = BackupJobs(state).snapshot()
    assert restored["job"]["status"] == "interrupted"
    assert restored["job"]["finished_at"] > 0
    assert restored["destination"] == str(existing)
