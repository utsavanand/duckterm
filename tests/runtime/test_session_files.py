"""Directory pages never leave the selected local session or enumerate a tree."""

import asyncio
import base64
import json
import os
import subprocess
from urllib.parse import quote

import pytest
from test_artifacts import app, register
from tests.runtime.test_session_api import Writer, dispatch

from duckterm import session_files
from duckterm.persistence import artifacts

__all__ = ["app"]


def set_paths(server, **fields):
    assert set(fields) <= {"cwd", "worktree_path"}
    with server.history._conn:
        for key, value in fields.items():
            server.history._conn.execute(
                f"UPDATE sessions SET {key}=? WHERE session_key='a'", (value,)
            )


def root_for(app, tmp_path):
    server, agents, owner = app
    root = tmp_path / "project"
    root.mkdir()
    set_paths(server, cwd=str(root))
    return server, agents, owner, root


def names(body):
    return {entry["name"] for entry in body["entries"]}


def test_worktree_root_metadata_and_saved_copy_states(app, tmp_path):
    server, agents, owner, root = root_for(app, tmp_path)
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    set_paths(server, worktree_path=str(worktree))
    (root / "wrong-root.txt").write_text("not this workspace")
    file = worktree / "report.md"
    file.write_text("# Report")
    saved = register(
        server, agents["a"], artifacts.registration(file, "Final report", kind="report")
    )[1]["artifact"]
    missing = worktree / "missing.md"
    missing.write_text("# Original")
    missing_id = register(server, agents["a"], artifacts.registration(missing, kind="report"))[1][
        "artifact"
    ]["id"]
    missing.unlink()
    outside = root / "wrong-root.txt"
    outside_id = register(server, agents["a"], artifacts.registration(outside, kind="report"))[1][
        "artifact"
    ]["id"]
    status, body = dispatch(server, "GET", "/sessions/a/files", owner)
    assert status == 200 and body["available"]
    assert body["root"] == str(worktree.resolve())
    assert names(body) == {"report.md"}
    entry = body["entries"][0]
    assert entry["size"] == 8 and entry["modified_at"] > 0
    assert entry["artifact"]["id"] == saved["id"]
    assert entry["artifact"]["title"] == "Final report"
    saved_by_id = {a["id"]: a for a in body["artifacts"]}
    assert saved_by_id[missing_id]["source_state"] == "missing"
    assert saved_by_id[missing_id]["content_only"]
    assert saved_by_id[outside_id]["source_state"] == "outside_root"
    assert "content_base64" not in json.dumps(body)
    assert dispatch(server, "GET", f"/sessions/a/artifacts/{missing_id}", owner)[0] == 200


@pytest.mark.parametrize("repository", [True, False])
def test_gitignore_nested_negation_spaces_and_tracked_files(app, tmp_path, repository):
    server, _, owner, root = root_for(app, tmp_path)
    if repository:
        subprocess.run(["git", "init", "--quiet", str(root)], check=True)
    (root / ".gitignore").write_text("*.tmp\n!keep.tmp\nnode_modules/\n")
    for name in ("hidden.tmp", "keep.tmp", "tracked.tmp", "space file.txt"):
        (root / name).write_text("data")
    if repository:
        subprocess.run(["git", "-C", str(root), "add", "-f", "tracked.tmp"], check=True)
    (root / "node_modules").mkdir()
    child = root / "child"
    child.mkdir()
    (child / ".gitignore").write_text("secret.txt\n!nested.tmp\n")
    for name in ("secret.txt", "nested.tmp", "ok.txt"):
        (child / name).write_text("data")
    status, body = dispatch(server, "GET", "/sessions/a/files", owner)
    assert status == 200
    assert {"keep.tmp", "space file.txt", "child", ".gitignore"} <= names(body)
    assert not {"hidden.tmp", "node_modules", ".git"} & names(body)
    assert ("tracked.tmp" in names(body)) is repository
    status, body = dispatch(server, "GET", "/sessions/a/files?path=child", owner)
    assert status == 200 and names(body) == {".gitignore", "nested.tmp", "ok.txt"}
    assert dispatch(server, "GET", "/sessions/a/files?path=node_modules", owner)[0] == 403
    if not repository:
        assert not (root / ".git").exists()


