"""A run on the built-in flow stops at its spec gate with no sandbox when the
spec policy asks for one, and carries on from the owner's answer in a new one.
The kit runner and the kit's policy script are real; the sandbox, gateway and
model steps are fakes."""

import hashlib
import json
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from duckterm.autopilot import delivery, executor, github_push, runs, store
from duckterm.kits import runner

SPEC = "# Fix\n\nProblem: the row cannot be collapsed.\n"
ROUTINE = {
    "surfaces": ["web"],
    "user_visible": False,
    "regression": {"level": "none", "features": []},
    "security": {"level": "none", "areas": []},
    "ai": "deterministic",
    "data": "none",
    "size": "small",
}


class _Steps(runner.Executor):
    """Answers each model step as Claude Code would, on the host."""

    name = "autopilot"
    calls: list[str] = []
    prompts: list[str] = []
    classification: dict[str, Any] = {}
    vary = True  # each spec attempt writes a different spec
    triage = "ready"
    fail_step = ""  # a step whose process fails every attempt
    qa_fail_once = False
    diffs: list[bool] = []  # per review: the diff file matches its stated hash

    def __init__(self, box: Any, dirs: executor.RunDirs, token: str, branch: str) -> None:
        self.box = box
        self.dirs = dirs

    # commit each step's work to the run branch, as the real executor does
    after_step = executor.AutopilotExecutor.after_step

    def harness_ready(self, *_: Any, **__: Any) -> str | None:
        return None

    def run_process(
        self, argv: list[str], *, cwd: Path, env: dict[str, str], stdin: str | None, timeout: float
    ) -> runner.ProcessResult:
        step = env["SKILL_KIT_STEP"]
        if argv[0].endswith(".py"):  # the kit's own scripts run for real
            done = subprocess.run(
                argv, cwd=cwd, input=stdin, capture_output=True, text=True, timeout=timeout
            )
            return runner.ProcessResult(done.returncode, done.stdout, done.stderr)
        self.calls.append(step)
        self.prompts.append(" ".join(argv))
        out = Path(cwd) / env["SKILL_KIT_OUTPUT_DIR"]
        structured: dict[str, Any] = {"status": "done"}
        if step == "triage":
            (out / "questions.md").write_text("Ready: the row and its toggle are named.\n")
            structured["verdict"] = self.triage
        if step == "spec":
            (out / "spec.md").write_text(
                SPEC + "\n" * (self.calls.count("spec") if self.vary else 1)
            )
            (out / "classification.json").write_text(json.dumps(self.classification))
        if step == "build":
            (Path(cwd) / "fix.txt").write_text("fixed\n")
        if step == "fix-ci":
            (Path(cwd) / "fix.txt").write_text(f"fixed for CI {self.calls.count('fix-ci')}\n")
        if step == "qa":
            (out / "qa.md").write_text("Pass.\n")
            failing = self.qa_fail_once and self.calls.count("qa") == 1
            structured["verdict"] = "fail" if failing else "pass"
        if step == "security":
            (out / "security.md").write_text("No findings.\n")
            (out / "classification.json").write_text(json.dumps(self.classification))
            structured["verdict"] = "approve"
        if step in ("qa", "security"):
            self.diffs.append(_diff_matches(argv[-1], Path(cwd)))
        if argv[0] == "codex":  # Codex writes its last message to the -o file
            Path(argv[argv.index("-o") + 1]).write_text(json.dumps(structured))
            return runner.ProcessResult(0, "", "")
        body = {"type": "result", "subtype": "success", "structured_output": structured}
        if self.fail_step == step:
            body = {"type": "result", "subtype": "error", "is_error": True}
            return runner.ProcessResult(1, json.dumps(body), "")
        # stream-json: the tool calls first, the result object last
        call = {"type": "tool_use", "name": "Bash", "input": {"command": f"run {step}"}}
        said = {"type": "assistant", "message": {"content": [call]}}
        return runner.ProcessResult(0, json.dumps(said) + "\n" + json.dumps(body), "")
        return runner.ProcessResult(0, json.dumps(body), "")


