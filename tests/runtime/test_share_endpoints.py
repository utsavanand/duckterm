"""The server's share endpoints: POST /sessions/:key/share exposes a live
session through the relay and returns a viewer link; GET lists; DELETE revokes;
stopping the session revokes its shares. The relay runs in-process on a private
port so the uplink has something to dial."""

import asyncio
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

_RELAY_DIR = Path(__file__).resolve().parents[2] / "relay"
sys.path.insert(0, str(_RELAY_DIR))

import relay as relay_mod  # noqa: E402

from duckterm.helpers import security  # noqa: E402
from duckterm.persistence.history import HistoryStore  # noqa: E402
from duckterm.runtimes.generic import GenericRuntime  # noqa: E402
from duckterm.server import Server  # noqa: E402


def _req(port: int, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=data,
        headers={
            "Content-Type": "application/json",
            "X-Duckterm-Token": security.load_or_create_token(),
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_share_a_running_session_then_list_and_revoke(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(relay_mod, "_DEVICE_TOKEN", "dev-relay-token")

    async def scenario() -> dict:
        # A relay on its own port for the uplink to dial.
        relay = relay_mod.Relay()
        relay_srv = await asyncio.start_server(relay.handle, "127.0.0.1", 0)
        relay_port = relay_srv.sockets[0].getsockname()[1]
        monkeypatch.setenv("RUBBERTERM_RELAY_URL", f"ws://127.0.0.1:{relay_port}")
        monkeypatch.setenv("RUBBERTERM_RELAY_WEB", f"http://127.0.0.1:{relay_port}")

        server = Server(history=HistoryStore(tmp_path / "db.sqlite"))
        # A long-lived session to share.
        key = await server.orchestrator.launch(
            runtime=GenericRuntime("sh -c 'echo shared-ready; sleep 5'"),
            cwd=str(tmp_path),
            session_key="shareme",
        )
        api = await asyncio.start_server(server.handle, "127.0.0.1", 0)
        port = api.sockets[0].getsockname()[1]

        out: dict = {}
        async with relay_srv, api:
            status, created = await asyncio.to_thread(
                _req, port, "POST", f"/sessions/{key}/share", {}
            )
            out["create_status"] = status
            out["created"] = created

            # The share is registered on the relay (uplink dialed in).
            for _ in range(50):
                if created["share_id"] in relay._shares:
                    break
                await asyncio.sleep(0.02)
            out["on_relay"] = created["share_id"] in relay._shares

            _, listed = await asyncio.to_thread(_req, port, "GET", f"/sessions/{key}/shares")
            out["listed_count"] = len(listed["shares"])

            del_status, _ = await asyncio.to_thread(
                _req, port, "DELETE", f"/shares/{created['share_id']}"
            )
            out["delete_status"] = del_status
            _, after = await asyncio.to_thread(_req, port, "GET", f"/sessions/{key}/shares")
            out["listed_after_revoke"] = len(after["shares"])

        await server.orchestrator.stop(key)
        await server.shares.stop_all()
        return out

    out = asyncio.run(scenario())
    assert out["create_status"] == 200
    # The viewer link is path-routed and carries the token in the fragment.
    assert "/s/" in out["created"]["url"]
    assert "#" in out["created"]["url"]
    assert out["on_relay"] is True
    assert out["listed_count"] == 1
    assert out["delete_status"] == 200
    assert out["listed_after_revoke"] == 0


def test_cannot_share_a_session_that_is_not_running(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    async def scenario() -> tuple[int, dict]:
        server = Server(history=HistoryStore(tmp_path / "db.sqlite"))
        api = await asyncio.start_server(server.handle, "127.0.0.1", 0)
        port = api.sockets[0].getsockname()[1]
        async with api:
            return await asyncio.to_thread(_req, port, "POST", "/sessions/ghost/share", {})

    status, body = asyncio.run(scenario())
    assert status == 400
    assert "no live session" in body["error"]


def test_stopping_a_session_revokes_its_shares(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(relay_mod, "_DEVICE_TOKEN", "dev-relay-token")

    async def scenario() -> tuple[int, int]:
        relay = relay_mod.Relay()
        relay_srv = await asyncio.start_server(relay.handle, "127.0.0.1", 0)
        relay_port = relay_srv.sockets[0].getsockname()[1]
        monkeypatch.setenv("RUBBERTERM_RELAY_URL", f"ws://127.0.0.1:{relay_port}")
        monkeypatch.setenv("RUBBERTERM_RELAY_WEB", f"http://127.0.0.1:{relay_port}")

        server = Server(history=HistoryStore(tmp_path / "db.sqlite"))
        key = await server.orchestrator.launch(
            runtime=GenericRuntime("sh -c 'sleep 5'"),
            cwd=str(tmp_path),
            session_key="stopshare",
        )
        api = await asyncio.start_server(server.handle, "127.0.0.1", 0)
        port = api.sockets[0].getsockname()[1]
        async with relay_srv, api:
            await asyncio.to_thread(_req, port, "POST", f"/sessions/{key}/share", {})
            before = len(server.shares.for_session(key))
            await asyncio.to_thread(_req, port, "POST", f"/sessions/{key}/stop")
            after = len(server.shares.for_session(key))
        await server.shares.stop_all()
        return before, after

    before, after = asyncio.run(scenario())
    assert before == 1
    assert after == 0
