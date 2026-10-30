"""duckterm doctor: each check must FAIL/WARN on a real problem and OK when
fixed. These drive the check functions with controlled paths so they don't
depend on the host's actual install."""

from pathlib import Path

import pytest

from duckterm import doctor


@pytest.fixture(autouse=True)
def isolated_shared_hook(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # A real Mac installation may already provide jq in ~/.duckterm-hooks.
    # Missing-dependency probes must never consult that host-owned runtime.
    monkeypatch.setattr(doctor, "shared_script_path", lambda: tmp_path / "hooks/duckterm-hook.sh")


def test_shared_hook_jq_satisfies_dependency(monkeypatch: pytest.MonkeyPatch) -> None:
    shared = doctor.shared_script_path().with_name("jq")
    shared.parent.mkdir(parents=True)
    shared.write_text("#!/bin/sh\n")
    shared.chmod(0o755)
    monkeypatch.delenv("DUCKTERM_BUNDLED_JQ", raising=False)
    monkeypatch.setattr(
        doctor.shutil, "which", lambda tool: None if tool == "jq" else "/usr/bin/" + tool
    )
    jq = next(r for r in doctor._check_system_deps() if "jq" in r.title)
    assert jq.status == "ok"
    assert "shared hook runtime" in jq.title


def test_missing_jq_is_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DUCKTERM_BUNDLED_JQ", raising=False)
    monkeypatch.setattr(doctor.shutil, "which", lambda t: None if t == "jq" else "/usr/bin/" + t)
    jq = next(r for r in doctor._check_system_deps() if "jq" in r.title)
    assert jq.status == "fail"
    assert "brew install jq" in jq.detail


def test_bundled_jq_satisfies_a_mac_without_jq(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bundled = tmp_path / "DuckTerm Lite.app/Contents/Resources/jq/bin/jq"
    bundled.parent.mkdir(parents=True)
    bundled.write_text("#!/bin/sh\n")
    bundled.chmod(0o755)
    monkeypatch.setattr(doctor.shutil, "which", lambda t: None if t == "jq" else "/usr/bin/" + t)
    monkeypatch.setenv("DUCKTERM_BUNDLED_JQ", str(bundled))
    jq = next(r for r in doctor._check_system_deps() if "jq" in r.title)
    assert jq.status == "ok"
    monkeypatch.setenv("DUCKTERM_BUNDLED_JQ", str(tmp_path / "missing/jq"))
    jq = next(r for r in doctor._check_system_deps() if "jq" in r.title)
    assert jq.status == "fail"


def test_all_deps_present_is_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor.shutil, "which", lambda t: "/usr/bin/" + t)
    assert all(r.status == "ok" for r in doctor._check_system_deps())


def test_server_unreachable_is_a_failure() -> None:
    # Port 1 is never listening; the check must report it down with the fix.
    r = doctor._check_server("http://127.0.0.1:1/")
    assert r.status == "fail"
    assert "duckterm serve" in r.detail


def test_token_missing_is_a_warning(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(doctor.paths, "home", lambda: tmp_path)  # no token file here
    r = doctor._check_token()
    assert r.status == "warn"


def test_token_present_is_ok(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "token").write_text("secret")
    monkeypatch.setattr(doctor.paths, "home", lambda: tmp_path)
    assert doctor._check_token().status == "ok"


def test_hook_not_installed_warns(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    missing = tmp_path / "settings.json"  # doesn't exist
    monkeypatch.setattr(doctor, "settings_path", lambda **kw: missing)
    r = doctor._check_hook_installed("claude-code")
    assert r.status == "warn"
    assert "install-hooks --agent claude-code --global" in r.detail


def test_hook_with_current_script_is_ok(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    script = "/abs/path/duckterm-hook.sh"
    settings = tmp_path / "settings.json"
    settings.write_text('{"hooks": {"PreToolUse": [{"command": "' + script + ' PreToolUse"}]}}')
    monkeypatch.setattr(doctor, "settings_path", lambda **kw: settings)
    monkeypatch.setattr(doctor, "hook_script_path", lambda: Path(script))
    assert doctor._check_hook_installed("claude-code").status == "ok"


def test_codex_async_true_is_flagged_as_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The exact bug from the field: codex hooks with async:true are silently skipped.
    script = "/abs/duckterm-hook.sh"
    settings = tmp_path / "hooks.json"
    settings.write_text('{"hooks":{"PreToolUse":[{"command":"' + script + '","async": true}]}}')
    monkeypatch.setattr(doctor, "settings_path", lambda **kw: settings)
    monkeypatch.setattr(doctor, "hook_script_path", lambda: Path(script))
    r = doctor._check_hook_installed("codex")
    assert r.status == "fail"
    assert "async" in r.title.lower()


def test_hook_pointing_at_old_path_is_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = tmp_path / "settings.json"
    settings.write_text('{"hooks":{"PreToolUse":[{"command":"/old/duckterm-hook.sh"}]}}')
    monkeypatch.setattr(doctor, "settings_path", lambda **kw: settings)
    monkeypatch.setattr(doctor, "hook_script_path", lambda: Path("/new/duckterm-hook.sh"))
    r = doctor._check_hook_installed("claude-code")
    assert r.status == "fail"
    assert "reinstall" in r.detail
