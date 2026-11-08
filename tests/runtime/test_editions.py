"""Edition boundaries preserve core sessions and reject unavailable work early."""

import asyncio
import base64
import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from test_memory import memory_rig
from test_session_api import Writer, dispatch

from duckterm.core.session_api import APIError
from duckterm.editions import CODE, CURRENT_EDITION, FULL, LITE, EditionConfig
from duckterm.helpers import session_instructions
from duckterm.persistence.history import HistoryStore
from duckterm.server import Server

__all__ = ["memory_rig"]


def status(server, method, headers, body=b"", *, path="/edition"):
    writer = Writer()

    async def run():
        await server._dispatch(method, path, asyncio.StreamReader(), writer, headers, body)

    asyncio.run(run())
    return int(writer.data.split(b"\r\n", 1)[0].split()[1])


def test_editions_keep_the_agreed_core_and_separate_the_full_additions():
    assert LITE.features == {
        "folders",
        "workstreams",
        "harnesses",
        "terminal",
        "shell",
        "messages",
        "forks",
        "session_status",
        "inbox",
        "checkpoints",
        "summaries",
        "context",
        "handoff",
        "artifacts",
        "overview",
    }
    assert LITE.features < CODE.features
    assert LITE.features < FULL.features
    assert CODE.features < FULL.features
    assert FULL.features - CODE.features == {"oracle", "remote_workspaces"}
    assert CODE.features - LITE.features == {
        "explorer",
        "file_editing",
        "git_changes",
        "folder_chat",
        "connectors",
        "pr_review",
        "rule_suggestions",
    }
    for feature in ("explorer", "folder_chat", "connectors", "rule_suggestions", "pr_review"):
        assert CODE.includes(feature)
        assert not LITE.includes(feature)
    for feature in ("checks", "ducky", "unknown"):
        assert not FULL.includes(feature)
    assert CURRENT_EDITION is FULL


def test_description_cannot_mutate_the_shared_feature_set():
    description = FULL.description()
    features = description["features"]
    assert isinstance(features, list)
    features.clear()
    assert FULL.includes("terminal")
    assert FULL.description()["features"] == sorted(FULL.features)


def test_edition_route_is_owner_read_only_and_preserves_data(memory_rig):
    from duckterm.helpers import instance

    server, agent, _, _ = memory_rig
    owner = {"x-duckterm-token": server.token}
    assert dispatch(server, "GET", "/edition", {})[0] == 401
    assert dispatch(server, "GET", "/edition", agent)[0] == 403
    assert status(server, "GET", {**owner, "origin": "https://evil.test"}) == 403
    before_changes = server.history._conn.total_changes
    before_schema = server.history._conn.execute("PRAGMA user_version").fetchone()[0]
    expected = {**FULL.description(), "instance": instance.description()}
    assert dispatch(server, "GET", "/edition", owner) == (200, expected)
    assert status(server, "POST", owner, json.dumps({"id": "lite"}).encode()) == 404
    assert dispatch(server, "GET", "/edition", owner) == (200, expected)
    assert server.history._conn.total_changes == before_changes
    assert server.history._conn.execute("PRAGMA user_version").fetchone()[0] == before_schema


@pytest.fixture
def edition_rig(tmp_path, monkeypatch):
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    servers = []

    def create(edition):
        root = tmp_path / edition.name
        root.mkdir()
        (root / "example.py").write_text("answer = 42\n")
        history = HistoryStore(root / "state.sqlite", edition=edition)
        server = Server(history=history, edition=edition)
        history.record(
            {
                "_id": "start",
                "_ts": 1,
                "event_type": "SessionStart",
                "session_key": "agent",
                "runtime": "claude-code",
                "cwd": str(root),
                "test": True,
            }
        )
        history.set_meta("agent", group="Tests")
        servers.append(server)
        return server, {"x-duckterm-token": server.token}, root

    yield create
    for server in servers:
        server.history.purge_test_sessions()
        server.history.close()


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/connectors", b""),
        ("POST", "/connectors/github/enable", b"{}"),
        ("POST", "/transfers/repositories", b"{}"),
        ("POST", "/transfers/clone", b'{"github_repository":"owner/repository"}'),
        ("POST", "/agents-md/suggest", b"{}"),
        ("GET", "/folders/Tests/chat", b""),
        ("GET", "/sessions/agent/files?path=", b""),
        ("GET", "/file?path=example.py", b""),
        ("POST", "/file", b"{}"),
        # Routes match the raw path, so a folder named "Tests?x=" is reachable
        # with its query-looking part unencoded.
        ("GET", "/folders/Tests?x=/chat", b""),
    ],
)
def test_lite_rejects_optional_work_before_handlers(edition_rig, method, path, body):
    server, owner, _ = edition_rig(LITE)
    before = server.history._conn.total_changes
    code, response = dispatch(server, method, path, owner, body)
    assert code == 403
    assert response["code"] == "feature_unavailable"
    assert server.history._conn.total_changes == before


