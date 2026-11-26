"""Release defaults gate work before side effects; local core stays available."""

import asyncio
import json
from unittest.mock import Mock

import pytest
from test_session_api import Writer

from duckterm import editions, release_features
from duckterm.core.session_api import APIError
from duckterm.llm import ai_settings, backend
from duckterm.persistence import backup
from duckterm.persistence.history import HistoryStore
from duckterm.server import Server


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path))
    monkeypatch.delenv("DUCKTERM_PROFILE", raising=False)
    return tmp_path


def configure(root, **flags):
    (root / "feature-flags.json").write_text(json.dumps({"flags": flags}))


@pytest.mark.parametrize("edition", [editions.LITE, editions.CODE, editions.FULL])
def test_release_defaults_preserve_core(isolated, edition):
    effective = release_features.apply(edition)
    assert not effective.features & release_features.FLAGS.keys()
    assert not effective.includes("remote_workspaces")
    assert {
        "terminal",
        "harnesses",
        "inbox",
        "checkpoints",
        "context",
        "handoff",
        "forks",
    } <= effective.features
    if edition != editions.LITE:
        assert effective.includes("folder_chat")


@pytest.mark.parametrize("edition", [editions.LITE, editions.CODE, editions.FULL])
def test_opt_in_does_not_unlock_edition(isolated, edition):
    configure(isolated, **dict.fromkeys(release_features.FLAGS, True))
    effective = release_features.apply(edition)
    assert effective.includes("byoc") == (edition.name == "full")
    assert effective.includes("remote_workspaces") == (edition.name == "full")
    assert effective.includes("backup") and effective.includes("byok")


@pytest.mark.parametrize(
    "text", ["null", "[]", "{", '{"flags":{"backup":"true"}}', '{"flags":{"backups":true}}']
)
def test_invalid_configuration_fails_closed(isolated, text):
    (isolated / "feature-flags.json").write_text(text)
    assert release_features.overrides()[1]
    assert not release_features.enabled("backup")


def request(server, method, path, body=None):
    w = Writer()

    async def run():
        await server._dispatch(
            method,
            path,
            asyncio.StreamReader(),
            w,
            {"x-duckterm-token": server.token},
            json.dumps(body or {}).encode(),
        )

    asyncio.run(run())
    headers, data = w.data.split(b"\r\n\r\n", 1)
    return int(headers.split()[1]), json.loads(data)


@pytest.mark.parametrize(
    "method,path,body,flag",
    [
        ("GET", "/harnesses", None, "skill_kits"),
        ("POST", "/harnesses/install", {}, "skill_kits"),
        ("GET", "/backup", None, "backup"),
        ("POST", "/backup", {}, "backup"),
        ("POST", "/ai-settings/key", {"provider": "anthropic", "key": "fixture"}, "byok"),
        ("POST", "/ai-settings", {"backend": "api"}, "byok"),
        ("GET", "/collaboration/status", None, "cross_computer_collaboration"),
        ("POST", "/api/v1/collaboration/exchange", {}, "cross_computer_collaboration"),
        ("POST", "/transfers/continue", {}, "remote_workspaces"),
    ],
)
def test_server_denies_disabled_work(isolated, method, path, body, flag):
    history = HistoryStore(isolated / "db.sqlite")
    try:
        server = Server(history=history, edition=editions.FULL)
        assert server.collaboration.store is None
        code, result = request(server, method, path, body)
        assert code == 403, result
        assert flag in result["error"]
        assert server._backup_jobs is None
        assert not (isolated / "backup-state.json").exists()
    finally:
        history.close()


def test_inventory_and_local_override_survive_restart(isolated):
    configure(isolated, **dict.fromkeys(release_features.FLAGS, True))
    for _ in range(2):
        history = HistoryStore(isolated / "db.sqlite")
        try:
            server = Server(history=history, edition=editions.FULL)
            code, inventory = request(server, "GET", "/feature-flags")
            assert code == 200 and len(inventory["flags"]) == 11
            assert {"autopilot", "oracle_messages", "fleet_monitor"} <= {
                f["id"] for f in inventory["flags"]
            }
            assert all(
                f["enabled"] == (f["id"] in release_features.FLAGS) and not f["default"]
                for f in inventory["flags"]
            )
            assert request(server, "GET", "/backup")[0] == 200
        finally:
            history.close()


def test_disabled_saved_byok_never_reads_keys_or_falls_back(isolated, monkeypatch):
    ai_settings.save({"backend": "api"})
    key = Mock(side_effect=AssertionError("must not read keys"))
    generate = Mock(side_effect=AssertionError("must not silently switch provider"))
    monkeypatch.setattr(ai_settings, "key", key)
    monkeypatch.setattr(backend.memory_provider, "generate", generate)
    assert not backend.ready()
    with pytest.raises(APIError, match="disabled"):
        asyncio.run(backend.complete("fixture", tier="fast"))
    assert not key.called and not generate.called


