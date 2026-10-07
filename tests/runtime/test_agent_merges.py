"""Reviewed independent-agent handoffs never mutate live worktrees or lifecycles."""

import json
import subprocess

import pytest
from tests.runtime.test_session_api import dispatch, enroll

from duckterm import agent_merges
from duckterm.agent_merge_git import snapshot
from duckterm.persistence.history import HistoryStore
from duckterm.server import Server


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("DUCKTERM_ORACLE", "off")
    history = HistoryStore(tmp_path / "history.sqlite")
    for key in ("source", "target", "other"):
        history.record(
            {
                "_id": key,
                "_ts": 1,
                "session_key": key,
                "event_type": "SessionStart",
                "test": True,
                "runtime": "generic",
            }
        )
        history.set_meta(key, name=key, group="work", notes=key + " original notes")
        enroll(history, key)
    history._conn.execute(
        "UPDATE sessions SET progress=? WHERE session_key='source'",
        (json.dumps({"summary": "Saved context", "learnings": ["Preserve compatibility"]}),),
    )
    history._conn.commit()
    server = Server(history=history)
    yield history, server, {"x-duckterm-token": server.token}
    history.close()


def request(rig, **updates):
    _, server, auth = rig
    code, preview = dispatch(
        server, "POST", "/sessions/source/agent-merge/preview", auth, b'{"target":"target"}'
    )
    assert code == 200, preview
    return {
        "target": "target",
        "revision": preview["revision"],
        "context": " Reviewed 🦆 context. \n",
        "notes": " Important note. \n",
        "code": False,
        "requestKey": "one",
        **updates,
    }


def send(rig, draft):
    _, server, auth = rig
    return dispatch(
        server, "POST", "/sessions/source/agent-merge", auth, json.dumps(draft).encode()
    )


def test_append_exact_notes_once_and_recover_lost_response(rig):
    history, server, auth = rig
    draft = request(rig)
    code, record = send(rig, draft)
    assert code == 200, record
    assert record["delivery"] == "inbox only"
    assert record["context"] == draft["context"]
    assert record["notes"] == draft["notes"]
    assert (
        history.session("target")["notes"]
        == "target original notes\n\nFrom source (source):\n" + draft["notes"]
    )
    assert history.session("source")["notes"] == "source original notes"
    assert history.session("source")["state"] != "merged"
    assert history.session("source")["merged_into"] is None
    history.set_meta("source", notes="Newer notes after successful send")
    # Retry an uncertain send succeeds even if the source subsequently changes.
    assert send(rig, draft)[1]["id"] == record["id"]
    assert history.session("target")["notes"].count("From source") == 1
    assert history._conn.execute("SELECT count(*) FROM session_questions").fetchone()[0] == 1
    assert send(rig, {**draft, "notes": "changed"})[0] == 409
    for key in ("source", "target"):
        result = dispatch(server, "GET", f"/sessions/{key}/agent-merges", auth)[1]
        assert result["merges"][0]["id"] == record["id"]


def test_options_and_stale_preview_do_not_copy_unselected_material(rig):
    history, _, _ = rig
    draft = request(rig, notes=None)
    assert send(rig, draft)[0] == 200
    assert history.session("target")["notes"] == "target original notes"
    notes_only = request(rig, context=None, requestKey="notes")
    assert send(rig, notes_only)[0] == 200
    stale = request(rig, requestKey="stale")
    history.set_meta("source", notes="Changed after preview")
    assert send(rig, stale)[0] == 409
    assert history._conn.execute("SELECT count(*) FROM session_questions").fetchone()[0] == 2


def test_notes_and_message_rollback_together_and_preserve_concurrent_notes(rig):
    history, _, _ = rig
    draft = request(rig)
    history.set_meta("target", notes="x" * 65536)
    assert send(rig, draft)[0] == 409
    assert history._conn.execute("SELECT count(*) FROM session_questions").fetchone()[0] == 0
    assert (
        history._conn.execute(
            "SELECT count(*) FROM digest_items WHERE bucket=?", (agent_merges.BUCKET,)
        ).fetchone()[0]
        == 0
    )
    history.set_meta("target", notes="Edited while reviewing")
    assert send(rig, draft)[0] == 200
    assert history.session("target")["notes"].startswith("Edited while reviewing\n\n")


