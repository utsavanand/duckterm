"""Connectors: credential resolution order, secret store hygiene, and
enable/disable writing (and cleaning) BOTH harness configs. CLI dependencies
(gh, github-mcp-server) are stub scripts on a controlled PATH — no network, no keychain
(DUCKTERM_NO_KEYCHAIN routes secrets to the 0600 file store)."""

import json
import stat
import sys
import tomllib
from pathlib import Path

import pytest

from duckterm import connectors

# Captured before isolated_env (autouse) replaces it, for the one test that
# must exercise the real network path.
_REAL_GITHUB_IDENTITY = connectors.github_identity


@pytest.fixture(autouse=True)
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DUCKTERM_NO_KEYCHAIN", "1")
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path / "duckterm-home"))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    monkeypatch.setenv("PATH", str(bin_dir))
    # PATH is replaced wholesale above, so enable() cannot find the real CLI.
    # _duckterm_bin() raises without it and the test fails on the environment
    # rather than on what it is checking -- which made 10 of these depend on
    # whether a duckterm binary happened to be installed on the machine.
    duckterm = bin_dir / "duckterm"
    duckterm.write_text("#!/bin/sh\nexit 0\n")
    duckterm.chmod(0o755)
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
    assert list(by_name) == ["github"]
    # The gap is named, so Connect cannot look like it worked while agents get
    # nothing: there is no github-mcp-server and no docker on the stub PATH.
    assert by_name["github"]["runner_ready"] is False
    assert "github-mcp-server" in str(by_name["github"]["detail"])


def test_unknown_connector_raises() -> None:
    with pytest.raises(ValueError):
        connectors.enable("gitlab")
    with pytest.raises(ValueError):
        connectors.disable("gitlab")


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


def test_offline_validation_does_not_claim_verified_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def offline(*args: object, **kwargs: object) -> None:
        raise OSError("network unavailable")

    monkeypatch.setattr(connectors.urllib.request, "urlopen", offline)
    # isolated_env is autouse and stubs github_identity for every test in this
    # file, so restore the real one or this asserts on the stub.
    monkeypatch.setattr(connectors, "github_identity", _REAL_GITHUB_IDENTITY)
    # A failed lookup must not be reported as a verified identity, and must not
    # be reported as a rejected credential either -- the token may be perfectly
    # good and the network merely down.
    with pytest.raises(RuntimeError, match="Cannot verify"):
        connectors.github_identity("ghp_whatever")


def _fake_mcp(path: Path, body: str) -> None:
    """A stdio MCP server that answers initialize + tools/list over real pipes."""
    path.write_text(f"#!{sys.executable}\nimport json,sys\n{body}\n")
    path.chmod(0o755)


_SERVES_TOOLS = """
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    if msg.get('id') == 1:
        ready = {'jsonrpc':'2.0','id':1,'result':{'protocolVersion':'2024-11-05'}}
        print(json.dumps(ready), flush=True)
    elif msg.get('id') == 2:
        tools = [{'name': f'tool_{i}'} for i in range(3)]
        print(json.dumps({'jsonrpc':'2.0','id':2,'result':{'tools':tools}}), flush=True)
"""


