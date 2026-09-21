import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from duckterm import connector_broker, connector_client, connectors, google_connectors


@pytest.fixture()
def google_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path / "duckterm"))
    monkeypatch.setenv("DUCKTERM_NO_KEYCHAIN", "1")
    for key in (
        "DUCKTERM_HOSTED",
        "DUCKTERM_CONNECTOR_CLIENT_CONFIG",
        "GMAIL_OAUTH_PATH",
        "GMAIL_CREDENTIALS_PATH",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(google_connectors, "node_runner", lambda: "/test/npx")
    return tmp_path


def gmail_files(home: Path, scopes: list[str] | None = None) -> None:
    root = home / ".gmail-mcp"
    root.mkdir()
    (root / "gcp-oauth.keys.json").write_text(
        json.dumps(
            {"installed": {"client_id": "client-id", "client_secret": "private-client-secret"}}
        )
    )
    (root / "credentials.json").write_text(
        json.dumps(
            {
                "tokens": {"refresh_token": "private-refresh-token"},
                "scopes": scopes or ["gmail.readonly"],
            }
        )
    )


def test_gmail_requires_scoped_oauth_and_never_writes_tokens_to_configs(google_home: Path) -> None:
    assert not google_connectors.gmail_ready()
    with pytest.raises(RuntimeError, match="connector-auth"):
        connectors.enable("gmail", home=google_home)
    gmail_files(google_home)
    result = connectors.enable("gmail", home=google_home)
    assert result["ready"] and result["enabled"]
    for config in (google_home / ".claude.json", google_home / ".codex/config.toml"):
        assert "private-" not in config.read_text()
        assert "connector-run" in config.read_text()
    argv, env = google_connectors.command("gmail")
    assert argv == ["/test/npx", "--yes", google_connectors.GMAIL_PACKAGE]
    assert env["GMAIL_CREDENTIALS_PATH"].endswith("credentials.json")
    connectors.disable("gmail", home=google_home)
    assert google_connectors.gmail_ready()  # disconnect doesn't delete shared CLI login
    assert connectors.execution_state("gmail")["enabled"] is False


def test_gmail_rejects_legacy_or_broad_scopes(google_home: Path) -> None:
    gmail_files(google_home, ["gmail.modify"])
    assert not google_connectors.gmail_ready()
    (google_home / ".gmail-mcp/credentials.json").write_text('{"refresh_token":"secret"}')
    assert not google_connectors.gmail_ready()


def test_gcp_reuses_login_without_storing_provider_credential(
    google_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(google_connectors, "gcp_ready", lambda: False)
    assert connectors.status("gcp", home=google_home)["ready"] is False
    monkeypatch.setattr(google_connectors, "gcp_ready", lambda: True)
    assert connectors.enable("gcp", home=google_home)["enabled"] is True
    assert connectors.load_secret("gcp") is None
    assert google_connectors.command("gcp")[0][-1] == google_connectors.GCP_PACKAGE


def test_shared_client_never_reads_provider_credentials(
    google_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DUCKTERM_CONNECTOR_CLIENT_CONFIG", "/missing/explicit-config.json")
    monkeypatch.setattr(connectors, "load_secret", lambda _: pytest.fail("client read a secret"))
    monkeypatch.setattr(
        connectors, "server_command", lambda _: pytest.fail("client launched provider")
    )
    monkeypatch.setattr(connector_client, "statuses", lambda: [{"name": "gmail", "enabled": True}])
    run = Mock()
    monkeypatch.setattr(connector_client, "run", run)
    assert connectors.enable("gmail", home=google_home)["enabled"] is True
    connectors.run("gmail")
    run.assert_called_once_with("gmail")
    with pytest.raises(RuntimeError, match="shared connector host"):
        connectors.enable("gmail", token="must-not-be-sent", home=google_home)
    assert connectors.disable("gmail", home=google_home)["enabled"] is False


def test_broker_google_credentials_are_private_temporary_files(
    google_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GMAIL_CREDENTIALS_PATH", "/must-not-inherit")
    credential = {
        "oauth_json": '{"installed":{"client_id":"client"}}',
        "credentials_json": json.dumps(
            {"tokens": {"refresh_token": "private-token"}, "scopes": ["gmail.readonly"]}
        ),
    }
    with connector_broker.provider_command("gmail", credential, False) as (argv, env):
        path = Path(env["GMAIL_CREDENTIALS_PATH"])
        assert path.stat().st_mode & 0o777 == 0o600
        assert "private-token" in path.read_text()
        assert "private-token" not in json.dumps(argv)
        assert str(path) != "/must-not-inherit"
    assert not path.exists()
    with connector_broker.provider_command(
        "gcp",
        {
            "credentials_json": json.dumps(
                {"type": "authorized_user", "refresh_token": "private-token"}
            ),
            "project": "test-project",
        },
        False,
    ) as (_, env):
        path = Path(env["CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE"])
        assert path.exists()
        assert env["CLOUDSDK_CORE_PROJECT"] == "test-project"
        assert "GOOGLE_APPLICATION_CREDENTIALS" not in env
    assert not path.exists()


def test_shared_host_policy_gates_workspace_and_local_disable(
    google_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = google_home / "broker.json"
    config.write_text(
        json.dumps(
            {
                "mode": "local",
                "workspaces": {"allowed": ["gmail"]},
                "connectors": {"gmail": {"enabled": True}},
            }
        )
    )
    broker = connector_broker.Broker(config)
    connectors._set_enabled("gmail", True)
    assert broker.permitted("allowed", "gmail")["local_state"]["enabled"]
    with pytest.raises(ValueError, match="authorized"):
        broker.permitted("other", "gmail")
    connectors._set_enabled("gmail", False)
    with pytest.raises(ValueError, match="disabled"):
        broker.permitted("allowed", "gmail")


def test_huggingface_broker_has_no_ambient_tokens(google_home: Path, monkeypatch) -> None:
    monkeypatch.setenv("HF_TOKEN", "must-not-leak")
    with connector_broker.provider_command("huggingface", {}, False) as (argv, env):
        assert "HF_TOKEN" not in env
        assert "--header" not in argv
        assert "bouquet=search" in " ".join(argv)


def test_enrollment_registers_granted_providers_before_they_are_enabled(google_home, monkeypatch):
    from contextlib import nullcontext

    settings = {
        "host": "localhost",
        "server_name": "broker",
        "server_ca": "ca.crt",
        "certificate": "client.crt",
        "private_key": "client.key",
    }
    config = google_home / "client.json"
    config.write_text(json.dumps(settings))
    monkeypatch.setattr(
        connector_client,
        "connect",
        lambda *args: (nullcontext(), b'{"connectors":[{"name":"gmail","enabled":false}]}'),
    )
    connector_client.attach(config)
    installed = (google_home / ".codex/config.toml").read_text()
    assert "gmail" in installed and "connector-run" in installed
    assert "client.key" not in installed
    saved = google_home / "duckterm/connector-client.json"
    assert saved.stat().st_mode & 0o777 == 0o600
    assert json.loads(saved.read_text())["certificate"] == str(google_home / "client.crt")


def test_missing_explicit_relay_config_does_not_fall_back(google_home, monkeypatch):
    monkeypatch.setenv("DUCKTERM_CONNECTOR_CLIENT_CONFIG", str(google_home / "missing.json"))
    monkeypatch.setattr(connectors, "server_command", lambda _: pytest.fail("local fallback"))
    rows = connectors.list_status(home=google_home)
    assert all(row["managed"] and not row["ready"] for row in rows)
    with pytest.raises(FileNotFoundError):
        connectors.run("gmail")
