#!/usr/bin/env python3
"""Pinned macOS native identity probe; no provider calls or production state.

Usage: python3 scripts/qa_opencode_identity.py /absolute/path/to/pinned/opencode [--models]
Creates two native synthetic sessions in a disposable home and removes them.
--models uses only a deterministic loopback provider with a synthetic key.
"""
import asyncio
import base64
import contextlib
import hashlib
import http.server
import json
import os
import pathlib
import secrets
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from duckterm.persistence import opencode_launch as journal  # noqa: E402
from duckterm.persistence.history import HistoryStore  # noqa: E402
from duckterm.core.eventbus import EventBus  # noqa: E402
from duckterm.core.orchestrator import Orchestrator  # noqa: E402
from duckterm.runtimes.opencode import OpenCodeRuntime  # noqa: E402
from duckterm.runtimes.generic import GenericRuntime  # noqa: E402
from duckterm.runtimes.opencode_identity import Selection  # noqa: E402
from duckterm.runtimes.opencode_prepare import prepare  # noqa: E402
from duckterm.runtimes.opencode_protocol import NativeClient, UncertainWrite, models  # noqa: E402

root = pathlib.Path(tempfile.mkdtemp(prefix="duckterm-opencode-probe-")).resolve()
binary = pathlib.Path(sys.argv[1]).resolve()
assert (
    hashlib.sha256(binary.read_bytes()).hexdigest()
    == "7b63b34fafabded7d9231f6a9032755d0cdeaf8b9d2b70df8e25535471469eea"
), "Unexpected native executable hash"
for name in ("home", "data", "config", "cache", "state", "tmp", "project"):
    (root / name).mkdir()
env = {
    "PATH": "/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
    "HOME": str(root / "home"),
    "XDG_DATA_HOME": str(root / "data"),
    "XDG_CONFIG_HOME": str(root / "config"),
    "XDG_CACHE_HOME": str(root / "cache"),
    "XDG_STATE_HOME": str(root / "state"),
    "TMPDIR": str(root / "tmp"),
    "TERM": "xterm-256color",
    "LANG": "en_US.UTF-8",
    "OPENCODE_DISABLE_DEFAULT_PLUGINS": "1",
    "OPENCODE_DISABLE_AUTOUPDATE": "1",
    "OPENCODE_DISABLE_MODELS_FETCH": "1",
    "OPENCODE_DISABLE_PROJECT_CONFIG": "1",
    "OPENCODE_CONFIG_DIR": str(root / "config/opencode"),
    "OPENCODE_CONFIG_CONTENT": json.dumps(
        {"autoupdate": False, "share": "disabled", "enabled_providers": []}
    ),
}
config = root / "config/opencode"
config.mkdir()
observer_token = secrets.token_urlsafe(24)
observed = []
condition = threading.Condition()


class Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        if self.headers.get("Authorization") != "Bearer " + observer_token:
            self.send_response(401)
            self.end_headers()
            return
        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        data["_received_at"] = time.monotonic()
        with condition:
            observed.append(data)
            condition.notify_all()
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *args):
        pass


collector = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=collector.serve_forever, daemon=True).start()
env["DUCKTERM_OPENCODE_OBSERVER"] = f"http://127.0.0.1:{collector.server_port}/"
env["DUCKTERM_OPENCODE_OBSERVER_TOKEN"] = observer_token
env["DUCKTERM_HARNESS_GENERATION"] = secrets.token_hex(16)
plugin = pathlib.Path(__file__).resolve().parents[1] / "src/duckterm/hooks/opencode-tui.js"
(config / "tui.json").write_text(
    json.dumps({"plugin": [str(plugin)], "attention": {"enabled": False}})
)
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
password = secrets.token_urlsafe(24)
env["OPENCODE_SERVER_PASSWORD"] = password
headers = {
    "Authorization": "Basic " + base64.b64encode(("opencode:" + password).encode()).decode(),
    "Content-Type": "application/json",
    "x-opencode-directory": str(root / "project"),
}


def request(method, path, body=None):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}" + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(req, timeout=3) as r:
        return json.load(r)


name = "oc-proof-" + secrets.token_hex(5)
native = []
provider_calls = []


