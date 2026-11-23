"""Explicit refresh stores progress; Stop and exit events never request generation."""

import asyncio
import json
import sys
import tempfile
from pathlib import Path

import pytest

from duckterm.persistence.history import HistoryStore
from duckterm.server import Server

_DIGEST = {
    "summary": "Standing up the demo app.",
    "deliverables": ["scaffolded the app"],
    "learnings": ["sqlite is enough"],
    "next_actions": ["wire login"],
}


@pytest.fixture()
def fake_summarizer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    script = tmp_path / "fake-llm.py"
    script.write_text(
        f"#!{sys.executable}\nimport json,sys\n"
        f"digest={_DIGEST!r}\n"
        "prompt=sys.stdin.read()\n"
        "result=({'accept':[{'bucket':k,'text':v} for k,values in digest.items() "
        "if k!='summary' for v in values], 'done_next_action_ids':[], "
        "'summary_validation':{'ready':True,'reason_codes':[]}} "
        "if 'validating a candidate' in prompt else digest)\n"
        "print(json.dumps(result))\n"
    )
    script.chmod(0o755)
    monkeypatch.setenv("DUCKTERM_SUMMARIZER_CMD", str(script))
    # The digest ARCHIVE (DigestStore) resolves its db via DUCKTERM_HOME —
    # isolate it so tests never write to the real ~/.duckterm.
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path / "home"))
    return script


def _server() -> Server:
    server = Server(history=HistoryStore(Path(tempfile.mkdtemp()) / "db.sqlite"))
    server.orchestrator.on_completed = None
    return server


def test_refresh_progress_stores_digest_on_session(fake_summarizer: Path) -> None:
    async def scenario() -> dict:
        server = _server()
        server.bus.publish(
            {"event_type": "SessionStart", "session_key": "S", "cwd": "/tmp", "test": True}
        )
        from duckterm.runtimes.generic import GenericRuntime

        await server.orchestrator.launch(
            runtime=GenericRuntime("sh -c 'echo WORKING_ON_APP; exec cat'"),
            cwd="/tmp",
            session_key="S",
            test=True,
        )
        await asyncio.sleep(0.3)  # let the pty produce a screen
        await server._refresh_progress("S")
        await server.orchestrator.stop("S")
        row = server.history.session("S")
        assert row is not None
        result = json.loads(row["progress"]), server.digests.items("S")
        server.history.purge_test_sessions()
        server.history.close()
        return result

    digest, archived = asyncio.run(scenario())
    assert digest.pop("revision_id")
    assert digest == {**_DIGEST, "user_learnings": []}  # parse fills the new bucket
    # Generation and validation are distinct replies; only validated list items archive.
    assert {(i["bucket"], i["text"]) for i in archived} == {
        ("deliverables", "scaffolded the app"),
        ("learnings", "sqlite is enough"),
        ("next_actions", "wire login"),
    }
    assert all(i["status"] == "active" for i in archived)


def test_refresh_twice_does_not_duplicate_archive(fake_summarizer: Path) -> None:
    async def scenario() -> list:
        server = _server()
        from duckterm.runtimes.generic import GenericRuntime

        server.bus.publish(
            {"event_type": "SessionStart", "session_key": "S", "cwd": "/tmp", "test": True}
        )
        await server.orchestrator.launch(
            runtime=GenericRuntime("sh -c 'echo WORKING_ON_APP; exec cat'"),
            cwd="/tmp",
            session_key="S",
            test=True,
        )
        await asyncio.sleep(0.3)
        await server._refresh_progress("S")
        await server._refresh_progress("S")  # same digest again
        await server.orchestrator.stop("S")
        result = server.digests.items("S")
        server.history.purge_test_sessions()
        server.history.close()
        return result

    archived = asyncio.run(scenario())
    assert len(archived) == 3  # not 6 — the normalized-text guard held


