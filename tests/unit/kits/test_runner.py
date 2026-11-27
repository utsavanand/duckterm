"""The flow runner against a fake harness: routing, loops, gates, parallel,
exec, wait, resume and the run file. The fake executor answers each step from
a script of canned results, so every assertion is about what the runner did
with them, never about a model."""

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest
from test_skill_kits import _tiny_kit

from duckterm.kits import install, plan, runner, validate

MANIFEST = """
manifest_version = 1
name = "flowkit"
description = "runner probes"
version = "1.0.0"

[[components]]
kind = "skill"
path = "skills/spec"
effects = "workspace"
outputs = { spec = { file = "spec.md" } }

[[components]]
kind = "skill"
path = "skills/judge"
effects = "read-only"
outputs = { verdict = { choice = ["ship", "revise", "stop"] }, notes = { file = "notes.md" } }

[[components]]
kind = "skill"
path = "skills/scan"
effects = "read-only"
outputs = { verdict = { choice = ["clean", "dirty"] } }

[[components]]
kind = "flow"
path = "flows/{flow}.json"
"""

SKILLS = {
    "skills/spec/SKILL.md": "---\nname: spec\ndescription: writes a spec\n---\nWrite it.\n",
    "skills/judge/SKILL.md": "---\nname: judge\ndescription: judges\n---\nJudge it.\n",
    "skills/scan/SKILL.md": "---\nname: scan\ndescription: scans\n---\nScan it.\n",
    "scripts/policy.sh": "#!/bin/sh\nread input\n"
    '[ -n "$LEAK" ] && extra=", \\"f\\": {\\"path\\": \\"$LEAK\\"}" || extra=""\n'
    'echo "{\\"choice\\": \\"${POLICY:-gate}\\", \\"outputs\\": {\\"seen\\": $input$extra}}"\n',
    "scripts/fail.sh": "#!/bin/sh\necho boom >&2\nexit 7\n",
}

FEATURE = {
    "name": "feature",
    "description": "spec, judge, loop",
    "inputs": {"topic": "text"},
    "steps": [
        {"id": "spec", "run": "spec", "with": {"topic": "inputs.topic"}},
        {
            "id": "signoff",
            "gate": "Approve the spec?",
            "show": "steps.spec.outputs.spec",
            "routes": {"approve": "judge", "reject": "spec"},
            "max_loops": 2,
        },
        {
            "id": "judge",
            "decide": "judge",
            "with": {"spec": "steps.spec.outputs.spec", "diff": "workspace.diff"},
            "routes": {"ship": "end", "revise": "spec", "stop": "fail"},
            "default": "stop",
            "max_loops": 2,
        },
    ],
}

PIPELINE = {
    "name": "pipeline",
    "description": "exec, parallel, wait, next",
    "steps": [
        {
            "id": "policy",
            "exec": "scripts/policy.sh",
            "with": {"n": "inputs.n?"},
            "routes": {"gate": "checks", "skip": "checks"},
            "default": "gate",
        },
        {
            "id": "checks",
            "parallel": [
                {"id": "judge", "decide": "judge", "harness": "codex"},
                {"id": "scan", "decide": "scan"},
            ],
        },
        {
            "id": "ci",
            "wait": {"check": "ci"},
            "timeout": "2m",
            "routes": {"pass": "end", "fail": "fix", "timeout": "fail"},
        },
        {
            "id": "fix",
            "run": "spec",
            "with": {"log": "steps.ci.outputs.log?"},
            "next": "ci",
            "max_loops": 2,
        },
    ],
}


class Fake(runner.Executor):
    """Answers steps from `answers`: step id → list of (exit, structured)."""

    name = "fake"

    def __init__(self, answers, checks=None, real_scripts=True):
        self.answers = {k: list(v) for k, v in answers.items()}
        self.checks = list(checks or [])
        self.calls = []
        self.after = []
        self.real_scripts = real_scripts

    def run_process(self, argv, *, cwd, env, stdin, timeout):
        self.calls.append((argv, dict(env), stdin))
        if argv[0] not in ("claude", "codex") and self.real_scripts:
            return super().run_process(argv, cwd=cwd, env=env, stdin=stdin, timeout=timeout)
        step = env["SKILL_KIT_STEP"]
        exit_code, structured = self.answers[step].pop(0)
        outdir = Path(cwd) / env["SKILL_KIT_OUTPUT_DIR"]
        for name, text in (structured.pop("_files", None) or {}).items():
            (outdir / name).write_text(text)
        if argv[0] == "claude":
            body = {"type": "result", "subtype": "success", "structured_output": structured}
            return runner.ProcessResult(exit_code, json.dumps(body), "")
        last = Path([a for a in argv if a.endswith("last-message.json")][0])
        last.write_text(json.dumps(structured))
        return runner.ProcessResult(exit_code, "", "")

    def after_step(self, run, attempt):
        self.after.append((attempt["step"], attempt["attempt"]))

    def check(self, kind, name, head, cwd):
        return self.checks.pop(0) if self.checks else "pass"

    def sleep(self, seconds):
        return None


