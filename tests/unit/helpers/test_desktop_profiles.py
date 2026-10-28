"""Desktop builds and callbacks must agree without changing legacy CLI targets."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from duckterm.helpers import instance


@pytest.fixture(autouse=True)
def clean_profile(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in instance.launch_env():
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))


@pytest.mark.parametrize(
    ("edition", "test", "iid", "port", "suffix"),
    [
        ("full", False, None, 4300, ""),
        ("lite", False, "lite", 4519, ".lite"),
        ("code", False, "code", 4800, ".code"),
        ("full", True, "test", 4655, ".test"),
        ("lite", True, "test-lite", 4840, ".test.lite"),
        ("code", True, "test-code", 4366, ".test.code"),
    ],
)
def test_build_contract_uses_resolver(
    monkeypatch: pytest.MonkeyPatch,
    edition: str,
    test: bool,
    iid: str | None,
    port: int,
    suffix: str,
) -> None:
    profile = instance.desktop_profile(edition, test=test)
    monkeypatch.setenv("DUCKTERM_INSTANCE", iid or "")
    monkeypatch.setenv("DUCKTERM_DESKTOP_PROFILE", profile["profile_id"])
    assert profile["instance_id"] == iid
    assert profile["port"] == instance.port() == port
    assert profile["home_name"] == instance.home().name
    assert profile["tmux_socket"] == instance.tmux_socket()
    assert profile["bundle_id"] == "com.rubberduckhq.rubberterm" + suffix
    instance.validate_desktop_context(edition)


def test_build_script_has_no_application_import(tmp_path: Path) -> None:
    # An import of the package would fail in this isolated script execution.
    script = tmp_path / "instance.py"
    script.write_text(Path(instance.__file__).read_text())
    result = subprocess.run(
        [sys.executable, "-I", str(script), "--desktop-profile", "lite"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout) == instance.desktop_profile("lite")


@pytest.mark.parametrize(
    "field,value",
    [
        ("DUCKTERM_INSTANCE", "code"),
        ("DUCKTERM_HOME", "/tmp/foreign-profile"),
        ("DUCKTERM_TMUX_SOCKET", "duckterm"),
        ("DUCKTERM_PORT", "4300"),
        ("DUCKTERM_URL", "http://127.0.0.1:4300"),
        ("DUCKTERM_DESKTOP_PROFILE", "unknown"),
    ],
)
def test_mixed_profile_rejected(monkeypatch: pytest.MonkeyPatch, field: str, value: str) -> None:
    monkeypatch.setenv("DUCKTERM_DESKTOP_PROFILE", "lite")
    monkeypatch.setenv("DUCKTERM_INSTANCE", "lite")
    monkeypatch.setenv(field, value)
    with pytest.raises(ValueError):
        instance.validate_desktop_context("lite")


def test_edition_does_not_retarget_legacy_or_custom_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DUCKTERM_EDITION", "lite")
    assert instance.port() == 4300
    assert instance.home().name == ".duckterm"
    monkeypatch.setenv("DUCKTERM_INSTANCE", "dev")
    monkeypatch.setenv("DUCKTERM_PORT", "9813")
    instance.validate_desktop_context("lite")
    assert instance.port() == 9813
    assert instance.home().name == ".duckterm-dev"


def test_child_context_clears_inherited_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    assert instance.launch_env()["DUCKTERM_INSTANCE"] == ""
    assert instance.launch_env()["DUCKTERM_DESKTOP_PROFILE"] == ""
    monkeypatch.setenv("DUCKTERM_INSTANCE", "lite")
    env = instance.launch_env()
    assert env["DUCKTERM_HOME"] == str(Path.home() / ".duckterm-lite")
    assert env["DUCKTERM_TMUX_SOCKET"] == "duckterm-lite"
    assert env["DUCKTERM_PORT"] == "4519"
    assert env["DUCKTERM_URL"] == "http://127.0.0.1:4519"


def test_desktop_edition_mismatch_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DUCKTERM_DESKTOP_PROFILE", "lite")
    monkeypatch.setenv("DUCKTERM_INSTANCE", "lite")
    with pytest.raises(ValueError, match="Conflicting environment"):
        instance.validate_desktop_context("full")
