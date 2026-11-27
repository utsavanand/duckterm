"""The default coding harness cascade (owner, 2026-11-27): the setting when it
is installed, else the only one installed, else Claude Code among several."""

import pytest

from duckterm import harness_preferences as hp
from duckterm.core.session_api import APIError
from duckterm.helpers import instance


@pytest.mark.parametrize(
    ("installed", "default", "asked", "expected"),
    [
        ([], None, None, None),
        ([], "codex", None, None),
        (["codex"], None, None, "codex"),
        (["claude-code", "codex"], None, None, "claude-code"),
        (["codex", "claude-code"], None, None, "claude-code"),
        (["codex", "copilot"], None, None, "codex"),
        (["claude-code", "codex"], "codex", None, "codex"),
        (["claude-code"], "codex", None, "claude-code"),  # the setting names an absent harness
        (["claude-code", "codex"], "claude-code", "codex", "codex"),  # a request's own choice wins
        (["claude-code"], None, "codex", "claude-code"),
    ],
)
def test_pick_follows_the_cascade(installed, default, asked, expected):
    assert hp.pick(installed, default, asked) == expected


def test_save_and_load_round_trip_and_reject_unknown_harnesses(tmp_path, monkeypatch):
    monkeypatch.setattr(instance, "home", lambda: tmp_path / "home")
    assert hp.load() == {"default_harness": None}
    assert hp.save({"default_harness": "codex"}) == {"default_harness": "codex"}
    assert hp.load() == {"default_harness": "codex"}
    assert (tmp_path / "home" / hp.FILE).read_text() == '{"default_harness": "codex"}'
    assert hp.save({"default_harness": None}) == {"default_harness": None}
    for bad in (
        {"default_harness": "vim"},
        {"other": 1},
        ["codex"],
        {"default_harness": "codex", "x": 1},
    ):
        with pytest.raises(APIError) as refused:
            hp.save(bad)
        assert refused.value.status == 400
    (tmp_path / "home" / hp.FILE).write_text('{"default_harness": "vim"}')
    assert hp.load() == {"default_harness": None}  # an unknown stored value reads as unset


def test_describe_reports_the_setting_the_present_harnesses_and_the_effective_one(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(instance, "home", lambda: tmp_path / "home")
    monkeypatch.setattr(hp, "present", lambda: ["claude-code", "codex"])
    assert hp.describe() == {
        "default_harness": None,
        "present": ["claude-code", "codex"],
        "effective": "claude-code",
    }
    hp.save({"default_harness": "codex"})
    assert hp.describe()["effective"] == "codex"
    assert hp.command_for("claude-code") == "claude" and hp.command_for("codex") == "codex"
