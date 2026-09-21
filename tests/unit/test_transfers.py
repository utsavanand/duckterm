"""Snapshots preserve Git state and never overwrite or launch on an ambiguous retry."""

import base64
import io
import os
import tarfile
import uuid
from pathlib import Path

import pytest

from duckterm import transfers as t


@pytest.fixture
def homes(tmp_path, monkeypatch):
    local, remote = tmp_path / "local", tmp_path / "remote"
    monkeypatch.setenv("DUCKTERM_HOME", str(local))
    return local, remote


def repo(path: Path) -> None:
    path.mkdir()
    t.git(path, "init", "--quiet")
    t.git(path, "config", "user.name", "Synthetic test")
    t.git(path, "config", "user.email", "test@example.invalid")
    (path / "file.txt").write_text("initial\n")
    (path / "deleted.txt").write_text("delete me")
    (path / ".gitignore").write_text("ignored.txt\n.env\nnode_modules/\n")
    t.git(path, "add", ".")
    t.git(path, "commit", "-qm", "initial")


def deliver(source, dest, homes, monkeypatch, selected=None, identifier=None):
    identifier = identifier or uuid.uuid4().hex
    monkeypatch.setenv("DUCKTERM_HOME", str(homes[0]))
    review = t.scan(source, selected or [])
    state = t.prepare(identifier, str(source), selected or [], review["fingerprint"])
    archive = (homes[0] / "transfers" / identifier / "snapshot.tar").read_bytes()
    monkeypatch.setenv("DUCKTERM_HOME", str(homes[1]))
    t.begin(identifier, str(dest), len(archive), t.digest(archive))
    for offset in range(0, len(archive), t.CHUNK):
        encoded = base64.b64encode(archive[offset : offset + t.CHUNK]).decode()
        t.receive(identifier, offset, encoded)
        t.receive(identifier, offset, encoded)  # safe retry after a lost response
    return identifier, state, t.finish(identifier)


def test_worktree_preserves_head_index_edits_modes_symlinks_and_selected_ignored(
    tmp_path, homes, monkeypatch
):
    root = tmp_path / "repo"
    repo(root)
    source = tmp_path / "work tree 日本語"
    t.git(root, "worktree", "add", "-b", "feature", str(source))
    (source / "file.txt").write_text("staged\n")
    t.git(source, "add", "file.txt")
    (source / "file.txt").write_text("unstaged\n")
    (source / "deleted.txt").unlink()
    (source / "new script.sh").write_text("#!/bin/sh\necho test\n")
    (source / "new script.sh").chmod(0o755)
    (source / "link").symlink_to("file.txt")
    (source / "ignored.txt").write_text("required data")
    (source / ".env").write_text("SYNTHETIC_SECRET=excluded")
    dest = tmp_path / "destination"
    identifier, _, state = deliver(source, dest, homes, monkeypatch, ["ignored.txt"])
    assert state["stage"] == "ready"
    assert (dest / ".git").is_dir()
    for args in [("rev-parse", "HEAD"), ("diff", "--cached", "--binary"), ("diff", "--binary")]:
        assert t.git(source, *args) == t.git(dest, *args)
    assert (dest / "ignored.txt").read_text() == "required data"
    assert (dest / "new script.sh").stat().st_mode & 0o111
    assert os.readlink(dest / "link") == "file.txt"
    assert not (dest / ".env").exists()
    assert t.finish(identifier) == state
    assert (source / ".env").exists()


def test_changed_source_requires_new_review(tmp_path, homes):
    source = tmp_path / "project"
    source.mkdir()
    (source / "a").write_text("before")
    review = t.scan(source, [])
    (source / "a").write_text("after")
    with pytest.raises(ValueError, match="changed after review"):
        t.prepare(uuid.uuid4().hex, str(source), [], review["fingerprint"])


@pytest.mark.parametrize("kind", ["symlink", "submodule", "lfs", "credential", "tracked-exclusion"])
def test_rejects_unsupported_or_sensitive_project(tmp_path, homes, kind):
    source = tmp_path / "project"
    repo(source)
    if kind == "symlink":
        (source / "escape").symlink_to(tmp_path)
    elif kind == "submodule":
        (source / ".gitmodules").write_text("submodule")
    elif kind == "lfs":
        (source / ".gitattributes").write_text("*.bin filter=lfs")
    elif kind == "credential":
        (source / "unexpected.txt").write_text("-----BEGIN PRIVATE KEY-----")
    else:
        (source / "node_modules").mkdir()
        (source / "node_modules" / "tracked").write_text("tracked")
        t.git(source, "add", "-f", "node_modules/tracked")
    with pytest.raises(ValueError):
        t.scan(source, [])


def test_existing_destination_is_never_overwritten(tmp_path, homes):
    dest = tmp_path / "occupied"
    dest.mkdir()
    (dest / "keep").write_text("unchanged")
    with pytest.raises(ValueError, match="exists"):
        t.begin(uuid.uuid4().hex, str(dest), 100, "a" * 64)
    stage = tmp_path / "stage"
    stage.mkdir()
    with pytest.raises(OSError):
        t.publish(stage, dest)
    assert (dest / "keep").read_text() == "unchanged"


