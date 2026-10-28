"""Shared hooks survive app removal without crossing independent profiles."""

import concurrent.futures
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from duckterm.agents import hook_dispatch, hooks_install
from duckterm.helpers import instance

SOURCE = hooks_install.hook_script_path()
APP_SCRIPT = (
    "Contents/Resources/python/lib/python3.13/site-packages/duckterm/hooks/duckterm-hook.sh"
)


@pytest.fixture
def home(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    for name in list(os.environ):
        if name.startswith("DUCKTERM_"):
            monkeypatch.delenv(name)
    return tmp_path


def profile(monkeypatch, name: str) -> None:
    for key, value in {
        "DUCKTERM_DESKTOP_PROFILE": name,
        "DUCKTERM_INSTANCE": "" if name == "full" else name,
    }.items():
        monkeypatch.setenv(key, value)
    for name in ("DUCKTERM_HOME", "DUCKTERM_PORT", "DUCKTERM_URL", "DUCKTERM_TMUX_SOCKET"):
        monkeypatch.delenv(name, raising=False)


def app(home: Path, name: str) -> Path:
    jq = shutil.which("jq")
    if not jq:
        pytest.skip("jq unavailable for fixture bundle")
    script = home / f"Apps/{name}.app" / APP_SCRIPT
    script.parent.mkdir(parents=True)
    shutil.copy2(SOURCE, script)
    bundled = home / f"Apps/{name}.app/Contents/Resources/jq/bin/jq"
    bundled.parent.mkdir(parents=True)
    fixture_jq = os.environ.get("HOOK_FIXTURE_JQ")
    if fixture_jq:
        shutil.copyfile(fixture_jq, bundled)
    else:
        # A portable unit fixture; mac acceptance supplies the signed static jq.
        # Copying Apple's /usr/bin/jq would retain its restricted signature.
        bundled.write_text(f'#!/bin/sh\nexec {shlex.quote(jq)} "$@"\n')
    bundled.chmod(0o700)
    license_file = bundled.parent.parent / "licenses/jq-COPYING"
    license_file.parent.mkdir()
    license_file.write_text("fixture license notice")
    return script


def read_config(home: Path) -> dict:
    return json.loads((home / ".claude/settings.json").read_text())


def install(home: Path) -> Path:
    return hooks_install.install(global_scope=True, project_dir=home)


def uninstall(home: Path) -> Path:
    return hooks_install.uninstall(global_scope=True, project_dir=home)


def test_profiles_share_one_hook_and_uninstall_only_their_membership(home, monkeypatch):
    for edition in ("full", "lite", "code"):
        profile(monkeypatch, edition)
        path = install(home)
    config = read_config(home)
    assert all(len(entries) == 1 for entries in config["hooks"].values())
    assert len(hook_dispatch.registered_profiles(path, "claude-code")) == 3
    before = path.read_bytes()
    for edition in ("lite", "full"):
        profile(monkeypatch, edition)
        uninstall(home)
        assert path.read_bytes() == before
    assert len(hook_dispatch.registered_profiles(path, "claude-code")) == 1
    profile(monkeypatch, "code")
    uninstall(home)
    assert read_config(home) == {}
    assert hook_dispatch.registered_profiles(path, "claude-code", legacy=False) == set()
    # Retained shared assets also keep already-running old command copies usable.
    assert hook_dispatch.shared_script_path().is_file()


def test_legacy_hooks_remain_full_owned_when_code_registers(home, monkeypatch):
    path = home / ".claude/settings.json"
    path.parent.mkdir()
    old = f"/Applications/Deleted DuckTerm.app/{APP_SCRIPT}"
    config = hooks_install.claude_style_build({}, old, "claude-code")
    config["hooks"]["Stop"][0]["hooks"].append({"command": "my-custom-hook"})
    path.write_text(json.dumps(config))
    profile(monkeypatch, "code")
    install(home)
    uninstall(home)
    assert hook_dispatch.registered_profiles(path, "claude-code") == {hook_dispatch.LEGACY_FULL}
    assert len(read_config(home)["hooks"]["Stop"][0]["hooks"]) == 1  # preserved custom group
    assert len(read_config(home)["hooks"]["Stop"]) == 2  # custom + shared handler
    profile(monkeypatch, "full")
    uninstall(home)
    assert read_config(home)["hooks"] == {
        "Stop": [{"matcher": "*", "hooks": [{"command": "my-custom-hook"}]}]
    }


def test_named_profile_cannot_remove_unregistered_legacy_hooks(home, monkeypatch):
    path = home / ".claude/settings.json"
    path.parent.mkdir()
    old = f"/Applications/Deleted DuckTerm.app/{APP_SCRIPT}"
    path.write_text(json.dumps(hooks_install.claude_style_build({}, old, "claude-code")))
    before = path.read_bytes()
    profile(monkeypatch, "lite")
    uninstall(home)
    assert path.read_bytes() == before
    assert not hook_dispatch.shared_script_path().parent.exists()


def test_code_startup_repairs_legacy_without_opting_in_or_changing_default(home, monkeypatch):
    source = app(home, "DuckTerm Code")
    monkeypatch.setattr(hooks_install, "hook_script_path", lambda: source)
    path = home / ".claude/settings.json"
    path.parent.mkdir()
    old = f"/Applications/Deleted DuckTerm.app/{APP_SCRIPT}"
    path.write_text(json.dumps(hooks_install.claude_style_build({}, old, "claude-code")))
    profile(monkeypatch, "code")
    assert hooks_install.repair_moved_app_hooks() == [path]
    assert hook_dispatch.registered_profiles(path, "claude-code") == {hook_dispatch.LEGACY_FULL}
    assert not (home / ".codex/hooks.json").exists()
    before = path.read_bytes()
    uninstall(home)
    assert path.read_bytes() == before
    assert hooks_install.repair_moved_app_hooks() == []


def test_newer_dispatch_revision_is_never_downgraded(home, monkeypatch):
    source = app(home, "DuckTerm")
    script = hook_dispatch.ensure_shared_script(source)
    receipt = script.with_name("assets.json")
    record = json.loads(receipt.read_text())
    record["revision"] = hook_dispatch.REVISION + 1
    receipt.write_text(json.dumps(record))
    source.write_text("#!/bin/sh\nexit 1\n")
    before = script.read_bytes(), script.with_name("jq").read_bytes(), receipt.read_bytes()
    assert hook_dispatch.ensure_shared_script(source) == script
    assert (
        script.read_bytes(),
        script.with_name("jq").read_bytes(),
        receipt.read_bytes(),
    ) == before


def test_shared_handler_upgrade_requires_a_higher_revision(home, monkeypatch):
    source = app(home, "DuckTerm")
    script = hook_dispatch.ensure_shared_script(source)
    before = script.read_bytes()
    source.write_bytes(before + b"\n# compatible v1 handler update\n")
    with pytest.raises(ValueError, match="revision increment"):
        hook_dispatch.ensure_shared_script(source)
    assert script.read_bytes() == before
    monkeypatch.setattr(hook_dispatch, "REVISION", hook_dispatch.REVISION + 1)
    hook_dispatch.ensure_shared_script(source)
    assert script.read_bytes() == source.read_bytes()
    assert script.stat().st_mode & 0o777 == 0o700
    assert json.loads(script.with_name("assets.json").read_text())["revision"] == 2
    assert b"fixture license notice" in script.with_name("NOTICES.txt").read_bytes()


def test_startup_repairs_only_known_projects_with_installed_hooks(home, monkeypatch):
    source = app(home, "DuckTerm Code")
    monkeypatch.setattr(hooks_install, "hook_script_path", lambda: source)
    projects = [home / name for name in ("Known Repo", "Custom Repo", "Unknown Repo", "Empty Repo")]
    old = f"/Applications/Deleted DuckTerm.app/{APP_SCRIPT}"
    config = hooks_install.claude_style_build({}, old, "claude-code")
    paths = [project / ".claude/settings.json" for project in projects]
    for path in paths[:3]:
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(config))
    custom = '{"hooks":{"Stop":[{"hooks":[{"command":"my-hook"}]}]}}'
    paths[1].write_text(custom)
    unknown_before = paths[2].read_bytes()
    profile(monkeypatch, "code")
    assert hooks_install.repair_moved_app_hooks([projects[0], projects[1], projects[3]]) == [
        paths[0]
    ]
    command = json.loads(paths[0].read_text())["hooks"]["Stop"][0]["hooks"][0]["command"]
    assert shlex.split(command)[0] == str(hook_dispatch.shared_script_path())
    assert paths[1].read_text() == custom
    assert paths[2].read_bytes() == unknown_before
    assert not projects[3].exists()
    assert hook_dispatch.registered_profiles(paths[0], "claude-code") == {hook_dispatch.LEGACY_FULL}


