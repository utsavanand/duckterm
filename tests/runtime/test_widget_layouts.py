"""Widget layouts persist, obey slots/auth, and follow committed folder changes."""

import json

import pytest
from tests.runtime.test_session_api import dispatch

from duckterm.persistence.history import HistoryStore
from duckterm.persistence.layouts import defaults
from duckterm.server import Server


@pytest.fixture
def app(tmp_path):
    history = HistoryStore(tmp_path / "db.sqlite")
    for folder in ["a", "a/child", "ab", "a%_"]:
        history.create_folder(folder)
    yield Server(history=history)
    history.close()


def req(app, method, surface, data=None, headers=None):
    return dispatch(
        app,
        method,
        "/layouts/" + surface,
        {"x-duckterm-token": app.token} if headers is None else headers,
        json.dumps(data).encode() if data is not None else b"",
    )


def test_layout_slots_auth_revision_and_persistence(app, tmp_path):
    assert req(app, "GET", "oracle", headers={})[0] == 401
    assert req(app, "PUT", "oracle", {}, headers={})[0] == 401
    initial = req(app, "GET", "oracle")[1]
    assert [x["type"] for x in initial["instances"]] == [
        "agents",
        "needs-you",
        "tokens",
        "mail",
        "backup",
        "remote",
    ]
    edited = {"instances": [], "revision": initial["revision"]}
    status, saved = req(app, "PUT", "oracle", edited)
    assert status == 200 and saved["instances"] == []
    assert req(app, "PUT", "oracle", edited)[0] == 409
    restarted = HistoryStore(tmp_path / "db.sqlite")
    assert restarted.layouts.get("oracle") == saved
    restarted.close()
    assert app.history.layouts.path.stat().st_mode & 0o777 == 0o600
    for bad in ["", "folder:", "folder:a/../ab", "unknown"]:
        assert req(app, "GET", bad)[0] in (400, 404)
    assert req(app, "GET", "folder:missing")[0] == 404
    assert (
        req(
            app, "PUT", "oracle", {"revision": saved["revision"], "instances": defaults("folder:a")}
        )[0]
        == 400
    )
    for mutate in [
        lambda x: x.update(type="external-code"),
        lambda x: x.update(params={"url": "https://example.test"}),
        lambda x: x.update(slot="folder"),
        lambda x: x.update(position=True),
    ]:
        rows = defaults("oracle")
        mutate(rows[0])
        assert (
            req(app, "PUT", "oracle", {"revision": saved["revision"], "instances": rows})[0] == 400
        )


def test_folder_layouts_rename_delete_and_crash_recovery(app):
    layouts = app.history.layouts
    saved = {}
    for folder in ["a", "a/child", "ab", "a%_"]:
        saved[folder] = layouts.put("folder:" + folder, defaults("folder:" + folder)[:1], "default")
    owner = {"x-duckterm-token": app.token}
    assert dispatch(app, "PATCH", "/folders/a", owner, b'{"name":"moved"}')[0] == 200
    moved = layouts.get("folder:moved/child")
    assert moved["instances"][0]["params"] == {"folder": "moved/child"}
    assert moved["revision"] != saved["a/child"]["revision"]
    assert layouts.get("folder:ab") == saved["ab"]
    assert layouts.get("folder:a%_") == saved["a%_"]
    assert dispatch(app, "DELETE", "/folders/moved", owner)[0] == 200
    app.history.create_folder("moved/child")
    assert layouts.get("folder:moved/child")["revision"] == "default"
    # Durable intent: before DB commit keeps the original; after commit follows it.
    for commit in [False, True]:
        data = layouts._load()
        data["pending"] = {"old": "ab", "new": "renamed"}
        layouts._save(data)
        if commit:
            app.history.move_folder("ab", "renamed")
        Server(history=app.history)
        target = "renamed" if commit else "ab"
        assert layouts.get("folder:" + target)["instances"][0]["params"] == {"folder": target}
    data = layouts._load()
    data["pending"] = {"old": "renamed", "new": None}
    layouts._save(data)
    app.history.delete_folder("renamed")
    Server(history=app.history)
    app.history.create_folder("renamed")
    assert layouts.get("folder:renamed")["revision"] == "default"


def test_damaged_layout_preserves_recovery_copy(app):
    layouts = app.history.layouts
    layouts.path.write_text("{broken")
    assert layouts.get("oracle")["instances"] == defaults("oracle")
    assert next(layouts.path.parent.glob("layouts-corrupt-*.json")).read_text() == "{broken"


def test_relay_exposes_existing_urgency_and_approval_class(app):
    for kind, urgency in [("approval", None), ("question", "offer"), ("choice", None)]:
        app.relay.add(
            {"session_key": "test", "kind": kind, **({"urgency": urgency} if urgency else {})}
        )
    # These are isolated fixture notes; no real approval is requested or answered.
    app._relay_sync_approvals = lambda: None
    status, result = dispatch(app, "GET", "/relay", {}, b"")
    assert status == 200
    assert [note["urgency"] for note in result["notes"]] == ["approval", "offer", "blocked"]
