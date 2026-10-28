"""Profile ownership and real shell dispatch, with fake CLIs and no credentials/network."""

import concurrent.futures
import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from duckterm import connectors
from duckterm.agents import connector_dispatch, hook_dispatch, mcp_install
from duckterm.helpers import instance


@pytest.fixture
def home(tmp_path, monkeypatch):
    for name in list(os.environ):
        if name.startswith("DUCKTERM_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DUCKTERM_NO_KEYCHAIN", "1")
    monkeypatch.setattr(connectors, "github_identity", lambda token: "fixture")
    monkeypatch.setattr(connectors, "_gh_cli_token", lambda: "fixture-token")
    monkeypatch.setattr(connectors, "github_server_argv", lambda: ["fixture-mcp"])
    return tmp_path


def select(monkeypatch, edition):
    monkeypatch.setenv("DUCKTERM_INSTANCE", "" if edition == "full" else edition)
    monkeypatch.setenv("DUCKTERM_DESKTOP_PROFILE", edition)
    for name in ("DUCKTERM_HOME", "DUCKTERM_PORT", "DUCKTERM_URL", "DUCKTERM_TMUX_SOCKET"):
        monkeypatch.delenv(name, raising=False)
    instance.home().mkdir(exist_ok=True)


def fake_cli(home, edition, *, label=None, compiled=True):
    path = home / f"Apps/{label or edition}.app/Contents/Resources/bin/duckterm"
    path.parent.mkdir(parents=True)
    path.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' '{edition}' \"$DUCKTERM_HOME\" \"$DUCKTERM_INSTANCE\" "
        '"$DUCKTERM_DESKTOP_PROFILE" "${DUCKTERM_SESSION_KEY:-cleared}" "$1" "$2"\n'
    )
    path.chmod(0o700)
    if compiled:
        path.parents[2].joinpath("Info.plist").write_bytes(
            plistlib.dumps({"DucktermProfile": edition})
        )
    return path


def activate(home, monkeypatch, edition, *, harnesses=None):
    select(monkeypatch, edition)
    path = fake_cli(home, edition)
    monkeypatch.setenv("DUCKTERM_BUNDLED_CLI", str(path))
    connectors.enable("github", source="gh-cli", harnesses=harnesses)
    return path


def config(home, harness):
    if harness == "claude-code":
        return json.loads((home / ".claude.json").read_text())["mcpServers"]
    return tomllib.loads((home / ".codex/config.toml").read_text()).get("mcp_servers", {})


def dispatch(home, context=None, *, extra=None):
    env = {"HOME": str(home), "PATH": "/missing-no-tools"}
    env.update(context or {})
    env.update(extra or {})
    return subprocess.run(
        [str(connector_dispatch.launcher_path()), "connector-run", "github"],
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
    )


def test_code_disconnect_preserves_full_and_deselect_is_local(home, monkeypatch):
    activate(home, monkeypatch, "full")
    activate(home, monkeypatch, "code")
    for harness in connectors.HARNESSES:
        assert mcp_install.managed_installed("github", harness)
        assert config(home, harness)["github"]["command"] == str(connector_dispatch.launcher_path())
    connectors.enable("github", source="gh-cli", harnesses=["claude-code"])
    assert not mcp_install.managed_installed("github", "codex")
    assert "github" in config(home, "codex")  # Full still owns the global entry.
    connectors.disable("github")
    assert connectors.status("github")["enabled"] is False
    select(monkeypatch, "full")
    assert connectors.status("github")["enabled"] is True
    assert all(mcp_install.managed_installed("github", h) for h in connectors.HARNESSES)
    connectors.disable("github")
    assert all("github" not in config(home, h) for h in connectors.HARNESSES)


def test_full_disconnect_does_not_remove_code_registration(home, monkeypatch):
    activate(home, monkeypatch, "full")
    activate(home, monkeypatch, "code")
    select(monkeypatch, "full")
    connectors.disable("github")
    select(monkeypatch, "code")
    assert connectors.status("github")["enabled"] is True
    assert all(mcp_install.managed_installed("github", h) for h in connectors.HARNESSES)


def test_fresh_code_does_not_infer_enabled_or_run_full_policy(home, monkeypatch):
    activate(home, monkeypatch, "full")
    select(monkeypatch, "code")
    monkeypatch.setattr(
        connectors, "_gh_cli_token", lambda: pytest.fail("foreign credential lookup")
    )
    assert connectors.policy("github")["enabled"] is False
    assert connectors._RunnerPolicy("github").read()["enabled"] is False
    assert connectors.status("github")["installed"] == {"claude-code": False, "codex": False}
    with pytest.raises(SystemExit, match="disabled"):
        connectors.run("github")