@pytest.mark.parametrize("asset", ["duckterm-hook.sh", "jq", "assets.json", "NOTICES.txt"])
def test_modified_shared_assets_are_not_overwritten(home, asset):
    source = app(home, "DuckTerm")
    script = hook_dispatch.ensure_shared_script(source)
    modified = script.with_name(asset)
    modified.write_text("user content")
    with pytest.raises(ValueError):
        hook_dispatch.ensure_shared_script(source)
    assert modified.read_text() == "user content"


def test_shared_asset_symlink_cannot_escape_home(home):
    outside = home / "outside"
    outside.mkdir()
    (home / ".duckterm-hooks").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="unsafe"):
        hook_dispatch.ensure_shared_script(SOURCE)
    assert list(outside.iterdir()) == []


def test_concurrent_installs_keep_all_profile_memberships(home):
    snippet = (
        "from pathlib import Path; from duckterm.agents.hooks_install import install; "
        "install(global_scope=True, project_dir=Path.home())"
    )

    def run(edition):
        env = dict(os.environ, HOME=str(home), PYTHONPATH=str(SOURCE.parents[2]))
        env["DUCKTERM_INSTANCE"] = "" if edition == "full" else edition
        env["DUCKTERM_DESKTOP_PROFILE"] = edition
        return subprocess.run(
            [sys.executable, "-c", snippet], env=env, capture_output=True, timeout=30
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(run, ("full", "lite", "code")))
    assert all(r.returncode == 0 for r in results), "\n".join(r.stderr.decode() for r in results)
    path = home / ".claude/settings.json"
    assert len(hook_dispatch.registered_profiles(path, "claude-code")) == 3
    assert all(len(entries) == 1 for entries in read_config(home)["hooks"].values())