def test_verify_reports_the_tools_a_working_connector_serves(
    isolated_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = tmp_path / "server"
    _fake_mcp(server, _SERVES_TOOLS)
    monkeypatch.setattr(connectors, "_duckterm_bin", lambda: str(server))
    assert connectors.verify("github") == {"ok": True, "detail": None, "tools": 3}


def test_verify_surfaces_the_servers_own_error_when_it_cannot_start(
    isolated_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = tmp_path / "server"
    _fake_mcp(server, "sys.stderr.write('github-mcp-server not found\\n')\nraise SystemExit(1)")
    monkeypatch.setattr(connectors, "_duckterm_bin", lambda: str(server))
    result = connectors.verify("github")
    assert result["ok"] is False and result["tools"] == 0
    assert "github-mcp-server not found" in str(result["detail"])


def test_verify_does_not_hang_on_a_server_that_never_answers(
    isolated_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = tmp_path / "server"
    _fake_mcp(server, "import time\ntime.sleep(60)")
    monkeypatch.setattr(connectors, "_duckterm_bin", lambda: str(server))
    result = connectors.verify("github", timeout=1.0)
    assert result["ok"] is False and "did not start" in str(result["detail"])


def _gh_ready(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(env, "gh", "echo gh-token")
    _stub(env, "github-mcp-server", "sleep 0")
    monkeypatch.setattr(connectors, "github_identity", lambda token: "tester")


def test_enable_registers_only_the_chosen_harness(
    isolated_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _gh_ready(isolated_env, monkeypatch)
    result = connectors.enable("github", source="gh-cli", home=tmp_path, harnesses=["claude-code"])
    assert result["harnesses"] == ["claude-code"]
    assert connectors.mcp_install.claude_installed("github", home=tmp_path)
    assert not connectors.mcp_install.codex_installed("github", home=tmp_path)
    # A connector serving one of two harnesses is enabled, not half-disabled.
    assert result["enabled"] is True


def test_deselecting_a_harness_withdraws_its_existing_entry(
    isolated_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Leaving the entry behind would keep serving a connector nobody can see."""
    _gh_ready(isolated_env, monkeypatch)
    connectors.enable("github", source="gh-cli", home=tmp_path)
    assert connectors.mcp_install.codex_installed("github", home=tmp_path)
    connectors.enable("github", source="gh-cli", home=tmp_path, harnesses=["claude-code"])
    assert not connectors.mcp_install.codex_installed("github", home=tmp_path)


def test_the_choice_survives_a_later_enable_that_does_not_restate_it(
    isolated_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _gh_ready(isolated_env, monkeypatch)
    connectors.enable("github", source="gh-cli", home=tmp_path, harnesses=["codex"])
    again = connectors.enable("github", source="gh-cli", home=tmp_path)
    assert again["harnesses"] == ["codex"]
    assert not connectors.mcp_install.claude_installed("github", home=tmp_path)


def test_a_connector_predating_the_setting_reports_what_is_registered(
    isolated_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without a stored choice, read the configs: claiming Codex when only
    Claude Code was ever registered is the false checkmark we are removing."""
    _gh_ready(isolated_env, monkeypatch)
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path / ".duckterm"))
    connectors.mcp_install.claude_install(
        "github", "duckterm", ["connector-run", "github"], home=tmp_path
    )
    connectors._save_policy("github", {"enabled": True, "source": "gh-cli"})
    assert connectors.harness_choice("github", home=tmp_path) == ["claude-code"]


def test_a_connector_with_nothing_registered_defaults_to_both(
    isolated_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _gh_ready(isolated_env, monkeypatch)
    connectors._save_policy("github", {"enabled": False, "source": "gh-cli"})
    assert connectors.harness_choice("github", home=tmp_path) == ["claude-code", "codex"]


def test_enable_rejects_an_empty_or_unknown_harness_list(
    isolated_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _gh_ready(isolated_env, monkeypatch)
    with pytest.raises(RuntimeError, match="at least one agent"):
        connectors.enable("github", source="gh-cli", home=tmp_path, harnesses=[])
    with pytest.raises(RuntimeError, match="Unknown harness"):
        connectors.enable("github", source="gh-cli", home=tmp_path, harnesses=["cursor"])


def test_status_reports_which_agent_clis_exist_here(
    isolated_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Writing a Codex entry on a machine without Codex must not read as working."""
    _gh_ready(isolated_env, monkeypatch)
    _stub(isolated_env, "claude", "true")
    connectors.enable("github", source="gh-cli", home=tmp_path)
    present = connectors.status("github", home=tmp_path)["harnesses_present"]
    assert present == {"claude-code": True, "codex": False}


@pytest.mark.parametrize(
    "drop_policy_flag,drop_harness_entry",
    [(False, False), (True, False), (False, True), (True, True)],
)
def test_panel_reports_availability_from_one_predicate(
    isolated_env: Path,
    tmp_path: Path,
    drop_policy_flag: bool,
    drop_harness_entry: bool,
) -> None:
    """Either store being off makes the panel say unavailable.

    The (False, True) case is the one that matters: a harness entry removed
    outside Duckterm leaves policy.enabled set, and the panel must still not
    claim the connector is available to an agent that cannot resolve it.
    """
    from duckterm.agents import mcp_install

    _stub(isolated_env, "github-mcp-server", "sleep 0")
    connectors.save_secret("github", "ghp_stored")
    connectors.enable("github", source="stored", home=tmp_path)
    assert connectors.status("github", home=tmp_path)["enabled"] is True

    if drop_policy_flag:
        connectors._save_policy(
            "github", {**connectors.policy("github", home=tmp_path), "enabled": False}
        )
    if drop_harness_entry:
        mcp_install.managed_remove("github", "codex", home=tmp_path)

    expected = not (drop_policy_flag or drop_harness_entry)
    assert connectors.available_to_agents("github", home=tmp_path) is expected
    assert connectors.status("github", home=tmp_path)["enabled"] is expected


def test_an_unreadable_harness_config_reads_as_unavailable_rather_than_raising(
    isolated_env: Path, tmp_path: Path
) -> None:
    """Fail-closed for display: a damaged config must not crash the panel."""
    _stub(isolated_env, "github-mcp-server", "sleep 0")
    connectors.save_secret("github", "ghp_stored")
    connectors.enable("github", source="stored", home=tmp_path)

    from duckterm.agents import mcp_install

    mcp_install._managed_path("codex", tmp_path).write_text("{ this is not json")
    assert connectors.available_to_agents("github", home=tmp_path) is False
    assert connectors.status("github", home=tmp_path)["enabled"] is False


def test_the_panel_can_tell_disable_apart_from_a_lost_harness_entry(
    isolated_env: Path, tmp_path: Path
) -> None:
    """The two doors, and which one is shut.

    `enabled` is false in both situations below, which is why one boolean made
    the panel describe a missing registration in Disable's words. These two
    fields are the whole point of the split: same `enabled`, different cause,
    different fix.
    """
    from duckterm.agents import mcp_install

    _stub(isolated_env, "github-mcp-server", "sleep 0")
    _stub(isolated_env, "duckterm", "exit 0")
    connectors.save_secret("github", "ghp_stored")
    connectors.enable("github", source="stored", home=tmp_path)

    on = connectors.status("github", home=tmp_path)
    assert (on["duckterm_uses"], on["agents_use"]) == (True, True)
    assert on["unreachable_harnesses"] == []

    connectors.disable("github", home=tmp_path)
    off = connectors.status("github", home=tmp_path)
    assert off["agents_use"] is False
    assert off["agents_off_by_choice"] is True
    # Disable is about agents. Duckterm's own features need a credential, and
    # Disable does not remove one, so this door stays open.
    assert off["duckterm_uses"] is True

    connectors.enable("github", source="stored", home=tmp_path)
    mcp_install.managed_remove("github", "codex", home=tmp_path)
    lost = connectors.status("github", home=tmp_path)
    assert lost["agents_use"] is False
    assert lost["agents_off_by_choice"] is False, "the owner did not turn this off"
    assert lost["unreachable_harnesses"] == ["codex"]


def test_a_deselected_harness_is_not_reported_as_unreachable(
    isolated_env: Path, tmp_path: Path
) -> None:
    """Absence the owner chose is not a problem to report.

    Counting a deselected harness would show a permanent warning naming an
    agent the owner deliberately excluded.
    """
    _stub(isolated_env, "github-mcp-server", "sleep 0")
    _stub(isolated_env, "duckterm", "exit 0")
    connectors.save_secret("github", "ghp_stored")
    connectors.enable("github", source="stored", home=tmp_path, harnesses=["claude-code"])

    row = connectors.status("github", home=tmp_path)
    assert row["unreachable_harnesses"] == []
    assert row["agents_use"] is True


def test_status_says_agents_cannot_run_the_server_when_nothing_can_run_it(
    isolated_env: Path, tmp_path: Path
) -> None:
    """A valid token with no server to run gives agents nothing.

    This was a detail string beside a row that still offered Connect, so the
    owner could connect successfully and find agents had no GitHub at all.
    """
    connectors.save_secret("github", "ghp_stored")
    # No github-mcp-server and no docker on the stubbed PATH.
    row = connectors.status("github", home=tmp_path)
    assert row["runner_ready"] is False
    assert row["ready"] is False
    assert "github-mcp-server" in str(row["detail"])


def test_only_github_remains() -> None:
    """Railway and Porkbun were MCP pass-throughs; nothing in Duckterm read them."""
    assert connectors.NAMES == ("github",)