def test_receive_recovers_unjournaled_chunk_and_rejects_changed_retry(tmp_path, homes):
    identifier = uuid.uuid4().hex
    payload = b"snapshot"
    t.begin(identifier, str(tmp_path / "new"), len(payload), t.digest(payload))
    path = homes[0] / "transfers" / identifier / "incoming.tar"
    path.write_bytes(payload)  # crash after fsync but before journal update
    state = t.receive(identifier, 0, base64.b64encode(payload).decode())
    assert state["offset"] == len(payload)
    with pytest.raises(ValueError, match="does not match"):
        t.receive(identifier, 0, base64.b64encode(b"different").decode())


def test_archive_tampering_and_path_traversal_are_rejected(tmp_path, homes):
    identifier = uuid.uuid4().hex
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w") as tar:
        info = tarfile.TarInfo("../../escape")
        info.type = tarfile.SYMTYPE
        info.linkname = "/tmp/escape"
        tar.addfile(info)
    data = out.getvalue()
    t.begin(identifier, str(tmp_path / "new"), len(data), t.digest(data))
    t.receive(identifier, 0, base64.b64encode(data).decode())
    with pytest.raises(ValueError, match="unsafe"):
        t.finish(identifier)
    assert not (tmp_path / "new").exists()


def test_publish_crash_recovers_without_copying_again(tmp_path, homes, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "file").write_text("data")
    dest = tmp_path / "dest"
    identifier, _, _ = deliver(source, dest, homes, monkeypatch)
    with t.locked(identifier) as directory:
        state = t.read_state(directory)
        t.save(directory, {**state, "stage": "publishing"})
    assert t.finish(identifier)["stage"] == "ready"
    assert (dest / "file").read_text() == "data"


def test_launch_claim_is_durable_and_cannot_be_replaced(tmp_path, homes, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "file").write_text("data")
    identifier, _, _ = deliver(source, tmp_path / "dest", homes, monkeypatch)
    monkeypatch.setattr(t, "check_runtime", lambda *args: "synthetic")
    first = t.claim_launch(identifier, "synthetic", "test", "hello")
    second = t.claim_launch(identifier, "another", "changed", "bad")
    assert first == second
    assert t.status(identifier)["stage"] == "launching"


@pytest.mark.parametrize(
    "url",
    [
        "https://token@github.com/org/repo",
        "file:///tmp/project",
        "ext::command",
        "-oProxyCommand=bad",
        "https://github.com/repo?token=secret",
    ],
)
def test_clone_refuses_credentials_and_untrusted_transports(url):
    with pytest.raises(ValueError):
        t.check_url(url)


def test_operation_ids_cannot_escape_storage(homes):
    with pytest.raises(ValueError):
        t.status("../../other")


def test_no_disk_space_prevents_transfer(tmp_path, homes, monkeypatch):
    monkeypatch.setattr(t.shutil, "disk_usage", lambda _: type("Disk", (), {"free": 0})())
    with pytest.raises(ValueError, match="disk"):
        t.begin(uuid.uuid4().hex, str(tmp_path / "dest"), 1024, "a" * 64)


def test_exact_conversation_is_required_without_latest_fallback(tmp_path, homes, monkeypatch):
    from duckterm.runtimes.claude_code import ClaudeCodeRuntime

    monkeypatch.setattr(
        ClaudeCodeRuntime, "latest_transcript", lambda **kwargs: pytest.fail("must not guess")
    )
    with pytest.raises(ValueError, match="exact provider"):
        t.conversation({"runtime": "claude-code", "cwd": str(tmp_path)}, None)
    with pytest.raises(ValueError, match="unavailable"):
        t.conversation({"runtime": "claude-code", "cwd": str(tmp_path)}, str(uuid.uuid4()))


def test_project_declaring_macos_only_is_rejected_for_linux(tmp_path, homes, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "package.json").write_text('{"os":["darwin"]}')
    review = t.scan(source, [])
    assert review["linux_compatible"] is False
    monkeypatch.setattr(t.sys, "platform", "linux")
    with pytest.raises(ValueError, match="Linux is unsupported"):
        t.check_requirements(review["requirements"], review["linux_compatible"])


def test_clone_journal_recovers_after_publishing(tmp_path, homes):
    identifier = uuid.uuid4().hex
    destination = tmp_path / "cloned"
    destination.mkdir()
    (destination / ".duckterm-transfer-id").write_text(identifier)
    with t.locked(identifier) as directory:
        t.save(
            directory,
            {
                "id": identifier,
                "stage": "publishing",
                "destination": str(destination),
                "url": "https://example.invalid/repo.git",
                "branch": "main",
            },
        )
    assert (
        t.clone(identifier, "https://example.invalid/repo.git", "main", str(destination))["stage"]
        == "ready"
    )


def test_clone_includes_committed_branch_without_local_edits(tmp_path, homes, monkeypatch):
    root = tmp_path / "origin"
    repo(root)
    t.git(root, "checkout", "-b", "feature")
    (root / "file.txt").write_text("uncommitted source edits\n")
    # Use a local transport fixture; production URL validation is tested separately.
    monkeypatch.setattr(t, "check_url", lambda url: None)
    identifier = uuid.uuid4().hex
    dest = tmp_path / "cloned"
    state = t.clone(identifier, str(root), "feature", str(dest))
    assert state["stage"] == "ready"
    assert (dest / "file.txt").read_text() == "initial\n"
    assert t.git(dest, "branch", "--show-current").strip() == b"feature"
    assert t.clone(identifier, str(root), "feature", str(dest)) == state


def test_snapshot_reader_never_follows_replaced_parent(tmp_path, homes):
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "credential").write_text("synthetic-private-value")
    (root / "replaced").symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError):
        t.read_project_file(root, "replaced/credential")