@pytest.fixture
def delivery(home):
    source = app(home, "DuckTerm Code")
    script = hook_dispatch.ensure_shared_script(source)
    shutil.rmtree(home / "Apps")  # No installed app remains to supply jq or Python.
    fake = home / "fake/curl"
    fake.parent.mkdir()
    calls = home / "calls"
    fake.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$@\" >> {shlex.quote(str(calls))}\n"
        'case "$*" in\n'
        '  *"/decision"*) printf \'{"status":"approve"}\' ;;\n'
        '  *"/approvals"*) printf \'{"id":"r1"}\' ;;\n'
        '  *) printf \'{"hook_output":{"notice":"inbox"}}\' ;;\n'
        "esac\n"
    )
    fake.chmod(0o700)
    env = {"HOME": str(home), "PATH": f"{fake.parent}:/usr/bin:/bin"}
    return script, env, calls


def run_hook(script: Path, env: dict, event: str = "Stop") -> subprocess.CompletedProcess:
    return subprocess.run(
        ["/bin/bash", str(script), event, "claude-code"],
        input='{"session_id":"fixture","tool_name":"Bash"}',
        text=True,
        capture_output=True,
        env=env,
        timeout=10,
    )


def test_removed_apps_leave_stop_and_approval_hooks_working(delivery, home):
    script, env, calls = delivery
    (home / ".duckterm").mkdir()
    (home / ".duckterm/token").write_text("fixture-full-token")
    result = run_hook(script, env)
    assert result.returncode == 0
    assert json.loads(result.stdout) == {"notice": "inbox"}
    assert "http://127.0.0.1:4300/events" in calls.read_text()
    assert "fixture-full-token" in calls.read_text()
    decision = json.loads(run_hook(script, env, "PermissionRequest").stdout)
    assert decision["hookSpecificOutput"]["decision"]["behavior"] == "allow"


def test_explicit_code_context_uses_only_code_endpoint_and_token(delivery, home, monkeypatch):
    script, env, calls = delivery
    (home / ".duckterm").mkdir()
    (home / ".duckterm/token").write_text("fixture-full-token")
    (home / ".duckterm-code").mkdir()
    (home / ".duckterm-code/token").write_text("fixture-code-token")
    profile(monkeypatch, "code")
    env.update(instance.launch_env())
    assert json.loads(run_hook(script, env).stdout) == {"notice": "inbox"}
    text = calls.read_text()
    assert "http://127.0.0.1:4800/events" in text
    assert "fixture-code-token" in text
    assert "4300" not in text and "fixture-full-token" not in text


def test_already_running_full_pane_with_only_default_url_stays_compatible(delivery, home):
    script, env, calls = delivery
    (home / ".duckterm").mkdir()
    (home / ".duckterm/token").write_text("fixture-full-token")
    env["DUCKTERM_URL"] = "http://127.0.0.1:4300"
    assert json.loads(run_hook(script, env).stdout) == {"notice": "inbox"}
    assert "fixture-full-token" in calls.read_text()


@pytest.mark.parametrize(
    "partial",
    [
        {"DUCKTERM_INSTANCE": "lite"},
        {"DUCKTERM_URL": "http://127.0.0.1:4519"},
        {"DUCKTERM_HOME": "/missing/code"},
        {"DUCKTERM_PORT": "4800"},
        {"DUCKTERM_TMUX_SOCKET": "duckterm-code"},
        {"DUCKTERM_INSTANCE": "code", "DUCKTERM_HOME": "/missing/code", "DUCKTERM_URL": "x"},
    ],
)
def test_partial_context_never_falls_back_to_full(delivery, partial):
    script, env, calls = delivery
    env.update(partial)
    assert run_hook(script, env).returncode == 0
    assert not calls.exists()
