"""Release publication must fail closed when source or CI cannot be verified."""

import json

import pytest
from scripts import release_preflight

COMMIT = "a" * 40


@pytest.fixture
def commands(monkeypatch: pytest.MonkeyPatch) -> dict:
    state = {
        "dirty": "",
        "branch": "main",
        "commit": COMMIT,
        "runs": [
            {
                "headSha": COMMIT,
                "headBranch": "main",
                "event": "push",
                "status": "completed",
                "conclusion": "success",
                "url": "https://example.test/ci",
            }
        ],
        "calls": [],
    }

    def output(*args: str) -> str:
        state["calls"].append(args)
        if args[:2] == ("git", "status"):
            return state["dirty"]
        if args[:2] == ("git", "branch"):
            return state["branch"]
        if args[:2] == ("git", "rev-parse"):
            return state["commit"]
        assert args[:3] == ("gh", "run", "list")
        assert args[args.index("--commit") + 1] == state["commit"]
        assert args[args.index("--workflow") + 1] == "ci.yml"
        if state.get("offline"):
            raise OSError("GitHub unavailable")
        return json.dumps(state["runs"])

    monkeypatch.setattr(release_preflight, "output", output)
    return state


def test_clean_source_with_successful_ci_can_release(commands: dict) -> None:
    assert release_preflight.verify() == COMMIT
    assert release_preflight.verify(COMMIT) == COMMIT


@pytest.mark.parametrize("dirty", [" M src/code.py", "?? untracked.py", "M  staged.py"])
def test_pending_changes_block_before_querying_ci(commands: dict, dirty: str) -> None:
    commands["dirty"] = dirty
    with pytest.raises(RuntimeError, match="clean checkout"):
        release_preflight.verify()
    assert all(call[0] == "git" for call in commands["calls"])


def test_feature_branch_cannot_release(commands: dict) -> None:
    commands["branch"] = "feature"
    with pytest.raises(RuntimeError, match="main branch"):
        release_preflight.verify()


def test_commit_changed_during_build_cannot_release(commands: dict) -> None:
    with pytest.raises(RuntimeError, match="HEAD changed"):
        release_preflight.verify("b" * 40)


@pytest.mark.parametrize("runs", [[], None, {}, [None]])
def test_missing_ci_evidence_blocks_release(commands: dict, runs) -> None:
    commands["runs"] = runs
    with pytest.raises(RuntimeError, match="no main push CI"):
        release_preflight.verify()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("headSha", "b" * 40),
        ("headBranch", "feature"),
        ("event", "pull_request"),
        ("status", "queued"),
        ("status", "in_progress"),
        ("conclusion", "failure"),
        ("conclusion", "cancelled"),
        ("conclusion", "skipped"),
        ("conclusion", None),
    ],
)
def test_only_completed_success_for_this_commit_is_accepted(
    commands: dict, field: str, value: str | None
) -> None:
    commands["runs"][0][field] = value
    with pytest.raises(RuntimeError, match="has not passed"):
        release_preflight.verify()


def test_api_failure_does_not_allow_release(commands: dict) -> None:
    commands["offline"] = True
    with pytest.raises(OSError, match="unavailable"):
        release_preflight.verify()