@pytest.mark.parametrize("edition", [LITE, CODE])
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/relay"),
        ("POST", "/relay/rules/propose"),
        ("POST", "/voice/warm"),
        ("POST", "/transfers/prepare"),
    ],
)
def test_restricted_editions_reject_full_features(edition_rig, edition, method, path):
    server, owner, _ = edition_rig(edition)
    assert dispatch(server, method, path, owner, b"{}")[0] == 403


@pytest.mark.parametrize("edition", [LITE, CODE, FULL])
def test_lite_refuses_pr_review_before_review_or_github_work(edition_rig, monkeypatch, edition):
    server, owner, _ = edition_rig(edition)
    review = AsyncMock()
    lists = AsyncMock()
    monkeypatch.setattr(server, "_review", review)
    monkeypatch.setattr(server, "_prs", lists)

    async def run():
        for method, path in [
            ("GET", "/review/repository/branch?head=abc"),
            ("POST", "/review/repository/branch/cards/regenerate"),
            ("POST", "/review/repository/branch/github-review"),
            ("GET", "/sessions/agent/prs"),
            ("GET", "/folders/Tests%2FNesting/prs"),
        ]:
            writer = Writer()
            await server._dispatch(method, path, asyncio.StreamReader(), writer, owner, b"{}")
            if edition is LITE:
                assert b"403" in writer.data.split(b"\r\n", 1)[0]

    asyncio.run(run())
    assert review.await_count == (0 if edition is LITE else 3)
    assert lists.await_count == (0 if edition is LITE else 2)


@pytest.mark.parametrize("edition", [LITE, CODE, FULL])
def test_folder_answers_persist_in_code_and_full_but_global_oracle_stays_full(
    edition_rig, monkeypatch, edition
):
    from duckterm.llm.summarizer import Summary

    server, owner, _ = edition_rig(edition)
    server.history.set_meta("agent", name="Included session")
    server.history.record(
        {
            "_id": "outside",
            "_ts": 1,
            "event_type": "SessionStart",
            "session_key": "outside",
            "test": True,
        }
    )
    server.history.set_meta("outside", group="TestsOther", name="Outside private session")
    model = Mock(return_value=Summary("The included session is running.", "test"))
    monkeypatch.setattr("duckterm.server.summarize", model)
    path = "/fleet/ask"
    body = b'{"folder":"Tests","question":"Status?"}'
    assert dispatch(server, "POST", path, {}, body)[0] == 401
    assert dispatch(server, "POST", path, {"authorization": "Bearer agent"}, body)[0] == 403
    code, response = dispatch(server, "POST", path, owner, body)
    if edition is LITE:
        assert code == 403 and response["feature"] == "folder_chat"
        model.assert_not_called()
        assert dispatch(server, "GET", "/folders/Tests/chat", owner)[0] == 403
    else:
        assert code == 200 and response["sessions"] == ["agent"]
        assert response["answer"] == "The included session is running."
        model.assert_called_once()
        prompt = model.call_args.args[0]
        assert "Included session" in prompt and "Outside private session" not in prompt
        code, history = dispatch(server, "GET", "/folders/Tests/chat", owner)
        assert code == 200 and history["messages"] == [response["exchange"]]
    code, response = dispatch(server, "POST", path, owner, b'{"question":"Global?"}')
    if edition is FULL:
        assert code == 200 and set(response["sessions"]) == {"agent", "outside"}
        assert model.call_count == 2
    else:
        assert code == 403 and response["feature"] == "oracle"
        assert model.call_count == (1 if edition is CODE else 0)