def test_stop_and_exit_do_not_schedule_refresh(fake_summarizer: Path, monkeypatch) -> None:
    server = _server()
    refreshes, model_calls = [], []

    async def refresh(key):
        refreshes.append(key)

    def summarize(prompt, **_kw):
        model_calls.append(prompt)
        raise AssertionError("Automatic events must not call the model")

    monkeypatch.setattr(server, "_refresh_progress", refresh)
    monkeypatch.setattr("duckterm.server.summarize", summarize)

    def events():
        for i in range(5):
            server.bus.publish(
                {
                    "event_type": "PreToolUse",
                    "session_key": "S",
                    "cwd": "/tmp",
                    "test": True,
                    "_ts": i,
                }
            )
        for _ in range(2):
            server.bus.publish({"event_type": "Stop", "session_key": "S", "test": True})
        server._progress_on_exit("S")

    async def running_loop():
        events()
        await asyncio.sleep(0)
        assert refreshes == [] and model_calls == []

    try:
        events()  # The same behavior is required without a running event loop.
        asyncio.run(running_loop())
        assert refreshes == [] and model_calls == []
        assert not server.history.session("S")["progress"]
        assert server.history.session("S")["outcome_summary"] is None
        assert server.digests.items("S") == []
    finally:
        server.history.purge_test_sessions()
        server.history.close()


def test_digest_bridge_reconciles_original_sources_without_installing_rules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.runtime.test_agents_md_suggest import _new

    from duckterm.llm.summarizer import Summary

    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path / "home"))
    server = _server()
    workdir = tmp_path / "proj"
    workdir.mkdir()
    try:
        server.bus.publish(
            {
                "event_type": "SessionStart",
                "session_key": "capture",
                "test": True,
                "cwd": str(workdir),
                "_id": "start",
                "_ts": 1,
            }
        )
        server.bus.publish(
            {
                "event_type": "UserPromptSubmit",
                "session_key": "capture",
                "prompt": "Always preserve test exit codes.",
                "_id": "original",
                "_ts": 2,
            }
        )
        row = server.history.session("capture")
        assert row is not None
        calls = []
        original_ref = server._corrections_for_dir(str(workdir))[0].ref

        def fake(prompt: str, **_kw) -> Summary:
            calls.append(prompt)
            return Summary(
                text=json.dumps(
                    {
                        "decisions": [
                            _new(
                                "Exit codes",
                                "Preserve test exit codes.",
                                original_ref,
                            )
                        ]
                    }
                ),
                backend="test",
            )

        monkeypatch.setattr("duckterm.llm.suggest.summarize", fake)
        asyncio.run(server._refresh_rule_candidates(row))
        assert not calls  # no typed-rule opt-in
        (workdir / ".duckterm-rules.json").write_text('{"rules": []}')
        asyncio.run(server._refresh_rule_candidates(row))
        assert not calls  # a cloned rules file does not enable background capture
        from duckterm import editions
        from duckterm.core.agents_rules import read_catalog

        server.edition = editions.EditionConfig(
            "full", editions.FULL.features | {"background_rule_suggestions"}
        )
        asyncio.run(server._refresh_rule_candidates(row))
        assert json.loads((workdir / ".duckterm-rules.json").read_text()) == {"rules": []}
        state = read_catalog(workdir)
        assert state["rules"] == []
        assert state["proposals"][0]["action"] == "create_rule"
        assert state["proposals"][0]["status"] == "pending"
        assert not (workdir / "AGENTS.md").exists()
        asyncio.run(server._refresh_rule_candidates(row))
        assert len(calls) == 1  # same source cannot count again
    finally:
        server.history.purge_test_sessions()
        server.history.close()


def test_repeated_digest_strings_are_not_original_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path / "home"))
    server = _server()
    from duckterm import editions

    server.edition = editions.EditionConfig(
        "full", editions.FULL.features | {"background_rule_suggestions"}
    )
    workdir = tmp_path / "proj"
    workdir.mkdir()
    (workdir / ".duckterm-rules.json").write_text('{"rules": []}')

    def unexpected(_prompt: str):
        raise AssertionError("A copied summary must not become original evidence")

    monkeypatch.setattr("duckterm.llm.suggest.summarize", unexpected)
    try:
        for i in range(3):
            key = f"copy-{i}"
            server.bus.publish(
                {
                    "event_type": "SessionStart",
                    "session_key": key,
                    "test": True,
                    "cwd": str(workdir),
                    "_ts": 1,
                }
            )
            server.digests.merge(
                key, [{"bucket": "user_learnings", "text": "Prefers merge."}], [], 1
            )
        row = server.history.session("copy-0")
        assert row is not None
        asyncio.run(server._refresh_rule_candidates(row))
        assert json.loads((workdir / ".duckterm-rules.json").read_text()) == {"rules": []}
    finally:
        server.history.purge_test_sessions()
        server.history.close()


