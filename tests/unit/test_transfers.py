"""Snapshots preserve Git state and never overwrite or launch on an ambiguous retry."""

import base64
import io
import os
import shlex
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


@pytest.mark.parametrize("cloned", [False, True])
def test_key_marker_literals_in_source_are_not_private_keys(tmp_path, homes, cloned):
    source = tmp_path / "project"
    repo(source)
    # Reproduce the repository's own scanner and negative-test literals.
    for name in ("transfers.py", "test_transfers.py"):
        content = 'marker = b"-----BEGIN PRIVATE KEY-----"\n'
        (source / name).write_text(content)
    review = t.scan(source, [], cloned=cloned)
    assert {e["path"] for e in review["entries"]} >= {"transfers.py", "test_transfers.py"}


@pytest.mark.parametrize("kind", ["PRIVATE KEY", "OPENSSH PRIVATE KEY", "RSA PRIVATE KEY"])
@pytest.mark.parametrize("style", ["standalone", "indented", "embedded", "escaped"])
def test_private_key_material_is_still_blocked(tmp_path, homes, kind, style):
    source = tmp_path / "project"
    source.mkdir()
    header = "-----BEGIN " + kind + "-----"
    payload = "QUJD" * 24  # Synthetic bytes only; no usable private key.
    content = header
    if style == "indented":
        content = "  " + header + "\r\n" + payload
    elif style == "embedded":
        content = 'value = """' + header + "\n" + payload + '\n"""'
    elif style == "escaped":
        content = 'value = "' + header + r"\n" + payload + '"'
    (source / "unexpected.txt").write_text(content)
    with pytest.raises(ValueError, match="Private key detected"):
        t.scan(source, [])


@pytest.mark.parametrize("cloned", [False, True])
@pytest.mark.parametrize("kind", ["PRIVATE KEY", "OPENSSH PRIVATE KEY", "RSA PRIVATE KEY"])
@pytest.mark.parametrize(
    "prefix,suffix",
    [
        ("# ", ""),
        ("// ", ""),
        ("-- ", ""),
        ("% ", ""),
        ("> ", ""),
        ("<!-- ", " -->"),
        ("/* ", " */"),
        (" * ", ""),
        ("; ", ""),
        ("key: ", ""),
        ("", " trailing annotation"),
    ],
)
def test_wrapped_private_key_is_blocked(tmp_path, homes, cloned, kind, prefix, suffix):
    source = tmp_path / "project"
    source.mkdir()
    header = "-----BEGIN " + kind + "-----"
    payload = "QUJD" * 24  # Synthetic bytes only, not a usable key.
    (source / "example.txt").write_text(prefix + header + suffix + "\r\n# " + payload)
    with pytest.raises(ValueError, match="Private key detected"):
        t.scan(source, [], cloned=cloned)


@pytest.mark.parametrize("cloned", [False, True])
@pytest.mark.parametrize("quote", [chr(34), chr(39), chr(96)])
def test_only_immediately_quoted_key_markers_are_exempt(tmp_path, homes, cloned, quote):
    source = tmp_path / "project"
    source.mkdir()
    header = "-----BEGIN " + "PRIVATE KEY-----"
    path = source / "example.txt"
    path.write_text("# marker = " + quote + header + quote)
    assert t.scan(source, [], cloned=cloned)["entries"]
    for content in (quote + header, header + quote, quote + header + " " + quote):
        path.write_text(content)
        with pytest.raises(ValueError, match="Private key detected"):
            t.scan(source, [], cloned=cloned)


@pytest.mark.parametrize("cloned", [False, True])
@pytest.mark.parametrize(
    "left,right",
    [(chr(a), chr(b)) for a in (34, 39, 96) for b in (34, 39, 96) if a != b],
)
def test_mismatched_quotes_do_not_exempt_key_headers(tmp_path, homes, cloned, left, right):
    source = tmp_path / "project"
    source.mkdir()
    header = "-----BEGIN " + "PRIVATE KEY-----"
    (source / "example.txt").write_text(left + header + right)
    with pytest.raises(ValueError, match="Private key detected"):
        t.scan(source, [], cloned=cloned)