def test_backup_disabled_preserves_saved_configuration(isolated):
    state = isolated / "backup-state.json"
    state.write_text('{"destination":"/Volumes/Backup"}')
    with pytest.raises(APIError, match="disabled"):
        backup.create()
    assert state.read_text() == '{"destination":"/Volumes/Backup"}'
    assert not (isolated / "backups").exists()


def test_cloud_backup_needs_both_flags(isolated):
    configure(isolated, backup=True)
    with pytest.raises(APIError, match="disabled"):
        backup.create("gs://fixture/backups")
    assert not (isolated / "backups").exists()


def test_flag_file_is_instance_local(isolated, monkeypatch, tmp_path):
    configure(isolated, backup=True)
    assert release_features.enabled("backup")
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setenv("DUCKTERM_HOME", str(other))
    assert not release_features.enabled("backup")


def test_existing_skill_kit_opt_in_survives_until_release_configuration_exists(isolated):
    (isolated / "flags.json").write_text(json.dumps({"enabled": ["skill_kits"]}))
    assert release_features.enabled("skill_kits")
    assert release_features.apply(editions.FULL).includes("skill_kits")
    assert not release_features.enabled("backup")
    configure(isolated, skill_kits=False)
    assert not release_features.enabled("skill_kits")
    configure(isolated)
    assert not release_features.enabled("skill_kits")


def test_malformed_legacy_flag_file_does_not_enable_release_features(isolated):
    (isolated / "flags.json").write_text("{broken")
    assert release_features.overrides()[1]
    assert not release_features.enabled("skill_kits")


@pytest.mark.parametrize("edition", [editions.LITE, editions.CODE, editions.FULL])
def test_disabled_errors_distinguish_flags_from_edition(isolated, edition):
    history = HistoryStore(isolated / "errors.sqlite")
    try:
        server = Server(history=history, edition=edition)
        for method, path in [
            ("GET", "/harnesses"),
            ("GET", "/backup"),
            ("DELETE", "/ai-settings/key"),
        ]:
            code, result = request(server, method, path)
            assert code == 403
            assert "disabled by its feature flag" in result["error"]
        with pytest.raises(APIError, match="disabled by its feature flag"):
            server.require_feature("harness_management")
        expected = (
            "disabled by its feature flag"
            if edition == editions.FULL
            else "unavailable in Duckterm"
        )
        with pytest.raises(APIError, match=expected):
            server.require_feature("remote_workspaces")
    finally:
        history.close()


@pytest.mark.parametrize("enabled", [False, True])
def test_optional_cli_gate_uses_release_flags_over_legacy(isolated, monkeypatch, capsys, enabled):
    from pathlib import Path

    from duckterm import cli

    monkeypatch.delenv("DUCKTERM_DESKTOP_PROFILE", raising=False)
    monkeypatch.delenv("DUCKTERM_INSTANCE", raising=False)
    monkeypatch.setenv("DUCKTERM_EDITION", "full")
    (isolated / "flags.json").write_text(json.dumps({"enabled": [] if enabled else ["skill_kits"]}))
    configure(isolated, skill_kits=enabled)
    sample = Path(__file__).resolve().parents[2] / "examples/skill-kits/sample-kit"
    assert cli.main(["kit", "validate", str(sample)]) == (0 if enabled else 1)
    out = capsys.readouterr()
    if enabled:
        assert "13 components" in out.out
        assert not out.err
    else:
        assert "skill_kits is disabled by its feature flag" in out.err
        assert not out.out


@pytest.mark.parametrize("preset", [editions.LITE, editions.CODE, editions.FULL])
@pytest.mark.parametrize("release", [None, {}, {"skill_kits": False}, {"skill_kits": True}])
def test_development_flags_survive_release_config_without_duplicate_rows(isolated, preset, release):
    (isolated / "flags.json").write_text(json.dumps({"enabled": sorted(editions.DEVELOPMENT)}))
    if release is not None:
        configure(isolated, **release)
    values, error = release_features.overrides()
    effective = release_features.apply(editions._with_flags(preset), values)
    assert effective.includes("home")
    assert effective.includes("background_rule_suggestions")
    assert effective.includes("skill_kits") == (release is None or release.get("skill_kits", False))
    rows = release_features.inventory(effective, values, error)["flags"]
    ids = [row["id"] for row in rows]
    assert len(ids) == len(set(ids))
    assert set(ids) == editions.DEVELOPMENT | release_features.FLAGS.keys()
    assert all(row["enabled"] == effective.includes(row["id"]) for row in rows)
    assert editions.request_features("GET", "/layouts/home") == ("home",)


def test_flag_document_has_one_row_per_effective_flag():
    from pathlib import Path

    doc = Path(__file__).resolve().parents[2] / "docs/feature-flags.md"
    text = doc.read_text()
    for flag in editions.DEVELOPMENT | release_features.FLAGS.keys():
        assert text.count(f"| `{flag}` |") == 1
