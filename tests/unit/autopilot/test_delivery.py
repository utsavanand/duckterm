"""What happens after a run's pull request is open: reading CI, waiting for
it, and the merge policy and gate. GitHub is faked; the policy script is real."""

from typing import Any

import pytest

from duckterm.autopilot import delivery, github_push

ROUTINE: dict[str, Any] = {
    "surfaces": ["web"],
    "user_visible": False,
    "regression": {"level": "none", "features": []},
    "security": {"level": "none", "areas": []},
    "ai": "deterministic",
    "data": "none",
    "size": "small",
}


def _github(monkeypatch: pytest.MonkeyPatch, answers: dict[str, Any]) -> None:
    def api(token: str, method: str, path: str, body: Any = None) -> Any:
        for prefix, value in answers.items():
            if path.startswith(prefix):
                return value
        raise AssertionError(path)

    monkeypatch.setattr(github_push, "_api", api)


@pytest.mark.parametrize(
    ("runs", "statuses", "state", "want"),
    [
        ([], [], "pending", "none"),
        ([{"status": "completed", "conclusion": "success"}], [], "pending", "pass"),
        ([{"status": "in_progress", "conclusion": None}], [], "pending", "pending"),
        ([], [{"state": "pending", "context": "ci"}], "pending", "pending"),
        ([], [{"state": "success", "context": "ci"}], "success", "pass"),
        ([], [{"state": "error", "context": "ci", "description": "x"}], "failure", "fail"),
    ],
)
def test_ci_on_a_commit_is_read_from_check_runs_and_statuses(
    monkeypatch: pytest.MonkeyPatch,
    runs: list[dict[str, Any]],
    statuses: list[dict[str, Any]],
    state: str,
    want: str,
) -> None:
    _github(
        monkeypatch,
        {
            "/repos/o/r/commits/abc/check-runs": {"check_runs": runs},
            "/repos/o/r/commits/abc/status": {"state": state, "statuses": statuses},
        },
    )
    assert github_push.checks("o/r", "abc", "t")[0] == want


def test_a_failed_check_logs_its_summary_and_annotations(monkeypatch: pytest.MonkeyPatch) -> None:
    failed = {
        "id": 9,
        "name": "tests",
        "status": "completed",
        "conclusion": "failure",
        "output": {"title": "1 failed", "summary": "test_row failed", "annotations_count": 1},
    }
    _github(
        monkeypatch,
        {
            "/repos/o/r/commits/abc/check-runs": {"check_runs": [failed]},
            "/repos/o/r/commits/abc/status": {"state": "pending", "statuses": []},
            "/repos/o/r/check-runs/9/annotations": [
                {"path": "row.py", "start_line": 3, "message": "assert 1 == 2"}
            ],
        },
    )
    result, log = github_push.checks("o/r", "abc", "t")
    assert result == "fail"
    assert "## tests: failure" in log and "test_row failed" in log
    assert "row.py:3: assert 1 == 2" in log


@pytest.mark.parametrize(
    ("seen", "want"),
    [
        (["pending", "pending", "pass"], "pass"),
        (["none"] * 20, "none"),
        (["pending"] * 200, "timeout"),
    ],
)
def test_waiting_for_ci_ends_on_a_result_on_no_ci_or_on_the_timeout(
    monkeypatch: pytest.MonkeyPatch, seen: list[str], want: str
) -> None:
    results = iter(seen)
    monkeypatch.setattr(github_push, "checks", lambda repo, sha, token: (next(results), ""))
    slept: list[float] = []
    result = delivery.wait_ci("o/r", "abc", "t", sleep=slept.append, now=lambda: sum(slept))
    assert result[0] == want


def test_a_job_log_is_cut_to_what_led_to_its_first_error() -> None:
    log = "\n".join(
        [f"2026-11-22T08:14:{i:02}.0Z setup line {i}" for i in range(200)]
        + ["2026-11-22T08:15:00.0Z version mismatch: kaho.py says 2.4.7"]
        + ["2026-11-22T08:15:01.0Z ##[error]Process completed with exit code 1."]
        + [f"2026-11-22T08:15:02.0Z cleanup {i}" for i in range(300)]
    )
    cut = github_push.around_first_error(log)
    assert "version mismatch: kaho.py says 2.4.7\n##[error]Process completed" in cut
    assert "08:14:" not in cut and "cleanup 299" not in cut and "setup line 0\n" not in cut


def test_path_rules_name_each_changed_file_they_match() -> None:
    hits = delivery.path_hits(
        ["src/auth/login.py", "README.md", ".github/workflows/ci.yml"],
        {"security": ["src/auth/*", ".github/workflows/*"], "data": ["db/*"]},
    )
    assert hits == [
        {"field": "security", "path": "src/auth/login.py"},
        {"field": "security", "path": ".github/workflows/ci.yml"},
    ]


@pytest.mark.parametrize(
    ("spec", "diff", "hits", "gated", "reasons"),
    [
        (ROUTINE, ROUTINE, [], False, []),
        (ROUTINE, ROUTINE, [], True, ["the spec gate was required"]),
        (
            {**ROUTINE, "regression": {"level": "contained", "features": ["inbox"]}},
            ROUTINE,
            [],
            False,
            ["could break inbox"],
        ),
        ({**ROUTINE, "user_visible": True}, ROUTINE, [], False, ["user visible"]),
        (
            ROUTINE,
            {**ROUTINE, "security": {"level": "touches", "areas": ["sandbox"]}},
            [],
            False,
            ["the review rated it higher than the spec: security"],
        ),
        (
            ROUTINE,
            ROUTINE,
            [{"field": "data", "path": "db/schema.sql"}],
            False,
            ["path rule (data): db/schema.sql"],
        ),
        (ROUTINE, None, [], False, ["the security review gave no readable classification"]),
    ],
)
def test_the_merge_policy_asks_for_each_risk_and_never_from_the_issue(
    spec: Any, diff: Any, hits: list[dict[str, str]], gated: bool, reasons: list[str]
) -> None:
    assert delivery.merge_policy(spec, diff, hits, gated) == reasons


@pytest.mark.parametrize(
    ("reasons", "mode", "gate"),
    [
        ([], "on", None),
        ([], "shadow", {"shadow": True, "shadow_run": 5, "why": ""}),
        ([], "off", {"shadow": False, "shadow_run": None, "why": "you approve every merge"}),
        (["user visible"], "on", {"shadow": False, "shadow_run": None, "why": "user visible"}),
    ],
)
def test_a_merge_asks_unless_auto_merge_is_on_and_nothing_needs_a_person(
    reasons: list[str], mode: str, gate: dict[str, Any] | None
) -> None:
    found = delivery.merge_gate(reasons, mode, 4, "abc", {"number": 5})
    if gate is None:
        assert found is None
    else:
        assert found is not None
        assert {k: found[k] for k in gate} == gate
        assert (found["step"], found["head"], found["question"]) == (
            "merge-gate",
            "abc",
            "Merge pull request #5?",
        )