@pytest.mark.parametrize("cloned", [False, True])
def test_marker_literal_does_not_hide_a_later_wrapped_key(tmp_path, homes, cloned):
    source = tmp_path / "project"
    source.mkdir()
    header = "-----BEGIN " + "PRIVATE KEY-----"
    (source / "example.txt").write_text('marker = "' + header + '"\n> ' + header)
    with pytest.raises(ValueError, match="Private key detected"):
        t.scan(source, [], cloned=cloned)


@pytest.mark.parametrize("name", ["tokens.py", "credentials.ts", "secrets.rs"])
def test_tracked_code_module_names_are_not_credential_exports(tmp_path, homes, name):
    source = tmp_path / "project"
    repo(source)
    (source / name).write_text("// synthetic source module\n")
    t.git(source, "add", name)
    assert name in {e["path"] for e in t.scan(source, [], cloned=True)["entries"]}
    with pytest.raises(ValueError, match="Tracked credential"):
        t.scan(source, [])  # Local-copy policy is unchanged.


@pytest.mark.parametrize(
    "name", ["credentials.json", "secrets.env", "tokens.txt", "id_rsa.py", ".env.py"]
)
def test_clone_still_blocks_tracked_credential_files(tmp_path, homes, name):
    source = tmp_path / "project"
    repo(source)
    (source / name).write_text("synthetic fixture\n")
    t.git(source, "add", "-f", name)
    with pytest.raises(ValueError, match="Tracked credential"):
        t.scan(source, [], cloned=True)


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


def test_copy_uses_exact_selected_empty_folder(tmp_path, homes, monkeypatch):
    source, dest = tmp_path / "source", tmp_path / "my-folder"
    source.mkdir()
    (source / "file.txt").write_text("project")
    dest.mkdir()
    identifier, _, state = deliver(source, dest, homes, monkeypatch)
    assert state["destination"] == str(dest)
    assert (dest / "file.txt").read_text() == "project"
    assert not (dest / "source").exists()
    assert t.finish(identifier) == state


def test_clone_uses_exact_empty_folder_and_retries(tmp_path, homes, monkeypatch):
    import shutil

    from duckterm import github_projects

    source, dest = tmp_path / "source", tmp_path / "chosen-name"
    repo(source)
    dest.mkdir()
    monkeypatch.setattr(
        github_projects, "clone", lambda repo, branch, stage: shutil.copytree(source, stage)
    )
    identifier = uuid.uuid4().hex
    state = t.clone(
        identifier, "https://github.com/fixture/sotto.git", "", str(dest), "fixture/sotto"
    )
    assert state["stage"] == "ready"
    assert (dest / "file.txt").exists()
    assert not (dest / "sotto").exists()
    assert (
        t.clone(identifier, "https://github.com/fixture/sotto.git", "", str(dest), "fixture/sotto")
        == state
    )


@pytest.mark.parametrize("kind", ["file", "hidden", "symlink"])
def test_selected_empty_folder_cannot_overwrite_concurrent_content(tmp_path, kind):
    stage, dest = tmp_path / "stage", tmp_path / "selected"
    stage.mkdir()
    (stage / "project").write_text("new")
    dest.mkdir()
    assert t.destination_path(str(dest)) == dest
    if kind == "symlink":
        dest.rmdir()
        dest.symlink_to(stage, target_is_directory=True)
    else:
        (dest / (".hidden" if kind == "hidden" else "keep")).write_text("unchanged")
    with pytest.raises(ValueError):
        t.destination_path(str(dest))
    with pytest.raises(OSError):
        t.publish(stage, dest, allow_empty=True)
    assert (stage / "project").read_text() == "new"
    if kind != "symlink":
        assert next(dest.iterdir()).read_text() == "unchanged"


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


