"""Act 8 runtime gate: a claude-code session with a JSONL transcript produces a
transcript-based summary; the same machinery falls back to mechanical when no
transcript exists."""

import asyncio
import json
import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from duckterm.git.worktrees import WorktreeManager
from duckterm.llm.summarizer import Summary
from duckterm.persistence.history import HistoryStore
from duckterm.runtimes.claude_code import ClaudeCodeRuntime, project_slug
from duckterm.server import Server

FAKE_AGENT = Path(__file__).parent.parent / "fakes" / "fake_agent.py"


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    (tmp_path / "home").mkdir()
    for k in ("DUCKTERM_SUMMARIZER_CMD", "DUCKTERM_SUMMARIZER_URL"):
        os.environ.pop(k, None)
    yield tmp_path / "home"


def test_claude_session_summary_uses_transcript(
    tmp_path: Path, fake_home: Path, monkeypatch
) -> None:
    prompts = []

    def summarize(prompt):
        prompts.append(prompt)
        data = (
            {
                "accept": [],
                "done_next_action_ids": [],
                "summary_validation": {"ready": True, "reason_codes": []},
            }
            if "validating a candidate" in prompt
            else {
                "summary": "Added /healthz.",
                "context": {
                    "overview": "Added /healthz.",
                    **{
                        k: []
                        for k in (
                            "goals",
                            "constraints",
                            "decisions",
                            "unfinished",
                            "questions",
                            "risks",
                        )
                    },
                },
            }
        )
        return Summary(json.dumps(data), "stub")

    monkeypatch.setattr("duckterm.server.summarize", summarize)
    work = tmp_path / "work"
    work.mkdir()

    # Plant a Claude transcript where the locator will look.
    slug = project_slug(work)
    sid = "11111111-2222-4333-8444-555555555555"
    transcript = fake_home / ".claude" / "projects" / slug / (sid + ".jsonl")
    transcript.parent.mkdir(parents=True)
    transcript.write_text(
        json.dumps(
            {"type": "user", "message": {"role": "user", "content": "add a healthcheck endpoint"}}
        )
        + "\n"
        + json.dumps(
            {"type": "assistant", "message": {"role": "assistant", "content": "Added /healthz."}}
        )
        + "\n"
    )

    store = HistoryStore(tmp_path / "db.sqlite")
    server = Server(history=store)
    bus, orch = server.bus, server.orchestrator
    orch.worktrees = WorktreeManager(root=tmp_path / "wt")
    runtime = ClaudeCodeRuntime(f"{sys.executable} {FAKE_AGENT} --session-id {sid}")

    async def scenario() -> str:
        finished = asyncio.Event()
        original = server._refresh_progress

        async def refresh(key):
            try:
                await original(key)
            finally:
                finished.set()

        monkeypatch.setattr(server, "_refresh_progress", refresh)
        key = await orch.launch(runtime=runtime, cwd=str(work), prompt="add healthcheck", test=True)
        # Emit the agent's own session_id so the locator can find the transcript.
        bus.publish({"event_type": "SessionStart", "session_key": key, "session_id": sid})
        await asyncio.wait_for(orch.get(key)._task, 5)  # type: ignore[union-attr,arg-type]
        await asyncio.wait_for(finished.wait(), 5)
        return key

    key = asyncio.run(scenario())

    row = store.session(key)
    assert row is not None
    assert "add a healthcheck endpoint" in prompts[0]
    assert "Added /healthz." in prompts[0]
    assert row["outcome_summary"] == "Added /healthz."
    revision_id = json.loads(row["progress"])["revision_id"]
    assert server.digests.revision(key, revision_id)["summary_validation"]["ready"]
    assert len(prompts) == 2
    store.purge_test_sessions()
    store.close()