@pytest.mark.parametrize("capture_result", ["proposal", "invalid", "stale"])
def test_checkpoint_refresh_captures_rules_across_thread_boundary(
    fake_summarizer: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    capture_result: str,
) -> None:
    import threading

    from tests.runtime.test_agents_md_suggest import _new

    from duckterm import editions
    from duckterm.core.agents_rules import read_catalog, save_rules
    from duckterm.llm.summarizer import Summary
    from duckterm.runtimes.generic import GenericRuntime

    workdir = tmp_path / "project"
    workdir.mkdir()
    save_rules(workdir, [])
    before = (workdir / ".duckterm-rules.json").read_bytes()
    markdown = (workdir / "AGENTS.md").read_bytes()
    owner_thread = threading.get_ident()
    model_threads: list[int] = []
    original_ref = ""

    def suggest(prompt: str, **_kw) -> Summary:
        model_threads.append(threading.get_ident())
        assert "Preserve checks." in prompt
        if capture_result == "stale":
            # Simulate an owner save while the model is working. Its state must win.
            save_rules(workdir, [], capture_fingerprint="owner-save", render=False)
        return Summary(
            text=(
                "not valid JSON"
                if capture_result == "invalid"
                else json.dumps({"decisions": [_new("Checks", "Preserve checks.", original_ref)]})
            ),
            backend="test",
        )

    monkeypatch.setattr("duckterm.llm.suggest.summarize", suggest)

    async def scenario() -> None:
        nonlocal original_ref
        server = _server()
        key = "checkpoint-rule-capture"
        try:
            server.bus.publish(
                {
                    "event_type": "SessionStart",
                    "session_key": key,
                    "cwd": str(workdir),
                    "test": True,
                }
            )
            event = server.bus.publish(
                {
                    "event_type": "UserPromptSubmit",
                    "session_key": key,
                    "prompt": "Preserve checks.",
                }
            )
            original_ref = f"event:{event['_id']}"
            await server.orchestrator.launch(
                runtime=GenericRuntime("sh -c 'echo CHECKPOINT; exec cat'"),
                cwd=str(workdir),
                session_key=key,
                test=True,
            )
            await asyncio.sleep(0.3)
            await server._refresh_progress(key)
            assert server.history.session(key)["progress"]
            assert not model_threads
            home = tmp_path / "home"
            (home / "flags.json").write_text(
                json.dumps({"enabled": ["background_rule_suggestions"]})
            )
            server.edition = editions.configured()
            await server._refresh_progress(key)
            assert len(model_threads) == 1
            assert model_threads[0] != owner_thread
            state = read_catalog(workdir)
            if capture_result == "proposal":
                assert state["proposals"][0]["status"] == "pending"
                assert state["proposals"][0]["action"] == "create_rule"
                assert state["capture_fingerprint"]
                # Repeating the checkpoint cannot count the same evidence again.
                await server._refresh_progress(key)
                assert len(model_threads) == 1
            else:
                assert not state["proposals"]
                expected = "owner-save" if capture_result == "stale" else ""
                assert state["capture_fingerprint"] == expected
                stderr = capsys.readouterr().err
                assert "rule capture failed: ValueError" in stderr
                assert "Preserve checks." not in stderr
            assert server.history.session(key)["progress"]
            assert (workdir / ".duckterm-rules.json").read_bytes() == before
            assert (workdir / "AGENTS.md").read_bytes() == markdown
        finally:
            await server.orchestrator.stop(key)
            server.history.purge_test_sessions()
            server.history.close()

    asyncio.run(scenario())