def test_owner_only_scope_and_invalid_options(rig):
    history, server, auth = rig
    for method, suffix in (
        ("GET", "agent-merge"),
        ("GET", "agent-merges"),
        ("POST", "agent-merge"),
        ("POST", "agent-merge/preview"),
    ):
        assert dispatch(server, method, f"/sessions/source/{suffix}", {}, b"{}")[0] == 401
    draft = request(rig)
    for patch in (
        {"target": "source"},
        {"target": "missing"},
        {"context": None, "notes": None},
        {"code": 1},
        {"context": "🦆" * 4000},
        {"requestKey": "../x"},
    ):
        assert send(rig, {**draft, **patch})[0] in (400, 404, 409, 413)
    history.set_meta("target", group="")
    assert send(rig, draft)[0] == 409
    targets = dispatch(server, "GET", "/sessions/source/agent-merge", auth)[1]["destinations"]
    assert not next(item for item in targets if item["key"] == "target")["allowed"]
    assert history._conn.execute("SELECT count(*) FROM session_questions").fetchone()[0] == 0


def test_changes_while_git_is_awaited_are_rechecked(rig, monkeypatch):
    history, _, _ = rig
    draft = request(rig)

    async def changed(fn, *args):
        history.set_meta("target", group="")
        return {"allowed": False, "reason": "No code"}

    monkeypatch.setattr(agent_merges.asyncio, "to_thread", changed)
    assert send(rig, draft)[0] == 409
    assert history.session("target")["notes"] == "target original notes"


def git(path, *args):
    return subprocess.run(
        ["git", "-C", str(path), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def test_code_request_binds_real_worktrees_and_never_changes_them(rig, tmp_path):
    history, _, _ = rig
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "QA")
    git(repo, "config", "user.email", "qa@example.test")
    (repo / "base.txt").write_text("base")
    git(repo, "add", "base.txt")
    git(repo, "commit", "-m", "base")
    worktree = tmp_path / "feature"
    git(repo, "worktree", "add", "-b", "feature", str(worktree))
    (worktree / "feature.txt").write_text("reviewed feature")
    git(worktree, "add", "feature.txt")
    git(worktree, "commit", "-m", "feature")
    assert not snapshot(str(repo), str(repo))["allowed"]
    history._conn.execute("UPDATE sessions SET cwd=? WHERE session_key='source'", (str(worktree),))
    history._conn.execute("UPDATE sessions SET cwd=? WHERE session_key='target'", (str(repo),))
    history._conn.commit()
    draft = request(rig, code=True)
    before = git(repo, "rev-parse", "HEAD")
    code, result = send(rig, draft)
    assert code == 200, result
    assert result["git"]["sourceCommit"] == git(worktree, "rev-parse", "HEAD")
    assert result["git"]["commits"] == 1
    assert "CODE INTEGRATION REQUEST" in result["packet"]
    assert git(repo, "rev-parse", "HEAD") == before
    assert not (repo / "feature.txt").exists()
    stale = request(rig, code=True, requestKey="new-code")
    (worktree / "uncommitted.txt").write_text("not approved")
    assert send(rig, stale)[0] == 409
    assert not snapshot(str(worktree), str(repo))["allowed"]
    (worktree / "uncommitted.txt").unlink()
    (repo / "uncommitted.txt").write_text("target work")
    assert not snapshot(str(worktree), str(repo))["allowed"]


def test_acknowledgement_is_not_code_merge_success(rig):
    history, server, auth = rig
    record = send(rig, request(rig))[1]
    history._conn.execute(
        "UPDATE session_questions SET status='answered', "
        "answer='Conflict; waiting for review' WHERE id=?",
        (record["messageId"],),
    )
    history._conn.commit()
    result = dispatch(server, "GET", "/sessions/source/agent-merges", auth)[1]["merges"][0]
    assert result["delivery"] == "acknowledged"
    assert result["reply"] == "Conflict; waiting for review"
    assert "codeMerged" not in result


def test_old_notes_editor_cannot_erase_a_merge(rig):
    history, server, auth = rig
    assert send(rig, request(rig))[0] == 200
    code, _ = dispatch(
        server,
        "PATCH",
        "/sessions/target",
        auth,
        json.dumps(
            {"notes": "Unsaved editor draft", "expected_notes": "target original notes"}
        ).encode(),
    )
    assert code == 409
    assert "From source" in history.session("target")["notes"]
