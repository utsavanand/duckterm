"""Verify must not report success from a malformed or rejecting server.

These reproduce main-qa's real-pipe probes on PR #149: a server that rejects
initialize and then answers tools/list, and one that returns a non-list where
the tool array belongs. Both were reported as Verified.
"""

import sys
from pathlib import Path

import pytest

from duckterm import connectors


@pytest.fixture(autouse=True)
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DUCKTERM_NO_KEYCHAIN", "1")
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path / "duckterm-home"))
    return tmp_path


def _server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str) -> None:
    path = tmp_path / "stub-mcp"
    path.write_text(f"#!{sys.executable}\nimport json,sys\n{body}\n")
    path.chmod(0o755)
    monkeypatch.setattr(connectors, "_duckterm_bin", lambda: str(path))


_READ_LOOP = """
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    if msg.get('id') == 1:
        print(json.dumps(INIT), flush=True)
    elif msg.get('id') == 2:
        print(json.dumps(LIST), flush=True)
"""


def test_initialize_error_is_a_failure_even_when_tools_list_answers(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A server that rejects the handshake is not usable, whatever it lists next."""
    _server(
        isolated_env,
        monkeypatch,
        "INIT = {'jsonrpc':'2.0','id':1,'error':{'code':-32602,"
        "'message':'Protocol version rejected'}}\n"
        "LIST = {'jsonrpc':'2.0','id':2,'result':{'tools':[{'name':'ghost'}]}}\n" + _READ_LOOP,
    )
    result = connectors.verify("github", timeout=10)
    assert result["ok"] is False
    assert result["tools"] == 0
    assert "Protocol version rejected" in str(result["detail"])


@pytest.mark.parametrize(
    "tools_value",
    ["'invalid'", "42", "None", "{'name': 'not-a-list'}", "[{'name':'ok'}, 'loose']"],
)
def test_a_tool_list_that_is_not_a_list_of_tools_is_a_failure(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch, tools_value: str
) -> None:
    """len() on a string counted characters and reported 7 tools."""
    _server(
        isolated_env,
        monkeypatch,
        "INIT = {'jsonrpc':'2.0','id':1,'result':{'protocolVersion':'2024-11-05'}}\n"
        f"LIST = {{'jsonrpc':'2.0','id':2,'result':{{'tools':{tools_value}}}}}\n" + _READ_LOOP,
    )
    result = connectors.verify("github", timeout=10)
    assert result["ok"] is False
    assert result["tools"] == 0
    assert "did not list its tools" in str(result["detail"])


def test_a_missing_result_is_a_failure(isolated_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _server(
        isolated_env,
        monkeypatch,
        "INIT = {'jsonrpc':'2.0','id':1,'result':{'protocolVersion':'2024-11-05'}}\n"
        "LIST = {'jsonrpc':'2.0','id':2}\n" + _READ_LOOP,
    )
    result = connectors.verify("github", timeout=10)
    assert result["ok"] is False and result["tools"] == 0


def test_a_server_serving_no_tools_is_reported_honestly(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty list is well-formed: report zero rather than inventing a failure."""
    _server(
        isolated_env,
        monkeypatch,
        "INIT = {'jsonrpc':'2.0','id':1,'result':{'protocolVersion':'2024-11-05'}}\n"
        "LIST = {'jsonrpc':'2.0','id':2,'result':{'tools':[]}}\n" + _READ_LOOP,
    )
    assert connectors.verify("github", timeout=10) == {"ok": True, "detail": None, "tools": 0}
