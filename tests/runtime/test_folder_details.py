"""Folder counts and artifact metadata preserve scope, content, and owner choices."""

import base64

import pytest
from tests.runtime.test_folder_view import app as folder_fixture
from tests.runtime.test_folder_view import req

from duckterm.persistence.artifacts import ArtifactError


@pytest.fixture
def app(tmp_path):
    yield from folder_fixture.__wrapped__(tmp_path)


def test_categories_keep_registration_and_removed_provenance(app):
    store = app.history.artifacts
    original = store.list("one")[0]
    assert original["kind"] == "other" and original["kind_source"] == "inferred"
    artifact = original["id"]
    path = f"/sessions/one/artifacts/{artifact}"
    assert req(app, "PATCH", path, {"kind": "spec", "kept": True}, headers={})[0] == 401
    assert req(app, "PATCH", path, {"kind": "spec", "kept": True})[0] == 200
    assert req(app, "DELETE", path)[0] == 409
    kept = store.get("one", artifact)
    assert kept["kind"] == "spec" and kept["kept"]
    assert kept["content_base64"] == base64.b64encode(b"report").decode()
    # An agent may update its file but cannot undo owner categorization or Keep.
    replacement = store.register(
        "one",
        {
            "title": "Changed title",
            "source_path": original["source_path"],
            "content_base64": base64.b64encode(b"changed").decode(),
            "kind": "report",
        },
    )
    assert (
        replacement["kind"] == "spec"
        and replacement["kind_source"] == "owner"
        and replacement["kept"]
    )
    assert req(app, "PATCH", path, {"kept": False})[0] == 200
    assert req(app, "DELETE", path)[0] == 200
    with pytest.raises(ArtifactError) as error:
        store.get("one", artifact)
    assert error.value.status == 404
    removed = next(
        row
        for row in req(app, "GET", "/folders/a/artifacts")[1]["artifacts"]
        if row["id"] == artifact
    )
    assert (
        removed["title"] == "Changed title"
        and removed["kind"] == "spec"
        and removed["removed_at"] > 0
    )
    assert "content_base64" not in removed and "content" not in removed
    assert store.list("one") == []
    assert req(app, "PATCH", path, {"kept": True})[0] == 404
    # Re-registering a removed path creates a new available file, retaining the old provenance.
    new = store.register(
        "one",
        {
            "title": "New preview",
            "source_path": original["source_path"],
            "content_base64": "eA==",
            "kind": "preview",
        },
    )
    assert new["id"] != artifact and new["kind_source"] == "declared"
    assert len(store.list_folder("a", -1, include_removed=True)) == 3
    app.history.purge_test_sessions()
    assert store.conn.execute("SELECT COUNT(*) FROM artifact_metadata").fetchone()[0] == 0


def test_folder_stats_exact_scope_periods_and_removed_counts(app, monkeypatch):
    monkeypatch.setattr(
        app._tokens,
        "analytics",
        lambda *args: {
            "today": "2026-09-30",
            "rows": [
                {
                    "session": "one",
                    "day": "2026-09-30",
                    "input": 10,
                    "cache_read": 20,
                    "cache_write": 0,
                    "output": 5,
                },
                {
                    "session": "two",
                    "day": "2026-09-29",
                    "input": 50,
                    "cache_read": 0,
                    "cache_write": 0,
                    "output": 5,
                },
                {
                    "session": "outside",
                    "day": "2026-09-30",
                    "input": 999,
                    "cache_read": 0,
                    "cache_write": 0,
                    "output": 0,
                },
            ],
        },
    )
    app.history.set_state("two", "waiting")
    artifact = app.history.artifacts.list("one")[0]
    app.history.artifacts.update_metadata("one", artifact["id"], {"kind": "evidence"})
    app.history.artifacts.remove("one", artifact["id"])
    assert req(app, "GET", "/folders/a/stats", headers={})[0] == 401
    status, stats = req(app, "GET", "/folders/a/stats")
    assert status == 200
    assert sum(stats["sessions"].values()) == 2
    assert stats["waiting"] == [{"key": "two", "name": "Session two"}]
    assert stats["periods"]["1"]["tokens"] == 35 and stats["periods"]["7"]["tokens"] == 90
    assert stats["artifacts"] == {
        "by_kind": {"other": 1, "evidence": 1},
        "available": 1,
        "removed": 1,
    }
    assert req(app, "GET", "/folders/missing/stats")[0] == 404
    app.history.create_folder("empty")
    assert req(app, "GET", "/folders/empty/stats")[1]["artifacts"] == {
        "by_kind": {},
        "available": 0,
        "removed": 0,
    }
    app.history.move_folder("a", "renamed")
    assert req(app, "GET", "/folders/renamed/stats")[1]["artifacts"] == stats["artifacts"]


def test_metadata_validation_and_inferred_kinds(app):
    store = app.history.artifacts
    for title, extension, kind in [
        ("Screen", "png", "evidence"),
        ("Plan", "html", "preview"),
        ("Research", "md", "research"),
        ("Release decision", "md", "kdd"),
    ]:
        row = store.register(
            "one",
            {"title": title, "source_path": f"/tmp/{title}.{extension}", "content_base64": "eA=="},
        )
        assert row["kind"] == kind and row["kind_source"] == "inferred"
        for patch in [{}, [], {"kind": "unknown"}, {"kept": 1}, {"remove": True}]:
            assert req(app, "PATCH", f'/sessions/one/artifacts/{row["id"]}', patch)[0] == 400