def _diff_matches(prompt: str, project: Path) -> bool:
    inputs = json.loads(prompt.split("root):\n", 1)[1].split("\nWrite each file", 1)[0])
    diff = inputs["diff"]
    data = (project / diff["path"]).read_bytes()
    return bool(data) and hashlib.sha256(data).hexdigest() == diff["sha256"]


_comments: list[tuple[str, int, str]] = []


class _Gateway:
    minted: list[int] = []

    def __init__(self, **_: Any) -> None:
        self.name = "gw"

    def ensure(self, **_: Any) -> None:
        pass

    def mint(self, run_id: str, *, budget: int, ttl: float) -> str:
        self.minted.append(budget)
        return "tok"

    def usage(self, token: str) -> dict[str, float]:
        return {"used": 1_000_000.5}

    def revoke(self, token: str) -> None:
        pass


class _Box:
    live = 0

    def __init__(self, *_: Any) -> None:
        self.name = "box"

    @classmethod
    def create(cls, *_: Any, **__: Any) -> "_Box":
        cls.live += 1
        return cls()

    def trust(self, path: str) -> None:
        pass

    def destroy(self) -> None:
        type(self).live -= 1


class _NowThread:
    def __init__(self, target: Any, args: tuple[Any, ...], **_: Any) -> None:
        self.target, self.args = target, args

    def start(self) -> None:
        self.target(*self.args)


