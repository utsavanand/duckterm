"""Ownership failures must be read-only; callbacks and cleanup stay in one home."""

import io
import json
import os
import socket
from pathlib import Path

import pytest

from duckterm import cli, connectors, editions
from duckterm.helpers import instance, profile_state, session_credentials
from duckterm.helpers.desktop_cli import apply_app_profile
from duckterm.server import Server, _acquire_home_lock, _release_home_lock


@pytest.fixture(autouse=True)
def context(monkeypatch, tmp_path):
    original = {k: v for k, v in os.environ.items() if k.startswith("DUCKTERM_")}
    for name in list(os.environ):
        if name.startswith("DUCKTERM_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DUCKTERM_NO_KEYCHAIN", "1")
    monkeypatch.setenv("DUCKTERM_NO_BROWSER", "1")
    monkeypatch.setenv("DUCKTERM_NO_TERMINAL", "1")
    monkeypatch.setenv("DUCKTERM_SUMMARIZER", "off")
    yield
    # CLI profile application intentionally mutates the process environment.
    # Restore those direct writes as well as the monkeypatched fixture values.
    monkeypatch.undo()
    for name in list(os.environ):
        if name.startswith("DUCKTERM_"):
            os.environ.pop(name)
    os.environ.update(original)


def select(monkeypatch, name):
    monkeypatch.setenv("DUCKTERM_APP_PROFILE", name)
    apply_app_profile()


def test_app_default_is_complete_and_conflicting_edition_is_rejected(monkeypatch):
    select(monkeypatch, "lite")
    assert instance.home() == Path.home() / ".duckterm-lite"
    assert editions.configured() is editions.LITE
    assert cli.main(["serve", "--edition", "full"]) == 1
    assert not instance.home().exists()


def test_explicit_custom_target_wins_without_changing_edition(monkeypatch):
    monkeypatch.setenv("DUCKTERM_INSTANCE", "developer")
    select(monkeypatch, "lite")
    assert instance.home() == Path.home() / ".duckterm-developer"
    assert instance.configured_profile() is None
    assert editions.configured() is editions.FULL


def test_other_app_cli_retains_verified_enrollment(monkeypatch):
    select(monkeypatch, "code")
    home = instance.home()
    key = "fixture"
    path = session_credentials.credential_path(key)
    session_credentials.write_private(path, {"session_id": key, "token": "fixture-token"})
    session_credentials.write_private(
        path.parent / "server.json",
        {
            "url": instance.server_url(),
            "instance": instance.description(),
        },
    )
    monkeypatch.setenv("DUCKTERM_SESSION_KEY", key)
    monkeypatch.setenv("DUCKTERM_SESSION_TOKEN_FILE", str(path))
    monkeypatch.setenv("DUCKTERM_APP_PROFILE", "lite")
    monkeypatch.setenv("DUCKTERM_URL", "http://127.0.0.1:4300")
    apply_app_profile()
    assert instance.home() == home
    assert instance.server_url() == "http://127.0.0.1:4800"
    assert editions.configured() is editions.CODE


def test_invalid_enrollment_never_falls_back_to_app_default(monkeypatch):
    monkeypatch.setenv("DUCKTERM_SESSION_KEY", "missing")
    monkeypatch.setenv("DUCKTERM_APP_PROFILE", "lite")
    with pytest.raises(ValueError):
        apply_app_profile()
    assert instance.home() == Path.home() / ".duckterm"
    assert not instance.home().exists()


def test_lite_pane_cli_keeps_workspace_without_inbox_enrollment(monkeypatch):
    select(monkeypatch, "lite")
    instance.home().mkdir()
    profile_state.remember_new_home()
    for name, value in session_credentials.launch_env("lite-fixture").items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("DUCKTERM_APP_PROFILE", "full")
    apply_app_profile()
    assert editions.configured() is editions.LITE
    assert instance.server_url() == "http://127.0.0.1:4519"
    assert not session_credentials.credential_path("lite-fixture").exists()


def test_unmarked_named_home_is_not_adopted_even_if_empty(monkeypatch):
    select(monkeypatch, "lite")
    assert profile_state.validate_home() is True
    instance.home().mkdir()
    with pytest.raises(ValueError, match="not owned"):
        profile_state.validate_home()
    assert list(instance.home().iterdir()) == []


