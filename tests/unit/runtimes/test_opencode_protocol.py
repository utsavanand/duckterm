import base64
import copy
import http.server
import json
import threading

import pytest

from duckterm.runtimes.opencode_protocol import (
    VERSION,
    NativeClient,
    OpenCodeError,
    UncertainWrite,
    messages,
    model_parts,
    models,
)


@pytest.fixture
def endpoint():
    calls = []
    state = {"status": 200, "body": b'{"healthy":true,"version":"1.18.34"}'}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append((self.command, self.path, dict(self.headers)))
            self.send_response(state["status"])
            self.send_header("Location", "http://example.invalid/never-follow")
            self.end_headers()
            self.wfile.write(state["body"])

        def do_POST(self):
            calls.append((self.command, self.path, dict(self.headers)))
            self.rfile.read(int(self.headers["Content-Length"]))
            self.close_connection = True  # accepted write, response lost

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield NativeClient(server.server_port, "fixture-only", "/synthetic/project"), state, calls
    server.shutdown()
    server.server_close()
    thread.join()


def test_client_bounds_auth_and_no_redirect_or_proxy(endpoint, monkeypatch):
    client, state, calls = endpoint
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
    client.health()
    assert (
        calls[0][2]["Authorization"]
        == "Basic " + base64.b64encode(b"opencode:fixture-only").decode()
    )
    assert "fixture-only" not in repr(client)
    state["status"] = 302
    with pytest.raises(OpenCodeError, match="302"):
        client.health()
    assert len(calls) == 2
    state["status"] = 200
    state["body"] = json.dumps({"healthy": True, "version": "different"}).encode()
    with pytest.raises(OpenCodeError, match=VERSION):
        client.health()


def test_accepted_write_without_response_is_uncertain_and_not_retried(endpoint):
    client, _, calls = endpoint
    with pytest.raises(UncertainWrite):
        client.request("POST", "/session", {"title": "Synthetic"})
    assert len(calls) == 1


def test_response_size_and_json_errors_fail_closed(endpoint):
    client, state, _ = endpoint
    client = NativeClient(client.port, "fixture-only", "/synthetic/project", limit=100)
    state["body"] = b"x" * 101
    with pytest.raises(OpenCodeError, match="size bound"):
        client.request("GET", "/session")
    state["body"] = b"{broken"
    with pytest.raises(OpenCodeError):
        client.request("GET", "/session")


@pytest.mark.parametrize(
    "value", ["no-provider", "/model", "provider/", "-provider/model", "a/b\n", "a b/c"]
)
def test_invalid_model_ids_are_rejected(value):
    with pytest.raises(OpenCodeError):
        model_parts(value)


def test_model_catalog_preserves_custom_ids_and_unknown_context():
    assert model_parts("local/org/model:quantized") == ("local", "org/model:quantized")
    result = models(
        {
            "all": [{"id": "local", "models": {"org/model:quantized": {"name": "My model"}}}],
            "connected": [],
        }
    )
    assert result == [
        {
            "id": "local/org/model:quantized",
            "label": "My model",
            "configured": False,
            "contextWindow": None,
        }
    ]


def record():
    return [
        {
            "info": {
                "id": "msg_1",
                "sessionID": "ses_1",
                "role": "assistant",
                "providerID": "local",
                "modelID": "org/model",
            },
            "parts": [
                {
                    "id": "part_1",
                    "sessionID": "ses_1",
                    "messageID": "msg_1",
                    "type": "text",
                    "text": "answer",
                }
            ],
        }
    ]


def test_messages_have_stable_ids_unknown_usage_and_no_invented_completion():
    row = record()
    normalized = messages(row, "ses_1")
    assert normalized[0]["id"] == "msg_1"
    assert normalized[0]["usage"]["input"] is None
    assert normalized[0]["cost"] is None
    assert not normalized[0]["complete"]
    assert messages(copy.deepcopy(row), "ses_1") == normalized
    row[0]["info"].update(
        tokens={"input": 0, "output": 12, "reasoning": 5, "cache": {"read": 7}},
        cost=0,
        time={"completed": 123},
    )
    new = messages(row, "ses_1")[0]
    assert new["id"] == normalized[0]["id"]
    assert new["usage"] == {
        "input": 0,
        "output": 12,
        "reasoning": 5,
        "cacheRead": 7,
        "cacheWrite": None,
    }
    assert new["cost"] == 0 and new["complete"]


@pytest.mark.parametrize(
    "change",
    ["wrong-session", "wrong-part", "duplicate-message", "duplicate-part", "bad-id", "bad-shape"],
)
def test_foreign_or_ambiguous_history_is_rejected(change):
    rows = record()
    if change == "wrong-session":
        rows[0]["info"]["sessionID"] = "ses_other"
    elif change == "wrong-part":
        rows[0]["parts"][0]["messageID"] = "msg_other"
    elif change == "duplicate-message":
        rows.append(copy.deepcopy(rows[0]))
    elif change == "duplicate-part":
        rows[0]["parts"].append(copy.deepcopy(rows[0]["parts"][0]))
    elif change == "bad-id":
        rows[0]["info"]["id"] = "../../another"
    else:
        rows[0]["parts"] = None
    with pytest.raises(OpenCodeError):
        messages(rows, "ses_1")