@pytest.fixture
def published(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    monkeypatch.setenv("DUCKTERM_HOME", str(tmp_path / "home"))
    source = tmp_path / "source"
    source.mkdir()
    git = ["git", "-C", str(source), "-c", "commit.gpgsign=false"]
    subprocess.run(["git", "init", "-q", "-b", "main", str(source)], check=True)
    (source / "README.md").write_text("hello\n")
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run(
        [*git, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"], check=True
    )
    monkeypatch.setattr(
        runs,
        "prepare_github",
        lambda repo, ref, branch, root: executor.prepare(str(source), "main", branch, root),
    )
    monkeypatch.setattr(runs, "readiness", lambda: [])
    monkeypatch.setattr(runs, "subscription_token", lambda: "sub")
    monkeypatch.setattr(runs, "Gateway", _Gateway)
    monkeypatch.setattr(runs, "Sandbox", _Box)
    monkeypatch.setattr(runs, "AutopilotExecutor", _Steps)
    # only runs.py's own threads run inline; the runner's pool for parallel
    # steps needs real ones
    monkeypatch.setattr(
        runs, "threading", SimpleNamespace(Thread=_NowThread, Timer=threading.Timer)
    )
    _Steps.calls, _Steps.prompts, _Gateway.minted, _Box.live = [], [], [], 0
    _Steps.vary, _Steps.triage, _Steps.qa_fail_once = True, "ready", False
    _Steps.diffs = []
    _Steps.fail_step = ""
    _Steps.classification = {**ROUTINE, "security": {"level": "touches", "areas": ["auth"]}}
    out: list[str] = []
    monkeypatch.setattr(runs, "_publish", lambda root, meta, *_: out.append(meta["id"]))
    # status comments go to a list, never to GitHub
    _comments.clear()
    monkeypatch.setattr(
        github_push, "comment", lambda repo, n, body, token: _comments.append((repo, n, body)) or 1
    )
    return out


def _start() -> str:
    entry = {
        "id": "a1",
        "name": "Fix bugs",
        "flow": "autopilot/issue-to-merge",
        "repo": "o/r",
        "budget_m": 5,
    }
    return runs.start(entry, "github-issue", {"number": 7, "title": "Row stuck"})


def test_a_run_waits_at_the_spec_with_no_sandbox_and_shows_the_spec(published: list[str]) -> None:
    run = runs.get(_start())
    assert run["state"] == "waiting_gate"
    assert "ended_at" not in run
    gate = run["gate"]
    assert {k: gate[k] for k in ("step", "question", "choices", "shown", "why")} == {
        "step": "spec-gate",
        "question": "Approve the fix spec?",
        "choices": ["approve", "revise", "reject"],
        "shown": SPEC + "\n",
        "why": "security touches auth",
    }
    assert run["classification"]["security"] == {"level": "touches", "areas": ["auth"]}
    assert _Steps.calls == ["triage", "spec"]
    assert _Box.live == 0
    assert published == []
    assert runs.summary()["waiting_gate"] == 1


def test_approving_builds_in_a_new_sandbox_on_what_is_left_of_the_budget(
    published: list[str],
) -> None:
    run_id = _start()
    runs.answer(run_id, {"step": "spec-gate", "answer": "approve"})
    run = runs.get(run_id)
    assert run["state"] == "succeeded"
    assert _Steps.calls[:3] == ["triage", "spec", "build"]
    assert sorted(_Steps.calls[3:]) == ["qa", "security"]  # in parallel, on Codex
    assert sum(p.startswith("codex exec") for p in _Steps.prompts) == 2
    # both reviewers read the same diff the hash names (they raced for it)
    assert _Steps.diffs == [True, True]
    assert _Gateway.minted == [5_000_000, 3_999_999]
    assert run["tokens"] == 2_000_001
    assert [(a["answer"], a["by"]) for a in run["answers"]] == [("approve", "owner")]
    assert published == [run_id]
    assert _Box.live == 0


def test_rejecting_ends_the_run_without_building_or_publishing(published: list[str]) -> None:
    run_id = _start()
    runs.answer(run_id, {"step": "spec-gate", "answer": "reject"})
    run = runs.get(run_id)
    assert run["state"] == "rejected"
    assert run["ended_at"]
    assert _Steps.calls == ["triage", "spec"]
    assert published == []


@pytest.mark.parametrize(
    "body",
    [{"step": "build", "answer": "approve"}, {"step": "spec-gate", "answer": "yes"}],
)
def test_an_answer_must_name_the_waiting_gate_and_one_of_its_choices(
    published: list[str], body: dict[str, str]
) -> None:
    run_id = _start()
    with pytest.raises(ValueError):
        runs.answer(run_id, body)
    assert runs.get(run_id)["state"] == "waiting_gate"


def test_a_gate_is_answered_once(published: list[str]) -> None:
    run_id = _start()
    runs.answer(run_id, {"step": "spec-gate", "answer": "approve"})
    with pytest.raises(ValueError):
        runs.answer(run_id, {"step": "spec-gate", "answer": "reject"})
    with pytest.raises(KeyError):
        runs.answer("nope", {"step": "spec-gate", "answer": "approve"})


def test_a_routine_fix_needs_no_spec_approval(published: list[str]) -> None:
    _Steps.classification = ROUTINE
    run = runs.get(_start())
    assert run["state"] == "succeeded"
    assert _Steps.calls[:3] == ["triage", "spec", "build"]
    assert published == [run["id"]]


def test_a_spec_sent_back_is_rewritten_with_the_note(published: list[str]) -> None:
    run_id = _start()
    note = "Keep the 400-char truncation."
    runs.answer(run_id, {"step": "spec-gate", "answer": "revise", "note": note})
    run = runs.get(run_id)
    assert run["state"] == "waiting_gate"
    assert _Steps.calls == ["triage", "spec", "spec"]
    assert note not in _Steps.prompts[1] and note in _Steps.prompts[2]
    assert run["answers"][0]["note"] == note


def test_a_rewrite_that_changes_nothing_fails_the_run(published: list[str]) -> None:
    run_id = _start()
    _Steps.vary = False
    runs.answer(run_id, {"step": "spec-gate", "answer": "revise", "note": "again"})
    receipt = runs._receipt(runs.runs_root() / run_id) or {}
    assert runs.get(run_id)["state"] == "failed"
    assert "unchanged from the previous attempt" in receipt["failure"]["reason"]


@pytest.mark.parametrize(
    ("choice", "state"), [("needs_info", "needs_info"), ("decline", "declined")]
)
def test_an_issue_triage_cannot_act_on_ends_before_any_spec(
    published: list[str], choice: str, state: str
) -> None:
    _Steps.triage = choice
    run = runs.get(_start())
    assert run["state"] == state
    assert _Steps.calls == ["triage"]
    assert published == []


def test_a_review_that_sends_the_work_back_runs_again_on_the_new_build(
    published: list[str],
) -> None:
    # the second review wrote the same files as the first and was failed for
    # doing no work, after comparing its verdict, a word, as if it were a file
    _Steps.classification, _Steps.qa_fail_once = ROUTINE, True
    run = runs.get(_start())
    assert run["state"] == "succeeded"
    assert _Steps.calls.count("build") == 2 and _Steps.calls.count("qa") == 2
    assert published == [run["id"]]


class _GitHub:
    """CI results to hand out in order, the pull request's head, and merges."""

    def __init__(self) -> None:
        self.ci: list[tuple[str, str]] = [("pass", "")]
        self.pushed = 0
        self.merged: list[str] = []
        self.moved_to: str | None = None


@pytest.fixture
def merging(published: list[str], monkeypatch: pytest.MonkeyPatch) -> _GitHub:
    from duckterm import github_projects
    from duckterm.review import approvals

    gh = _GitHub()

    def publish(root: Path, meta: dict[str, Any], *_: Any) -> None:
        published.append(meta["id"])
        runs._update(root, pushed=True, pr={"number": 5, "url": "u", "base": "main"})
        runs._announce(root, "pr")

    def head(root: Path) -> str:
        return runs._git_out(root / "repo", "rev-parse", "HEAD")

    monkeypatch.setattr(runs, "_publish", publish)
    monkeypatch.setattr(github_projects, "local_token", lambda: ("gh-token", {}))
    monkeypatch.setattr(delivery, "wait_ci", lambda repo, sha, token, stop: gh.ci.pop(0))
    monkeypatch.setattr(github_push, "push", lambda *a: setattr(gh, "pushed", gh.pushed + 1))
    monkeypatch.setattr(
        github_push,
        "pull_head",
        lambda repo, n, token: gh.moved_to or head(runs.runs_root() / published[0]),
    )
    monkeypatch.setattr(
        github_push, "merge", lambda repo, n, sha, title, token: gh.merged.append(sha) or "m1"
    )
    monkeypatch.setattr(approvals, "key_status", lambda: {"enrolled": True})
    monkeypatch.setattr(approvals, "verify", lambda payload, sig: sig == "touch-id")
    _Steps.classification = ROUTINE
    return gh


def _autopilot(**over: Any) -> dict[str, Any]:
    entry = store.create(
        {
            "flow": "autopilot/issue-to-merge",
            "repo": "o/r",
            "triggers": {"github-issue": {"labels": ["autopilot"]}},
            **over,
        }
    )
    return entry


def _approve(run_id: str, signature: str = "touch-id") -> None:
    payload = runs.merge_challenge(run_id)["payload"]
    runs.merge_approve(run_id, {"payload": payload, "signature": signature})


def test_a_routine_fix_waits_for_ci_then_asks_before_merging_in_shadow_mode(
    merging: _GitHub,
) -> None:
    entry = _autopilot()
    run = runs.get(runs.start(entry, "github-issue", {"number": 7, "title": "Row stuck"}))
    assert run["state"] == "waiting_gate"
    gate = run["gate"]
    assert (gate["step"], gate["shadow"], gate["shadow_run"], gate["why"]) == (
        "merge-gate",
        True,
        1,
        "",
    )
    assert gate["head"] == run["ci_head"]
    assert merging.merged == []


def test_approving_with_touch_id_merges_at_the_approved_head_and_counts_it(
    merging: _GitHub,
) -> None:
    entry = _autopilot()
    run_id = runs.start(entry, "github-issue", {"number": 7, "title": "Row stuck"})
    with pytest.raises(ValueError, match="Touch ID"):
        runs.answer(run_id, {"step": "merge-gate", "answer": "approve"})
    with pytest.raises(ValueError, match="signature"):
        _approve(run_id, signature="forged")
    _approve(run_id)
    run = runs.get(run_id)
    assert run["state"] == "merged"
    assert merging.merged == [run["ci_head"]]
    assert run["answers"][-1]["touch_id"] is True
    assert store.get(entry["id"])["shadow_done"] == 1


def test_a_push_after_approval_stops_the_merge_and_starts_the_count_over(
    merging: _GitHub,
) -> None:
    entry = _autopilot()
    store.set_shadow(entry["id"], 4)
    run_id = runs.start(entry, "github-issue", {"number": 7, "title": "Row stuck"})
    merging.moved_to = "f" * 40
    _approve(run_id)
    run = runs.get(run_id)
    assert run["state"] == "failed" and "moved" in run["error"]
    assert merging.merged == []
    assert store.get(entry["id"])["shadow_done"] == 0


def test_rejecting_a_merge_ends_the_run_with_the_pull_request_open(merging: _GitHub) -> None:
    entry = _autopilot()
    store.set_shadow(entry["id"], 3)
    run_id = runs.start(entry, "github-issue", {"number": 7, "title": "Row stuck"})
    runs.answer(run_id, {"step": "merge-gate", "answer": "reject"})
    run = runs.get(run_id)
    assert (run["state"], run["outcome"]) == (
        "ended",
        "Merge rejected; the pull request stays open",
    )
    assert store.get(entry["id"])["shadow_done"] == 0 and merging.merged == []


def test_a_failed_ci_is_fixed_in_a_sandbox_pushed_and_checked_again(merging: _GitHub) -> None:
    merging.ci = [("fail", "## tests: failure\ntest_row.py:3 assert"), ("pass", "")]
    entry = _autopilot()
    run = runs.get(runs.start(entry, "github-issue", {"number": 7, "title": "Row stuck"}))
    assert run["state"] == "waiting_gate" and run["ci_fixes"] == 1
    assert merging.pushed == 1
    assert "fix-ci" in _Steps.calls
    assert any("test_row.py:3" in p for p in _Steps.prompts if "fix-ci" in p)
    assert [a["step"] for a in runs.detail(run["id"])["attempts"]][-2:] == ["fix-ci", "tests"]


def test_ci_that_keeps_failing_ends_the_run_after_three_fixes(merging: _GitHub) -> None:
    merging.ci = [("fail", "boom")] * 4
    entry = _autopilot()
    run = runs.get(runs.start(entry, "github-issue", {"number": 7, "title": "Row stuck"}))
    assert run["state"] == "failed" and "after 3 fixes" in run["error"]
    assert merging.merged == []


def test_a_fix_a_path_rule_names_asks_even_with_auto_merge_on(merging: _GitHub) -> None:
    entry = _autopilot(rules={"security": ["fix.txt"]})
    store.set_shadow(entry["id"], 10)
    store.update(entry["id"], {"auto_merge": "on"})
    run = runs.get(runs.start(entry, "github-issue", {"number": 7, "title": "Row stuck"}))
    assert run["state"] == "waiting_gate"
    assert run["gate"]["why"] == "path rule (security): fix.txt"


def test_with_auto_merge_on_a_routine_fix_merges_when_ci_passes(merging: _GitHub) -> None:
    entry = _autopilot()
    with pytest.raises(ValueError, match="after 10"):
        store.update(entry["id"], {"auto_merge": "on"})
    store.set_shadow(entry["id"], 10)
    store.update(entry["id"], {"auto_merge": "on"})
    run = runs.get(runs.start(entry, "github-issue", {"number": 7, "title": "Row stuck"}))
    assert run["state"] == "merged" and merging.merged == [run["ci_head"]]


def _kinds() -> list[str]:
    """Which status each comment posted, by its first words."""
    first = {
        "Autopilot started": "started",
        "Opened pull": "pr",
        "Pull request": "merge-gate",
        "Merged pull": "merged",
        "The fix spec": "spec-gate",
        "The run was cancelled": "cancelled",
        "The run ended without": "failed",
    }
    return [next(k for p, k in first.items() if body.startswith(p)) for _, _, body in _comments]


def test_the_issue_hears_each_status_once_and_never_an_error(merging: _GitHub) -> None:
    entry = _autopilot()
    run_id = runs.start(entry, "github-issue", {"number": 7, "title": "Row stuck"})
    runs.get(run_id)
    _approve(run_id)
    assert _kinds() == ["started", "pr", "merge-gate", "merged"]
    assert {(repo, n) for repo, n, _ in _comments} == {("o/r", 7)}


def test_a_failed_run_says_so_without_the_error_text(
    published: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from duckterm import github_projects

    monkeypatch.setattr(github_projects, "local_token", lambda: ("t", {}))
    _Steps.fail_step = "triage"
    run_id = _start()
    assert runs.get(run_id)["state"] == "failed"
    assert _kinds() == ["started", "failed"]
    reason = (runs._receipt(runs.runs_root() / run_id) or {})["failure"]["reason"]
    assert reason and reason not in _comments[-1][2]


def test_cancelling_a_run_waiting_for_its_spec_ends_it_and_tells_the_issue(
    published: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from duckterm import github_projects

    monkeypatch.setattr(github_projects, "local_token", lambda: ("t", {}))
    run_id = _start()
    runs.cancel(run_id, "cancelled by the owner")
    run = runs.get(run_id)
    assert (run["state"], run["cancel_reason"]) == ("cancelled", "cancelled by the owner")
    assert _kinds()[-1] == "cancelled"
    with pytest.raises(ValueError, match="already ended"):
        runs.cancel(run_id)


def test_removing_the_label_cancels_only_runs_the_label_started(published: list[str]) -> None:
    by_label = runs.start(
        {"id": "a1", "name": "x", "flow": "autopilot/issue-to-merge", "repo": "o/r", "budget_m": 5},
        "github-issue",
        {"number": 7, "title": "Row stuck", "started_by": "maintainer"},
    )
    by_hand = _start()
    runs.cancel_unlabelled("a1", labelled={8})
    assert runs.get(by_label)["state"] == "cancelled"
    assert runs.get(by_label)["cancel_reason"] == "the label was removed"
    assert runs.get(by_hand)["state"] == "waiting_gate"


def test_a_cancelled_ci_wait_stops_and_merges_nothing(
    merging: _GitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    def wait(repo: str, sha: str, token: str, stop: Any) -> tuple[str, str]:
        runs.cancel(published_id(), "cancelled by the owner")
        return ("stopped", "") if stop() else ("pass", "")

    monkeypatch.setattr(delivery, "wait_ci", wait)
    entry = _autopilot()
    run = runs.get(runs.start(entry, "github-issue", {"number": 7, "title": "Row stuck"}))
    assert run["state"] == "cancelled" and merging.merged == []


def published_id() -> str:
    return sorted(p.name for p in runs.runs_root().iterdir())[-1]


def test_a_failed_run_runs_again_from_the_step_it_failed_at(published: list[str]) -> None:
    _Steps.classification, _Steps.fail_step = ROUTINE, "build"
    run_id = _start()
    assert runs.get(run_id)["state"] == "failed"
    with pytest.raises(ValueError, match="never reached"):
        runs.rerun(run_id, {"step": "verdict"})
    _Steps.fail_step = ""
    runs.rerun(run_id, {"step": "build"})
    run = runs.get(run_id)
    assert run["state"] == "succeeded" and run["rerun_from"] == "build"
    assert _Steps.calls.count("triage") == 1 and _Steps.calls.count("spec") == 1
    assert published == [run_id]


def test_a_steps_transcript_lists_what_it_ran(published: list[str]) -> None:
    run_id = _start()
    seen = runs.transcript(run_id, {"step": "spec", "attempt": 1})
    assert seen["kept"] is True
    assert seen["events"] == [{"kind": "tool", "name": "Bash", "text": "run spec", "error": False}]
    with pytest.raises(ValueError):
        runs.transcript(run_id, {"step": "../x", "attempt": 1})