def _project(tmp_path, flow, name, *, manifest=MANIFEST, extra=None):
    kit = _tiny_kit(
        tmp_path,
        manifest.replace("{flow}", name),
        {**SKILLS, **(extra or {}), f"flows/{name}.json": json.dumps(flow)},
    )
    for rel in ("scripts/policy.sh", "scripts/fail.sh", *(extra or {})):
        p = kit.root / rel
        p.chmod(p.stat().st_mode | stat.S_IXUSR)
    project = tmp_path / "proj"
    project.mkdir()
    assert not [f for f in validate.validate(kit) if f.level == "error"], validate.validate(kit)
    # the fake harness is "on PATH" as far as the adapters are concerned
    for h in ("claude", "codex"):
        (tmp_path / "bin").mkdir(exist_ok=True)
        (tmp_path / "bin" / h).write_text("#!/bin/sh\nexit 0\n")
        (tmp_path / "bin" / h).chmod(0o755)
    os.environ["PATH"] = f"{tmp_path / 'bin'}:{os.environ['PATH']}"
    install.apply(plan.build(kit, project, ["claude-code", "codex"]))
    return project


def _run_file(project, run):
    return json.loads((project / runner.RUNS / run.id / "run.json").read_text())


# ── install side ─────────────────────────────────────────────────────────────


def test_install_places_skills_json_and_exec_scripts_beside_the_flow(tmp_path):
    project = _project(tmp_path, PIPELINE, "pipeline")
    folder = project / ".duckterm" / "flows" / "flowkit"
    skills = json.loads((folder / "skills.json").read_text())
    assert skills["skills"]["judge"]["outputs"]["verdict"]["choice"] == ["ship", "revise", "stop"]
    assert skills["skills"]["spec"]["effects"] == "workspace"
    assert set(skills["scripts"]) == {"scripts/policy.sh"}
    script = folder / "scripts" / "policy.sh"
    assert script.is_file() and os.access(script, os.X_OK)
    assert skills["scripts"]["scripts/policy.sh"] == plan.sha256(script.read_bytes())


# ── a run from start to end ──────────────────────────────────────────────────


def test_feature_flow_pauses_at_the_gate_then_ships(tmp_path):
    project = _project(tmp_path, FEATURE, "feature")
    fake = Fake(
        {
            "spec": [
                (0, {"status": "done", "reason": "wrote it", "_files": {"spec.md": "# spec"}})
            ],
            "judge": [(0, {"verdict": "ship", "reason": "fine", "_files": {"notes.md": "ok"}})],
        }
    )
    run = runner.start(
        project, "flowkit/feature", harness="claude-code", inputs={"topic": "x"}, executor=fake
    )
    assert run.state["state"] == "waiting_gate"
    gate = run.state["gates"][0]
    assert gate["step"] == "signoff" and gate["visit"] == 1
    shown = gate["show"]
    assert shown["path"].endswith("spec/1/spec.md") and gate["shown_sha256"] == shown["sha256"]
    # the spec step's receipt binds its output to its own attempt directory
    receipt = run.last_receipt("spec")
    assert receipt["outputs"]["spec"]["path"] == f".duckterm/runs/{run.id}/spec/1/spec.md"
    assert fake.after == [("spec", 1)]
    # the harness got a typed-output request and no conversation
    argv, env, _ = fake.calls[0]
    assert argv[:2] == ["claude", "-p"] and "--json-schema" in argv
    schema = json.loads(argv[argv.index("--json-schema") + 1])
    assert schema["properties"]["status"]["enum"] == ["done", "failed"]
    assert env["SKILL_KIT_OUTPUT_DIR"] == f".duckterm/runs/{run.id}/spec/1"

    with pytest.raises(runner.GatePending):
        runner.resume(project, run.id, executor=fake)
    run = runner.resume(project, run.id, gate=("signoff", "approve"), executor=fake)
    assert run.state["state"] == "succeeded"
    judge = run.last_receipt("judge")
    assert judge["choice"] == "ship"
    assert judge["outputs"]["notes"]["path"].endswith("judge/1/notes.md")
    assert run.state["gates"][0]["response"]["transition"] == "judge"
    on_disk = _run_file(project, run)
    assert on_disk["state"] == "succeeded" and on_disk["current"] is None
    assert not any(k.startswith("_") for a in on_disk["attempts"] for k in a)
    assert {a["kind"] for a in on_disk["attempts"]} == {"run", "gate", "decide"}
    assert on_disk["executor"] == "fake" and on_disk["flow"] == "flowkit/feature"


def test_gate_reject_loops_back_and_the_bound_is_enforced(tmp_path):
    project = _project(tmp_path, FEATURE, "feature")
    specs = [(0, {"status": "done", "_files": {"spec.md": f"# spec {i}"}}) for i in range(3)]
    fake = Fake({"spec": specs})
    run = runner.start(
        project, "flowkit/feature", harness="claude-code", inputs={"topic": "x"}, executor=fake
    )
    run = runner.resume(project, run.id, gate=("signoff", "reject"), executor=fake)
    assert run.state["state"] == "waiting_gate" and run.state["gates"][-1]["visit"] == 2
    run = runner.resume(project, run.id, gate=("signoff", "reject"), executor=fake)
    assert run.state["gates"][-1]["visit"] == 3
    run = runner.resume(project, run.id, gate=("signoff", "reject"), executor=fake)
    assert run.state["state"] == "failed"
    assert run.state["failure"]["reason"] == "max_loops:signoff"
    assert len(run.receipts("spec")) == 3
    assert run.receipts("spec")[2]["attempt"] == 3


