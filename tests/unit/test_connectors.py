"""Connectors: credential resolution order, secret store hygiene, and
enable/disable writing (and cleaning) BOTH harness configs. CLI dependencies
(gh, railway) are stub scripts on a controlled PATH — no network, no keychain
(DUCKTERM_NO_KEYCHAIN routes secrets to the 0600 file store)."""

import json
import stat
import tomllib
from pathlib import Path

import pytest

from duckterm import connectors


@pytest.fixture(autouse=True)
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DUCKTERM_NO_KEYCHAIN", "1")
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path / "duckterm-home"))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    monkeypatch.setenv("PATH", str(bin_dir))
    return bin_dir


def _stub(bin_dir: Path, name: str, script: str) -> Path:
    path = bin_dir / name
    path.write_text(f"#!/bin/sh\n{script}\n")
    path.chmod(0o755)
    return path


def test_secret_store_file_backend_roundtrip(isolated_env: Path) -> None:
    connectors.save_secret("github", "ghp_abc123")
    stored = connectors._secret_file("github")
    assert stat.S_IMODE(stored.stat().st_mode) == 0o600  # not world-readable
    assert connectors.load_secret("github") == "ghp_abc123"
    connectors.delete_secret("github")
    assert connectors.load_secret("github") is None


def test_github_token_prefers_gh_cli_over_stored(isolated_env: Path) -> None:
    connectors.save_secret("github", "ghp_stored")
    assert connectors.github_token() == ("ghp_stored", "stored")
    _stub(isolated_env, "gh", 'echo "ghp_from_cli"')
    assert connectors.github_token() == ("ghp_from_cli", "gh-cli")


def test_github_enable_writes_both_harness_configs(isolated_env: Path, tmp_path: Path) -> None:
    _stub(isolated_env, "gh", 'echo "ghp_from_cli"')
    _stub(isolated_env, "github-mcp-server", "exit 0")
    home = tmp_path / "home"
    home.mkdir()

    result = connectors.enable("github", home=home)

    claude = json.loads((home / ".claude.json").read_text())
    entry = claude["mcpServers"]["github"]
    assert entry["args"] == ["connector-run", "github"]
    assert "ghp_" not in json.dumps(claude)  # the token must NOT be in the config
    codex = tomllib.loads((home / ".codex" / "config.toml").read_text())
    assert codex["mcp_servers"]["github"]["args"] == ["connector-run", "github"]
    assert result["enabled"] is True
    assert result["credential"] == "gh-cli"


def test_github_enable_without_credential_fails_clean(isolated_env: Path, tmp_path: Path) -> None:
    _stub(isolated_env, "github-mcp-server", "exit 0")
    home = tmp_path / "home"
    home.mkdir()
    with pytest.raises(RuntimeError, match="no GitHub credential"):
        connectors.enable("github", home=home)
    assert not (home / ".claude.json").exists()  # nothing half-installed


@pytest.mark.parametrize("previous_token", [None, "ghp_known_good"])
def test_github_enable_rejects_bad_token_before_storing(
    isolated_env: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    previous_token: str | None,
) -> None:
    _stub(isolated_env, "github-mcp-server", "exit 0")
    monkeypatch.setattr(connectors, "github_token_valid", lambda t: False)
    home = tmp_path / "home"
    home.mkdir()
    if previous_token is not None:
        connectors.save_secret("github", previous_token)
    with pytest.raises(RuntimeError, match="rejected"):
        connectors.enable("github", token="ghp_bad", home=home)
    # A rejected replacement must also preserve an already configured credential.
    assert connectors.load_secret("github") == previous_token

    connectors.save_secret("github", "ghp_existing")
    with pytest.raises(RuntimeError, match="rejected"):
        connectors.enable("github", token="ghp_bad", home=home)
    assert connectors.load_secret("github") == "ghp_existing"  # good token not overwritten


def test_railway_enable_requires_login_and_uses_cli_mcp(isolated_env: Path, tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    with pytest.raises(RuntimeError, match="not installed"):
        connectors.enable("railway", home=home)

    railway = _stub(isolated_env, "railway", "exit 1")  # installed, logged out
    with pytest.raises(RuntimeError, match="railway login"):
        connectors.enable("railway", home=home)

    _stub(isolated_env, "railway", "exit 0")  # logged in
    result = connectors.enable("railway", home=home)
    claude = json.loads((home / ".claude.json").read_text())
    assert claude["mcpServers"]["railway"] == {"command": str(railway), "args": ["mcp"]}
    assert result["enabled"] is True
    assert result["credential"] == "railway-cli"


def test_disable_removes_configs_and_secret(isolated_env: Path, tmp_path: Path) -> None:
    _stub(isolated_env, "gh", 'echo "ghp_x"')
    _stub(isolated_env, "github-mcp-server", "exit 0")
    home = tmp_path / "home"
    home.mkdir()
    connectors.save_secret("github", "ghp_fallback")
    connectors.enable("github", home=home)

    result = connectors.disable("github", home=home)

    assert json.loads((home / ".claude.json").read_text())["mcpServers"] == {}
    assert "github" not in tomllib.loads((home / ".codex" / "config.toml").read_text()).get(
        "mcp_servers", {}
    )
    assert connectors.load_secret("github") is None  # secret gone too
    assert result["enabled"] is False


def test_status_is_local_only_and_reports_gaps(isolated_env: Path, tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    rows = connectors.list_status(home=home)
    by_name = {r["name"]: r for r in rows}
    assert by_name["github"]["ready"] is False  # no cred, no server runner
    assert by_name["github"]["enabled"] is False
    assert by_name["railway"]["detail"] == "Railway CLI not installed"


def test_unknown_connector_raises() -> None:
    with pytest.raises(ValueError):
        connectors.enable("gitlab")
    with pytest.raises(ValueError):
        connectors.disable("gitlab")


def test_porkbun_enable_needs_both_keys_and_validates(
    isolated_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub(isolated_env, "uvx", "exit 0")
    home = tmp_path / "home"
    home.mkdir()

    with pytest.raises(RuntimeError, match="API key AND secret"):
        connectors.enable("porkbun", home=home)

    monkeypatch.setattr(connectors, "porkbun_keys_valid", lambda t, s: False)
    with pytest.raises(RuntimeError, match="rejected"):
        connectors.enable("porkbun", token="pk1_x", secret="sk1_y", home=home)
    assert connectors.load_secret("porkbun") is None  # bad keys not persisted

    monkeypatch.setattr(connectors, "porkbun_keys_valid", lambda t, s: True)
    result = connectors.enable("porkbun", token="pk1_x", secret="sk1_y", home=home)
    assert result["enabled"] is True
    assert result["credential"] == "stored"
    claude = json.loads((home / ".claude.json").read_text())
    entry = claude["mcpServers"]["porkbun"]
    assert entry["args"] == ["connector-run", "porkbun"]
    assert "pk1_" not in json.dumps(claude)  # keys never land in the config
    assert "sk1_" not in json.dumps(claude)


def test_porkbun_disable_clears_both_secrets(
    isolated_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub(isolated_env, "uvx", "exit 0")
    monkeypatch.setattr(connectors, "porkbun_keys_valid", lambda t, s: True)
    home = tmp_path / "home"
    home.mkdir()
    connectors.enable("porkbun", token="pk1_x", secret="sk1_y", home=home)

    result = connectors.disable("porkbun", home=home)

    assert result["enabled"] is False
    assert connectors.load_secret("porkbun") is None
    assert connectors.load_secret("porkbun-secret") is None
