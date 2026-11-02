"""Real private tmux terminals and TLS exercise the phone message boundary."""

import asyncio
import json
import secrets
import shlex
import shutil
import ssl
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest

from duckterm.core.orchestrator import SessionSupervisor
from duckterm.editions import LITE
from duckterm.mobile import conversation, delivery
from duckterm.mobile.bridge import start
from duckterm.mobile.cli import configure
from duckterm.mobile.grants import Grants
from duckterm.mobile.messages import Messages
from duckterm.persistence.history import HistoryStore
from duckterm.runtimes.codex import CodexRuntime
from duckterm.server import Server

TERMINAL = """import os, sys, tty
from pathlib import Path
tty.setraw(0)
capture = os.open(sys.argv[1], os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
os.write(1, '\\x1b[?2004h› '.encode())
Path(sys.argv[2]).touch()
while True:
    data = os.read(0, 8192)
    if not data:
        break
    os.write(capture, data)
"""


def wait_until(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Owned fixture did not reach the expected state")


class Terminal:
    def __init__(self, root, socket):
        self.root = root
        self.socket = socket
        self.executable = shutil.which("tmux")
        if self.executable is None:
            pytest.skip("tmux is unavailable")
        self.script = root / "terminal.py"
        self.script.write_text(TERMINAL)
        self.env = {"PATH": "/opt/homebrew/bin:/usr/bin:/bin", "LANG": "en_US.UTF-8"}

    def command(self, *args, check=True):
        return subprocess.run(
            [self.executable, "-L", self.socket, "-f", "/dev/null", *args],
            capture_output=True,
            timeout=5,
            check=check,
            env=self.env,
        )

    def spawn(self, target, *, replace=False):
        identifier = uuid.uuid4().hex
        capture, ready = self.root / f"{identifier}.bytes", self.root / f"{identifier}.ready"
        command = shlex.join([sys.executable, "-I", str(self.script), str(capture), str(ready)])
        if replace:
            self.command("respawn-pane", "-k", "-t", "=" + target + ":", command)
        else:
            self.command("new-session", "-d", "-s", target, "-x", "120", "-y", "30", command)
        wait_until(ready.exists)
        wait_until(lambda: CodexRuntime().prompt_is_empty(delivery.screen(target)))
        return capture

    def close(self):
        # This socket is fresh and owned by this fixture, never an owner instance.
        self.command("kill-server", check=False)


@pytest.fixture
def app(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    monkeypatch.setenv("DUCKTERM_HOME", str(root))
    monkeypatch.setenv("DUCKTERM_NO_KEYCHAIN", "1")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: root))
    socket = "ducky-message-test-" + uuid.uuid4().hex
    monkeypatch.setenv("DUCKTERM_TMUX_SOCKET", socket)
    terminal = Terminal(root, socket)
    history = HistoryStore(root / "state.sqlite", edition=LITE)
    server = Server(history=history, edition=LITE)
    target = "rd_ducky_message_test"
    try:
        capture = terminal.spawn(target)
        history.record(
            {
                "_id": uuid.uuid4().hex,
                "_ts": 1,
                "event_type": "SessionStart",
                "session_key": "phone-test",
                "runtime": "codex",
                "session_id": "owned-native-test",
                "cwd": str(root),
                "test": True,
            }
        )
        history.set_state("phone-test", "idle", now=2)
        supervisor = SessionSupervisor(
            bus=server.bus,
            runtime=CodexRuntime(),
            session_key="phone-test",
            cwd=str(root),
            extra={"test": True},
        )
        supervisor._tmux_target = target
        server.orchestrator._supervisors["phone-test"] = supervisor
        configure(root, "127.0.0.1", 4430)
        config = json.loads((root / "ducky-bridge.json").read_text())
        config["port"] = 0
        (root / "ducky-bridge.json").write_text(json.dumps(config))
        grants = Grants(root)
        invitation = grants.invite()
        token = secrets.token_urlsafe(32)
        pending = grants.claim(invitation["invite"], token, "Isolated message phone")
        grants.approve(pending["id"], capabilities=["conversation:read", "session:send"])
        service = Messages(server, root)
        # Advance the real worker's attempt explicitly at each race boundary.
        monkeypatch.setattr(service, "ensure_worker", lambda: None)
        yield {
            "server": server,
            "root": root,
            "terminal": terminal,
            "target": target,
            "capture": capture,
            "supervisor": supervisor,
            "service": service,
            "token": token,
            "device": grants.authenticate(token),
        }
    finally:
        terminal.close()
        server.digests.close()
        history.purge_test_sessions()
        assert history.sessions() == []
        history.close()
        shutil.rmtree(root)


