"""A run on the built-in flow stops at its spec gate with no sandbox when the
spec policy asks for one, and carries on from the owner's answer in a new one.
The kit runner and the kit's policy script are real; the sandbox, gateway and
model steps are fakes."""

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from duckterm.autopilot import executor, runs
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

    def __init__(self, box: Any, dirs: executor.RunDirs, token: str, branch: str) -> None:
        self.box = box

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
        if step == "spec":
            (out / "spec.md").write_text(
                SPEC + "\n" * (self.calls.count("spec") if self.vary else 1)
            )
            (out / "classification.json").write_text(json.dumps(self.classification))
        if step == "build":
            (Path(cwd) / "fix.txt").write_text("fixed\n")
        if step == "check":
            (out / "review.md").write_text("No findings.\n")
            structured["verdict"] = "approve"
        body = {"type": "result", "subtype": "success", "structured_output": structured}
        return runner.ProcessResult(0, json.dumps(body), "")


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

    def __init__(self) -> None:
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
    monkeypatch.setattr(runs.threading, "Thread", _NowThread)
    _Steps.calls, _Steps.prompts, _Gateway.minted, _Box.live = [], [], [], 0
    _Steps.vary = True
    _Steps.classification = {**ROUTINE, "security": {"level": "touches", "areas": ["auth"]}}
    out: list[str] = []
    monkeypatch.setattr(runs, "_publish", lambda root, meta, *_: out.append(meta["id"]))
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
    assert _Steps.calls == ["spec"]
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
    assert _Steps.calls == ["spec", "build", "check"]
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
    assert _Steps.calls == ["spec"]
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
    assert _Steps.calls == ["spec", "build", "check"]
    assert published == [run["id"]]


def test_a_spec_sent_back_is_rewritten_with_the_note(published: list[str]) -> None:
    run_id = _start()
    note = "Keep the 400-char truncation."
    runs.answer(run_id, {"step": "spec-gate", "answer": "revise", "note": note})
    run = runs.get(run_id)
    assert run["state"] == "waiting_gate"
    assert _Steps.calls == ["spec", "spec"]
    assert note not in _Steps.prompts[0] and note in _Steps.prompts[1]
    assert run["answers"][0]["note"] == note


def test_a_rewrite_that_changes_nothing_fails_the_run(published: list[str]) -> None:
    run_id = _start()
    _Steps.vary = False
    runs.answer(run_id, {"step": "spec-gate", "answer": "revise", "note": "again"})
    receipt = runs._receipt(runs.runs_root() / run_id) or {}
    assert runs.get(run_id)["state"] == "failed"
    assert "unchanged from the previous attempt" in receipt["failure"]["reason"]