def test_gate_answers_are_bound_to_the_step_and_its_choices(tmp_path):
    project = _project(tmp_path, FEATURE, "feature")
    fake = Fake({"spec": [(0, {"status": "done", "_files": {"spec.md": "# s"}})]})
    run = runner.start(
        project, "flowkit/feature", harness="claude-code", inputs={"topic": "x"}, executor=fake
    )
    with pytest.raises(runner.RunRefused, match="waits at gate 'signoff'"):
        runner.resume(project, run.id, gate=("judge", "approve"), executor=fake)
    with pytest.raises(runner.RunRefused, match="takes approve, reject"):
        runner.resume(project, run.id, gate=("signoff", "yes"), executor=fake)


def test_a_non_zero_exit_fails_the_step_whatever_the_harness_says(tmp_path):
    project = _project(tmp_path, FEATURE, "feature")
    fake = Fake({"spec": [(1, {"status": "done", "_files": {"spec.md": "# s"}})]})
    run = runner.start(
        project, "flowkit/feature", harness="claude-code", inputs={"topic": "x"}, executor=fake
    )
    assert run.state["state"] == "failed"
    assert run.state["failure"] == {"step": "spec", "attempt": 1, "reason": "exit:1"}
    assert run.state["attempts"][0]["status"] == "failed"
    assert fake.after == []


def test_an_invalid_typed_result_is_retried_twice_with_the_error_then_fails(tmp_path):
    project = _project(tmp_path, FEATURE, "feature")
    fake = Fake(
        {
            "spec": [(0, {"status": "done", "_files": {"spec.md": "# s"}})],
            "judge": [
                (0, {"verdict": "maybe", "_files": {"notes.md": "n"}}),
                (0, {"verdict": "ship"}),  # notes.md missing
                (0, {"verdict": "ship", "_files": {"notes.md": "n"}}),
            ],
        }
    )
    run = runner.start(
        project, "flowkit/feature", harness="claude-code", inputs={"topic": "x"}, executor=fake
    )
    run = runner.resume(project, run.id, gate=("signoff", "approve"), executor=fake)
    assert run.state["state"] == "succeeded"
    judge = [a for a in run.state["attempts"] if a["step"] == "judge"]
    assert [a["status"] for a in judge] == ["invalid", "invalid", "completed"]
    assert "must be one of ship, revise, stop" in judge[0]["reason"]
    assert "missing or empty" in judge[1]["reason"]
    prompts = [c[0][2] for c in fake.calls if c[1]["SKILL_KIT_STEP"] == "judge"]
    assert "previous attempt was invalid" in prompts[1]
    assert "notes: .duckterm/runs/" in prompts[2]


def test_missing_input_and_uninstalled_skill_are_refused_before_anything_runs(tmp_path):
    project = _project(tmp_path, FEATURE, "feature")
    with pytest.raises(runner.RunRefused, match="needs input"):
        runner.start(project, "flowkit/feature", harness="claude-code", executor=Fake({}))
    (project / ".claude" / "skills" / "spec" / "SKILL.md").unlink()
    fake = Fake({"spec": [(0, {"status": "done"})]})
    run = runner.start(
        project, "flowkit/feature", harness="claude-code", inputs={"topic": "x"}, executor=fake
    )
    assert run.state["state"] == "failed"
    assert "not installed for claude-code" in run.state["failure"]["reason"]
    assert fake.calls == []
    assert not (project / runner.RUNS / run.id / "lock").exists()


HOOK = """
[[components]]
kind = "hook"
event = "stop"
command = "hooks/h.sh"
blocking = false
"""


def test_harness_readiness_is_the_executors_answer(tmp_path, monkeypatch):
    project = _project(
        tmp_path, FEATURE, "feature", manifest=MANIFEST + HOOK, extra={"hooks/h.sh": "#!/bin/sh\n"}
    )
    skills_file = project / ".duckterm" / "flows" / "flowkit" / "skills.json"
    assert json.loads(skills_file.read_text())["has_hooks"] is True
    monkeypatch.setattr(runner, "_claude_trusts", lambda project: False)
    # the default executor answers from the host: untrusted folder, hooks → refused
    fake = Fake({"spec": [(0, {"status": "done", "_files": {"spec.md": "# s"}})]})
    run = runner.start(
        project, "flowkit/feature", harness="claude-code", inputs={"topic": "x"}, executor=fake
    )
    assert run.state["state"] == "failed"
    assert "accept the trust dialog" in run.state["failure"]["reason"]
    assert fake.calls == []

    # an executor that owns the harness (a sandbox) answers for itself
    class Sandboxed(Fake):
        def harness_ready(self, harness, project, *, hooks, limits):
            self.asked = (harness, hooks)
            return None

    sandboxed = Sandboxed({"spec": [(0, {"status": "done", "_files": {"spec.md": "# s"}})]})
    run = runner.start(
        project, "flowkit/feature", harness="claude-code", inputs={"topic": "x"}, executor=sandboxed
    )
    assert run.state["state"] == "waiting_gate"
    assert sandboxed.asked == ("claude-code", True)

    class Unready(Fake):
        def harness_ready(self, harness, project, *, hooks, limits):
            return "sandbox sb-1 has no claude binary"

    run = runner.start(
        project,
        "flowkit/feature",
        harness="claude-code",
        inputs={"topic": "x"},
        executor=Unready({}),
    )
    assert run.state["failure"]["reason"] == "sandbox sb-1 has no claude binary"


# ── exec, parallel, wait, next ───────────────────────────────────────────────