async def with_bridge(app, exercise):
    bridge = await start(app["server"], directory=app["root"], messages=app["service"])
    assert bridge is not None and bridge.listener is not None
    port = bridge.listener.sockets[0].getsockname()[1]
    context = ssl.create_default_context(cafile=str(app["root"] / "ducky-cert.pem"))

    async def request(method, path, payload=None, *, token=None, declared=None):
        reader, writer = await asyncio.open_connection(
            "127.0.0.1", port, ssl=context, server_hostname="127.0.0.1"
        )
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        if payload is None:
            data = b""
        length = len(data) if declared is None else declared
        headers = (
            f"{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
            f"Content-Length: {length}\r\n"
            f"Authorization: Ducky {token or app['token']}\r\n\r\n"
        )
        try:
            writer.write(headers.encode() + data)
            await writer.drain()
            raw = await asyncio.wait_for(reader.read(), 4)
        finally:
            writer.close()
            await writer.wait_closed()
        header, body = raw.split(b"\r\n\r\n", 1)
        assert b"Cache-Control: no-store" in header
        return int(header.split()[1]), json.loads(body)

    try:
        await exercise(request)
    finally:
        await bridge.close()
        await app["service"].close()
        task = app["supervisor"]._input_task
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def accepted(app, request, text="First line\nSecond line with 🦆 and a tab\tend"):
    code, snapshot = await request("GET", "/v2/snapshot")
    assert code == 200 and snapshot["version"] == 2
    row = next(row for row in snapshot["sessions"] if row["id"] == "phone-test")
    assert row["can_send"] is True
    payload = {
        "session_id": row["id"],
        "send_context": row["send_context"],
        "request_id": str(uuid.uuid4()),
        "text": text,
    }
    code, response = await request("POST", "/v2/messages", payload)
    assert code == 202 and response["receipt"]["status"] == "queued", (code, response)
    saved = app["service"].store.find(app["device"]["id"], payload["request_id"])
    assert saved is not None
    return payload, saved


def test_real_terminal_receives_one_exact_bracketed_paste_and_enter(app):
    async def exercise(request):
        payload, saved = await accepted(app, request)
        assert app["capture"].read_bytes() == b""
        await app["service"].attempt(saved)
        expected = b"\x1b[200~" + payload["text"].encode() + b"\x1b[201~\r"
        await asyncio.to_thread(wait_until, lambda: app["capture"].read_bytes() == expected)
        code, receipt = await request("GET", "/v2/messages?request_id=" + payload["request_id"])
        assert code == 200 and receipt["receipt"]["status"] == "delivered"
        assert "text" not in receipt["receipt"]
        code, retry = await request("POST", "/v2/messages", payload)
        assert code == 202 and retry["receipt"]["status"] == "delivered"
        app["supervisor"].last_owner_input_ms = 0
        await app["service"].attempt(saved)
        await asyncio.sleep(0.05)
        assert app["capture"].read_bytes() == expected

    asyncio.run(with_bridge(app, exercise))


@pytest.mark.parametrize("change", ["stopped", "replaced", "prefix_sibling"])
def test_queued_send_cannot_reach_stopped_replaced_or_prefix_sibling_terminal(app, change):
    async def exercise(request):
        payload, saved = await accepted(app, request)
        watched = [app["capture"]]
        if change == "stopped":
            app["server"].history.set_state("phone-test", "stopped", now=3)
        elif change == "replaced":
            watched.append(app["terminal"].spawn(app["target"], replace=True))
            assert delivery.identity(app["target"]) != saved["pane"]
        else:
            sibling = app["target"] + "_sibling"
            watched.append(app["terminal"].spawn(sibling))
            app["terminal"].command("kill-session", "-t", "=" + app["target"])
            with pytest.raises(OSError):
                delivery.identity(app["target"])
            code, error = await request(
                "POST", "/v2/messages", {**payload, "request_id": str(uuid.uuid4())}
            )
            assert code == 409 and error["error"] == "terminal_unavailable"
        await app["service"].attempt(saved)
        await asyncio.sleep(0.05)
        assert all(path.read_bytes() == b"" for path in watched)
        row = app["service"].store.find(app["device"]["id"], payload["request_id"])
        assert row["status"] != "delivered"

    asyncio.run(with_bridge(app, exercise))