def test_explorer_still_works_when_artifacts_are_explicitly_disabled(edition_rig, monkeypatch):
    server, owner, _ = edition_rig(EditionConfig("code", CODE.features - {"artifacts"}))

    def unexpected(*args, **kwargs):
        raise AssertionError("Code Explorer must not read artifact registrations")

    monkeypatch.setattr(server.history.artifacts, "list", unexpected)
    code, listing = dispatch(server, "GET", "/sessions/agent/files", owner)
    assert code == 200
    assert listing["available"]
    assert any(item["name"] == "example.py" for item in listing["entries"])
    assert not listing.get("artifacts")
    code, contents = dispatch(server, "GET", "/sessions/agent/file-content?path=example.py", owner)
    assert code == 200
    assert contents["available"]


def test_disabled_features_do_not_enroll_or_schedule_model_work(edition_rig, monkeypatch):
    server, owner, _ = edition_rig(
        EditionConfig(
            "lite",
            LITE.features
            - {
                "inbox",
                "checkpoints",
                "context",
                "summaries",
                "handoff",
            },
        )
    )
    monkeypatch.setenv("DUCKTERM_SUMMARIZER_CMD", "configured-but-must-never-run")
    classifier = AsyncMock(side_effect=AssertionError("No model classifier without Oracle"))
    monkeypatch.setattr(server, "_relay_detect_question", classifier)
    assert (
        server.history._conn.execute("SELECT COUNT(*) FROM session_api_members").fetchone()[0] == 0
    )
    assert not list(server.history.session_api.credential_dir.glob("*.json"))

    async def run():
        server.bus.publish({"event_type": "Stop", "session_key": "agent", "test": True})
        await asyncio.sleep(0)
        with pytest.raises(APIError) as error:
            await server._create_checkpoint("agent", server.history.session("agent"), "manual")
        assert error.value.status == 403
        with pytest.raises(APIError):
            await server.memory_preparation.start("agent", {})
        with pytest.raises(APIError):
            await server.progress_coordinator.refresh_result("agent")

    asyncio.run(run())
    classifier.assert_not_awaited()
    assert (
        server._inbox_hook_output({"event_type": "Stop", "stop_hook_active": False}, "agent")
        is None
    )
    assert dispatch(server, "GET", "/sessions", owner)[0] == 200
    assert dispatch(server, "GET", "/folders", owner)[0] == 200


@pytest.mark.parametrize("edition", [LITE, CODE, FULL])
def test_every_edition_exchanges_scoped_inbox_messages_with_real_credentials(edition_rig, edition):
    server, owner, _ = edition_rig(edition)
    credentials = {}
    for key, folder in (("agent", "Tests"), ("peer", "Tests"), ("outside", "Other")):
        if key != "agent":
            server.history.record(
                {
                    "_id": key,
                    "_ts": 1,
                    "event_type": "SessionStart",
                    "session_key": key,
                    "test": True,
                }
            )
            server.history.set_meta(key, group=folder)
        token = server.history.session_api.enroll(key, {"root": folder})["token"]
        credentials[key] = {"authorization": "Bearer " + token}
    path = "/api/v1/session/questions"
    body = json.dumps(
        {"target_session_id": "peer", "question": "What contract is approved?"}
    ).encode()
    assert dispatch(server, "POST", path, {}, body)[0] == 401
    headers = {**credentials["agent"], "idempotency-key": "edition-exchange"}
    code, question = dispatch(server, "POST", path, headers, body)
    assert code == 202 and question["status"] == "queued"
    assert dispatch(server, "POST", path, headers, body)[1]["id"] == question["id"]
    code, inbox = dispatch(server, "GET", "/api/v1/session/inbox", credentials["peer"])
    assert code == 200 and [item["id"] for item in inbox["messages"]] == [question["id"]]
    answer_path = path + "/" + question["id"] + "/answer"
    answer = b'{"text":"Use the reviewed contract."}'
    assert dispatch(server, "POST", answer_path, credentials["outside"], answer)[0] == 404
    assert dispatch(server, "POST", answer_path, credentials["agent"], answer)[0] == 404
    assert dispatch(server, "POST", answer_path, credentials["peer"], answer)[0] == 200
    code, replied = dispatch(server, "GET", path + "/" + question["id"], credentials["agent"])
    assert code == 200 and replied["answer"] == "Use the reviewed contract."
    assert dispatch(server, "GET", "/sessions/peer/inbox", credentials["agent"])[0] == 403
    assert (
        dispatch(server, "GET", "/sessions/peer/inbox", owner)[1]["messages"][0]["id"]
        == question["id"]
    )
    before = server.history._conn.total_changes
    assert (
        status(server, "POST", {**headers, "origin": "https://evil.test"}, body, path=path) == 403
    )
    assert server.history._conn.total_changes == before
    server.history.set_state("agent", "stopped", now=2)
    assert dispatch(server, "GET", "/api/v1/session/self", credentials["agent"])[0] == 401