class Provider(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        size = int(self.headers.get("Content-Length", "0"))
        if not 0 < size <= 4 * 1024 * 1024:
            self.send_error(413)
            return
        body = json.loads(self.rfile.read(size))
        model = body.get("model")
        if model not in ("alpha", "beta"):
            self.send_error(400)
            return
        provider_calls.append(model)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for delta, finish in [
            ({"role": "assistant", "content": "fixture response"}, None),
            ({}, "stop"),
        ]:
            data = {
                "id": "chatcmpl_fixture",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }
            if finish:
                data["usage"] = {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}
            self.wfile.write(("data: " + json.dumps(data) + "\n\n").encode())
        self.wfile.write(b"data: [DONE]\n\n")

    def log_message(self, *args):
        pass


provider = None
if any(flag in sys.argv[2:] for flag in ("--models", "--prepare", "--supervisor")):
    provider = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    threading.Thread(target=provider.serve_forever, daemon=True).start()
    env["OPENCODE_CONFIG_CONTENT"] = json.dumps(
        {
            "autoupdate": False,
            "share": "disabled",
            "enabled_providers": ["fixture"],
            "model": "fixture/alpha",
            "small_model": "fixture/alpha",
            "permission": "deny",
            "provider": {
                "fixture": {
                    "npm": "@ai-sdk/openai-compatible",
                    "name": "Local fixture",
                    "options": {
                        "baseURL": f"http://127.0.0.1:{provider.server_port}/v1",
                        "apiKey": "synthetic-only",
                    },
                    "models": {
                        model: {"name": model, "limit": {"context": 8192, "output": 1024}}
                        for model in ("alpha", "beta")
                    },
                }
            },
        }
    )


def verify_models(client):
    catalog = models(client.request("GET", "/provider"))
    assert {"fixture/alpha", "fixture/beta"} <= {item["id"] for item in catalog}
    for index, model in enumerate(("alpha", "beta")):
        message_id = "msg_ducktermfixture" + str(index)
        assert (
            client.request(
                "POST",
                "/session/" + native[-1] + "/prompt_async",
                {
                    "messageID": message_id,
                    "model": {"providerID": "fixture", "modelID": model},
                    "parts": [{"type": "text", "text": "Return a short fixture response."}],
                },
            )
            is None
        )
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            rows = client.request("GET", "/session/" + native[-1] + "/message")
            match = [
                row
                for row in rows
                if row["info"].get("parentID") == message_id
                and row["info"].get("time", {}).get("completed")
            ]
            if match:
                assert match[-1]["info"]["providerID"] == "fixture"
                assert match[-1]["info"]["modelID"] == model
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("Native fixture prompt did not complete")
    normalized = client.messages(native[-1])
    observed_models = {item["model"] for item in normalized if item["role"] == "assistant"}
    assert {"alpha", "beta"} <= observed_models
    assert len({item["id"] for item in normalized}) == len(normalized)
    assert set(provider_calls) == {"alpha", "beta"}


def verify_preparation(client):
    """Real native create/select/prompt; lose the prompt receipt and recover it.

    The test card is local to the disposable DB and flagged test:true. No
    production server or credentials are involved. This exercises preparation,
    not the still-unregistered DuckTerm supervisor adapter.
    """
    db = root / "duckterm-fixture.sqlite"
    history = HistoryStore(db)
    key = "opencode-preparation-fixture"
    history.record(
        {
            "_id": "fixture-start",
            "_ts": int(time.time() * 1000),
            "event_type": "SessionStart",
            "session_key": key,
            "runtime": "opencode",
            "cwd": client.directory,
            "test": True,
        }
    )
    generation = env["DUCKTERM_HARNESS_GENERATION"]
    pid = observed[-1]["pid"]
    writes = []

    class LostReceiptClient(NativeClient):
        def request(self, method, path, body=None):
            if method == "POST":
                writes.append(path)
            result = super().request(method, path, body)
            if path.endswith("/prompt_async"):
                raise UncertainWrite("Fixture dropped acknowledgment after native acceptance")
            return result

    async def selected(state):
        while True:
            with condition:
                event = dict(observed[-1]) if observed else None
            if event and event.get("sessionID") == state.session_id:
                proof = Selection(generation, client.directory, pid, state.session_id)
                return proof.observe(event, now=event["_received_at"])
            await asyncio.sleep(0.05)

    probe = LostReceiptClient(client.port, client.password, client.directory)
    state = journal.Launch(
        generation,
        "isolated-fixture",
        str(root / "data/opencode"),
        client.directory,
        pid,
        prompt="Return a short fixture response.",
        model="fixture/alpha",
    )
    try:
        journal.begin(history._conn, key, state)
        result = asyncio.run(prepare(history._conn, key, generation, probe, selected, lambda: True))
        assert result.phase == "ready"
        assert writes == [
            "/session",
            "/tui/select-session",
            "/session/" + result.session_id + "/prompt_async",
        ]
        # Reopen the actual DuckTerm journal to model server recovery; this must
        # read the same native identity and never send the initial prompt again.
        history.close()

        history = HistoryStore(db)
        recovered = asyncio.run(
            prepare(history._conn, key, generation, probe, selected, lambda: True)
        )
        assert recovered == result and len(writes) == 3
        rows = client.messages(result.session_id)
        assert sum(row["id"] == result.message_id for row in rows) == 1
        return {"passed": True, "native_id": result.session_id, "native_write_count": len(writes)}
    finally:
        saved = journal.read(history._conn, key, generation)
        if saved.session_id and saved.session_id not in native:
            native.append(saved.session_id)
        history.delete_session(key)
        assert history.session(key) is None
        history.close()


def verify_supervisor():
    """Exercise the actual owned terminal and early-input gate, no registry activation."""
    overrides = {
        "DUCKTERM_HOME": str(root / "duckterm"),
        "DUCKTERM_TMUX_SOCKET": name + "-supervisor",
    }
    saved_env = {key: os.environ.get(key) for key in overrides}
    os.environ.update(overrides)
    history = HistoryStore(root / "duckterm/db.sqlite")
    bus = EventBus(sink=history.record)
    owner = Orchestrator(bus, history=history)
    key = "native-supervisor-fixture"

    async def run():
        nonlocal owner
        from duckterm.harnesses import REGISTRY

        # Registry injection is confined to this disposable QA process; the
        # product registry deliberately stays unchanged in source.
        previous_runtime = REGISTRY.get("opencode")
        REGISTRY["opencode"] = OpenCodeRuntime
        await owner.launch(
            runtime=GenericRuntime("/bin/cat"),
            cwd=str(root / "project"),
            session_key="native-sibling-fixture",
            env=env,
            test=True,
        )
        sibling_pid = subprocess.check_output(
            [
                "tmux",
                "-L",
                overrides["DUCKTERM_TMUX_SOCKET"],
                "display-message",
                "-p",
                "-t",
                "=rd_native-sibling-fixture:",
                "#{pane_pid}",
            ],
            text=True,
        ).strip()
        launch = asyncio.create_task(
            owner.launch(
                runtime=OpenCodeRuntime(shlex.join([str(binary), "--model", "fixture/alpha"])),
                cwd=str(root / "project"),
                session_key=key,
                prompt="Return a short fixture response.",
                env={k: v for k, v in env.items() if not k.startswith("DUCKTERM_")},
                test=True,
            )
        )
        try:
            while owner.get(key) is None and not launch.done():
                await asyncio.sleep(0)
            sup = owner.get(key)
            assert sup is not None
            assert not sup.write_bytes(b"must not reach native input")
            assert not sup.write_input("must not reach native input\n")
            assert not await sup.write_queued_bytes(b"must not be queued")
            assert await launch == key
            assert not sup._preparing and sup.running
            state = history.restart_control(key)["opencode_launch"]
            assert state["phase"] == "ready"
            native.append(state["session_id"])
            assert history.session(key)["test"]
            prep_client = sup._preparation.client
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                rows = await asyncio.to_thread(prep_client.messages, state["session_id"])
                if any(
                    row["role"] == "assistant"
                    and row["complete"]
                    and row["provider"] == "fixture"
                    and row["model"] == "alpha"
                    for row in rows
                ):
                    break
                await asyncio.sleep(0.1)
            else:
                raise RuntimeError("Supervised native initial prompt did not complete")
            calls_before_recovery = list(provider_calls)
            for old in list(owner._supervisors.values()):
                old._task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await old._task
            owner = Orchestrator(bus, history=history)
            adopted = await owner.reconcile()
            assert set(adopted) == {key, "native-sibling-fixture"}
            recovered = owner.get(key)
            assert not recovered._preparing and recovered.running
            assert history.restart_control(key)["opencode_launch"] == state
            assert calls_before_recovery == provider_calls
            assert (
                sibling_pid
                == subprocess.check_output(
                    [
                        "tmux",
                        "-L",
                        overrides["DUCKTERM_TMUX_SOCKET"],
                        "display-message",
                        "-p",
                        "-t",
                        "=rd_native-sibling-fixture:",
                        "#{pane_pid}",
                    ],
                    text=True,
                ).strip()
            )
            return {
                "passed": True,
                "native_id": state["session_id"],
                "pid": state["pid"],
                "early_input_blocked": True,
                "same_process_recovered": True,
                "sibling_preserved": True,
                "initial_prompt_not_replayed": True,
            }
        finally:
            if not launch.done():
                launch.cancel()
                try:
                    await launch
                except asyncio.CancelledError:
                    pass
            control = history.restart_control(key).get("opencode_launch", {})
            if control.get("session_id") and control["session_id"] not in native:
                native.append(control["session_id"])
            await owner.stop(key)
            await owner.stop("native-sibling-fixture")
            if previous_runtime is None:
                REGISTRY.pop("opencode", None)
            else:
                REGISTRY["opencode"] = previous_runtime

    try:
        return asyncio.run(run())
    finally:
        history.delete_session(key)
        history.delete_session("native-sibling-fixture")
        assert history.session(key) is None
        history.close()
        subprocess.run(
            ["tmux", "-L", overrides["DUCKTERM_TMUX_SOCKET"], "kill-server"], capture_output=True
        )
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


try:
    version = subprocess.check_output(
        [str(binary), "--version"], env=env, cwd=root / "project", text=True, timeout=20
    ).strip()
    assert version == "1.18.34", version
    subprocess.run(
        [
            "tmux",
            "-L",
            name,
            "new-session",
            "-d",
            "-s",
            "proof",
            "-x",
            "120",
            "-y",
            "40",
            shlex.join(
                [str(binary), str(root / "project"), "--hostname", "127.0.0.1", "--port", str(port)]
            ),
        ],
        env=env,
        check=True,
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            health = request("GET", "/global/health")
            if health.get("healthy"):
                break
        except OSError:
            pass
        time.sleep(0.1)
    else:
        raise RuntimeError("Native server not ready")
    with condition:
        assert condition.wait_for(
            lambda: any(x.get("ready") for x in observed), timeout=10
        ), "TUI not ready"
    for i in range(2):
        session = request("POST", "/session", {"title": "DuckTerm isolated QA " + str(i)})
        native.append(session["id"])
        request("POST", "/tui/select-session", {"sessionID": session["id"]})
        with condition:
            proven = condition.wait_for(
                lambda identity=session["id"]: any(
                    item.get("sessionID") == identity for item in observed
                ),
                timeout=10,
            )
        assert proven, "No TUI selection proof"
    client = NativeClient(port, password, str(root / "project"))
    client.health()
    if provider is not None:
        verify_models(client)
    preparation = verify_preparation(client) if "--prepare" in sys.argv[2:] else None
    supervisor = verify_supervisor() if "--supervisor" in sys.argv[2:] else None
    assert all(x["generation"] == env["DUCKTERM_HARNESS_GENERATION"] for x in observed)
    assert all(x["directory"] == str(root / "project") for x in observed)
    assert len({x["pid"] for x in observed}) == 1
    print(
        json.dumps(
            {
                "passed": True,
                "version": version,
                "executable_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
                "native_ids": native,
                "fixture_models_passed": provider_calls,
                "preparation": preparation,
                "supervisor": supervisor,
                "observed": [
                    {k: v for k, v in item.items() if k != "generation"} for item in observed
                ],
                "isolated_root": str(root),
            }
        ),
        flush=True,
    )
except BaseException:
    screen = subprocess.run(
        ["tmux", "-L", name, "capture-pane", "-p", "-t", "proof"], capture_output=True, text=True
    )
    (root / "screen.txt").write_text(screen.stdout)
    print(
        json.dumps(
            {
                "passed": False,
                "observed": [
                    {k: v for k, v in item.items() if k != "generation"} for item in observed
                ],
                "isolated_root": str(root),
            }
        ),
        flush=True,
    )
    raise
finally:
    try:
        for identity in native:
            request("DELETE", "/session/" + identity)
    finally:
        subprocess.run(["tmux", "-L", name, "kill-server"], capture_output=True)
        collector.shutdown()
        collector.server_close()
        if provider is not None:
            provider.shutdown()
            provider.server_close()
        shutil.rmtree(root)