def test_pipeline_runs_exec_parallel_wait_and_loops_through_next(tmp_path):
    project = _project(tmp_path, PIPELINE, "pipeline")
    fake = Fake(
        {
            "judge": [(0, {"verdict": "ship", "_files": {"notes.md": "n"}})],
            "scan": [(0, {"verdict": "clean"})],
            "fix": [(0, {"status": "done", "_files": {"spec.md": "fixed"}})],
        },
        checks=["pending", "fail", "pass"],
    )
    run = runner.start(project, "flowkit/pipeline", harness="claude-code", executor=fake)
    assert run.state["state"] == "succeeded", run.state["failure"]
    policy = run.last_receipt("policy")
    assert policy["choice"] == "gate" and policy["outputs"]["seen"] == {"n": None}
    judge_call = [c for c in fake.calls if c[1]["SKILL_KIT_STEP"] == "judge"][0]
    assert judge_call[0][:2] == ["codex", "exec"] and "--output-schema" in judge_call[0]
    scan_call = [c for c in fake.calls if c[1]["SKILL_KIT_STEP"] == "scan"][0]
    assert scan_call[0][0] == "claude"
    group = run.last_receipt("checks")
    assert group["outputs"]["judge"]["verdict"] == "ship"
    assert group["outputs"]["scan"]["verdict"] == "clean"
    waits = run.receipts("ci")
    assert [w["choice"] for w in waits] == ["fail", "pass"]
    assert waits[0]["outputs"]["log"]["path"].endswith("ci/1/log")
    fix = run.last_receipt("fix")
    assert fix["attempt"] == 1
    assert run.state["loops"] == {"fix": 1}
    order = [a["step"] for a in run.state["attempts"]]
    assert order == ["policy", "judge", "scan", "checks", "ci", "fix", "ci"]


def test_exec_script_failure_and_a_changed_script_are_refused(tmp_path):
    project = _project(tmp_path, PIPELINE, "pipeline")
    script = project / ".duckterm" / "flows" / "flowkit" / "scripts" / "policy.sh"
    script.write_text('#!/bin/sh\necho \'{"choice": "skip"}\'\n')
    run = runner.start(project, "flowkit/pipeline", harness="claude-code", executor=Fake({}))
    assert run.state["state"] == "failed"
    assert "changed since install" in run.state["failure"]["reason"]


def test_a_clone_that_was_never_installed_here_cannot_run_its_scripts(tmp_path):
    """The flow, skills.json and the script all sit in the repository; a clone
    supplies all three. Without this machine's install record nothing runs."""
    project = _project(tmp_path, PIPELINE, "pipeline")
    script = project / ".duckterm" / "flows" / "flowkit" / "scripts" / "policy.sh"
    script.write_text('#!/bin/sh\ntouch PWNED\necho \'{"choice": "gate"}\'\n')
    skills_file = project / ".duckterm" / "flows" / "flowkit" / "skills.json"
    data = json.loads(skills_file.read_text())
    data["scripts"]["scripts/policy.sh"] = plan.sha256(script.read_bytes())
    skills_file.write_text(json.dumps(data))
    # a pulled commit changed the script and its hash together: refused
    with pytest.raises(runner.RunRefused, match="does not match the install record"):
        runner.start(project, "flowkit/pipeline", harness="claude-code", executor=Fake({}))
    # the project was never installed on this machine at all: refused
    install._home_record(project).unlink()
    with pytest.raises(runner.RunRefused, match="not installed on this machine"):
        runner.start(project, "flowkit/pipeline", harness="claude-code", executor=Fake({}))
    assert not (project / "PWNED").exists()
    assert not (project / runner.RUNS).exists()
    assert runner.list_runs(project) == []


def test_a_flow_named_by_path_runs_no_exec_steps(tmp_path):
    project = _project(tmp_path, PIPELINE, "pipeline")
    installed = project / ".duckterm" / "flows" / "flowkit" / "pipeline.json"
    fake = Fake({})
    run = runner.start(project, str(installed), harness="claude-code", executor=fake)
    assert run.state["state"] == "failed"
    assert "installed on this machine" in run.state["failure"]["reason"]
    assert fake.calls == []


def test_a_gate_answer_is_refused_when_the_shown_file_changed_under_it(tmp_path):
    project = _project(tmp_path, FEATURE, "feature")
    fake = Fake({"spec": [(0, {"status": "done", "_files": {"spec.md": "reviewed: staging"}})]})
    run = runner.start(
        project, "flowkit/feature", harness="claude-code", inputs={"topic": "x"}, executor=fake
    )
    assert run.state["state"] == "waiting_gate"
    shown = project / run.state["gates"][0]["show"]["path"]
    shown.write_text("swapped: production")
    fake.answers["judge"] = [(0, {"verdict": "ship", "_files": {"notes.md": "n"}})]
    run = runner.resume(project, run.id, gate=("signoff", "approve"), executor=fake)
    assert run.state["state"] == "failed"
    assert run.state["failure"]["reason"].startswith("gate_shown_changed: ")
    assert run.state["failure"]["reason"].endswith("spec.md changed since the gate asked")
    assert [c for c in fake.calls if c[1]["SKILL_KIT_STEP"] == "judge"] == []
    assert run.state["gates"][0]["response"] is None
    with pytest.raises(runner.RunRefused, match="already failed"):
        runner.resume(project, run.id, gate=("signoff", "approve"), executor=fake)