@pytest.mark.parametrize("edition", [LITE, CODE, FULL])
def test_every_edition_registers_and_reads_artifacts_without_weakening_auth(edition_rig, edition):
    from duckterm.persistence.artifacts import registration

    server, owner, root = edition_rig(edition)
    enrolled = server.history.session_api.enroll("agent", {"root": "Tests"})
    agent = {"authorization": "Bearer " + enrolled["token"]}
    code, card = dispatch(server, "GET", "/api/v1/session/self", agent)
    assert code == 200 and "artifacts" in card["features"]
    assert {"artifacts.register", "artifacts.list", "artifacts.read"} <= set(card["capabilities"])
    source = root / "review.md"
    source.write_text("# Reviewed output\n")
    body = json.dumps(registration(source, kind="report")).encode()
    path = "/api/v1/session/artifacts"
    assert dispatch(server, "POST", path, {}, body)[0] == 401
    code, result = dispatch(server, "POST", path, agent, body)
    assert code == 200
    artifact = result["artifact"]
    source.unlink()
    assert dispatch(server, "GET", path, agent)[1]["artifacts"] == [artifact]
    endpoint = "/sessions/agent/artifacts/" + artifact["id"]
    assert dispatch(server, "GET", endpoint, {})[0] == 401
    assert dispatch(server, "GET", endpoint, agent)[0] == 403
    assert status(server, "GET", {**owner, "origin": "https://evil.test"}, path=endpoint) == 403
    code, saved = dispatch(server, "GET", endpoint, owner)
    assert (
        code == 200
        and base64.b64decode(saved["artifact"]["content_base64"]) == b"# Reviewed output\n"
    )
    code, folder = dispatch(server, "GET", "/folders/Tests/artifacts", owner)
    assert code == 200 and [item["id"] for item in folder["artifacts"]] == [artifact["id"]]
    assert dispatch(server, "GET", endpoint.replace("/agent/", "/absent/"), owner)[0] == 404


@pytest.mark.parametrize("edition", [LITE, CODE, FULL])
def test_overview_reports_saved_facts_without_oracle_or_model_work(
    edition_rig, monkeypatch, edition
):
    from duckterm.core.tokens import TokenLedger

    server, owner, root = edition_rig(edition)
    server._tokens = TokenLedger(root / "no-claude", root / "no-codex")
    model = Mock(side_effect=AssertionError("Overview must not invoke a model"))
    classifier = AsyncMock(side_effect=AssertionError("Overview must not classify questions"))
    monkeypatch.setattr("duckterm.server.summarize", model)
    monkeypatch.setattr(server, "_relay_detect_question", classifier)
    server.history.session_api.enroll("agent", {"root": "Tests"})
    code, delivered = dispatch(
        server,
        "POST",
        "/sessions/agent/message",
        owner,
        b'{"text":"Review the saved result","mode":"inbox"}',
    )
    assert code == 200 and delivered["delivered"] == "inbox"
    path = "/control-tower"
    assert dispatch(server, "GET", path, {})[0] == 401
    assert dispatch(server, "GET", path, {"authorization": "Bearer agent"})[0] == 403
    assert status(server, "GET", {**owner, "origin": "https://evil.test"}, path=path) == 403
    before = server.history._conn.total_changes
    code, facts = dispatch(server, "GET", path, owner)
    assert code == 200
    # An owner notice is present, but it is not an agent-to-agent question or
    # an Oracle nudge. The factual counters must preserve that distinction.
    assert facts["mail"] == {"sent": 0, "answered": 0, "nudges": 0}
    assert facts["tokens"]["days"] == 7 and facts["tokens"]["by_agent"]["codex"]["output"] == 0
    assert facts["backup"] == {"destination": None, "status": None, "finished_at": None}
    assert facts["remote"] == {"available": False, "count": 0}
    assert server.history._conn.total_changes == before
    assert not server.relay.notes
    if edition is not FULL:

        async def stop():
            server._relay_observe({"event_type": "Stop", "session_key": "agent", "test": True})
            await asyncio.sleep(0)

        asyncio.run(stop())
    model.assert_not_called()
    classifier.assert_not_awaited()


