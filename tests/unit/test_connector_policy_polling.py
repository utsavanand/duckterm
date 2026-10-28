"""Policy watching does no credential work and preserves revocation changes."""

import json
import os
from pathlib import Path
from unittest.mock import Mock

import pytest

from duckterm import connectors


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path / ".duckterm"))
    monkeypatch.setattr(
        connectors, "_gh_cli_token", Mock(side_effect=AssertionError("token lookup"))
    )
    monkeypatch.setattr(connectors, "load_secret", Mock(side_effect=AssertionError("keychain")))
    return tmp_path


def test_legacy_poll_reads_once_until_config_changes(isolated, monkeypatch):
    home = isolated
    claude = home / ".claude.json"
    claude.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "github": {"command": "duckterm", "args": ["connector-run", "github"]}
                },
                "padding": "x" * 200_000,
            }
        )
    )
    real_claude = connectors._claude_registered
    read = Mock(wraps=real_claude)
    monkeypatch.setattr(connectors, "_claude_registered", read)
    watcher = connectors._RunnerPolicy("github")
    for _ in range(100):
        assert watcher.read() == {"enabled": True, "generation": "legacy"}
    assert read.call_count == 1
    claude.write_text('{"mcpServers": {}}')
    assert watcher.read()["enabled"] is False
    assert read.call_count == 2
    codex = home / ".codex" / "config.toml"
    codex.parent.mkdir()
    codex.write_text('[mcp_servers.github]\ncommand="duckterm"\nargs=["connector-run", "github"]\n')
    assert watcher.read()["enabled"] is True
    codex.unlink()
    assert watcher.read()["enabled"] is False


def test_policy_creation_replacement_and_removal_invalidate(isolated):
    (isolated / ".claude.json").write_text(
        '{"mcpServers": {"github": {"command": "duckterm", "args": ["connector-run", "github"]}}}'
    )
    watcher = connectors._RunnerPolicy("github")
    assert watcher.read()["generation"] == "legacy"
    connectors._save_policy("github", {"enabled": True, "generation": "a"})
    assert watcher.read()["generation"] == "a"
    path = connectors._policy_path("github")
    old_stat = path.stat()
    replacement = path.with_suffix(".replacement")
    replacement.write_text(path.read_text().replace('"a"', '"b"'))
    os.utime(replacement, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
    replacement.replace(path)
    assert watcher.read()["generation"] == "b"
    connectors._save_policy("github", {"enabled": False, "generation": "c"})
    assert watcher.read()["enabled"] is False
    path.unlink()
    assert watcher.read() == {"enabled": True, "generation": "legacy"}


def test_explicit_policy_poll_does_not_reparse_or_read_harness_configs(isolated, monkeypatch):
    connectors._save_policy("github", {"enabled": True, "generation": "a"})
    monkeypatch.setattr(connectors, "_claude_registered", Mock(side_effect=AssertionError))
    monkeypatch.setattr(connectors, "_codex_registered", Mock(side_effect=AssertionError))
    parse = Mock(wraps=json.loads)
    monkeypatch.setattr(connectors.json, "loads", parse)
    watcher = connectors._RunnerPolicy("github")
    for _ in range(100):
        assert watcher.read()["generation"] == "a"
    assert parse.call_count == 1


def test_malformed_changed_policy_does_not_reuse_enabled_state(isolated):
    connectors._save_policy("github", {"enabled": True, "generation": "a"})
    watcher = connectors._RunnerPolicy("github")
    assert watcher.read()["enabled"] is True
    path = connectors._policy_path("github")
    path.write_text("[]")
    with pytest.raises(RuntimeError, match="invalid connector policy"):
        watcher.read()
    path.write_text("{")
    with pytest.raises(json.JSONDecodeError):
        watcher.read()


def test_change_during_read_is_checked_again_next_tick(isolated, monkeypatch):
    path = isolated / ".claude.json"
    path.write_text(
        '{"mcpServers": {"github": {"command": "duckterm", "args": ["connector-run", "github"]}}}'
    )
    original = connectors._claude_registered

    def racing_read(name, *, home):
        enabled = original(name, home=home)
        path.write_text('{"mcpServers": {}}')
        return enabled

    monkeypatch.setattr(connectors, "_claude_registered", racing_read)
    watcher = connectors._RunnerPolicy("github")
    assert watcher.read()["enabled"] is True
    assert watcher.read()["enabled"] is False


def test_legacy_supervisor_stops_on_config_removal_without_repeated_token_lookups(
    isolated, monkeypatch
):
    import subprocess
    import sys
    import time

    bin_dir = isolated / "bin"
    bin_dir.mkdir()
    token_calls = isolated / "token-calls"
    gh = bin_dir / "gh"
    gh.write_text(
        f"#!{sys.executable}\nfrom pathlib import Path\n"
        f"with Path({str(token_calls)!r}).open('a') as f: f.write('call\\n')\n"
        "print('test-only-token')\n"
    )
    gh.chmod(0o700)
    ready = isolated / "ready"
    server = bin_dir / "github-mcp-server"
    server.write_text(
        f"#!{sys.executable}\nimport time\nfrom pathlib import Path\n"
        f"Path({str(ready)!r}).touch()\ntime.sleep(60)\n"
    )
    server.chmod(0o700)
    monkeypatch.setenv("PATH", str(bin_dir))
    config = isolated / ".claude.json"
    config.write_text(
        '{"mcpServers": {"github": {"command": "duckterm", "args": ["connector-run", "github"]}}}'
    )
    proc = subprocess.Popen([sys.executable, "-m", "duckterm.cli", "connector-run", "github"])
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists()
        time.sleep(0.65)  # At least three unchanged supervisor ticks.
        assert proc.poll() is None
        assert token_calls.read_text().splitlines() == ["call", "call"]
        config.write_text('{"mcpServers": {}}')
        proc.wait(timeout=5)
        assert token_calls.read_text().splitlines() == ["call", "call"]
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=5)