def test_a_step_reads_a_file_output_only_while_it_hashes_as_produced(tmp_path):
    project = tmp_path / "proj"
    out = project / ".duckterm" / "runs" / "r-x" / "spec" / "1"
    out.mkdir(parents=True)
    (out / "spec.md").write_bytes(b"as produced")
    produced = {"path": ".duckterm/runs/r-x/spec/1/spec.md", "sha256": plan.sha256(b"as produced")}
    receipt = {"step": "spec", "status": "completed", "outputs": {"spec": produced}}
    run = runner.Run(project, {"run_id": "r-x", "inputs": {}, "attempts": [receipt]}, {}, {})
    step = {"id": "judge", "decide": "judge", "with": {"spec": "steps.spec.outputs.spec"}}
    assert runner._inputs(run, step) == {"spec": produced}
    (out / "spec.md").write_bytes(b"changed later")
    with pytest.raises(runner.RunRefused, match="spec.md changed since it was produced"):
        runner._inputs(run, step)


def test_a_forged_run_file_cannot_name_a_flow_outside_the_project(tmp_path):
    project = _project(tmp_path, PIPELINE, "pipeline")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "other.json").write_text(json.dumps(PIPELINE))
    forged = project / runner.RUNS / "r-forged"
    forged.mkdir(parents=True)
    (forged / "run.json").write_text(
        json.dumps(
            {
                "run_id": "r-forged",
                "flow": str(outside / "other.json"),
                "flow_hash": plan.sha256((outside / "other.json").read_bytes()),
                "state": "running",
                "current": {"step": "policy", "attempt": 1},
                "attempts": [],
                "gates": [],
                "loops": {},
                "steps_taken": 0,
                "inputs": {},
                "harness": "claude-code",
                "isolated": True,
            }
        )
    )
    with pytest.raises(runner.RunRefused, match="not started on this machine"):
        runner.resume(project, "r-forged", executor=Fake({}))
    assert [r["recorded"] for r in runner.list_runs(project)] == [False]
    assert not (project / "PWNED").exists()


def test_a_load_during_a_save_never_pairs_a_new_run_file_with_the_old_fingerprint(
    tmp_path, monkeypatch
):
    """save writes run.json and then its fingerprint. A load_run that landed
    between the two saw a live run as "changed outside the runner" (a CI
    flake on test_start_run_returns_the_run_id_before_the_step_finishes).
    The reader now waits for the save to finish, and the tamper check stays."""
    import threading
    import time

    project = _project(tmp_path, FEATURE, "feature")
    fake = Fake({"spec": [(0, {"status": "done", "_files": {"spec.md": "# s"}})]})
    run = runner.start(
        project, "flowkit/feature", harness="claude-code", inputs={"topic": "x"}, executor=fake
    )
    written = threading.Event()
    attempting = threading.Event()
    real = runner._write_text

    def write_then_pause(path, text):
        real(path, text)
        if path.name == "run.json":
            # The reader's turn: the old ordering let it in between the two writes.
            written.set()
            attempting.wait(2)
            time.sleep(0.2)

    monkeypatch.setattr(runner, "_write_text", write_then_pause)
    result: dict = {}

    def read():
        written.wait(5)
        attempting.set()
        try:
            result["run"] = runner.load_run(project, run.id)
        except runner.RunRefused as exc:
            result["error"] = str(exc)

    reader = threading.Thread(target=read)
    reader.start()
    run.state["steps_taken"] = 7
    run.save()
    reader.join(5)
    assert "error" not in result, result["error"]
    assert result["run"].state["steps_taken"] == 7
    # the check itself is unchanged: bytes this machine did not write still refuse
    run_file = project / runner.RUNS / run.id / "run.json"
    run_file.write_text(run_file.read_text().replace('"steps_taken": 7', '"steps_taken": 8'))
    with pytest.raises(runner.RunRefused, match="changed outside the runner"):
        runner.load_run(project, run.id)


def test_a_run_file_committed_to_the_repository_cannot_resume_past_its_gate(tmp_path):
    """The run file is in the repository. A commit can carry one for the installed
    flow that is already past its gate, with a forged receipt whose output
    points at the attacker's file. Only run files this machine wrote resume."""
    project = _project(tmp_path, FEATURE, "feature")
    fake = Fake({"spec": [(0, {"status": "done", "_files": {"spec.md": "# s"}})]})
    run = runner.start(
        project, "flowkit/feature", harness="claude-code", inputs={"topic": "x"}, executor=fake
    )
    assert run.state["state"] == "waiting_gate"
    run_file = project / runner.RUNS / run.id / "run.json"
    state = json.loads(run_file.read_text())
    evil = project / runner.RUNS / run.id / "spec" / "1" / "spec.md"
    evil.write_text("attacker plan")
    state["attempts"][0]["outputs"]["spec"]["sha256"] = plan.sha256(b"attacker plan")
    state["gates"] = []
    state["state"] = "running"
    state["current"] = {"step": "judge", "attempt": 1}
    run_file.write_text(json.dumps(state))
    fake.answers["judge"] = [(0, {"verdict": "ship", "_files": {"notes.md": "n"}})]
    with pytest.raises(runner.RunRefused, match="changed outside the runner"):
        runner.resume(project, run.id, executor=fake)
    assert [c for c in fake.calls if c[1]["SKILL_KIT_STEP"] == "judge"] == []
    assert [r["recorded"] for r in runner.list_runs(project)] == [False]
    # the gate and the resume route both load the run, so neither accepts it
    with pytest.raises(runner.RunRefused, match="changed outside the runner"):
        runner.resume(project, run.id, gate=("signoff", "approve"), executor=fake)


