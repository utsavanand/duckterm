import pytest

from duckterm.runtimes.opencode_identity import Selection
from duckterm.runtimes.opencode_protocol import OpenCodeError


def observation(project, **changes):
    return {
        "version": "1.18.34",
        "generation": "current",
        "directory": str(project.resolve()),
        "pid": 12,
        "route": "session",
        "sessionID": "ses_expected",
        "ready": True,
        "sequence": 1,
        **changes,
    }


def test_selection_not_api_success_gates_prompt_and_later_switch_is_contested(tmp_path):
    binding = Selection("current", str(tmp_path.resolve()), 12, "ses_expected")
    with pytest.raises(OpenCodeError):
        binding.require_selected()
    home = binding.observe(observation(tmp_path, route="home", sessionID=None))
    with pytest.raises(OpenCodeError):
        home.require_selected()
    ready = home.observe(observation(tmp_path, sequence=2))
    ready.require_selected()
    changed = ready.observe(observation(tmp_path, sequence=3, sessionID="ses_other"))
    assert changed.session_id == "ses_expected" and changed.contested
    returned = changed.observe(observation(tmp_path, sequence=4))
    with pytest.raises(OpenCodeError):
        returned.require_selected()


@pytest.mark.parametrize(
    "changes",
    [
        {"generation": "old"},
        {"version": "new"},
        {"pid": 13},
        {"pid": True},
        {"directory": "/other"},
        {"sequence": 0},
        {"sequence": True},
    ],
)
def test_foreign_and_stale_observations_do_not_rebind_or_grant_readiness(tmp_path, changes):
    binding = Selection("current", str(tmp_path.resolve()), 12, "ses_expected")
    with pytest.raises(OpenCodeError):
        binding.observe(observation(tmp_path, **changes))
    assert not binding.selected and binding.session_id == "ses_expected"


def test_sequence_gap_accepts_new_snapshot_but_cannot_replay_an_older_one(tmp_path):
    binding = Selection("current", str(tmp_path.resolve()), 12, "ses_expected")
    ready = binding.observe(observation(tmp_path, sequence=12))
    assert ready.selected
    with pytest.raises(OpenCodeError):
        ready.observe(observation(tmp_path, sequence=11))


def test_noncanonical_directory_refuses_before_launch(tmp_path):
    alias = tmp_path / "alias"
    target = tmp_path / "target"
    target.mkdir()
    alias.symlink_to(target, target_is_directory=True)
    with pytest.raises(OpenCodeError, match="canonical"):
        Selection("current", str(alias), 12, "ses_expected")


def test_selection_proof_expires_without_heartbeat(tmp_path):
    binding = Selection("current", str(tmp_path.resolve()), 12, "ses_expected")
    ready = binding.observe(observation(tmp_path), now=10.0)
    ready.require_selected(now=12.0)
    with pytest.raises(OpenCodeError):
        ready.require_selected(now=16.0)
    with pytest.raises(OpenCodeError):
        ready.require_selected(now=9.0)
