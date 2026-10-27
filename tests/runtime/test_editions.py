"""Edition boundaries preserve core sessions and reject unavailable work early."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from test_memory import memory_rig
from test_session_api import Writer, dispatch

from duckterm.core.session_api import APIError
from duckterm.editions import CODE, CURRENT_EDITION, FULL, LITE
from duckterm.helpers import session_instructions
from duckterm.persistence.history import HistoryStore
from duckterm.server import Server

__all__ = ["memory_rig"]


def status(server, method, headers, body=b""):
    writer = Writer()

    async def run():
        await server._dispatch(method, "/edition", asyncio.StreamReader(), writer, headers, body)

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
    }
    assert LITE.features < CODE.features
    assert LITE.features < FULL.features
    assert CODE.features - FULL.features == {"pr_review"}
    assert FULL.features - CODE.features == {"oracle", "remote_workspaces", "artifacts"}
    for feature in ("explorer", "inbox", "folder_chat", "checkpoints", "connectors"):
        assert CODE.includes(feature)
        assert not LITE.includes(feature)
    for feature in ("checks", "pr_review", "ducky", "unknown"):
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
    server, agent, _, _ = memory_rig
    owner = {"x-duckterm-token": server.token}
    assert dispatch(server, "GET", "/edition", {})[0] == 401
    assert dispatch(server, "GET", "/edition", agent)[0] == 403
    assert status(server, "GET", {**owner, "origin": "https://evil.test"}) == 403
    before_changes = server.history._conn.total_changes
    before_schema = server.history._conn.execute("PRAGMA user_version").fetchone()[0]
    assert dispatch(server, "GET", "/edition", owner) == (200, FULL.description())
    assert status(server, "POST", owner, json.dumps({"id": "lite"}).encode()) == 404
    assert dispatch(server, "GET", "/edition", owner) == (200, FULL.description())
    assert server.history._conn.total_changes == before_changes
    assert server.history._conn.execute("PRAGMA user_version").fetchone()[0] == before_schema


@pytest.fixture
def edition_rig(tmp_path, monkeypatch):
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path / "home"))
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
        ("POST", "/agents-md/suggest", b"{}"),
        ("GET", "/session-inbox-counts", b""),
        ("GET", "/folder-interactions?folder=Tests", b""),
        ("GET", "/folders/Tests/chat", b""),
        ("POST", "/folders/Tests/dispatch", b"{}"),
        ("POST", "/folders/Tests/broadcast", b"{}"),
        ("GET", "/sessions/agent/memory?format=text", b""),
        ("POST", "/sessions/agent/restart-preparation", b"{}"),
        ("GET", "/sessions/agent/checkpoint-plan", b""),
        ("POST", "/sessions/agent/checkpoint", b"{}"),
        ("GET", "/sessions/agent/checkpoint-attempts/example", b""),
        ("GET", "/sessions/agent/digest", b""),
        ("POST", "/sessions/agent/agent-merge/preview", b"{}"),
        ("GET", "/sessions/agent/files?path=", b""),
        ("GET", "/file?path=example.py", b""),
        ("POST", "/file", b"{}"),
        ("POST", "/snapshots", b"{}"),
        # Routes match the raw path, so a folder named "Tests?x=" is reachable
        # with its query-looking part unencoded.
        ("GET", "/folders/Tests?x=/chat", b""),
        ("GET", "/folders/Tests?x=/stats", b""),
        ("GET", "/folders/Tests?x=/recipients", b""),
        ("POST", "/folders/Tests?x=/dispatch", b"{}"),
        ("GET", "/sessions/agent?x=/digest", b""),
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
        ("GET", "/control-tower"),
        ("GET", "/relay"),
        ("POST", "/relay/rules/propose"),
        ("POST", "/voice/warm"),
        ("POST", "/transfers/prepare"),
        ("GET", "/sessions/agent/artifacts"),
        ("GET", "/folders/Tests/artifacts"),
    ],
)
def test_restricted_editions_reject_full_features(edition_rig, edition, method, path):
    server, owner, _ = edition_rig(edition)
    assert dispatch(server, method, path, owner, b"{}")[0] == 403


@pytest.mark.parametrize("edition", [LITE, CODE, FULL])
def test_pr_review_is_code_only_before_review_or_github_work(edition_rig, monkeypatch, edition):
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
            if edition is not CODE:
                assert b"403" in writer.data.split(b"\r\n", 1)[0]

    asyncio.run(run())
    assert review.await_count == (3 if edition is CODE else 0)
    assert lists.await_count == (2 if edition is CODE else 0)


def test_code_keeps_folder_answers_without_global_oracle(edition_rig, monkeypatch):
    server, owner, _ = edition_rig(CODE)
    handler = AsyncMock()
    monkeypatch.setattr(server, "_fleet_ask", handler)

    async def run():
        writer = Writer()
        await server._dispatch(
            "POST",
            "/fleet/ask",
            asyncio.StreamReader(),
            writer,
            owner,
            json.dumps({"folder": "Tests", "question": "Status?"}).encode(),
        )

    asyncio.run(run())
    handler.assert_awaited_once()
    assert dispatch(server, "POST", "/fleet/ask", owner, b'{"question":"Global?"}')[0] == 403
    assert handler.await_count == 1


def test_code_explorer_reads_files_without_querying_artifacts(edition_rig, monkeypatch):
    server, owner, _ = edition_rig(CODE)

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


def test_lite_start_and_stop_do_not_enroll_or_schedule_model_work(edition_rig, monkeypatch):
    server, owner, _ = edition_rig(LITE)
    monkeypatch.setenv("DUCKTERM_SUMMARIZER_CMD", "configured-but-must-never-run")
    classifier = AsyncMock(side_effect=AssertionError("No model classifier in Lite"))
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


def test_code_session_api_keeps_inbox_but_does_not_offer_artifacts(edition_rig):
    server, _, _ = edition_rig(CODE)
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