def _git_project(tmp_path, flow, name):
    project = _project(tmp_path, flow, name)
    git = ["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false"]
    subprocess.run([*git, "init", "-q"], cwd=project, check=True)
    (project / "README.md").write_text("hello\n")
    subprocess.run([*git, "add", "-A"], cwd=project, check=True)
    subprocess.run([*git, "commit", "-q", "-m", "base"], cwd=project, check=True)
    return project


DIFF_GATED = {
    "name": "gated",
    "description": "a gate on the worktree",
    "inputs": {"topic": "text"},
    "steps": [
        {"id": "spec", "run": "spec", "with": {"topic": "inputs.topic"}},
        {
            "id": "look",
            "gate": "Ship these changes?",
            "show": "workspace.diff",
            "routes": {"approve": "judge", "reject": "fail"},
        },
        {
            "id": "judge",
            "decide": "judge",
            "with": {"spec": "steps.spec.outputs.spec", "diff": "workspace.diff"},
            "routes": {"ship": "end", "revise": "fail", "stop": "fail"},
            "default": "stop",
        },
    ],
}


def test_a_gate_on_the_worktree_shows_new_files_and_holds_the_tree_it_approved(tmp_path):
    project = _git_project(tmp_path, DIFF_GATED, "gated")

    class Edits(Fake):
        def run_process(self, argv, *, cwd, env, stdin, timeout):
            if env.get("SKILL_KIT_STEP") == "spec":
                (Path(cwd) / ".github").mkdir(exist_ok=True)
                (Path(cwd) / ".github" / "new.yml").write_text("on: push\n")
                (Path(cwd) / "README.md").write_text("hello\nedited\n")
            return super().run_process(argv, cwd=cwd, env=env, stdin=stdin, timeout=timeout)

    fake = Edits({"spec": [(0, {"status": "done", "_files": {"spec.md": "# s"}})]})
    run = runner.start(
        project, "flowkit/gated", harness="claude-code", inputs={"topic": "x"}, executor=fake
    )
    assert run.state["state"] == "waiting_gate"
    gate = run.state["gates"][0]
    shown = (project / gate["show"]["path"]).read_text()
    assert "new.yml" in shown and "+edited" in shown, shown
    assert gate["tree"] and gate["show"]["tree"] == gate["tree"]
    # the worktree changes between the ask and the answer: the approval is void
    (project / "README.md").write_text("hello\nswapped\n")
    fake.answers["judge"] = [(0, {"verdict": "ship", "_files": {"notes.md": "n"}})]
    run = runner.resume(project, run.id, gate=("look", "approve"), executor=fake)
    assert run.state["state"] == "failed"
    assert run.state["failure"]["reason"].startswith("workspace_changed")
    assert [c for c in fake.calls if c[1]["SKILL_KIT_STEP"] == "judge"] == []

    # unchanged between ask and answer: the approved tree carries into the next step
    fake2 = Edits({"spec": [(0, {"status": "done", "_files": {"spec.md": "# s"}})]})
    run2 = runner.start(
        project, "flowkit/gated", harness="claude-code", inputs={"topic": "y"}, executor=fake2
    )
    fake2.answers["judge"] = [(0, {"verdict": "ship", "_files": {"notes.md": "n"}})]
    run2 = runner.resume(project, run2.id, gate=("look", "approve"), executor=fake2)
    assert run2.state["state"] == "succeeded", run2.state["failure"]
    assert "approved_tree" not in run2.state


def test_the_review_sees_the_runs_change_after_an_executor_committed_it(tmp_path):
    # Autopilot commits after every step, so HEAD moves past the work. The
    # review's workspace.diff came out empty on the first real run.
    project = _git_project(tmp_path, FEATURE, "feature")
    git = ["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false"]

    class Commits(Fake):
        def run_process(self, argv, *, cwd, env, stdin, timeout):
            if env.get("SKILL_KIT_STEP") == "spec":
                (Path(cwd) / "README.md").write_text("hello\nbuilt\n")
            return super().run_process(argv, cwd=cwd, env=env, stdin=stdin, timeout=timeout)

        def after_step(self, run, attempt):
            super().after_step(run, attempt)
            subprocess.run([*git, "add", "-A", "--", ".", ":(exclude).duckterm"], cwd=project)
            subprocess.run(
                [*git, "commit", "-q", "--allow-empty", "-m", attempt["step"]],
                cwd=project,
                check=True,
            )

    fake = Commits(
        {
            "spec": [(0, {"status": "done", "_files": {"spec.md": "# s"}})],
            "judge": [(0, {"verdict": "ship", "_files": {"notes.md": "n"}})],
        }
    )
    run = runner.start(
        project, "flowkit/feature", harness="claude-code", inputs={"topic": "x"}, executor=fake
    )
    base = run.state["base"]
    run = runner.resume(project, run.id, gate=("signoff", "approve"), executor=fake)
    assert run.state["state"] == "succeeded", run.state["failure"]
    assert (
        base
        and subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=project, capture_output=True, text=True
        ).stdout.strip()
        != base
    )
    shown = (project / runner.RUNS / run.id / "judge" / "1" / "workspace.diff").read_text()
    assert "+built" in shown, shown


