"""Disposable loopback TLS hosts for native tests; no real configuration or credentials."""

import asyncio
import json
import os
import ssl
import subprocess
import sys
import tempfile
from pathlib import Path

from duckterm.editions import LITE
from duckterm.mobile.bridge import Bridge
from duckterm.mobile.cli import configure, invitation
from duckterm.persistence.history import HistoryStore
from duckterm.server import Server


def ordinary_fixture_view(history: HistoryStore) -> None:
    """Expose only the named protocol fixture as ordinary; keep persisted test:true."""
    session, sessions, folders = history.session, history.sessions, history.folders

    def view(row):
        return {**row, "test": False} if row and row["session_key"] == "test-agent" else row

    def folder_view(*, include_test=True):
        names = set(folders(include_test=include_test))
        row = session("test-agent")
        group = row.get("grp") if row else None
        while not include_test and group:
            names.add(group)
            group = group.rsplit("/", 1)[0] if "/" in group else None
        return sorted(names, key=str.lower)

    history.session = lambda key: view(session(key))
    history.sessions = lambda: [view(row) for row in sessions()]
    history.folders = folder_view


async def run(root: Path, mode: str) -> None:
    os.environ["HOME"] = str(root)
    os.environ["DUCKTERM_HOME"] = str(root)
    os.environ["DUCKTERM_TMUX_SOCKET"] = "ducky-native-test-" + str(os.getpid())
    os.environ["DUCKTERM_SUMMARIZER"] = "off"
    configure(
        root, "127.0.0.1", 4430, hostname="ducky-native-fixture.local" if mode == "local" else None
    )
    path = root / "ducky-bridge.json"
    config = json.loads(path.read_text())
    if mode == "legacy-local":
        # Reuse an existing IP-only leaf while newly pairing with a stable name.
        config.update(hostname="ducky-native-fixture.local", tls_name="127.0.0.1")
    history = HistoryStore(root / "state.sqlite", edition=LITE)
    server = Server(history=history, edition=LITE)
    ordinary_fixture_view(history)
    bridge = Bridge(server, root)
    secondary = None
    counters = {"requests": 0, "handshakes": 0}
    redirect_next = False
    transcript = None
    original_dispatch = bridge.dispatch

    async def dispatch(*args):
        counters["requests"] += 1
        if mode == "v1" and args[1].startswith("/v2/"):
            return 404, {"error": "Read-only endpoint unavailable"}
        return await original_dispatch(*args)

    bridge.dispatch = dispatch

    async def handle(reader, writer):
        nonlocal redirect_next
        if not redirect_next:
            await bridge.handle(reader, writer)
            return
        redirect_next = False
        try:
            async with asyncio.timeout(3):
                await reader.readuntil(b"\r\n\r\n")
                counters["requests"] += 1
                # A loopback-only redirect detects following without risking external traffic.
                writer.write(
                    f"HTTP/1.1 302 Found\r\nLocation: https://[::1]:{config['port']}/v1/pair/status\r\n"
                    "Content-Length: 0\r\nConnection: close\r\n\r\n".encode()
                )
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(root / "ducky-cert.pem", root / "ducky-key.pem")

    def handshake(*_args):
        counters["handshakes"] += 1

    context.set_servername_callback(handshake)
    try:
        event_extra = {}
        if mode == "conversations":
            from types import SimpleNamespace

            from duckterm.core.orchestrator import SessionSupervisor
            from duckterm.mobile import delivery
            from duckterm.runtimes.claude_code import project_slug
            from duckterm.runtimes.generic import GenericRuntime

            cwd = root / "project"
            cwd.mkdir()
            event_extra = {
                "runtime": "claude-code",
                "cwd": str(cwd),
                "session_id": "fixture-native",
            }
            transcript = root / ".claude/projects" / project_slug(cwd) / "fixture-native.jsonl"
            transcript.parent.mkdir(parents=True)
            transcript.write_text(
                json.dumps(
                    {"type": "user", "message": {"role": "user", "content": "Fixture question"}}
                )
                + "\n"
                + json.dumps(
                    {
                        "type": "assistant",
                        "message": {"role": "assistant", "content": "Fixture reply"},
                    }
                )
                + "\n"
            )
            supervisor = SessionSupervisor(
                bus=server.bus,
                runtime=GenericRuntime("cat"),
                session_key="test-agent",
                cwd=str(cwd),
                extra={"test": True},
            )
            supervisor.runtime = SimpleNamespace(name="claude-code")
            supervisor._tmux_target = "rd_test_mobile_synthetic"
            server.orchestrator._supervisors["test-agent"] = supervisor
            # This fixture checks protocol acceptance, never a real terminal.
            delivery.identity = lambda _: "%fixture:1:0"

            def prohibited_terminal(*_args):
                raise AssertionError("TLS conversation fixture must never touch a terminal")

            delivery.write = prohibited_terminal
            delivery.screen = prohibited_terminal
            bridge.messages.ensure_worker = lambda: None
        history.record(
            {
                "_id": "initial",
                "_ts": 1,
                "event_type": "SessionStart",
                "session_key": "test-agent",
                "test": True,
                **event_extra,
            }
        )
        history.set_meta("test-agent", name="Native protocol test", group="Test folder")
        assert history._conn.execute("SELECT test FROM sessions").fetchone()[0] == 1
        if mode == "conversations":
            history.set_state("test-agent", "busy", now=2)
        bridge.listener = await asyncio.start_server(
            handle, "127.0.0.1", 0, ssl=context, ssl_handshake_timeout=3, limit=8192
        )
        config["port"] = bridge.listener.sockets[0].getsockname()[1]
        # One identity on two distinct private literals models DHCP/address-family churn.
        secondary = await asyncio.start_server(
            handle, "::1", config["port"], ssl=context, ssl_handshake_timeout=3, limit=8192
        )
        path.write_text(json.dumps(config))
        certificate = subprocess.run(
            ["openssl", "x509", "-in", str(root / "ducky-cert.pem"), "-noout", "-text"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
        assert "CA:FALSE" in certificate and "CA:TRUE" not in certificate
        print(invitation(root), flush=True)
        while line := await asyncio.to_thread(sys.stdin.readline):
            operation, identifier = line.strip().split(" ", 1)
            if operation == "approve":
                bridge.grants.approve(identifier)
            elif operation == "approve-conversations" and mode == "conversations":
                bridge.grants.approve(
                    identifier, capabilities=["conversation:read", "session:send"]
                )
            elif operation == "stop-session" and mode == "conversations":
                history.set_state("test-agent", "stopped", now=3)
            elif operation == "remove-history" and transcript is not None:
                transcript.unlink()
            elif operation == "revoke":
                bridge.grants.revoke(identifier)
            elif operation == "redirect":
                redirect_next = True
            elif operation == "stop-v4":
                bridge.listener.close()
                await bridge.listener.wait_closed()
                bridge.listener = None
            elif operation == "stats":
                print(json.dumps({**counters, "ca": False}), flush=True)
                continue
            else:
                raise ValueError("Unknown fixture action")
            print("ok", flush=True)
    finally:
        if secondary is not None:
            secondary.close()
            await secondary.wait_closed()
        await bridge.close()
        history.purge_test_sessions()
        history.close()


if __name__ == "__main__":
    fixture_mode = sys.argv[1] if len(sys.argv) > 1 else "literal"
    if fixture_mode not in {"literal", "local", "legacy-local", "v1", "conversations"}:
        raise ValueError("Unknown fixture mode")
    with tempfile.TemporaryDirectory(prefix="ducky-native-test-") as directory:
        asyncio.run(run(Path(directory).resolve(), fixture_mode))
