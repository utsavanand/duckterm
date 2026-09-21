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
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DUCKTERM_NO_KEYCHAIN", "1")
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path / "duckterm-home"))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setattr(connectors, "github_identity", lambda token: "test-user")
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


def test_github_token_uses_only_explicit_source(isolated_env: Path) -> None:
    connectors.save_secret("github", "ghp_stored")
    assert connectors.github_token("stored") == ("ghp_stored", "stored")
    _stub(isolated_env, "gh", 'echo "ghp_from_cli"')
    assert connectors.github_token("stored") == ("ghp_stored", "stored")
    assert connectors.github_token("gh-cli") == ("ghp_from_cli", "gh-cli")
    assert connectors.github_token() is None


def test_github_enable_writes_both_harness_configs(isolated_env: Path, tmp_path: Path) -> None:
    _stub(isolated_env, "gh", 'echo "ghp_from_cli"')
    _stub(isolated_env, "github-mcp-server", "exit 0")
    home = tmp_path / "home"
    home.mkdir()

    result = connectors.enable("github", source="gh-cli", home=home)

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
        connectors.enable("github", source="gh-cli", home=home)
    assert not (home / ".claude.json").exists()  # nothing half-installed


@pytest.mark.parametrize("previous_token", [None, "ghp_known_good"])
def test_github_enable_rejects_bad_token_before_storing(
    isolated_env: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    previous_token: str | None,
) -> None:
    _stub(isolated_env, "github-mcp-server", "exit 0")

    def reject(token: str) -> str:
        raise RuntimeError("GitHub rejected that token")

    monkeypatch.setattr(connectors, "github_identity", reject)
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
        connectors.enable("railway", source="railway-cli", home=home)

    _stub(isolated_env, "railway", "exit 1")  # installed, logged out
    with pytest.raises(RuntimeError, match="railway login"):
        connectors.enable("railway", source="railway-cli", home=home)

    _stub(isolated_env, "railway", "exit 0")  # logged in
    result = connectors.enable("railway", source="railway-cli", home=home)
    claude = json.loads((home / ".claude.json").read_text())
    assert claude["mcpServers"]["railway"]["args"] == ["connector-run", "railway"]
    assert result["enabled"] is True
    assert result["credential"] == "railway-cli"


def test_disable_removes_configs_and_retains_authorization(
    isolated_env: Path, tmp_path: Path
) -> None:
    _stub(isolated_env, "gh", 'echo "ghp_x"')
    _stub(isolated_env, "github-mcp-server", "exit 0")
    home = tmp_path / "home"
    home.mkdir()
    connectors.save_secret("github", "ghp_fallback")
    connectors.enable("github", source="gh-cli", home=home)

    result = connectors.disable("github", home=home)

    assert json.loads((home / ".claude.json").read_text())["mcpServers"] == {}
    assert "github" not in tomllib.loads((home / ".codex" / "config.toml").read_text()).get(
        "mcp_servers", {}
    )
    assert connectors.load_secret("github") == "ghp_fallback"
    connectors.forget("github", home=home)
    assert connectors.load_secret("github") is None
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


