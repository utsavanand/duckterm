"""Startup and standalone commands agree without modifying the session database."""

import json

import pytest

from duckterm import cli, editions
from duckterm.persistence.history import HistoryStore


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path))
    monkeypatch.delenv("DUCKTERM_EDITION", raising=False)
    return tmp_path


def test_existing_install_defaults_to_full_without_writing(home):
    assert editions.configured() is editions.FULL
    assert list(home.iterdir()) == []


def test_successful_selection_is_instance_local_and_environment_wins(home, monkeypatch):
    editions.remember(editions.LITE)
    assert editions.configured() is editions.LITE
    assert json.loads((home / "edition.json").read_text()) == {"id": "lite"}
    monkeypatch.setenv("DUCKTERM_EDITION", "code")
    assert editions.configured() is editions.CODE
    assert json.loads((home / "edition.json").read_text()) == {"id": "lite"}
    monkeypatch.delenv("DUCKTERM_EDITION")
    monkeypatch.setenv("DUCKTERM_HOME", str(home / "another-instance"))
    assert editions.configured() is editions.FULL


@pytest.mark.parametrize("value", ["", "invalid", "FULL"])
def test_invalid_environment_is_rejected_before_database_or_restart(home, monkeypatch, value):
    monkeypatch.setenv("DUCKTERM_EDITION", value)
    with pytest.raises(ValueError, match="edition"):
        HistoryStore(home / "state.sqlite")
    assert not (home / "state.sqlite").exists()
    monkeypatch.setattr(
        cli, "_duckterm_responds", lambda *args: pytest.fail("restart probed before validating")
    )
    with pytest.raises(ValueError, match="edition"):
        cli._restart("127.0.0.1", 4300)
    assert cli.main(["restart"]) == 1


@pytest.mark.parametrize("value", ['{"id":"unknown"}', "null", "[]", "{", '{"id":false}'])
def test_invalid_saved_selection_never_falls_back_to_full(home, value):
    (home / "edition.json").write_text(value)
    with pytest.raises(ValueError):
        editions.configured()


@pytest.mark.parametrize(
    "command",
    [
        ["voice", "install"],
        ["snapshot"],
        ["connector-run", "github"],
        ["connector-serve"],
        ["connector-service"],
        ["connector-attach", "--config", "missing"],
        ["connector-admin", "github"],
    ],
)
def test_lite_cli_blocks_before_optional_handler(home, monkeypatch, capsys, command):
    editions.remember(editions.LITE)
    monkeypatch.setattr(cli, "_voice", lambda *args: pytest.fail("voice ran"))
    monkeypatch.setattr(cli, "_snapshot", lambda *args: pytest.fail("snapshot ran"))
    assert cli.main(command) == 1
    assert "unavailable in Duckterm Lite" in capsys.readouterr().err
    assert list(home.iterdir()) == [home / "edition.json"]


def test_cli_edition_option_is_startup_only(home, monkeypatch):
    editions.remember(editions.FULL)
    received = []
    monkeypatch.setattr(
        cli, "_serve", lambda *args, **kwargs: received.append(editions.configured()) or 0
    )
    # Restore the environment after the CLI's intentional process-wide selection.
    monkeypatch.setenv("DUCKTERM_EDITION", "full")
    assert cli.main(["serve", "--edition", "lite"]) == 0
    assert received == [editions.LITE]
    # Configuration is published only when a real server successfully listens.
    assert json.loads((home / "edition.json").read_text()) == {"id": "full"}