@pytest.mark.parametrize("edition", [LITE, CODE, FULL])
def test_oracle_relay_notes_and_answers_remain_full_only(edition_rig, edition):
    server, owner, _ = edition_rig(edition)
    approval = server.approvals.register("agent", "Bash", {"command": "pytest"}, 1, blocking=True)
    server._relay_sync_approvals()
    code, response = dispatch(server, "GET", "/relay", owner)
    if edition is not FULL:
        assert code == 403 and response["feature"] == "oracle"
        assert not server.relay.notes
        assert (
            dispatch(server, "POST", "/relay/unknown/answer", owner, b'{"answer":"approve"}')[0]
            == 403
        )
        assert server.approvals.decision_of(approval.id) is None
    else:
        assert code == 200
        [note] = response["notes"]
        assert note["approval_id"] == approval.id
        path = "/relay/" + note["id"] + "/answer"
        assert dispatch(server, "POST", path, {}, b'{"answer":"approve"}')[0] == 401
        assert dispatch(server, "POST", path, owner, b'{"answer":"approve"}')[0] == 200
        assert server.approvals.decision_of(approval.id) == "approve"


@pytest.mark.parametrize("edition", [LITE, CODE, FULL])
def test_rule_suggestions_have_their_own_capability_and_never_write_rules(
    edition_rig, monkeypatch, edition
):
    # Context remains available in Lite; it must not grant model-authored rule suggestions.
    server, owner, root = edition_rig(edition)
    rules = root / "AGENTS.md"
    rules.write_text("Keep owner rules.\n")
    model = Mock(return_value=[])
    monkeypatch.setattr("duckterm.server.suggest_rules", model)
    body = json.dumps({"dir": str(root)}).encode()
    path = "/agents-md/suggest"
    assert dispatch(server, "POST", path, {}, body)[0] == 401
    assert dispatch(server, "POST", path, {"authorization": "Bearer agent"}, body)[0] == 403
    code, response = dispatch(server, "POST", path, owner, body)
    if edition is LITE:
        assert code == 403 and response["feature"] == "rule_suggestions"
        model.assert_not_called()
    else:
        assert code == 200 and response["suggestions"] == []
        model.assert_called_once()
    assert rules.read_text() == "Keep owner rules.\n"


def test_generated_guides_follow_features_and_preserve_project_rules(tmp_path, monkeypatch):
    monkeypatch.setattr(session_instructions, "scoped_rules", lambda *args: "Keep project rules")
    lite = session_instructions.launch_prompt(
        "codex",
        "lite",
        "Fix the task",
        home=tmp_path,
        cwd=tmp_path,
        collaboration=False,
    )
    assert "Keep project rules" in lite
    assert "Fix the task" in lite
    assert "session capability" not in lite
    assert not (tmp_path / "session-instructions").exists()
    session_instructions.launch_prompt("codex", "code", "", home=tmp_path, artifacts=False)
    guides = list((tmp_path / "session-instructions").glob("*/collaboration.md"))
    assert len(guides) == 1
    assert "duckterm session ask" in guides[0].read_text()
    assert "duckterm session artifact" not in guides[0].read_text()


def test_session_api_respects_explicit_artifact_capability_removal(edition_rig):
    server, _, _ = edition_rig(EditionConfig("code", CODE.features - {"artifacts"}))
    enrolled = server.history.session_api.enroll("agent", {"root": "Tests"})
    agent = {"authorization": "Bearer " + enrolled["token"]}
    status, card = dispatch(server, "GET", "/api/v1/session/self", agent)
    assert status == 200
    assert "artifacts" not in card["features"]
    assert "inbox.read" in card["capabilities"]
    assert not any(capability.startswith("artifacts.") for capability in card["capabilities"])
    before = server.history._conn.total_changes
    for method in ("GET", "POST"):
        assert dispatch(server, method, "/api/v1/session/artifacts", agent, b"{}")[0] == 403
    assert server.history._conn.total_changes == before


