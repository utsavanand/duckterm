"""MCP config writers: touch only our entries, preserve everything else,
never write a config the harness can't parse."""

import json
import tomllib
from pathlib import Path

import pytest

from duckterm.agents import mcp_install


def test_claude_install_preserves_existing_config(tmp_path: Path) -> None:
    path = tmp_path / ".claude.json"
    path.write_text(json.dumps({"numStartups": 7, "mcpServers": {"other": {"command": "x"}}}))

    mcp_install.claude_install(
        "github", "/bin/duckterm", ["connector-run", "github"], home=tmp_path
    )

    config = json.loads(path.read_text())
    assert config["numStartups"] == 7  # untouched
    assert config["mcpServers"]["other"] == {"command": "x"}  # untouched
    assert config["mcpServers"]["github"] == {
        "command": "/bin/duckterm",
        "args": ["connector-run", "github"],
    }


def test_claude_install_creates_file_and_remove_roundtrip(tmp_path: Path) -> None:
    assert mcp_install.claude_installed("github", home=tmp_path) is False
    mcp_install.claude_install("github", "duckterm", ["connector-run", "github"], home=tmp_path)
    assert mcp_install.claude_installed("github", home=tmp_path) is True
    assert mcp_install.claude_remove("github", home=tmp_path) is True
    assert mcp_install.claude_installed("github", home=tmp_path) is False
    assert mcp_install.claude_remove("github", home=tmp_path) is False  # already gone


def _codex_config(tmp_path: Path) -> Path:
    path = tmp_path / ".codex" / "config.toml"
    path.parent.mkdir(parents=True)
    path.write_text(
        'model = "gpt-5"\n'
        "[mcp_servers.node_repl]\n"
        'command = "node"\n'
        'args = ["repl.js"]\n'
        "[mcp_servers.node_repl.env]\n"
        'NODE_ENV = "dev"\n'
    )
    return path


def test_codex_install_preserves_other_servers_and_keys(tmp_path: Path) -> None:
    path = _codex_config(tmp_path)
    mcp_install.codex_install("railway", "/opt/railway", ["mcp"], home=tmp_path)

    config = tomllib.loads(path.read_text())
    assert config["model"] == "gpt-5"
    assert config["mcp_servers"]["node_repl"]["env"] == {"NODE_ENV": "dev"}
    assert config["mcp_servers"]["railway"] == {"command": "/opt/railway", "args": ["mcp"]}


def test_codex_install_is_idempotent_and_remove_keeps_others(tmp_path: Path) -> None:
    path = _codex_config(tmp_path)
    mcp_install.codex_install("github", "duckterm", ["connector-run", "github"], home=tmp_path)
    mcp_install.codex_install("github", "duckterm2", ["connector-run", "github"], home=tmp_path)

    config = tomllib.loads(path.read_text())
    assert config["mcp_servers"]["github"]["command"] == "duckterm2"  # replaced, not duplicated

    assert mcp_install.codex_remove("github", home=tmp_path) is True
    config = tomllib.loads(path.read_text())
    assert "github" not in config["mcp_servers"]
    assert "node_repl" in config["mcp_servers"]  # bystander survives


def test_codex_install_env_subtable_and_escaping(tmp_path: Path) -> None:
    _codex_config(tmp_path)
    mcp_install.codex_install("github", "duckterm", ["run"], env={"TOKEN": 'a"b\\c'}, home=tmp_path)
    config = tomllib.loads((tmp_path / ".codex" / "config.toml").read_text())
    assert config["mcp_servers"]["github"]["env"]["TOKEN"] == 'a"b\\c'


def test_codex_refuses_to_write_over_broken_toml(tmp_path: Path) -> None:
    path = tmp_path / ".codex" / "config.toml"
    path.parent.mkdir(parents=True)
    path.write_text("this is [not valid toml\n")
    with pytest.raises(tomllib.TOMLDecodeError):
        mcp_install.codex_install("github", "duckterm", ["run"], home=tmp_path)
    assert path.read_text() == "this is [not valid toml\n"  # untouched