@pytest.mark.parametrize(
    "relative",
    ["..", "../other", "/etc", "child/../../other", ".git", "child\\other", "\x00", "\ud800"],
)
def test_invalid_paths_fail_before_filesystem(app, tmp_path, monkeypatch, relative):
    server, _, owner, _ = root_for(app, tmp_path)
    monkeypatch.setattr(session_files, "listing", lambda *args: pytest.fail("filesystem read"))
    if "\ud800" in relative:
        with pytest.raises(session_files.APIError):
            session_files.relative_path(relative)
    else:
        assert dispatch(server, "GET", "/sessions/a/files?path=" + quote(relative), owner)[0] == 400


def test_symlink_escape_and_directory_swap_are_refused(app, tmp_path, monkeypatch):
    server, _, owner, root = root_for(app, tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.txt").write_text("private")
    (root / "escape").symlink_to(outside, target_is_directory=True)
    status, body = dispatch(server, "GET", "/sessions/a/files", owner)
    assert status == 200
    entry = next(e for e in body["entries"] if e["name"] == "escape")
    assert entry["type"] == "symlink" and not entry["browsable"]
    status, body = dispatch(server, "GET", "/sessions/a/files?path=escape", owner)
    assert status == 200 and not body["available"]
    inner = root / "inner"
    inner.mkdir()
    (inner / "safe.txt").write_text("safe")
    original = session_files.ignored

    def swap(base, paths):
        result = original(base, paths)
        inner.rename(root / "original")
        inner.symlink_to(outside, target_is_directory=True)
        return result

    monkeypatch.setattr(session_files, "ignored", swap)
    status, body = dispatch(server, "GET", "/sessions/a/files?path=inner", owner)
    assert status == 409 and "private.txt" not in json.dumps(body)


def test_lazy_pages_and_changed_directory_cursor(app, tmp_path, monkeypatch):
    server, _, owner, root = root_for(app, tmp_path)
    monkeypatch.setattr(session_files, "PAGE_SIZE", 2)
    monkeypatch.setattr(session_files, "MAX_ENTRIES", 4)
    monkeypatch.setattr(session_files, "ignored", lambda *args: set())
    for i in range(6):
        (root / str(i)).mkdir()
        (root / str(i) / "never-read.txt").write_text("deep")
    original = os.scandir
    scanned = []

    def scan(directory):
        assert isinstance(directory, int)
        scanned.append(os.fstat(directory).st_ino)
        return original(directory)

    monkeypatch.setattr(os, "scandir", scan)
    first = dispatch(server, "GET", "/sessions/a/files", owner)[1]
    assert len(first["entries"]) == 2 and first["next_cursor"]
    second = dispatch(server, "GET", "/sessions/a/files?cursor=" + first["next_cursor"], owner)[1]
    assert len(second["entries"]) == 2 and names(first).isdisjoint(names(second))
    assert second["truncated"] and second["limit_reached"] and second["next_cursor"] is None
    assert scanned == [root.stat().st_ino] * 2
    (root / "new.txt").write_text("changed")
    assert (
        dispatch(server, "GET", "/sessions/a/files?cursor=" + first["next_cursor"], owner)[0] == 409
    )


def test_owner_auth_methods_remote_and_missing_root_do_not_fall_back(app, tmp_path, monkeypatch):
    server, agents, owner, root = root_for(app, tmp_path)
    for headers, status in (
        ({}, 401),
        (agents["a"], 403),
    ):
        assert dispatch(server, "GET", "/sessions/a/files", headers)[0] == status
    writer = Writer()

    async def cross_origin():
        await server._dispatch(
            "GET",
            "/sessions/a/files",
            asyncio.StreamReader(),
            writer,
            {**owner, "origin": "https://evil.test"},
            b"",
        )

    asyncio.run(cross_origin())
    assert writer.data.split(b"\r\n", 1)[0].split()[1] == b"403"
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        assert dispatch(server, method, "/sessions/a/files", owner)[0] == 405
    monkeypatch.setattr(session_files, "listing", lambda *args: pytest.fail("filesystem read"))
    status, body = dispatch(server, "GET", "/sessions/~remote~host~a/files", owner)
    assert status == 200 and body["reason"] == "remote_not_browsable"
    assert dispatch(server, "GET", "/sessions/absent/files", owner)[0] == 404
    set_paths(server, cwd="")
    assert (
        dispatch(server, "GET", "/sessions/a/files", owner)[1]["reason"] == "directory_not_recorded"
    )
    set_paths(server, cwd="relative")
    assert (
        dispatch(server, "GET", "/sessions/a/files", owner)[1]["reason"] == "directory_not_recorded"
    )


def test_missing_root_preserves_saved_artifact_access(app, tmp_path):
    server, agents, owner, root = root_for(app, tmp_path)
    file = root / "saved.md"
    file.write_text("keep")
    saved = register(server, agents["a"], artifacts.registration(file, kind="report"))[1][
        "artifact"
    ]
    file.unlink()
    root.rmdir()
    status, body = dispatch(server, "GET", "/sessions/a/files", owner)
    assert status == 200 and body["reason"] == "directory_missing"
    assert body["artifacts"][0]["id"] == saved["id"]
    assert body["artifacts"][0]["content_only"]


def test_git_unavailable_is_explicit(app, tmp_path, monkeypatch):
    server, _, owner, root = root_for(app, tmp_path)
    (root / "file").write_text("x")

    def missing(*args, **kwargs):
        raise FileNotFoundError()

    monkeypatch.setattr(subprocess, "run", missing)
    assert dispatch(server, "GET", "/sessions/a/files", owner)[0] == 503


def test_changed_session_root_cannot_return_previous_listing(app, tmp_path, monkeypatch):
    server, _, owner, root = root_for(app, tmp_path)
    original = session_files.listing

    async def worker(fn, *args):
        result = fn(*args)
        set_paths(server, cwd=str(tmp_path / "other"))
        return result

    monkeypatch.setattr(asyncio, "to_thread", worker)
    monkeypatch.setattr(session_files, "listing", original)
    assert dispatch(server, "GET", "/sessions/a/files", owner)[0] == 409


@pytest.mark.parametrize(
    "name,data,media",
    [
        ("report.md", b"# Saved", "text/markdown"),
        ("view.html", b"<script>alert(1)</script>", "text/html"),
        ("code.py", b"print('hello')", "text/plain"),
        ("data.bin", b"\x00\xff", "application/octet-stream"),
        ("bad.txt", b"\xff", "application/octet-stream"),
    ],
)
def test_content_is_complete_authenticated_json(app, tmp_path, name, data, media):
    server, agents, owner, root = root_for(app, tmp_path)
    (root / name).write_bytes(data)
    url = "/sessions/a/file-content?path=" + quote(name)
    assert dispatch(server, "GET", url, {})[0] == 401
    assert dispatch(server, "GET", url, agents["a"])[0] == 403
    status, body = dispatch(server, "GET", url, owner)
    assert status == 200 and body["available"]
    assert body["media_type"] == media and body["size"] == len(data)
    assert base64.b64decode(body["content_base64"]) == data
    assert body["name"] == name and body["path"] == name
    assert "<script>" not in json.dumps(body)


def test_content_limits_ignored_files_and_symlinks(app, tmp_path, monkeypatch):
    server, _, owner, root = root_for(app, tmp_path)
    monkeypatch.setattr(session_files, "MAX_FILE_BYTES", 3)
    file = root / "file.txt"
    file.write_bytes(b"four")
    status, body = dispatch(server, "GET", "/sessions/a/file-content?path=file.txt", owner)
    assert status == 200 and body["reason"] == "file_too_large"
    assert body["size"] == 4 and body["content_limit"] == 3
    assert "content_base64" not in body
    assert (
        dispatch(server, "GET", "/sessions/a/files", owner)[1]["entries"][0]["browsable"] is False
    )
    outside = tmp_path / "outside.txt"
    outside.write_text("x")
    (root / "link.txt").symlink_to(outside)
    assert not dispatch(server, "GET", "/sessions/a/file-content?path=link.txt", owner)[1][
        "available"
    ]
    (root / ".gitignore").write_text("secret.txt\n")
    (root / "secret.txt").write_text("x")
    assert dispatch(server, "GET", "/sessions/a/file-content?path=secret.txt", owner)[0] == 403
    assert dispatch(server, "GET", "/sessions/a/file-content?path=../outside.txt", owner)[0] == 400
    assert (
        dispatch(server, "GET", "/sessions/a/file-content?path=missing.txt", owner)[1]["reason"]
        == "file_missing"
    )
    assert dispatch(server, "GET", "/sessions/a/file-content?path=", owner)[0] == 400


def test_file_change_during_content_read_refuses_stale_bytes(app, tmp_path, monkeypatch):
    server, _, owner, root = root_for(app, tmp_path)
    file = root / "file.txt"
    file.write_text("original")

    def changed(*args):
        file.write_text("changed content")
        return set()

    monkeypatch.setattr(session_files, "ignored", changed)
    status, body = dispatch(server, "GET", "/sessions/a/file-content?path=file.txt", owner)
    assert status == 409 and "content_base64" not in body


@pytest.mark.parametrize("query", ["path=a&path=b", "cursor=wrong", "extra=1"])
def test_invalid_query_is_rejected(app, tmp_path, query):
    server, _, owner, _ = root_for(app, tmp_path)
    assert dispatch(server, "GET", "/sessions/a/files?" + query, owner)[0] == 400


def test_listing_does_not_block_the_server_event_loop(app, tmp_path, monkeypatch):
    import threading

    server, _, owner, root = root_for(app, tmp_path)
    entered, released = threading.Event(), threading.Event()
    original = session_files.listing

    def slow(*args):
        entered.set()
        assert released.wait(5)
        return original(*args)

    monkeypatch.setattr(session_files, "listing", slow)

    async def run():
        writer = Writer()
        task = asyncio.create_task(
            server._dispatch("GET", "/sessions/a/files", asyncio.StreamReader(), writer, owner, b"")
        )
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            assert not task.done()
            await asyncio.sleep(0)
        finally:
            released.set()
        await asyncio.wait_for(task, 5)
        assert json.loads(writer.data.split(b"\r\n\r\n", 1)[1])["available"]

    asyncio.run(run())


@pytest.mark.parametrize("route", ["files", "file-content?path=safe.txt"])
def test_replaced_root_ancestor_cannot_redirect_reads(app, tmp_path, monkeypatch, route):
    server, _, owner = app
    parent = tmp_path / "workspace-parent"
    root = parent / "project"
    root.mkdir(parents=True)
    (root / "safe.txt").write_text("safe")
    outside = tmp_path / "outside"
    (outside / "project").mkdir(parents=True)
    (outside / "project" / "outside.txt").write_text("outside")
    (outside / "project" / "safe.txt").write_text("outside synthetic bytes")
    set_paths(server, cwd=str(root))
    original = session_files.directory

    def swapped(base, relative):
        parent.rename(tmp_path / "original-parent")
        parent.symlink_to(outside, target_is_directory=True)
        return original(base, relative)

    monkeypatch.setattr(session_files, "directory", swapped)
    status, body = dispatch(server, "GET", "/sessions/a/" + route, owner)
    assert status == 200 and not body["available"]
    assert "outside.txt" not in json.dumps(body) and "content_base64" not in body


@pytest.mark.parametrize("path", [".GIT", "child/.GiT"])
def test_git_case_aliases_cannot_be_browsed_or_marked_present(app, tmp_path, path):
    server, agents, owner, root = root_for(app, tmp_path)
    reserved = root / path
    reserved.mkdir(parents=True)
    config = reserved / "config.txt"
    config.write_text("synthetic repository metadata")
    saved = register(server, agents["a"], artifacts.registration(config, kind="report"))[1][
        "artifact"
    ]
    parent = str(reserved.parent.relative_to(root))
    status, body = dispatch(server, "GET", "/sessions/a/files?path=" + quote(parent), owner)
    assert status == 200 and reserved.name not in names(body)
    metadata = next(a for a in body["artifacts"] if a["id"] == saved["id"])
    assert metadata["source_state"] == "unavailable" and metadata["content_only"]
    assert dispatch(server, "GET", "/sessions/a/files?path=" + quote(path), owner)[0] == 400
    assert (
        dispatch(
            server, "GET", "/sessions/a/file-content?path=" + quote(path + "/config.txt"), owner
        )[0]
        == 400
    )
    assert dispatch(server, "GET", f"/sessions/a/artifacts/{saved['id']}", owner)[0] == 200


@pytest.mark.parametrize("route", ["files?path=.GIT", "file-content?path=.GIT/config"])
def test_mac_case_alias_of_git_metadata_is_refused(app, tmp_path, route):
    server, _, owner, root = root_for(app, tmp_path)
    subprocess.run(["git", "init", "--quiet", str(root)], check=True)
    if not (root / ".GIT").exists():
        pytest.skip("requires a case-insensitive filesystem")
    status, body = dispatch(server, "GET", "/sessions/a/" + route, owner)
    assert status == 400 and "content_base64" not in body
