"""Local clone access must not expose journals or launches for imported sessions."""

import asyncio
import contextlib
import json
import shutil
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from test_session_api import Writer

from duckterm import editions, github_projects, transfer_api, transfers
from duckterm.agents import tmux
from duckterm.core.session_api import APIError
from duckterm.persistence.history import HistoryStore
from duckterm.server import Server


@pytest.fixture
def clone_rig(tmp_path, monkeypatch):
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    stores = []

    def create(edition):
        history = HistoryStore(tmp_path / f"{edition.name}.sqlite", edition=edition)
        stores.append(history)
        server = Server(history=history, edition=edition)
        return server, {"x-duckterm-token": server.token}

    yield create
    for history in stores:
        history.purge_test_sessions()
        history.close()


def request(server, owner, operation, value):
    writer = Writer()

    async def run():
        await server._dispatch(
            "POST",
            f"/transfers/{operation}",
            asyncio.StreamReader(),
            writer,
            owner,
            json.dumps(value).encode(),
        )

    asyncio.run(run())
    head, body = writer.data.split(b"\r\n\r\n", 1)
    try:
        response = json.loads(body)
    except ValueError:
        response = {"error": body.decode()}
    return int(head.split()[1]), response


def clone_state(identifier, destination, **changes):
    return {
        "id": identifier,
        "stage": "ready",
        "url": "https://example.invalid/project.git",
        "github_repository": "",
        "branch": "",
        "destination": str(destination),
        **changes,
    }


def save(identifier, state):
    with transfers.locked(identifier) as directory:
        transfers.save(directory, state)


@pytest.mark.parametrize(
    ("edition", "use_connector"),
    [
        (editions.LITE, False),
        (editions.CODE, False),
        (editions.FULL, False),
        (editions.CODE, True),
        (editions.FULL, True),
    ],
)
@pytest.mark.parametrize("automatic", [False, True])
def test_local_clone_chain_keeps_runtime_checks_and_one_test_launch(
    clone_rig, git_repo, tmp_path, monkeypatch, edition, use_connector, automatic
):
    server, owner = clone_rig(edition)
    identifier = uuid.uuid4().hex
    code, folder = request(server, owner, "mkdir", {"parent": str(tmp_path), "name": "projects"})
    assert code == 200
    assert (tmp_path / "projects").is_dir()
    monkeypatch.setattr(github_projects, "list_repositories", lambda page: {"repositories": []})
    monkeypatch.setattr(
        github_projects, "clone", lambda repo, branch, stage: shutil.copytree(git_repo, stage)
    )
    if use_connector:
        assert request(server, owner, "repositories", {}) == (200, {"repositories": []})
    repository = "fixture/local-clone"
    url = f"https://github.com/{repository}.git"
    real_run = transfers.subprocess.run

    def clone_fixture(args, **kwargs):
        if args[0] == "git" and "clone" in args:
            assert args[-2] == url
            args = [*args[:-2], str(git_repo), args[-1]]
            result = real_run(args, **kwargs)
            if result.returncode == 0:
                # The clone is offline, but retains the permitted remote URL
                # that a real HTTPS clone records for the verification scan.
                real_run(["git", "-C", args[-1], "remote", "set-url", "origin", url], check=True)
            return result
        return real_run(args, **kwargs)

    monkeypatch.setattr(transfers.subprocess, "run", clone_fixture)
    destination = tmp_path / "projects" / "checkout"
    preflight = {"destination": str(destination), "command": sys.executable}
    if automatic:
        destination = transfers.automatic_workspace(identifier, url)
        preflight.update(id=identifier, url=url, branch="main", destination="", auto_workspace=True)
    elif edition is not editions.FULL:
        preflight.update(runtime=None, requirements=None, linux_compatible=None)
    assert (
        request(server, owner, "preflight", {**preflight, "command": "missing-clone-agent"})[0]
        == 400
    )
    assert not destination.exists()
    assert request(server, owner, "preflight", preflight)[0] == 200
    clone = {
        "id": identifier,
        "url": url,
        "github_repository": repository if use_connector else "",
        "attempt_id": uuid.uuid4().hex,
        "branch": "main",
        "destination": str(destination),
        "source": "",
        "selected": [],
        "destination_source": url,
    }
    if automatic:
        clone = {
            "id": identifier,
            "url": url,
            "repository": repository,
            "branch": "main",
            "github_repository": repository if use_connector else "",
            "destination": str(destination),
            "auto_workspace": True,
        }
    elif edition is not editions.FULL:
        clone.update(source_session=None, fingerprint=None, conversation_sha256=None)
    code, ready = request(server, owner, "clone", clone)
    assert code == 200 and ready["stage"] == "ready", ready
    assert (destination / "README.md").read_text() == "base\n"
    assert request(server, owner, "clone", clone) == (200, ready)
    assert request(server, owner, "status", {"id": identifier}) == (200, ready)

    async def launch(**kwargs):
        assert kwargs["test"] is True
        assert kwargs["cwd"] == str(destination)
        key = kwargs["session_key"]
        server.history.record(
            {
                "_id": "start",
                "_ts": 1,
                "event_type": "SessionStart",
                "session_key": key,
                "runtime": "generic",
                "test": True,
            }
        )
        return key

    launcher = AsyncMock(side_effect=launch)
    monkeypatch.setattr(server.orchestrator, "launch", launcher)
    monkeypatch.setattr(tmux, "session_exists", lambda target: True)
    monkeypatch.setattr(transfer_api.asyncio, "sleep", AsyncMock())
    launch_request = {"id": identifier, "command": sys.executable, "test": True}
    code, launched = request(server, owner, "launch", launch_request)
    assert code == 200 and launched["stage"] == "launched"
    assert request(server, owner, "launch", launch_request) == (200, launched)
    assert launcher.await_count == 1
    assert server.history.session(launched["session_key"])["test"] == 1


