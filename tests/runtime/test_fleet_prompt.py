"""Ask Oracle's prompt carries detail only where it's likely to matter (owner's
question, 2026-10-06: every question sent every live session's last 30
terminal lines, about 60k characters for 34 sessions, 90% idle screens)."""

import json

import pytest
from tests.runtime.test_session_api import dispatch

from duckterm.llm.summarizer import Summary
from duckterm.persistence.history import HistoryStore
from duckterm.server import Server


class Pane:
    def __init__(self, key: str) -> None:
        self.key = key

    def screen_text(self, lines: int) -> str:
        return f"SCREEN-{self.key}-{lines}"


@pytest.fixture
def fleet(tmp_path, monkeypatch):
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path))
    history = HistoryStore(tmp_path / "db.sqlite")
    for key, state in [("arch", "idle"), ("qa", "waiting"), ("dev", "busy"), ("docs", "idle")]:
        history.record(
            {"_id": key, "_ts": 1, "event_type": "SessionStart", "session_key": key, "test": True}
        )
        history.set_meta(key, name=key, group="work")
        history.set_state(key, state)
    server = Server(history=history)
    monkeypatch.setattr(server.orchestrator, "get", lambda key: Pane(key))
    prompts: list[str] = []

    def fake(prompt: str) -> Summary:
        prompts.append(prompt)
        return Summary(text="ok", backend="cli")

    monkeypatch.setattr("duckterm.server.summarize", fake)
    owner = {"x-duckterm-token": server.token}

    def ask(question: str) -> dict:
        body = json.dumps({"question": question}).encode()
        return dispatch(server, "POST", "/fleet/ask", owner, body)[1]

    yield server, ask, prompts
    history.close()


def test_screens_go_to_named_waiting_and_busy_sessions_only(fleet) -> None:
    _, ask, prompts = fleet
    body = ask("what is arch doing?")
    prompt = prompts[-1]
    assert "SCREEN-arch-120" in prompt  # named: the deepest look
    assert "SCREEN-qa-30" in prompt and "SCREEN-dev-30" in prompt  # needs you, busy
    assert "SCREEN-docs" not in prompt  # idle and unnamed: one line, no screen
    assert "- docs (docs) | folder: work | state: idle" in prompt
    assert prompt.index("### arch") < prompt.index("### qa") < prompt.index("- docs")
    assert (body["sessions_detailed"], body["sessions_brief"]) == (3, 1)
    assert body["prompt_chars"] == len(prompt)


def test_the_budget_drops_least_relevant_first_and_says_so(fleet) -> None:
    server, ask, prompts = fleet
    server._FLEET_BUDGET = 1  # room for nothing but the named session
    body = ask("is dev blocked?")
    assert "SCREEN-dev-120" in prompts[-1]  # the named session is never dropped
    assert body["sessions_omitted"] == ["qa", "arch", "docs"]
    assert "left out for length (ask about one by name): qa, arch, docs" in prompts[-1]
