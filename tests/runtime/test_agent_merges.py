"""Reviewed handoffs retain memory and close only with explicit, safe consent."""

import json
import subprocess
from pathlib import Path

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
    for key in ("target", "source", "other"):
        history.record(
            {
                "_id": key,
                "_ts": 1,
                "session_key": key,
                "event_type": "SessionStart",
                "test": True,
                "runtime": "generic",
                "parent_session_key": "target" if key == "source" else None,
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


def test_memory_grant_is_explicit_and_remains_as_deleted_source_pointer(rig):
    history, server, _ = rig
    history._conn.execute("UPDATE sessions SET parent_session_key=NULL WHERE session_key='source'")
    history._conn.commit()
    target_auth = enroll(history, "target")
    path = "/api/v1/session/memory/sources?session=source"
    assert send(rig, request(rig))[0] == 200
    assert dispatch(server, "GET", path, target_auth)[0] == 403
    code, receipt = send(rig, request(rig, memory=True, requestKey="linked"))
    assert code == 200 and receipt["memorySource"] == "source"
    assert "SOURCE MEMORY: source" in receipt["packet"]
    assert dispatch(server, "GET", path, target_auth)[0] == 200
    assert send(rig, request(rig, memory=True, requestKey="linked"))[0] == 200
    assert (
        history._conn.execute(
            "SELECT count(*) FROM digest_items WHERE bucket='session_memory_link_v1'"
        ).fetchone()[0]
        == 1
    )
    history.delete_session("source")
    links = dispatch(server, "GET", "/api/v1/session/memory/sessions", target_auth)[1]
    assert links["sessions"] == [
        {
            "session": "source",
            "available": False,
            "reason": "Source session was deleted; its memory is unavailable",
        }
    ]
    assert dispatch(server, "GET", path, target_auth)[0] == 404


def test_new_merge_grant_does_not_survive_target_scope_change(rig):
    history, server, _ = rig
    history._conn.execute("UPDATE sessions SET parent_session_key=NULL WHERE session_key='source'")
    history._conn.commit()
    target_auth = enroll(history, "target")
    assert send(rig, request(rig, memory=True))[0] == 200
    history._conn.execute(
        "UPDATE session_api_members SET root='different' WHERE session_key='target'"
    )
    history._conn.commit()
    assert (
        dispatch(server, "GET", "/api/v1/session/memory/sources?session=source", target_auth)[0]
        == 403
    )


def test_close_waits_for_turn_and_empty_draft_then_recovers_without_redelivery(rig, monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from duckterm import merge_closing

    history, server, _ = rig
    history._conn.execute("UPDATE sessions SET launched=1 WHERE session_key='source'")
    history._conn.commit()
    ready, empty, stopped = [False], [True], []
    sup = SimpleNamespace(last_owner_input_ms=0, running=True)
    monkeypatch.setattr(server.orchestrator, "get", lambda key: sup)
    monkeypatch.setattr(server.restarts, "turn_finished", lambda key: ready[0])

    async def draft(*_):
        return empty[0]

    async def stop(key):
        stopped.append(key)
        return True

    monkeypatch.setattr(server.restarts, "draft_free", draft)
    monkeypatch.setattr(server.orchestrator, "stop", stop)
    draft_request = request(rig, memory=True, closeSource=True)
    code, receipt = send(rig, draft_request)
    assert code == 200 and receipt["closeState"] == "waiting_for_turn"
    assert not stopped and history.fork_merges.closing("source")
    ready[0], empty[0] = True, False
    assert send(rig, draft_request)[1]["closeState"] == "draft_not_empty"
    assert not stopped
    empty[0] = True
    asyncio.run(merge_closing.recover(server))
    assert stopped == ["source"] and history.session("source")["state"] == "merged"
    assert history.session("source")["notes"] == "source original notes"
    assert send(rig, draft_request)[1]["sourceClosed"]
    assert len(stopped) == 1
    assert history._conn.execute("SELECT count(*) FROM session_questions").fetchone()[0] == 1


def test_new_keystroke_during_draft_probe_defers_close(rig, monkeypatch):
    from types import SimpleNamespace

    history, server, _ = rig
    history._conn.execute("UPDATE sessions SET launched=1 WHERE session_key='source'")
    history._conn.commit()
    sup = SimpleNamespace(last_owner_input_ms=0, running=True)
    monkeypatch.setattr(server.orchestrator, "get", lambda key: sup)
    monkeypatch.setattr(server.restarts, "turn_finished", lambda key: True)

    async def draft(*_):
        sup.last_owner_input_ms += 1
        return True

    async def stop(_):
        pytest.fail("must preserve a new keystroke")

    monkeypatch.setattr(server.restarts, "draft_free", draft)
    monkeypatch.setattr(server.orchestrator, "stop", stop)
    code, result = send(rig, request(rig, memory=True, closeSource=True))
    assert code == 200 and result["closeState"] == "source_changed"
    assert history.session("source")["state"] != "merged"


def test_close_choice_survives_server_reopen_and_preserves_source(rig):
    import asyncio

    from duckterm import merge_closing

    history, _, _ = rig
    history._conn.execute("UPDATE sessions SET launched=1 WHERE session_key='source'")
    history._conn.commit()
    code, receipt = send(rig, request(rig, memory=True, closeSource=True))
    assert code == 200 and receipt["closeState"] == "terminal_unavailable"
    path = history._conn.execute("PRAGMA database_list").fetchone()[2]
    reopened_history = HistoryStore(Path(path))
    try:
        reopened = Server(history=reopened_history)
        # The source exited while the app was closed. Its retained identity,
        # message, notes and grant are sufficient to finish without resending.
        reopened_history._conn.execute(
            "UPDATE sessions SET state='terminated' WHERE session_key='source'"
        )
        reopened_history._conn.commit()
        asyncio.run(merge_closing.recover(reopened))
        assert reopened_history.session("source")["state"] == "merged"
        assert reopened_history.session("source")["notes"] == "source original notes"
        assert (
            reopened_history._conn.execute("SELECT count(*) FROM session_questions").fetchone()[0]
            == 1
        )
    finally:
        reopened_history.close()


def test_retry_reports_closed_immediately_and_stopped_supervisor_is_safe(rig, monkeypatch):
    from types import SimpleNamespace

    history, server, _ = rig
    history._conn.execute("UPDATE sessions SET launched=1 WHERE session_key='source'")
    history._conn.commit()
    draft = request(rig, memory=True, closeSource=True)
    assert send(rig, draft)[1]["closeState"] == "terminal_unavailable"
    history._conn.execute("UPDATE sessions SET state='terminated' WHERE session_key='source'")
    history._conn.commit()
    monkeypatch.setattr(server.orchestrator, "get", lambda _: SimpleNamespace(running=False))
    code, result = send(rig, draft)
    # get must return a stable supervisor: replace the intentionally different
    # object before the successful retry, exercising the identity fence too.
    assert code == 200 and result["closeState"] == "source_changed"
    ended = SimpleNamespace(running=False)
    monkeypatch.setattr(server.orchestrator, "get", lambda _: ended)
    code, result = send(rig, draft)
    assert code == 200 and result["sourceClosed"] and result["closeState"] == "closed"


def test_memory_grant_and_message_roll_back_as_one_transaction(rig):
    history, _, _ = rig
    draft = request(rig, memory=True)
    history._conn.execute(
        "CREATE TEMP TRIGGER reject_receipt BEFORE INSERT ON digest_items "
        "WHEN NEW.bucket='agent_merge_v1' BEGIN SELECT RAISE(ABORT, 'simulated write failure'); END"
    )
    # Failure after grant insertion must roll back grant, notes and inbox.
    assert send(rig, draft)[0] == 500
    assert history._conn.execute("SELECT count(*) FROM session_questions").fetchone()[0] == 0
    assert (
        history._conn.execute(
            "SELECT count(*) FROM digest_items WHERE bucket='session_memory_link_v1'"
        ).fetchone()[0]
        == 0
    )
    assert history.session("target")["notes"] == "target original notes"


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


def test_other_sessions_are_listed_for_roots_and_children(rig):
    history, server, auth = rig
    targets = dispatch(server, "GET", "/sessions/source/agent-merge", auth)[1]["destinations"]
    assert {item["key"] for item in targets} == {"target", "other"}
    assert all(item["allowed"] for item in targets)
    roots = dispatch(server, "GET", "/sessions/target/agent-merge", auth)[1]["destinations"]
    assert {item["key"] for item in roots} == {"source", "other"}
    history._conn.execute("UPDATE sessions SET state='archived' WHERE session_key='target'")
    history._conn.commit()
    targets = dispatch(server, "GET", "/sessions/source/agent-merge", auth)[1]["destinations"]
    target = next(item for item in targets if item["key"] == "target")
    assert not target["allowed"] and "closed" in target["reason"]


@pytest.mark.parametrize(
    "relation", ["root", "unrelated", "sibling", "grandparent", "parent_to_child"]
)
def test_general_merge_accepts_any_eligible_relationship(rig, relation):
    history, server, auth = rig
    target = "other"
    if relation == "root":
        history._conn.execute(
            "UPDATE sessions SET parent_session_key=NULL WHERE session_key='source'"
        )
    elif relation == "sibling":
        history._conn.execute(
            "UPDATE sessions SET parent_session_key='target' WHERE session_key='other'"
        )
    elif relation == "grandparent":
        history._conn.execute(
            "UPDATE sessions SET parent_session_key='other' WHERE session_key='target'"
        )
    elif relation == "parent_to_child":
        history._conn.execute(
            "UPDATE sessions SET parent_session_key='source' WHERE session_key='other'"
        )
    history._conn.commit()
    # The owner can select another folder's inbox on this computer too.
    history.set_meta(target, group="another-project")
    code, preview = dispatch(
        server,
        "POST",
        "/sessions/source/agent-merge/preview",
        auth,
        json.dumps({"target": target}).encode(),
    )
    assert code == 200, preview
    assert preview["target"]["allowed"]
    draft = {
        "target": target,
        "revision": preview["revision"],
        "context": "Reviewed context",
        "notes": "Reviewed notes",
        "code": False,
        "requestKey": "general",
    }
    code, receipt = send(rig, draft)
    assert code == 200, receipt
    assert receipt["target"] == target
    assert history.session(target)["notes"].endswith("Reviewed notes")
    assert history.session("target")["notes"] == "target original notes"
    assert history.session("source")["state"] != "merged"
    assert history.session("source")["merged_into"] is None
    assert history._conn.execute("SELECT count(*) FROM session_questions").fetchone()[0] == 1
    assert send(rig, draft)[1]["id"] == receipt["id"]
    assert history.session(target)["notes"].count("Reviewed notes") == 1


@pytest.mark.parametrize("destination", ["self", "missing", "closed", "no_inbox"])
def test_invalid_destinations_cannot_send(rig, destination):
    history, server, auth = rig
    draft = request(rig)
    if destination == "self":
        draft["target"] = "source"
    elif destination == "missing":
        draft["target"] = "missing"
    elif destination == "closed":
        history._conn.execute("UPDATE sessions SET state='archived' WHERE session_key='target'")
        history._conn.commit()
    else:
        history.set_meta("target", group="")
    before = {key: history.session(key)["notes"] for key in ("source", "target", "other")}
    code, result = dispatch(
        server,
        "POST",
        "/sessions/source/agent-merge/preview",
        auth,
        json.dumps({"target": draft["target"]}).encode(),
    )
    if destination in ("closed", "no_inbox"):
        assert code == 200 and not result["target"]["allowed"]
    else:
        assert code in (404, 409), result
    assert send(rig, draft)[0] in (404, 409)
    assert history._conn.execute("SELECT count(*) FROM session_questions").fetchone()[0] == 0
    assert (
        history._conn.execute(
            "SELECT count(*) FROM digest_items WHERE bucket=?", (agent_merges.BUCKET,)
        ).fetchone()[0]
        == 0
    )
    assert {key: history.session(key)["notes"] for key in before} == before


def test_parent_change_does_not_retarget_explicit_general_merge(rig, monkeypatch):
    history, _, _ = rig
    draft = request(rig)
    original = agent_merges.snapshot

    async def changed(fn, *args):
        history._conn.execute(
            "UPDATE sessions SET parent_session_key='other' WHERE session_key='source'"
        )
        history._conn.commit()
        return original(*args)

    monkeypatch.setattr(agent_merges.asyncio, "to_thread", changed)
    code, receipt = send(rig, draft)
    assert code == 200, receipt
    assert receipt["target"] == "target"
    assert history.session("other")["notes"] == "other original notes"


def test_saved_receipt_retry_after_relationship_change_does_not_deliver_again(rig):
    history, _, _ = rig
    draft = request(rig)
    code, receipt = send(rig, draft)
    assert code == 200
    history._conn.execute("UPDATE sessions SET parent_session_key=NULL WHERE session_key='source'")
    history._conn.commit()
    assert send(rig, draft)[1]["id"] == receipt["id"]
    assert send(rig, {**draft, "notes": "changed"})[0] == 409
    assert history._conn.execute("SELECT count(*) FROM session_questions").fetchone()[0] == 1
    assert history.session("target")["notes"].count("From source") == 1


def test_closed_destination_before_delivery_rolls_back_message_and_notes(rig, monkeypatch):
    history, server, _ = rig
    draft = request(rig)
    original = history.session_api.owner_message

    def changed(*args, **kwargs):
        history._conn.execute("UPDATE sessions SET state='archived' WHERE session_key='target'")
        history._conn.commit()
        return original(*args, **kwargs)

    monkeypatch.setattr(history.session_api, "owner_message", changed)
    assert send(rig, draft)[0] == 409
    assert history.session("target")["notes"] == "target original notes"
    assert history._conn.execute("SELECT count(*) FROM session_questions").fetchone()[0] == 0
    assert (
        history._conn.execute(
            "SELECT count(*) FROM digest_items WHERE bucket=?", (agent_merges.BUCKET,)
        ).fetchone()[0]
        == 0
    )
