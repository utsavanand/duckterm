"""Support answers from the bundled guide plus this instance's doctor results."""

import json

from tests.runtime.test_session_api import dispatch

from duckterm.doctor import Result
from duckterm.editions import FULL, EditionConfig
from duckterm.llm.summarizer import Summary
from duckterm.persistence.history import HistoryStore
from duckterm.server import Server


def _server(tmp_path, monkeypatch, features=FULL.features | {"support"}):
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path))
    guide = tmp_path / "guide.md"
    guide.write_text("Restart with `duckterm restart`.")
    monkeypatch.setattr("duckterm.core.support.GUIDE", guide)
    monkeypatch.setattr(
        "duckterm.server.doctor.check_install",
        lambda agents: [Result("fail", "jq not found", "brew install jq")],
    )
    edition = EditionConfig("full", features)
    history = HistoryStore(tmp_path / "db.sqlite", edition=edition)
    return history, Server(history=history, edition=edition)


def test_support_answers_from_guide_and_failing_checks_and_keeps_the_conversation(
    tmp_path, monkeypatch
) -> None:
    history, server = _server(tmp_path, monkeypatch)
    owner = {"x-duckterm-token": server.token}
    prompts: list[str] = []

    def fake(prompt: str, **_kw) -> Summary:
        prompts.append(prompt)
        return Summary(text=f"answer {len(prompts)}", backend="cli")

    monkeypatch.setattr("duckterm.server.summarize", fake)

    def ask(q: str) -> tuple:
        return dispatch(server, "POST", "/support/ask", owner, json.dumps({"question": q}).encode())

    assert ask("how do I restart?")[0] == 200
    assert ask("and then?")[1]["exchange"]["q"] == "and then?"
    assert "Restart with `duckterm restart`." in prompts[0]
    assert "- FAIL: jq not found (fix: brew install jq)" in prompts[0]
    assert "Q: how do I restart?\nA: answer 1" in prompts[1]

    stored = dispatch(server, "GET", "/support/chat", owner)[1]["messages"]
    assert [m["q"] for m in stored] == ["how do I restart?", "and then?"]
    # Oracle's conversation is a separate log.
    assert dispatch(server, "GET", "/oracle/chat", owner)[1] == {"messages": []}
    assert dispatch(server, "DELETE", "/support/chat", owner)[1] == {"messages": []}
    history.close()


def test_support_rejects_empty_questions_and_reports_a_missing_backend(
    tmp_path, monkeypatch
) -> None:
    history, server = _server(tmp_path, monkeypatch)
    owner = {"x-duckterm-token": server.token}
    monkeypatch.setattr(
        "duckterm.server.summarize", lambda prompt, **_kw: Summary(text="", backend="none")
    )
    assert dispatch(server, "POST", "/support/ask", owner, b'{"question": " "}')[0] == 400
    status, body = dispatch(server, "POST", "/support/ask", owner, b'{"question": "hi"}')
    assert status == 502
    assert "summarizer" in body["error"]
    assert dispatch(server, "GET", "/support/chat", owner)[1] == {"messages": []}
    history.close()


def test_support_routes_are_off_without_the_flag(tmp_path, monkeypatch) -> None:
    history, server = _server(tmp_path, monkeypatch, features=FULL.features)
    owner = {"x-duckterm-token": server.token}
    assert dispatch(server, "GET", "/support/chat", owner)[0] == 403
    assert dispatch(server, "POST", "/support/ask", owner, b'{"question": "hi"}')[0] == 403
    history.close()
