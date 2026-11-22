"""The model proxy: run tokens in, real key out, usage charged, refusals as 403."""

import http.client
import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from duckterm.autopilot.model_proxy import ModelProxy

ANTHROPIC_KEY = "sk-ant-REAL"
OPENAI_KEY = "sk-oai-REAL"

ANTHROPIC_STREAM = [
    {
        "type": "message_start",
        "message": {
            "model": "claude-x",
            "usage": {
                "input_tokens": 1000,
                "cache_read_input_tokens": 20000,
                "cache_creation_input_tokens": 500,
                "output_tokens": 1,
            },
        },
    },
    {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "hi"}},
    {
        "type": "message_delta",
        "delta": {"stop_reason": "end_turn"},
        "usage": {"output_tokens": 300},
    },
    {"type": "message_stop"},
]
OPENAI_STREAM = [
    {"type": "response.created", "response": {"id": "r1", "model": "gpt-x"}},
    {"type": "response.output_text.delta", "delta": "hi"},
    {
        "type": "response.completed",
        "response": {
            "id": "r1",
            "model": "gpt-x",
            "usage": {
                "input_tokens": 3000,
                "input_tokens_details": {"cached_tokens": 2000},
                "output_tokens": 50,
            },
        },
    },
]


def sse(events: list[dict[str, Any]]) -> bytes:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