def test_porkbun_forget_clears_both_secrets(
    isolated_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub(isolated_env, "uvx", "exit 0")
    monkeypatch.setattr(connectors, "porkbun_keys_valid", lambda t, s: True)
    home = tmp_path / "home"
    home.mkdir()
    connectors.enable("porkbun", token="pk1_x", secret="sk1_y", home=home)

    result = connectors.forget("porkbun", home=home)

    assert result["enabled"] is False
    assert connectors.load_secret("porkbun") is None
    assert connectors.load_secret("porkbun-secret") is None


def test_selected_source_never_falls_back(isolated_env: Path) -> None:
    connectors.save_secret("github", "stored-secret")
    _stub(isolated_env, "gh", "exit 1")
    assert connectors.github_token("gh-cli") is None
    assert connectors.github_token("stored") == ("stored-secret", "stored")


def test_hosted_machine_refuses_local_secret_storage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DUCKTERM_HOSTED", "1")
    with pytest.raises(RuntimeError, match="connector service"):
        connectors.save_secret("github", "must-not-persist")
    assert not connectors._secret_file("github").exists()


def test_porkbun_write_opt_in_overrides_ambient_environment(
    isolated_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub(isolated_env, "uvx", "exit 0")
    monkeypatch.setattr(connectors, "porkbun_keys_valid", lambda t, s: True)
    monkeypatch.setenv("PORKBUN_GET_MUDDY", "true")
    home = tmp_path / "home"
    home.mkdir()
    result = connectors.enable("porkbun", "pk-test", "sk-test", home=home)
    assert result["write_access"] is False
    argv, env = connectors.server_command("porkbun", connectors.policy("porkbun"))
    assert "--get-muddy" not in argv
    assert env["PORKBUN_GET_MUDDY"] == "false"
    connectors.enable("porkbun", home=home, write_access=True)
    argv, _ = connectors.server_command("porkbun", connectors.policy("porkbun"))
    assert "--get-muddy" in argv


def test_offline_validation_does_not_claim_verified_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def offline(*args: object, **kwargs: object) -> None:
        raise OSError("network unavailable")

    monkeypatch.setattr(connectors.urllib.request, "urlopen", offline)
    # Restore the real validator because the fixture stubs GitHub's network.
    with pytest.raises(RuntimeError, match="Cannot verify"):
        connectors.porkbun_keys_valid("pk", "sk")


@pytest.fixture()
def hf_runner(isolated_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HF_TOKEN", raising=False)
    _stub(isolated_env, "npx", "exit 0")
    _stub(isolated_env, "node", "echo v22.14.0")


def test_huggingface_anonymous_lifecycle(hf_runner: None, tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    result = connectors.enable("huggingface", home=home)
    assert result["ready"] is True
    assert result["enabled"] is True
    assert result["credential"] == "anonymous"
    claude = json.loads((home / ".claude.json").read_text())
    codex = tomllib.loads((home / ".codex/config.toml").read_text())
    for entry in (claude["mcpServers"]["huggingface"], codex["mcp_servers"]["huggingface"]):
        assert entry["args"] == ["connector-run", "huggingface"]
        assert "env" not in entry
    assert connectors.disable("huggingface", home=home)["enabled"] is False
    assert "huggingface" not in json.loads((home / ".claude.json").read_text())["mcpServers"]
    assert "huggingface" not in tomllib.loads((home / ".codex/config.toml").read_text()).get(
        "mcp_servers", {}
    )


def test_huggingface_missing_runner_does_not_store_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(connectors, "huggingface_token_valid", lambda _: pytest.fail("network"))
    with pytest.raises(RuntimeError, match="Node.js"):
        connectors.enable("huggingface", token="hf_test", home=tmp_path)
    assert connectors.load_secret("huggingface") is None
    assert not (tmp_path / ".claude.json").exists()
    assert connectors.status("huggingface", home=tmp_path)["ready"] is False


def test_huggingface_token_validation_preserves_existing_secret(
    hf_runner: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    connectors.save_secret("huggingface", "hf_old")
    monkeypatch.setattr(connectors, "huggingface_token_valid", lambda _: False)
    with pytest.raises(RuntimeError, match="rejected"):
        connectors.enable("huggingface", token="hf_bad", home=tmp_path)
    assert connectors.load_secret("huggingface") == "hf_old"
    assert not (tmp_path / ".claude.json").exists()
    monkeypatch.setattr(connectors, "huggingface_token_valid", lambda _: True)
    result = connectors.enable("huggingface", token="hf_new", home=tmp_path)
    assert result["credential"] == "stored"
    for path in (tmp_path / ".claude.json", tmp_path / ".codex/config.toml"):
        assert "hf_new" not in path.read_text()
    connectors.disable("huggingface", home=tmp_path)
    assert connectors.load_secret("huggingface") == "hf_new"
    connectors.forget("huggingface", home=tmp_path)
    assert connectors.load_secret("huggingface") is None


@pytest.mark.parametrize("credential", [None, "stored", "env"])
def test_huggingface_launch_resolves_optional_token_without_exposing_it(
    hf_runner: None, monkeypatch: pytest.MonkeyPatch, credential: str | None
) -> None:
    if credential:
        connectors.save_secret("huggingface", "hf_stored")
    if credential == "env":
        monkeypatch.setenv("HF_TOKEN", "hf_environment")

    argv, env = connectors.server_command("huggingface")
    binary = argv[0]
    assert binary.endswith("/npx")
    assert "mcp-remote@0.1.38" in argv
    assert "https://huggingface.co/mcp?bouquet=search" in argv
    assert "--silent" in argv
    assert not any("hf_stored" in arg or "hf_environment" in arg for arg in argv)
    if credential:
        expected = "hf_environment" if credential == "env" else "hf_stored"
        assert env["DUCKTERM_HF_AUTH"] == f"Bearer {expected}"
        assert argv[-2:] == ["--header", "Authorization:${DUCKTERM_HF_AUTH}"]
    else:
        assert "--header" not in argv


@pytest.mark.parametrize("code, rejected", [(401, True), (403, True), (429, False), (503, False)])
def test_huggingface_validation_distinguishes_rejection_from_outage(
    monkeypatch: pytest.MonkeyPatch, code: int, rejected: bool
) -> None:
    def fail(req, timeout):
        assert req.full_url == "https://huggingface.co/api/whoami-v2"
        assert req.get_header("Authorization") == "Bearer hf_test"
        raise connectors.urllib.error.HTTPError(req.full_url, code, "failure", {}, None)

    monkeypatch.setattr(connectors.urllib.request, "urlopen", fail)
    if rejected:
        assert connectors.huggingface_token_valid("hf_test") is False
    else:
        with pytest.raises(RuntimeError, match="unavailable"):
            connectors.huggingface_token_valid("hf_test")


@pytest.mark.parametrize("version", ["v18.20.0", "not-a-version"])
def test_huggingface_rejects_unsupported_node(
    hf_runner: None, isolated_env: Path, tmp_path: Path, version: str
) -> None:
    _stub(isolated_env, "node", f"echo {version}")
    with pytest.raises(RuntimeError, match="Node.js 22"):
        connectors.enable("huggingface", home=tmp_path)
    assert not (tmp_path / ".claude.json").exists()