def test_failed_code_install_cleans_only_code_membership(home, monkeypatch):
    activate(home, monkeypatch, "full")
    before = (home / ".claude.json").read_bytes(), (home / ".codex/config.toml").read_bytes()
    select(monkeypatch, "code")
    monkeypatch.setenv("DUCKTERM_BUNDLED_CLI", str(fake_cli(home, "code")))
    real = mcp_install.managed_install

    def fail(name, harness, *, home=None):
        if harness == "codex":
            raise ValueError("fixture install failure")
        return real(name, harness, home=home)

    monkeypatch.setattr(mcp_install, "managed_install", fail)
    with pytest.raises(ValueError, match="fixture install failure"):
        connectors.enable("github", source="gh-cli")
    assert before == (
        (home / ".claude.json").read_bytes(),
        (home / ".codex/config.toml").read_bytes(),
    )
    select(monkeypatch, "full")
    assert connectors.status("github")["enabled"] is True


@pytest.mark.parametrize("harness", connectors.HARNESSES)
def test_custom_same_name_definition_is_never_overwritten_or_removed(home, monkeypatch, harness):
    select(monkeypatch, "code")
    monkeypatch.setenv("DUCKTERM_BUNDLED_CLI", str(fake_cli(home, "code")))
    writer = mcp_install.claude_install if harness == "claude-code" else mcp_install.codex_install
    path = writer("github", "/custom/github", ["custom-stdio"], env={"CUSTOM": "fixture"})
    before = path.read_bytes()
    with pytest.raises(ValueError, match="custom MCP"):
        connectors.enable("github", source="gh-cli", harnesses=[harness])
    assert path.read_bytes() == before
    connectors.disable("github")
    assert path.read_bytes() == before
    connectors.refresh_runtime_registration()
    assert path.read_bytes() == before


def test_dispatch_has_no_path_dependency_and_only_selects_requested_runtime(home, monkeypatch):
    activate(home, monkeypatch, "full")
    full_context = instance.launch_env()
    activate(home, monkeypatch, "code")
    code_context = instance.launch_env()
    for edition, context in (("full", full_context), ("code", code_context)):
        result = dispatch(home, context, extra={"DUCKTERM_SESSION_KEY": "stale-enrollment"})
        assert result.returncode == 0, result.stderr
        lines = result.stdout.splitlines()
        assert lines == [
            edition,
            context["DUCKTERM_HOME"],
            context["DUCKTERM_INSTANCE"],
            edition,
            "cleared",
            "connector-run",
            "github",
        ]
    assert dispatch(home).stdout.splitlines()[0] == "full"
    assert (
        dispatch(home, {"DUCKTERM_URL": "http://127.0.0.1:4300"}).stdout.splitlines()[0] == "full"
    )
    forwarded = config(home, "codex")["github"]["env_vars"]
    assert set(connector_dispatch.CONTEXT).issubset(forwarded)
    assert "TOKEN" not in " ".join(forwarded)


def test_move_refresh_and_removal_never_select_other_edition(home, monkeypatch):
    activate(home, monkeypatch, "full")
    old = activate(home, monkeypatch, "code")
    context = instance.launch_env()
    before = (home / ".claude.json").read_bytes()
    moved = fake_cli(home, "code", label="Code Moved $HOME `id`")
    shutil.rmtree(old.parents[3])
    assert dispatch(home, context).returncode == 1
    assert dispatch(home).stdout.splitlines()[0] == "full"
    monkeypatch.setenv("DUCKTERM_BUNDLED_CLI", str(moved))
    connectors.refresh_runtime_registration()
    assert dispatch(home, context).stdout.splitlines()[0] == "code"
    assert (home / ".claude.json").read_bytes() == before
    shutil.rmtree(moved.parents[3])
    with pytest.raises(RuntimeError, match="moved or was removed"):
        connectors.refresh_runtime_registration()
    assert dispatch(home, context).returncode == 1
    assert dispatch(home).stdout.splitlines()[0] == "full"


@pytest.mark.parametrize(
    "context",
    [
        {"DUCKTERM_INSTANCE": "code"},
        {"DUCKTERM_HOME": "/custom"},
        {"DUCKTERM_URL": "http://127.0.0.1:4800"},
        {"DUCKTERM_SESSION_KEY": "orphan"},
    ],
)
def test_partial_context_never_falls_back_to_full(home, monkeypatch, context):
    activate(home, monkeypatch, "full")
    result = dispatch(home, context)
    assert result.returncode == 1
    assert not result.stdout