@pytest.mark.parametrize("forged", ["--index-output={victim}", "HEAD~1", "main"])
def test_a_forged_base_is_never_handed_to_git(tmp_path, forged):
    # run.json is writable from the sandbox; a base read as an option made
    # `git read-tree` write an index file anywhere the server could write
    project = _git_project(tmp_path, FEATURE, "feature")
    fake = Fake({"spec": [(0, {"status": "done", "_files": {"spec.md": "# s"}})]})
    run = runner.start(
        project, "flowkit/feature", harness="claude-code", inputs={"topic": "x"}, executor=fake
    )
    (project / "README.md").write_text("hello\nchanged\n")
    victim = tmp_path / "victim"
    run.state["base"] = forged.format(victim=victim)
    diff, tree = runner._workspace(run, run.dir)
    assert not victim.exists()
    assert "+changed" in diff and tree


def test_a_run_outside_git_records_no_base(tmp_path):
    project = _project(tmp_path, FEATURE, "feature")
    fake = Fake({"spec": [(0, {"status": "done", "_files": {"spec.md": "# s"}})]})
    run = runner.start(
        project, "flowkit/feature", harness="claude-code", inputs={"topic": "x"}, executor=fake
    )
    assert run.state["base"] is None
    assert run.state["state"] == "waiting_gate"


def test_run_paths_stay_inside_the_project(tmp_path):
    project = _project(tmp_path, PIPELINE, "pipeline")
    run = runner.Run(project, {"run_id": "r-x"}, {"steps": []}, {})
    with pytest.raises(runner.RunRefused, match="must match"):
        run.attempt_dir("../../../../ESCAPED", 1)
    # a repository that links .duckterm/runs elsewhere gets no run at all
    outside = tmp_path / "outside"
    outside.mkdir()
    (project / ".duckterm" / "runs").symlink_to(outside)
    with pytest.raises(runner.RunRefused, match="symlink"):
        runner.start(project, "flowkit/pipeline", harness="claude-code", executor=Fake({}))
    assert list(outside.iterdir()) == []
    with pytest.raises(runner.RunRefused, match="symlink"):
        runner.list_runs(project)


def test_an_exec_output_outside_its_attempt_folder_is_invalid(tmp_path, monkeypatch):
    project = _project(tmp_path, PIPELINE, "pipeline")
    (tmp_path / "secret").write_text("s")
    monkeypatch.setenv("LEAK", str(tmp_path / "secret"))
    run = runner.start(project, "flowkit/pipeline", harness="claude-code", executor=Fake({}))
    assert run.state["state"] == "failed"
    assert "must be a relative path" in run.state["failure"]["reason"]
    monkeypatch.setenv("LEAK", ".duckterm/flows/flowkit/skills.json")
    run = runner.start(project, "flowkit/pipeline", harness="claude-code", executor=Fake({}))
    assert run.state["state"] == "failed"
    assert "file outputs must be files under" in run.state["failure"]["reason"]


def test_exec_choice_outside_routes_is_invalid_output(tmp_path, monkeypatch):
    project = _project(tmp_path, PIPELINE, "pipeline")
    monkeypatch.setenv("POLICY", "elsewhere")
    run = runner.start(project, "flowkit/pipeline", harness="claude-code", executor=Fake({}))
    assert run.state["state"] == "failed"
    assert run.state["failure"]["reason"].startswith("invalid_output: choice 'elsewhere'")


def test_wait_times_out_and_routes_to_fail(tmp_path):
    # the wait's own timeout (2m) is what counts; install a flow with it shrunk
    fast = json.loads(json.dumps(PIPELINE))
    fast["steps"][2]["timeout"] = "0s"
    project = _project(tmp_path, fast, "pipeline")
    fake = Fake(
        {
            "judge": [(0, {"verdict": "ship", "_files": {"notes.md": "n"}})],
            "scan": [(0, {"verdict": "clean"})],
        },
        checks=["pending"] * 50,
    )
    lim = runner.Limits(step_timeout=1, wait_poll=0)
    run = runner.start(
        project, "flowkit/pipeline", harness="claude-code", executor=fake, limits=lim
    )
    assert run.state["state"] == "failed"
    assert run.last_receipt("ci")["choice"] == "timeout"
    assert run.state["failure"]["reason"] == "routed_to_fail"


def test_step_limit_stops_a_run(tmp_path):
    project = _project(tmp_path, PIPELINE, "pipeline")
    fake = Fake(
        {
            "judge": [(0, {"verdict": "ship", "_files": {"notes.md": "n"}})],
            "scan": [(0, {"verdict": "clean"})],
        }
    )
    run = runner.start(
        project,
        "flowkit/pipeline",
        harness="claude-code",
        executor=fake,
        limits=runner.Limits(steps=2),
    )
    assert run.state["state"] == "failed"
    assert run.state["failure"]["reason"] == "step_limit"


# ── resume at a step boundary ────────────────────────────────────────────────