def test_changing_editions_preserves_saved_data_and_schema(tmp_path, monkeypatch):
    from duckterm.persistence.artifacts import registration

    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path))
    path = tmp_path / "db.sqlite"
    source = tmp_path / "example.txt"
    source.write_text("Saved work")
    history = HistoryStore(path, edition=FULL)
    history.record(
        {
            "_id": "start",
            "_ts": 1,
            "event_type": "SessionStart",
            "session_key": "agent",
            "test": True,
        }
    )
    history.set_meta("agent", group="Tests", notes="Keep owner notes")
    artifact = history.artifacts.register("agent", registration(source, kind="report"))
    history.add_checkpoint(
        checkpoint_id="saved",
        session_key="agent",
        label="Saved",
        summary="Keep summary",
        record={"text": "Kept"},
        markdown_path=None,
        created_at=1,
    )
    schema = history._conn.execute("PRAGMA user_version").fetchone()[0]
    snapshots = {
        table: [tuple(row) for row in history._conn.execute("SELECT * FROM " + table)]
        for table in ("artifacts", "checkpoints", "session_api_members")
    }
    history.close()
    for edition in (LITE, CODE, FULL):
        history = HistoryStore(path, edition=edition)
        assert history._conn.execute("PRAGMA user_version").fetchone()[0] == schema
        assert history.session("agent")["notes"] == "Keep owner notes"
        assert history.artifacts.get("agent", artifact["id"])["title"] == "example.txt"
        for table, expected in snapshots.items():
            assert [
                tuple(row) for row in history._conn.execute("SELECT * FROM " + table)
            ] == expected
        history.close()
    history = HistoryStore(path, edition=FULL)
    history.purge_test_sessions()
    history.close()


def test_lite_approval_detection_keeps_status_without_oracle_actions(edition_rig, monkeypatch):
    from types import SimpleNamespace

    server, _, _ = edition_rig(LITE)
    approval = server.approvals.register("agent", "Bash", {"command": "test"}, 1, blocking=False)
    monkeypatch.setattr(
        server, "_relay_harness", lambda key: SimpleNamespace(auto_approves_requests=True)
    )
    watched = []
    monkeypatch.setattr(
        server, "_relay_watch", lambda identifier, key: watched.append((identifier, key))
    )
    server._relay_sync_approvals()
    assert watched == [(approval.id, "agent")]
    server._relay_confirm(approval.id, "agent")
    assert server.history.session("agent")["state"] == "waiting"
    assert not server.relay.notes
    assert approval in server.approvals.pending()


def test_lite_stop_revokes_old_grants_without_reenrolling(edition_rig):
    server, _, _ = edition_rig(FULL)
    enrolled = server.history.session_api.enroll("agent", {"root": "Tests"})
    credentials = {"authorization": "Bearer " + enrolled["token"]}
    server.history.session_api.edition = LITE
    server.history.set_state("agent", "stopped", now=2)
    # Returning to a coding edition must not resurrect a stopped agent's grant.
    server.history.session_api.edition = CODE
    with pytest.raises(APIError) as error:
        server.history.session_api.authenticate(credentials)
    assert error.value.status == 401


@pytest.mark.parametrize(
    ("edition", "path", "missing"),
    [
        (LITE, "/folders/Tests/artifacts?x=/chat", "folder_chat"),
        (LITE, "/sessions/agent/digest?x=/files", "explorer"),
    ],
)
def test_allowed_parsed_path_cannot_hide_a_restricted_raw_route(
    edition_rig, edition, path, missing
):
    server, owner, _ = edition_rig(edition)
    code, response = dispatch(server, "GET", path, owner)
    assert code == 403
    assert response["feature"] == missing


def test_session_api_query_cannot_hide_a_disabled_artifact_capability(edition_rig):
    server, _, _ = edition_rig(EditionConfig("code", CODE.features - {"artifacts"}))
    enrolled = server.history.session_api.enroll("agent", {"root": "Tests"})
    agent = {"authorization": "Bearer " + enrolled["token"]}
    with pytest.raises(APIError, match="artifacts is unavailable"):
        server.history.session_api.handle("GET", "/api/v1/session/artifacts?x=/memory", agent, b"")