def test_code_startup_never_claims_legacy_full_or_custom_entries(home, monkeypatch):
    old = fake_cli(home, "full", compiled=False)
    path = mcp_install.claude_install("github", str(old), ["connector-run", "github"])
    before = path.read_bytes()
    select(monkeypatch, "code")
    monkeypatch.setenv("DUCKTERM_BUNDLED_CLI", str(fake_cli(home, "code")))
    connectors.refresh_runtime_registration()
    assert path.read_bytes() == before
    assert connectors.policy("github")["enabled"] is False
    select(monkeypatch, "full")
    monkeypatch.setenv("DUCKTERM_BUNDLED_CLI", str(fake_cli(home, "full", label="Moved Full")))
    connectors.refresh_runtime_registration()
    assert config(home, "claude-code")["github"]["command"] == str(
        connector_dispatch.launcher_path()
    )
    assert not (home / ".codex/config.toml").exists()  # No new harness opt-in.
    assert dispatch(home).stdout.splitlines()[0] == "full"


def test_hosted_startup_does_not_register_runtime_or_touch_harness_config(home, monkeypatch):
    config_path = mcp_install.claude_install("github", "duckterm", ["connector-run", "github"])
    before = config_path.read_bytes()
    monkeypatch.setenv("DUCKTERM_HOSTED", "1")
    monkeypatch.setattr(connectors, "_duckterm_bin", lambda: pytest.fail("desktop CLI resolved"))
    connectors.refresh_runtime_registration()
    assert config_path.read_bytes() == before
    assert not connector_dispatch.launcher_path().parent.exists()


def test_code_connect_preserves_existing_legacy_full_runtime(home, monkeypatch):
    old = fake_cli(home, "full", compiled=False)
    mcp_install.claude_install("github", str(old), ["connector-run", "github"])
    activate(home, monkeypatch, "code", harnesses=["claude-code"])
    assert dispatch(home).stdout.splitlines()[0] == "full"
    connectors.disable("github")
    assert dispatch(home).stdout.splitlines()[0] == "full"
    assert "github" in config(home, "claude-code")


def test_compiled_code_cli_cannot_be_registered_for_full(home, monkeypatch):
    select(monkeypatch, "full")
    with pytest.raises(ValueError, match="another desktop profile"):
        connector_dispatch.register_runtime(str(fake_cli(home, "code")))


def test_repointing_shared_cli_shim_never_changes_full_runtime(home, monkeypatch):
    full = fake_cli(home, "full")
    code = fake_cli(home, "code")
    shim = home / ".local/bin/duckterm"
    shim.parent.mkdir(parents=True)
    shim.symlink_to(full)
    select(monkeypatch, "full")
    connector_dispatch.register_runtime(str(shim))
    shim.unlink()
    shim.symlink_to(code)
    assert dispatch(home).stdout.splitlines()[0] == "full"
    full.unlink()
    assert dispatch(home).returncode == 1


def test_concurrent_profile_registration_keeps_both_members(home, monkeypatch):
    full = fake_cli(home, "full")
    code = fake_cli(home, "code")
    source = str(Path(connectors.__file__).parents[1])
    snippet = "from duckterm import connectors; connectors._install('github')"

    def install(edition, cli):
        env = dict(
            os.environ,
            PYTHONPATH=source,
            DUCKTERM_INSTANCE="" if edition == "full" else edition,
            DUCKTERM_DESKTOP_PROFILE=edition,
            DUCKTERM_BUNDLED_CLI=str(cli),
        )
        return subprocess.run(
            [sys.executable, "-c", snippet], env=env, capture_output=True, timeout=20
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(install, "full", full), pool.submit(install, "code", code)]
        results = [future.result() for future in futures]
    assert all(result.returncode == 0 for result in results), [result.stderr for result in results]
    for harness in connectors.HARNESSES:
        path = mcp_install._managed_path(harness, None)
        assert (
            len(hook_dispatch.registered_profiles(path, f"mcp:{harness}:github", legacy=False)) == 2
        )