class Upstream(BaseHTTPRequestHandler):
    seen: list[dict[str, Any]]
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:
        self.rfile.read(int(self.headers.get("content-length") or 0))
        self.seen.append(
            {"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}}
        )
        if self.path == "/v1/messages?plain":
            body = json.dumps(
                {"model": "claude-x", "usage": {"input_tokens": 70, "output_tokens": 30}}
            ).encode()
            ctype = "application/json"
        else:
            body = sse(OPENAI_STREAM if self.path.startswith("/v1/responses") else ANTHROPIC_STREAM)
            ctype = "text/event-stream"
        self.send_response(200)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        pass


def serve(server: ThreadingHTTPServer) -> None:
    threading.Thread(target=server.serve_forever, daemon=True).start()


@pytest.fixture
def setup(tmp_path: Path) -> Iterator[tuple[ModelProxy, int, list[dict[str, Any]]]]:
    seen: list[dict[str, Any]] = []
    handler = type("H", (Upstream,), {"seen": seen})
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    serve(upstream)
    url = f"http://127.0.0.1:{upstream.server_address[1]}"
    proxy = ModelProxy(
        anthropic_key=ANTHROPIC_KEY,
        openai_key=OPENAI_KEY,
        log_path=tmp_path / "calls.jsonl",
        anthropic_url=url,
        openai_url=url,
    )
    server = proxy.server("127.0.0.1", 0)
    serve(server)
    yield proxy, server.server_address[1], seen
    server.shutdown()
    upstream.shutdown()


def call(port: int, path: str, headers: dict[str, str]) -> tuple[int, bytes, dict[str, str]]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("POST", path, body=b'{"stream": true}', headers=headers)
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    return resp.status, body, {k.lower(): v for k, v in resp.getheaders()}


def test_claude_call_swaps_token_for_key_and_charges_usage(setup: Any) -> None:
    proxy, port, seen = setup
    token = proxy.issue("run-1", budget=100_000, ttl=60)
    status, body, _ = call(
        port,
        "/v1/messages",
        {"authorization": f"Bearer {token}", "x-duckterm-step": "build", "accept-encoding": "gzip"},
    )
    assert status == 200
    assert body == sse(ANTHROPIC_STREAM)
    sent = seen[0]["headers"]
    assert sent["x-api-key"] == ANTHROPIC_KEY
    assert "authorization" not in sent and "x-duckterm-step" not in sent
    # Compressed replies would hide their usage from the proxy.
    assert sent["accept-encoding"] == "identity"
    # 1000 input + 500 cache write + 300 output + 20000 cache reads at a tenth
    assert proxy.run(token).used == 3800
    (entry,) = [json.loads(line) for line in (proxy._log_path).read_text().splitlines()]
    assert entry | {"ts": 0} == {
        "ts": 0,
        "run": "run-1",
        "step": "build",
        "path": "/v1/messages",
        "model": "claude-x",
        "status": 200,
        "allowed": True,
        "input": 1000,
        "output": 300,
        "cache_read": 20000,
        "cache_write": 500,
    }


def test_codex_call_uses_bearer_key_and_charges_usage(setup: Any) -> None:
    proxy, port, seen = setup
    token = proxy.issue("run-1", budget=100_000, ttl=60)
    status, _, _ = call(port, "/v1/responses", {"authorization": f"Bearer {token}"})
    assert status == 200
    assert seen[0]["headers"]["authorization"] == f"Bearer {OPENAI_KEY}"
    # 1000 uncached input + 50 output + 2000 cached at a tenth
    assert proxy.run(token).used == 1250


def test_plain_json_reply_is_charged(setup: Any) -> None:
    proxy, port, _ = setup
    token = proxy.issue("run-1", budget=100_000, ttl=60)
    assert call(port, "/v1/messages?plain", {"x-api-key": token})[0] == 200
    assert proxy.run(token).used == 100


@pytest.mark.parametrize("case", ["unknown_token", "token_expired", "budget_exhausted"])
def test_refusals_are_403_permission_error_and_never_reach_upstream(setup: Any, case: str) -> None:
    proxy, port, seen = setup
    token = {
        "unknown_token": "dtrun-nope",
        "token_expired": proxy.issue("run-1", budget=100, ttl=-1),
        "budget_exhausted": proxy.issue("run-1", budget=0, ttl=60),
    }[case]
    status, body, _ = call(port, "/v1/messages", {"x-api-key": token})
    assert status == 403
    assert json.loads(body) == {
        "type": "error",
        "error": {"type": "permission_error", "message": case},
    }
    assert seen == []
    if case != "unknown_token":
        assert proxy.run(token).refusal == case


def test_run_is_refused_once_its_spend_reaches_the_budget(setup: Any) -> None:
    proxy, port, seen = setup
    token = proxy.issue("run-1", budget=5000, ttl=60)
    assert call(port, "/v1/messages", {"x-api-key": token})[0] == 200
    assert call(port, "/v1/messages", {"x-api-key": token})[0] == 200
    assert call(port, "/v1/messages", {"x-api-key": token})[0] == 403
    assert len(seen) == 2
    assert proxy.run(token).refusal == "budget_exhausted"


def test_revoked_token_is_refused(setup: Any) -> None:
    proxy, port, seen = setup
    token = proxy.issue("run-1", budget=100_000, ttl=60)
    proxy.revoke(token)
    assert call(port, "/v1/messages", {"x-api-key": token})[0] == 403
    assert seen == []


def test_unreachable_upstream_is_502_and_charges_nothing(tmp_path: Path) -> None:
    proxy = ModelProxy(
        anthropic_key=ANTHROPIC_KEY,
        openai_key=None,
        log_path=tmp_path / "calls.jsonl",
        anthropic_url="http://127.0.0.1:9",
    )
    server = proxy.server("127.0.0.1", 0)
    serve(server)
    token = proxy.issue("run-1", budget=100, ttl=60)
    try:
        assert call(server.server_address[1], "/v1/messages", {"x-api-key": token})[0] == 502
        # No OpenAI key configured: a Codex call is refused, not forwarded keyless.
        assert call(server.server_address[1], "/v1/responses", {"x-api-key": token})[0] == 403
    finally:
        server.shutdown()
    assert proxy.run(token).used == 0


def test_control_api_mints_reports_and_revokes_run_tokens(setup: Any) -> None:
    proxy, port, seen = setup
    control = proxy.control_server("127.0.0.1", 0, "s3cret")
    serve(control)
    cport = control.server_address[1]

    def ctl(
        method: str, path: str, body: dict[str, Any] | None = None, secret: str = "s3cret"
    ) -> tuple[int, Any]:
        conn = http.client.HTTPConnection("127.0.0.1", cport, timeout=10)
        conn.request(
            method,
            path,
            body=json.dumps(body) if body else None,
            headers={"authorization": f"Bearer {secret}"},
        )
        resp = conn.getresponse()
        raw = resp.read()
        conn.close()
        return resp.status, json.loads(raw) if raw else None

    try:
        assert (
            ctl("POST", "/runs", {"run_id": "run-9", "budget": 100_000, "ttl": 60}, secret="guess")[
                0
            ]
            == 401
        )
        status, minted = ctl("POST", "/runs", {"run_id": "run-9", "budget": 100_000, "ttl": 60})
        assert status == 201
        token = minted["token"]
        assert call(port, "/v1/messages", {"x-api-key": token})[0] == 200
        status, state = ctl("GET", f"/runs/{token}")
        assert (status, state["run_id"], state["used"], state["refusal"]) == (
            200,
            "run-9",
            3800,
            None,
        )
        assert ctl("DELETE", f"/runs/{token}")[0] == 204
        assert call(port, "/v1/messages", {"x-api-key": token})[0] == 403
        assert ctl("GET", f"/runs/{token}")[0] == 404
    finally:
        control.shutdown()


def test_subscription_token_is_sent_as_oauth_bearer_with_the_beta_header(tmp_path: Path) -> None:
    seen: list[dict[str, Any]] = []
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), type("H", (Upstream,), {"seen": seen}))
    serve(upstream)
    proxy = ModelProxy(
        anthropic_key=None,
        openai_key=None,
        log_path=tmp_path / "calls.jsonl",
        anthropic_url=f"http://127.0.0.1:{upstream.server_address[1]}",
        anthropic_oauth_token="sk-ant-oat-SUBSCRIPTION",
    )
    server = proxy.server("127.0.0.1", 0)
    serve(server)
    token = proxy.issue("run-1", budget=100_000, ttl=60)
    try:
        headers = {"x-api-key": token, "anthropic-beta": "fine-grained-tool-streaming-2025-05-14"}
        assert call(server.server_address[1], "/v1/messages", headers)[0] == 200
    finally:
        server.shutdown()
        upstream.shutdown()
    sent = seen[0]["headers"]
    assert sent["authorization"] == "Bearer sk-ant-oat-SUBSCRIPTION"
    assert "x-api-key" not in sent
    assert sent["anthropic-beta"] == "fine-grained-tool-streaming-2025-05-14,oauth-2025-04-20"
