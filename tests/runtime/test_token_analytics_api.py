import json
from datetime import UTC, datetime

from tests.runtime.test_session_api import dispatch

from duckterm.persistence.history import HistoryStore
from duckterm.server import Server


def test_owner_auth_range_validation_and_exact_model(tmp_path, monkeypatch):
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    path = tmp_path / "claude/projects/example.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            dict(
                type="assistant",
                timestamp=datetime.now(UTC).isoformat(),
                message=dict(
                    id="m",
                    model="claude-opus-4-1-20250805",
                    usage=dict(input_tokens=8, output_tokens=2),
                ),
            )
        )
        + "\n"
    )
    history = HistoryStore(tmp_path / "test.sqlite")
    try:
        server = Server(history=history)
        owner = {"x-duckterm-token": server.token}
        assert dispatch(server, "GET", "/analytics/tokens", {})[0] == 401
        for query in ["days=0", "days=no", "days=7&days=1", "days=99999"]:
            assert dispatch(server, "GET", "/analytics/tokens?" + query, owner)[0] == 400
        status, data = dispatch(server, "GET", "/analytics/tokens?days=all", owner)
        assert status == 200
        assert data["rows"][0]["model"] == "claude-opus-4-1-20250805"
        assert data["rows"][0]["session"] == "outside"
        assert data["rows"][0]["input"] == 8
        assert dispatch(server, "GET", "/analytics/tokens?agent=codex", owner)[1]["rows"] == []
    finally:
        history.close()