def test_profile_marker_is_exclusive_and_recognized(monkeypatch):
    select(monkeypatch, "code")
    assert profile_state.validate_home()
    instance.home().mkdir()
    profile_state.remember_new_home()
    assert profile_state.validate_home() is False
    marker = instance.home() / "profile.json"
    before = marker.read_bytes()
    with pytest.raises(FileExistsError):
        profile_state.remember_new_home()
    assert marker.read_bytes() == before
    marker.write_text('{"format":2,"profile_id":"code","edition":"code"}')
    with pytest.raises(ValueError, match="different or newer"):
        profile_state.validate_home()


def test_full_legacy_home_untouched_and_named_symlink_rejected(monkeypatch):
    legacy = Path.home() / ".duckterm"
    legacy.mkdir()
    (legacy / "edition.json").write_text('{"id":"full"}')
    assert profile_state.validate_home() is False
    select(monkeypatch, "lite")
    instance.home().symlink_to(legacy, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        profile_state.validate_home()
    assert list(legacy.iterdir()) == [legacy / "edition.json"]


def test_occupied_port_does_not_create_state_or_reconcile(monkeypatch):
    monkeypatch.setenv("DUCKTERM_INSTANCE", "custom")
    monkeypatch.setattr(Server, "__init__", lambda *a, **k: pytest.fail("Server constructed"))
    monkeypatch.setattr(cli, "_duckterm_responds", lambda *a: False)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        assert cli._serve("127.0.0.1", listener.getsockname()[1]) == 1
    assert not instance.home().exists()


def test_live_home_lock_is_atomic_even_for_same_process():
    lock = _acquire_home_lock()
    try:
        with pytest.raises(SystemExit, match="already owns"):
            _acquire_home_lock()
        assert int(lock.read_text()) == os.getpid()
    finally:
        _release_home_lock(lock)


def test_restart_refuses_foreign_identity_before_process_discovery(monkeypatch):
    monkeypatch.setenv("DUCKTERM_INSTANCE", "code")
    monkeypatch.setattr(cli, "_duckterm_responds", lambda *a: True)
    monkeypatch.setattr(cli, "_restart_identity_matches", lambda *a: False)
    monkeypatch.setattr(cli, "_listening_pids", lambda *a: pytest.fail("process lookup"))
    assert cli._restart("127.0.0.1", 4800) == 1
    assert not instance.home().exists()


@pytest.mark.parametrize(
    "changed", [None, "home", "instance_id", "tmux_socket", "port", "identity_version"]
)
def test_restart_identity_requires_authenticated_matching_facets(monkeypatch, changed):
    select(monkeypatch, "code")
    instance.home().mkdir()
    (instance.home() / "token").write_text("fixture-token")
    identity = instance.description()
    if changed:
        identity[changed] = "foreign"

    def read(request, timeout):
        assert request.get_header("X-duckterm-token") == "fixture-token"
        return io.BytesIO(json.dumps({"id": "code", "instance": identity}).encode())

    monkeypatch.setattr(cli.urllib.request, "urlopen", read)
    assert cli._restart_identity_matches("127.0.0.1", 4800) is (changed is None)


def test_owned_worktree_containment_in_each_profile(monkeypatch, tmp_path):
    server = object.__new__(Server)
    for iid in ("", "lite", "code", "custom"):
        monkeypatch.setenv("DUCKTERM_INSTANCE", iid)
        root = instance.home() / "worktrees"
        owned = root / "repo" / "branch"
        owned.mkdir(parents=True)
        assert server._worktree_path_of({"worktree_path": str(owned)}) == owned.resolve()
        assert server._worktree_path_of({"worktree_path": str(root)}) is None
        assert (
            server._worktree_path_of(
                {"worktree_path": str(tmp_path / ".duckterm/worktrees-other/repo")}
            )
            is None
        )
        foreign = tmp_path / "foreign-repo"
        foreign.mkdir(exist_ok=True)
        escaped = root / "escape"
        escaped.symlink_to(foreign, target_is_directory=True)
        assert server._worktree_path_of({"worktree_path": str(escaped)}) is None


def test_connector_keys_isolate_custom_homes_and_preserve_full(monkeypatch, tmp_path):
    full = connectors._keychain_service("github")
    assert full == "duckterm-connector-github"
    monkeypatch.setenv("DUCKTERM_INSTANCE", "code")
    code = connectors._keychain_service("github")
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path / "custom-code"))
    custom = connectors._keychain_service("github")
    assert len({full, code, custom}) == 3
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path / ".duckterm"))
    assert connectors._keychain_service("github") == full