@pytest.mark.parametrize("operation", ["repositories", "clone"])
def test_lite_cannot_reach_github_connector_through_clone_routes(clone_rig, monkeypatch, operation):
    server, owner = clone_rig(editions.LITE)

    def forbidden(*args, **kwargs):
        pytest.fail("Connector credentials must not be read")

    monkeypatch.setattr(github_projects, "list_repositories", forbidden)
    monkeypatch.setattr(github_projects, "clone", forbidden)
    params = {} if operation == "repositories" else {"github_repository": "fixture/private"}
    assert request(server, owner, operation, params)[0] == 403
    with pytest.raises(APIError) as error:
        asyncio.run(transfer_api.dispatch(server, operation, params))
    assert error.value.status == 403


@pytest.mark.parametrize("edition", [editions.LITE, editions.CODE])
@pytest.mark.parametrize(
    "operation", ["preview", "prepare", "chunk", "begin", "receive", "finish", "link", "continue"]
)
def test_transfer_operations_stay_forbidden_before_handler(
    clone_rig, monkeypatch, edition, operation
):
    server, owner = clone_rig(edition)
    handler = AsyncMock(side_effect=AssertionError("Transfer handler must not run"))
    monkeypatch.setattr(server, "_transfer", handler)
    assert request(server, owner, operation, {})[0] == 403
    handler.assert_not_awaited()


@pytest.mark.parametrize("edition", [editions.LITE, editions.CODE])
@pytest.mark.parametrize("operation", ["status", "launch", "clone", "preflight"])
@pytest.mark.parametrize(
    "metadata",
    [
        {"stage": "prepared", "source_session": "source"},
        {"stage": "moved", "source_session": "source", "target": "remote"},
        {"stage": "ready", "conversation": {}, "source_session": None},
        {"stage": "launched", "conversation": {"runtime": "codex", "id": "imported"}},
    ],
)
def test_transfer_journal_cannot_be_reused_for_clone_access(
    clone_rig, tmp_path, monkeypatch, edition, operation, metadata
):
    server, owner = clone_rig(edition)
    identifier = uuid.uuid4().hex
    destination = tmp_path / "never-created"
    # Even a transfer journal carrying clone-looking URL fields stays blocked.
    state = clone_state(identifier, destination, **metadata)
    save(identifier, state)
    launcher = AsyncMock(side_effect=AssertionError("Imported conversation must not launch"))
    monkeypatch.setattr(server.orchestrator, "launch", launcher)
    params = {"id": identifier}
    if operation in ("clone", "preflight"):
        params.update(url=state["url"], destination=str(destination), command=sys.executable)
        if operation == "clone":
            params.pop("command")
    assert request(server, owner, operation, params)[0] == 403
    assert transfers.status(identifier) == state
    assert not destination.exists()
    launcher.assert_not_awaited()
    assert not server._transfer_launches


