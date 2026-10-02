"""Settings may read release data but cannot start an update."""

import asyncio
import io
import json

from tests.runtime.test_session_api import Writer, dispatch

from duckterm import __version__, update_status
from duckterm.persistence.history import HistoryStore
from duckterm.server import Server


def test_release_check_is_read_only_and_versions_are_numeric(monkeypatch):
    monkeypatch.delenv("DUCKTERM_RELEASE_CHECK", raising=False)
    monkeypatch.setattr(update_status, "__version__", "0.4.9")
    seen = []

    def response(request, **kwargs):
        seen.append(request.full_url)
        return io.BytesIO(
            json.dumps({"tag_name": "v0.4.10", "html_url": "javascript:bad"}).encode()
        )

    monkeypatch.setattr(update_status.urllib.request, "urlopen", response)
    status = update_status.status()
    assert seen == ["https://api.github.com/repos/utsavanand/duckterm/releases/latest"]
    assert status["update_available"] is True and status["install_available"] is False
    assert status["release_url"] == update_status.RELEASES + "/tag/v0.4.10"
    assert status["operation"] is None


def test_check_failure_never_returns_stale_latest_or_up_to_date(monkeypatch):
    monkeypatch.delenv("DUCKTERM_RELEASE_CHECK", raising=False)

    def fail(*args, **kwargs):
        raise OSError("offline")

    monkeypatch.setattr(update_status.urllib.request, "urlopen", fail)
    status = update_status.status()
    assert status["installed_version"] == __version__
    assert status["check_status"] == "failed" and status["latest_version"] is None
    assert status["update_available"] is None and status["install_available"] is False


def test_owner_only_endpoint_and_no_install_route(tmp_path, monkeypatch):
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path))
    monkeypatch.setenv("DUCKTERM_RELEASE_CHECK", "off")
    history = HistoryStore(tmp_path / "db.sqlite")
    try:
        server = Server(history=history)
        assert dispatch(server, "GET", "/update/status", {})[0] == 401
        auth = {"x-duckterm-token": server.token}
        code, status = dispatch(server, "GET", "/update/status", auth)
        assert code == 200 and status["install_available"] is False
        writer = Writer()

        async def request():
            await server._dispatch(
                "POST", "/update/install", asyncio.StreamReader(), writer, auth, b"{}"
            )

        asyncio.run(request())
        assert writer.data.startswith(b"HTTP/1.1 404")
    finally:
        history.close()