def test_session_callback_uses_own_home_token_and_socket(monkeypatch):
    select(monkeypatch, "lite")
    env = session_credentials.launch_env("fixture")
    assert env["DUCKTERM_HOME"] == str(Path.home() / ".duckterm-lite")
    assert env["DUCKTERM_URL"] == "http://127.0.0.1:4519"
    assert env["DUCKTERM_TMUX_SOCKET"] == "duckterm-lite"
    assert (
        Path(env["DUCKTERM_SESSION_TOKEN_FILE"]).parent == instance.home() / "session-credentials"
    )


@pytest.mark.parametrize("name", ["code", "full"])
def test_private_native_update_does_not_offer_public_lite(monkeypatch, name):
    from duckterm import update_status

    select(monkeypatch, name)
    monkeypatch.setattr(
        update_status.urllib.request, "urlopen", lambda *a, **k: pytest.fail("public fetch")
    )
    result = update_status.status()
    assert result["latest_version"] is None
    assert result["update_available"] is None
    assert result["install_available"] is False
    assert "invite-only" in result["reason"]
    assert "github.com" not in result["release_url"]


@pytest.mark.parametrize("has_app", [True, False])
def test_lite_update_requires_matching_native_asset(monkeypatch, has_app):
    from duckterm import update_status

    select(monkeypatch, "lite")
    monkeypatch.setattr(
        update_status.build_info, "installed", lambda: {"installed_is_release": True}
    )
    asset = "DuckTerm-Lite-99.0.1-macos-arm64.zip"
    payload = {"tag_name": "v99.0.1", "assets": [{"name": asset}] if has_app else []}
    monkeypatch.setattr(
        update_status.urllib.request,
        "urlopen",
        lambda *a, **k: io.BytesIO(json.dumps(payload).encode()),
    )
    result = update_status.status()
    assert result["update_available"] is (True if has_app else None)
    assert result["release_url"].endswith(asset) is has_app


def test_native_uninstall_keeps_shared_cli_and_other_editions(monkeypatch):
    from duckterm import uninstall_plan

    select(monkeypatch, "lite")
    monkeypatch.setattr(uninstall_plan, "installable_agents", lambda: [])
    result = uninstall_plan.inventory(instance.home(), [])
    steps = result["remaining_manual_steps_after_cleanup"]
    assert "DuckTerm Lite.app" in steps[0]
    assert not any("pipx uninstall" in step for step in steps)
    assert result["data"]["action"] == "keep"
    assert not instance.home().exists()


def test_restart_nonduckterm_collision_does_not_open_db(monkeypatch):
    monkeypatch.setattr(cli, "_duckterm_responds", lambda *a: False)
    monkeypatch.setattr(Server, "__init__", lambda *a, **k: pytest.fail("DB opened"))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        assert cli._restart("127.0.0.1", listener.getsockname()[1]) == 1
    assert not instance.home().exists()


def test_restart_mismatched_pidfile_leaves_listener_running(monkeypatch):
    monkeypatch.setattr(cli, "_duckterm_responds", lambda *a: True)
    monkeypatch.setattr(cli, "_restart_identity_matches", lambda *a: True)
    monkeypatch.setattr(cli, "_listening_pids", lambda *a: {43})
    monkeypatch.setattr(cli.os, "kill", lambda *a: pytest.fail("foreign process signalled"))
    instance.home().mkdir()
    (instance.home() / "server.pid").write_text("42")
    assert cli._restart("127.0.0.1", 4300) == 1
    assert (instance.home() / "server.pid").read_text() == "42"


def test_workspace_cli_cannot_create_an_unmarked_desktop_home(monkeypatch):
    select(monkeypatch, "code")
    assert cli.main(["purge-test"]) == 1
    assert not instance.home().exists()
    instance.home().mkdir()
    (instance.home() / "foreign.txt").write_text("keep")
    assert cli.main(["install-hooks", "--global", "--agent", "claude-code"]) == 1
    assert list(instance.home().iterdir()) == [instance.home() / "foreign.txt"]


def test_global_cli_cannot_change_a_desktop_owned_home_to_another_edition(monkeypatch):
    select(monkeypatch, "code")
    instance.home().mkdir()
    profile_state.remember_new_home()
    marker = (instance.home() / "profile.json").read_bytes()
    monkeypatch.delenv("DUCKTERM_DESKTOP_PROFILE")
    monkeypatch.delenv("DUCKTERM_APP_PROFILE")
    monkeypatch.delenv("DUCKTERM_EDITION")
    assert instance.configured_profile()["profile_id"] == "code"
    monkeypatch.setattr(cli, "_restart", lambda *a, **k: pytest.fail("foreign restart"))
    assert cli.main(["restart", "--edition", "full"]) == 1
    assert (instance.home() / "profile.json").read_bytes() == marker