@pytest.mark.parametrize("change", ["stopped", "replaced"])
def test_terminal_change_after_paste_never_sends_enter_or_replays(app, monkeypatch, change):
    async def exercise(request):
        payload, saved = await accepted(app, request)
        pasted = asyncio.Event()
        release = threading.Event()
        loop = asyncio.get_running_loop()
        write = delivery.write

        def pause_after_real_paste(target, data):
            write(target, data)
            if data.startswith(b"\x1b[200~"):
                loop.call_soon_threadsafe(pasted.set)
                assert release.wait(3)

        monkeypatch.setattr(delivery, "write", pause_after_real_paste)
        attempt = asyncio.create_task(app["service"].attempt(saved))
        expected = b"\x1b[200~" + payload["text"].encode() + b"\x1b[201~"
        replacement = None
        try:
            await asyncio.wait_for(pasted.wait(), 2)
            await asyncio.to_thread(wait_until, lambda: app["capture"].read_bytes() == expected)
            if change == "stopped":
                app["server"].history.set_state("phone-test", "stopped", now=3)
            else:
                replacement = app["terminal"].spawn(app["target"], replace=True)
            release.set()
            await attempt
            code, response = await request(
                "GET", "/v2/messages?request_id=" + payload["request_id"]
            )
            assert code == 200 and response["receipt"]["status"] == "unknown"
            assert response["receipt"]["reason"] == "delivery_uncertain"
            code, retry = await request("POST", "/v2/messages", payload)
            assert code == 202 and retry["receipt"]["status"] == "unknown"
            await asyncio.sleep(0.05)
            assert app["capture"].read_bytes() == expected
            if replacement is not None:
                assert replacement.read_bytes() == b""
        finally:
            release.set()
            await asyncio.gather(attempt, return_exceptions=True)

    asyncio.run(with_bridge(app, exercise))


def test_real_http_limits_utf8_message_size_and_preserves_v1_contract(app):
    async def exercise(request):
        # Reject oversized declared bodies before waiting for or parsing their bytes.
        assert (await request("POST", "/v2/messages", b"", declared=32769))[0] == 413
        assert (await request("POST", "/v1/pair", b"", declared=2049))[0] == 413
        payload, _ = await accepted(app, request, text="🦆" * 1024)
        # The 4 KiB UTF-8 text is valid even though JSON escaping exceeds v1's body limit.
        assert len(json.dumps(payload).encode()) > 2048
        code, error = await request(
            "POST",
            "/v2/messages",
            {**payload, "request_id": str(uuid.uuid4()), "text": "🦆" * 1025},
        )
        assert code == 413 and error["error"] == "message_too_large"
        code, active = await request("GET", "/v1/snapshot")
        assert code == 200 and active["version"] == 1 and active["read_only"] is True
        assert set(active["sessions"][0]) == {
            "id",
            "name",
            "folder",
            "runtime",
            "state",
            "observed_at",
            "approval_visibility",
        }
        invite = app["service"].grants.invite()
        old_token = secrets.token_urlsafe(32)
        old_phone = app["service"].grants.claim(invite["invite"], old_token, "Legacy status phone")
        app["service"].grants.approve(old_phone["id"])
        assert (await request("GET", "/v1/snapshot", token=old_token))[0] == 200
        assert (await request("POST", "/v2/messages", payload, token=old_token))[0] == 403
        assert (await request("GET", "/v2/conversation?session_id=phone-test", token=old_token))[
            0
        ] == 403
        app["server"].history.set_state("phone-test", "stopped", now=3)
        code, old = await request("GET", "/v1/snapshot")
        assert code == 200 and old["version"] == 1 and old["read_only"] is True
        assert old["sessions"] == [] and "features" not in old and "capabilities" not in old
        code, new = await request("GET", "/v2/snapshot")
        assert code == 200 and len(new["sessions"]) == 1
        assert new["sessions"][0]["can_send"] is False
        assert (await request("POST", "/v1/snapshot", {}))[0] == 404
        assert app["capture"].read_bytes() == b""

    asyncio.run(with_bridge(app, exercise))


def test_revocation_during_real_transcript_read_returns_no_conversation(app, monkeypatch):
    path = app["root"] / ".codex/sessions/2026/11/01/rollout-test-owned-native-test.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "PRIVATE_FIXTURE_REPLY"}],
                },
            }
        )
        + "\n"
    )
    entered, release = threading.Event(), threading.Event()
    read = conversation._read

    def paused(source):
        entered.set()
        assert release.wait(3)
        return read(source)

    monkeypatch.setattr(conversation, "_read", paused)

    async def exercise(request):
        task = asyncio.create_task(request("GET", "/v2/conversation?session_id=phone-test"))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            await asyncio.to_thread(app["service"].grants.revoke, app["device"]["id"])
            release.set()
            code, response = await task
            assert code == 403 and "messages" not in response
            assert "PRIVATE_FIXTURE_REPLY" not in json.dumps(response)
            assert (await request("GET", "/v1/snapshot"))[0] == 403
            assert app["capture"].read_bytes() == b""
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(with_bridge(app, exercise))