def test_codex_resume_uses_reviewed_destination_instead_of_recorded_source(
    tmp_path, homes, monkeypatch
):
    monkeypatch.setattr(t.Path, "home", lambda: tmp_path / "provider-home")
    monkeypatch.setattr(t, "check_runtime", lambda *args: "codex")
    identifier, sid = uuid.uuid4().hex, str(uuid.uuid4())
    destination = str(tmp_path / "remote project 日本語")
    with t.locked(identifier) as directory:
        (directory / "conversation.jsonl").write_text("synthetic transcript")
        t.save(
            directory,
            {
                "id": identifier,
                "stage": "ready",
                "destination": destination,
                "conversation": {"runtime": "codex", "id": sid},
            },
        )
    state = t.claim_launch(identifier, "ignored", "Migration", "")
    argv = shlex.split(state["command"])
    assert argv[:2] == ["codex", "resume"]
    assert argv[argv.index("--cd") + 1] == destination
    assert argv[-1] == sid


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


def test_automatic_workspace_uses_destination_home_and_isolates_sessions(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    first, second = uuid.uuid4().hex, uuid.uuid4().hex
    path = t.automatic_workspace(first, "https://github.com/fixture/sotto.git")
    assert path == tmp_path / "projects" / "fixture" / "sotto" / ("session-" + first)
    assert not path.parent.exists()  # Review does not create directories.
    assert t.automatic_workspace(first, "https://github.com/fixture/duckterm.git") != path
    assert t.automatic_workspace(second, "https://github.com/fixture/sotto.git") != path
    assert t.automatic_workspace(first, "git@github.com:fixture/sotto.git") == path
    (tmp_path / "projects").symlink_to(tmp_path.parent, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        t.automatic_workspace(first, "https://github.com/fixture/sotto.git", create=True)


def test_automatic_clone_records_failure_then_retries_same_workspace(tmp_path, homes, monkeypatch):
    import shutil

    from duckterm import github_projects

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    source = tmp_path / "source"
    repo(source)
    identifier = uuid.uuid4().hex
    url = "https://github.com/fixture/sotto.git"
    dest = t.automatic_workspace(identifier, url)

    def fail(*args):
        raise ValueError("Synthetic access denied")

    monkeypatch.setattr(github_projects, "clone", fail)
    with pytest.raises(ValueError, match="Synthetic access denied"):
        t.clone(identifier, url, "", str(dest), "fixture/sotto", True)
    assert t.status(identifier)["stage"] == "failed"
    assert t.status(identifier)["error"] == "Synthetic access denied"
    assert not dest.exists()
    monkeypatch.setattr(github_projects, "clone", lambda r, b, p: shutil.copytree(source, p))
    state = t.clone(identifier, url, "", str(dest), "fixture/sotto", True)
    assert state["stage"] == "ready"
    assert "error" not in state
    assert (dest / "file.txt").exists()
    assert t.clone(identifier, url, "", str(dest), "fixture/sotto", True) == state
    with pytest.raises(ValueError, match="Workspace changed"):
        t.clone(identifier, url, "", str(tmp_path / "elsewhere"), "fixture/sotto", True)


def test_clone_accepts_committed_claude_rules_but_not_private_agent_state(
    tmp_path, homes, monkeypatch
):
    source = tmp_path / "source"
    repo(source)
    rules = source / ".claude" / "rules"
    rules.mkdir(parents=True)
    (rules / "architecture.md").write_text("Project coding conventions")
    t.git(source, "add", ".claude")
    t.git(source, "commit", "-qm", "project rules")
    monkeypatch.setattr(t, "check_url", lambda url: None)
    state = t.clone(uuid.uuid4().hex, str(source), "", str(tmp_path / "checkout"))
    assert state["stage"] == "ready"
    assert (tmp_path / "checkout/.claude/rules/architecture.md").is_file()
    # Local copy retains its stricter transfer review policy.
    with pytest.raises(ValueError, match="excluded directory"):
        t.scan(source, [])
    (source / ".claude" / "credentials.json").write_text('{"synthetic": true}')
    t.git(source, "add", ".claude")
    t.git(source, "commit", "-qm", "private fixture")
    identifier = uuid.uuid4().hex
    with pytest.raises(ValueError, match="excluded directory"):
        t.clone(identifier, str(source), "", str(tmp_path / "blocked"))
    assert t.status(identifier)["stage"] == "failed"
    assert not (tmp_path / "blocked").exists()


def test_automatic_preflight_recovers_ready_operation_without_recloning(
    tmp_path, homes, monkeypatch
):
    import asyncio

    from duckterm import transfer_api

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(t, "check_runtime", lambda *args: None)
    identifier = uuid.uuid4().hex
    url = "https://github.com/fixture/sotto.git"
    req = {
        "id": identifier,
        "url": url,
        "auto_workspace": True,
        "command": "claude",
        "branch": "main",
    }
    result = asyncio.run(transfer_api.dispatch(None, "preflight", req))
    dest = Path(result["destination"])
    assert result["auto_workspace"] is True
    assert not dest.exists()
    dest.mkdir(parents=True)
    (dest / "file.txt").write_text("Existing work")
    with pytest.raises(ValueError, match="already exists"):
        asyncio.run(transfer_api.dispatch(None, "preflight", req))
    with t.locked(identifier) as directory:
        t.save(
            directory, {"stage": "ready", "destination": str(dest), "url": url, "branch": "main"}
        )
    assert asyncio.run(transfer_api.dispatch(None, "preflight", req))["destination"] == str(dest)
    with pytest.raises(ValueError, match="already exists"):
        asyncio.run(transfer_api.dispatch(None, "preflight", {**req, "branch": "different"}))


def test_progress_is_readable_while_clone_holds_operation_lock(homes):
    from concurrent.futures import ThreadPoolExecutor

    identifier = uuid.uuid4().hex
    with ThreadPoolExecutor(max_workers=1) as pool, t.locked(identifier) as directory:
        t.save(directory, {"id": identifier, "stage": "cloning"})
        result = pool.submit(t.status, identifier)
        assert result.result(timeout=1)["stage"] == "cloning"


def test_clone_error_records_failed_state(tmp_path, homes, monkeypatch):
    from duckterm import github_projects

    dest = tmp_path / "empty"
    dest.mkdir()
    identifier = uuid.uuid4().hex

    def fail(*args):
        raise ValueError("Synthetic download failure before git checkout")

    monkeypatch.setattr(github_projects, "clone", fail)
    with pytest.raises(ValueError, match="Synthetic download failure"):
        t.clone(identifier, "https://github.com/fixture/test.git", "", str(dest), "fixture/test")
    assert list(dest.iterdir()) == []
    assert t.status(identifier)["stage"] == "failed"
    assert "error" in t.status(identifier)

    import shutil

    source = tmp_path / "source"
    repo(source)
    monkeypatch.setattr(
        github_projects, "clone", lambda name, branch, stage: shutil.copytree(source, stage)
    )
    recovered = t.clone(
        identifier, "https://github.com/fixture/test.git", "", str(dest), "fixture/test"
    )
    assert recovered["stage"] == "ready"
    assert "error" not in recovered
    assert (dest / "file.txt").is_file()


def test_clone_status_remains_responsive_while_download_runs(tmp_path, homes, monkeypatch):
    import concurrent.futures
    import threading

    from duckterm import github_projects

    entered, release, status_started = threading.Event(), threading.Event(), threading.Event()
    identifier = uuid.uuid4().hex
    dest = tmp_path / "empty"
    dest.mkdir()

    def blocked(*args):
        entered.set()
        assert release.wait(5)
        raise ValueError("Synthetic clone ended")

    def status():
        status_started.set()
        return t.status(identifier)

    monkeypatch.setattr(github_projects, "clone", blocked)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        clone = pool.submit(
            t.clone,
            identifier,
            "https://github.com/fixture/test.git",
            "",
            str(dest),
            "fixture/test",
        )
        assert entered.wait(2)
        pending = pool.submit(status)
        assert status_started.wait(2)
        try:
            assert pending.result(timeout=1)["stage"] == "cloning"
        finally:
            release.set()
        with pytest.raises(ValueError, match="Synthetic clone ended"):
            clone.result(timeout=2)
        assert t.status(identifier)["stage"] == "failed"