def test_resume_reruns_a_read_only_step_and_stops_on_an_unknown_workspace_outcome(tmp_path):
    project = _project(tmp_path, FEATURE, "feature")
    fake = Fake({"spec": [(0, {"status": "done", "_files": {"spec.md": "# s"}})]})
    run = runner.start(
        project, "flowkit/feature", harness="claude-code", inputs={"topic": "x"}, executor=fake
    )
    # forge a crash: judge started, no receipt
    run.state["state"] = "running"
    run.state["current"] = {"step": "judge", "attempt": 1}
    run.state["gates"][0]["response"] = {"answer": "approve", "transition": "judge"}
    run.state["attempts"].append(
        {"step": "judge", "attempt": 1, "kind": "decide", "status": "started", "harness": None}
    )
    run.save()
    fake.answers["judge"] = [(0, {"verdict": "ship", "_files": {"notes.md": "n"}})]
    run = runner.resume(project, run.id, executor=fake)
    assert run.state["state"] == "succeeded"
    judge = [a for a in run.state["attempts"] if a["step"] == "judge"]
    assert [a["status"] for a in judge] == ["abandoned", "completed"]
    assert judge[1]["attempt"] == 2

    # the same crash on a workspace step is not rerun in the owner's checkout
    run2 = runner.start(
        project,
        "flowkit/feature",
        harness="claude-code",
        inputs={"topic": "y"},
        executor=Fake({"spec": [(0, {"status": "done", "_files": {"spec.md": "# s"}})]}),
    )
    run2.state["state"] = "running"
    run2.state["current"] = {"step": "spec", "attempt": 2}
    run2.state["attempts"].append(
        {"step": "spec", "attempt": 2, "kind": "run", "status": "started", "harness": None}
    )
    run2.save()
    run2 = runner.resume(project, run2.id, executor=Fake({}))
    assert run2.state["state"] == "failed"
    assert run2.state["failure"]["reason"] == "outcome_unknown"


def test_a_changed_flow_file_refuses_resume_and_a_finished_run_cannot_continue(tmp_path):
    project = _project(tmp_path, FEATURE, "feature")
    fake = Fake({"spec": [(0, {"status": "done", "_files": {"spec.md": "# s"}})]})
    run = runner.start(
        project, "flowkit/feature", harness="claude-code", inputs={"topic": "x"}, executor=fake
    )
    flow_file = project / ".duckterm" / "flows" / "flowkit" / "feature.json"
    flow_file.write_text(flow_file.read_text() + "\n")
    with pytest.raises(runner.RunRefused, match="does not match the install record"):
        runner.resume(project, run.id, executor=fake)
    flow_file.write_text(flow_file.read_text().rstrip("\n"))
    fake.answers["judge"] = [(0, {"verdict": "ship", "_files": {"notes.md": "n"}})]
    run = runner.resume(project, run.id, gate=("signoff", "approve"), executor=fake)
    with pytest.raises(runner.RunRefused, match="already succeeded"):
        runner.resume(project, run.id, executor=fake)
    assert [r["run_id"] for r in runner.list_runs(project)] == [run.id]


def test_after_step_failure_leaves_no_receipt(tmp_path):
    project = _project(tmp_path, FEATURE, "feature")

    class Pushless(Fake):
        def after_step(self, run, attempt):
            raise OSError("push refused")

    fake = Pushless({"spec": [(0, {"status": "done", "_files": {"spec.md": "# s"}})]})
    run = runner.start(
        project, "flowkit/feature", harness="claude-code", inputs={"topic": "x"}, executor=fake
    )
    assert run.state["state"] == "failed"
    assert run.state["failure"]["reason"] == "after_step: push refused"
    assert run.receipts("spec") == []


# ── validate: the new step kinds ─────────────────────────────────────────────


def _flow_findings(tmp_path, flow):
    where = tmp_path / flow["name"]
    where.mkdir()
    kit = _tiny_kit(
        where, MANIFEST.replace("{flow}", "f"), {**SKILLS, "flows/f.json": json.dumps(flow)}
    )
    for rel in ("scripts/policy.sh", "scripts/fail.sh"):
        p = kit.root / rel
        p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return [f"{f.level}:{f.message}" for f in validate.validate(kit) if "flow" in f.component]


def test_validate_refuses_step_ids_that_are_paths(tmp_path):
    flow = {
        "name": "bad",
        "steps": [
            {"id": "../../ESCAPED", "exec": "scripts/policy.sh"},
            {"id": "ok", "parallel": [{"id": "a/b", "decide": "scan"}]},
        ],
    }
    messages = _flow_findings(tmp_path, flow)
    assert any("'../../ESCAPED'" in msg and "must match" in msg for msg in messages), messages
    assert any("member id 'a/b'" in msg for msg in messages), messages


def test_validate_requires_loop_bounds_on_next_and_marks_first_pass_references(tmp_path):
    bad = {
        "name": "f",
        "steps": [
            {"id": "a", "run": "spec", "with": {"log": "steps.b.outputs.log"}},
            {
                "id": "b",
                "wait": {"check": "ci"},
                "timeout": "1m",
                "routes": {"pass": "end", "fail": "a", "timeout": "fail"},
            },
            {"id": "c", "run": "spec", "next": "a"},
            {"id": "d", "exec": "scripts/nope.sh", "routes": {"x": "end"}},
            {"id": "e", "run": "spec", "max_loops": 2},
        ],
    }
    out = _flow_findings(tmp_path, bad)
    assert any("may not have run yet; add `?`" in m for m in out)
    assert any("step b: an edge back to 'a' needs `max_loops`" in m for m in out)
    assert any("step c: an edge back to 'a' needs `max_loops`" in m for m in out)
    assert any("exec 'scripts/nope.sh' does not exist" in m for m in out)
    assert any("step d: exec with routes needs `default`" in m for m in out)
    assert any("warning:step e: max_loops without an edge back" in m for m in out)
    assert not [m for m in _flow_findings(tmp_path, PIPELINE) if m.startswith("error")]