def test_trusted_home_alias_keeps_registration_and_dispatch(home, monkeypatch):
    real = home / "actual-home"
    real.mkdir()
    alias = home / "home-alias"
    alias.symlink_to(real, target_is_directory=True)
    monkeypatch.setenv("HOME", str(alias))
    activate(alias, monkeypatch, "full")
    activate(alias, monkeypatch, "code")
    context = instance.launch_env()
    context["DUCKTERM_HOME"] = str(alias / ".duckterm-code")
    assert dispatch(alias, context).stdout.splitlines()[0] == "code"
    monkeypatch.setenv("HOME", str(real))
    assert connectors.status("github")["enabled"] is True
    connectors.disable("github")
    select(monkeypatch, "full")
    assert connectors.status("github")["enabled"] is True
    assert dispatch(real).stdout.splitlines()[0] == "full"


def test_shared_launcher_refuses_modifications_and_preserves_newer_revision(home, monkeypatch):
    activate(home, monkeypatch, "full")
    path = connector_dispatch.launcher_path()
    original = path.read_bytes()
    path.write_bytes(original + b"# modified\n")
    with pytest.raises(ValueError, match="modified"):
        connectors.refresh_runtime_registration()
    newer = path.read_bytes()
    receipt_path = path.parent / "assets.json"
    receipt = json.loads(receipt_path.read_text())
    receipt.update(
        revision=connector_dispatch.REVISION + 1, sha256=hashlib.sha256(newer).hexdigest()
    )
    receipt_path.write_text(json.dumps(receipt))
    connectors.refresh_runtime_registration()
    assert path.read_bytes() == newer
    assert json.loads(receipt_path.read_text())["revision"] == connector_dispatch.REVISION + 1


def test_unrelated_entries_options_and_external_login_files_stay_intact(home, monkeypatch):
    full = fake_cli(home, "full", compiled=False)
    claude = {
        "mcpServers": {
            "github": {"command": str(full), "args": ["connector-run", "github"], "timeout": 42},
            "custom": {"command": "custom", "env": {"CUSTOM": "fixture"}},
        },
        "userSetting": "fixture",
    }
    (home / ".claude.json").write_text(json.dumps(claude))
    codex = home / ".codex/config.toml"
    codex.parent.mkdir()
    codex.write_text(
        '# keep this comment\nmodel="fixture"\n'
        '[mcp_servers.custom]\ncommand="custom"\n'
        "[mcp_servers.github]\ncommand=" + json.dumps(str(full)) + "\n"
        'args=["connector-run", "github"]\nstartup_timeout_sec=42\n'
        'env_vars=["CUSTOM_CONTEXT"]\n'
    )
    external = home / ".config/gh/hosts.yml"
    external.parent.mkdir(parents=True)
    external.write_text("fixture provider login configuration\n")
    before_external = external.read_bytes()
    activate(home, monkeypatch, "code")
    connectors.disable("github")
    assert json.loads((home / ".claude.json").read_text())["userSetting"] == "fixture"
    assert config(home, "claude-code")["custom"] == claude["mcpServers"]["custom"]
    assert config(home, "claude-code")["github"]["timeout"] == 42
    assert config(home, "codex")["github"]["startup_timeout_sec"] == 42
    assert "CUSTOM_CONTEXT" in config(home, "codex")["github"]["env_vars"]
    assert '# keep this comment\nmodel="fixture"\n' in codex.read_text()
    assert external.read_bytes() == before_external


def test_legacy_runner_rechecks_membership_even_when_shared_config_is_unchanged(home, monkeypatch):
    activate(home, monkeypatch, "full")
    connectors._policy_path("github").unlink()
    activate(home, monkeypatch, "code")
    select(monkeypatch, "full")
    watcher = connectors._RunnerPolicy("github")
    assert watcher.read()["enabled"] is True
    before = (home / ".claude.json").read_bytes(), (home / ".codex/config.toml").read_bytes()
    for harness in connectors.HARNESSES:
        mcp_install.managed_remove("github", harness)
    assert before == (
        (home / ".claude.json").read_bytes(),
        (home / ".codex/config.toml").read_bytes(),
    )
    assert watcher.read()["enabled"] is False


@pytest.mark.parametrize("directory", [".duckterm-connectors", ".codex"])
def test_config_or_runtime_symlink_is_refused_without_touching_target(home, monkeypatch, directory):
    outside = home / "outside"
    outside.mkdir()
    (home / directory).symlink_to(outside, target_is_directory=True)
    select(monkeypatch, "code")
    monkeypatch.setenv("DUCKTERM_BUNDLED_CLI", str(fake_cli(home, "code")))
    with pytest.raises(ValueError, match="unsafe plugin directory"):
        connectors._install("github", harnesses=["codex"])
    assert list(outside.iterdir()) == []