@pytest.mark.parametrize("edition", [editions.LITE, editions.CODE])
@pytest.mark.parametrize(
    "operation,extra",
    [
        ("mkdir", {"source_session": "source"}),
        ("repositories", {"target": "remote"}),
        ("preflight", {"requirements": []}),
        ("clone", {"source_session": ""}),
        ("clone", {"source": "/private/project"}),
        ("clone", {"selected": ["private.txt"]}),
        ("status", {"conversation": {}}),
        ("launch", {"source_session": "source"}),
    ],
)
def test_remote_parameters_rejected_before_any_side_effect(
    clone_rig, tmp_path, edition, operation, extra
):
    server, owner = clone_rig(edition)
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
    assert request(server, owner, operation, extra)[0] == 403
    assert sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*")) == before


@pytest.mark.parametrize("operation", sorted(editions.LOCAL_PROJECT_OPERATIONS))
def test_local_clone_routes_keep_owner_auth(clone_rig, operation):
    server, owner = clone_rig(editions.LITE)
    assert request(server, {}, operation, {})[0] == 401
    assert request(server, {**owner, "origin": "https://evil.invalid"}, operation, {})[0] == 403


@pytest.mark.parametrize("operation", ["status", "launch"])
def test_full_retains_imported_journal_access(clone_rig, tmp_path, monkeypatch, operation):
    server, owner = clone_rig(editions.FULL)
    identifier = uuid.uuid4().hex
    state = {
        "id": identifier,
        "stage": "ready",
        "destination": str(tmp_path),
        "conversation": {},
        "source_session": "source",
    }
    save(identifier, state)
    if operation == "status":
        assert request(server, owner, operation, {"id": identifier}) == (200, state)
    else:
        launcher = AsyncMock(return_value="transfer-" + identifier)
        monkeypatch.setattr(server.orchestrator, "launch", launcher)
        monkeypatch.setattr(tmux, "session_exists", lambda target: True)
        monkeypatch.setattr(transfer_api.asyncio, "sleep", AsyncMock())
        assert (
            request(
                server,
                owner,
                operation,
                {"id": identifier, "command": sys.executable, "test": True},
            )[0]
            == 200
        )
        launcher.assert_awaited_once()


@pytest.mark.parametrize("operation", ["clone", "launch", "retry"])
def test_replaced_journal_is_checked_under_mutation_lock(
    clone_rig, tmp_path, monkeypatch, operation
):
    server, owner = clone_rig(editions.LITE)
    identifier = uuid.uuid4().hex
    url = "https://example.invalid/project.git"
    destination = transfers.automatic_workspace(identifier, url)
    initial = clone_state(identifier, destination)
    if operation == "retry":
        initial.update(stage="launching", session_key="fixture")
        monkeypatch.setattr(server.history, "session", lambda key: {"test": True})
        monkeypatch.setattr(tmux, "session_exists", lambda target: True)
    save(identifier, initial)
    replacement = {**initial, "conversation": {"runtime": "codex", "id": "do-not-import"}}
    original_lock = transfers.locked

    @contextlib.contextmanager
    def raced_lock(key):
        with original_lock(key) as directory:
            transfers.save(directory, replacement)
            yield directory

    monkeypatch.setattr(transfers, "locked", raced_lock)
    launcher = AsyncMock(side_effect=AssertionError("No launch after journal replacement"))
    monkeypatch.setattr(server.orchestrator, "launch", launcher)
    params = {"id": identifier, "command": sys.executable, "test": True}
    if operation == "clone":
        params = {
            "id": identifier,
            "url": url,
            "destination": str(destination),
            "auto_workspace": True,
        }
    code, _ = request(server, owner, "clone" if operation == "clone" else "launch", params)
    assert code == 403
    assert transfers.status(identifier) == replacement
    assert not (tmp_path / "projects").exists()
    launcher.assert_not_awaited()
    assert not server._transfer_launches


def test_direct_dispatch_does_not_bypass_remote_feature(clone_rig):
    server, _ = clone_rig(editions.CODE)
    with pytest.raises(APIError) as error:
        asyncio.run(transfer_api.dispatch(server, "continue", {"source_session": "source"}))
    assert error.value.status == 403
