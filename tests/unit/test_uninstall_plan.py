import json
from pathlib import Path

from duckterm import cli
from duckterm.agents import hooks_install
from duckterm.uninstall_plan import footprint, inspect_hook, inventory


def test_inventory_preserves_files_and_data(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    for agent in ("claude-code", "codex", "copilot"):
        hooks_install.install(global_scope=True, project_dir=tmp_path, agent=agent)
    root = tmp_path / ".duckterm"
    root.mkdir()
    (root / "history").write_bytes(b"history")
    before = {
        str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in tmp_path.rglob("*") if p.is_file()
    }
    report = inventory(root, [])
    assert report["data"]["bytes"] == 7
    assert report["data"]["action"] == "keep"
    assert sum(h["owned_commands"] for h in report["hooks"]) > 0
    after = {
        str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in tmp_path.rglob("*") if p.is_file()
    }
    assert before == after


def test_bad_config_redacts_parse_details(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    path = hooks_install.settings_path(global_scope=True, project_dir=tmp_path)
    path.parent.mkdir()
    path.write_text("secret-token: broken JSON")
    result = inspect_hook("claude-code")
    assert result["state"] == "blocked"
    assert "secret-token" not in json.dumps(result)
    assert path.read_text() == "secret-token: broken JSON"


def test_inventory_does_not_follow_data_symlinks(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    other = tmp_path / "external"
    other.mkdir()
    (other / "large").write_bytes(b"x" * 100)
    (root / "link").symlink_to(other, target_is_directory=True)
    assert footprint(root)["bytes"] == 0
    assert footprint(root, limit=0)["complete"] is False


def test_cli_defaults_to_read_only_plan(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path / "data"))
    assert cli.main(["uninstall", "--dry-run", "--project", str(tmp_path / "project")]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["dry_run"] is True
    assert result["project_scope"] == [str(tmp_path / "project")]
    assert not list(tmp_path.iterdir())
