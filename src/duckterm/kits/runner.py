"""The flow runner (spec §3.7, §III.1, §III.5, §III.7): `duckterm kit run`.

A flow is skills in a declared order. Each step is a fresh, non-interactive
harness run; the model fills in a typed value, the runner validates it and
picks the next step. Run state lives in `.duckterm/runs/<run-id>/run.json`,
each attempt owns `.duckterm/runs/<run-id>/<step>/<attempt>/`, and a run
resumes only at a step boundary.

The executor seam: the runner builds argv and env for a step and hands them
to `Executor.run_process`; autopilot replaces that with "the same argv inside
the run's sandbox" and uses `after_step` to commit and push the run branch
before the receipt is written. Everything here is standard library.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import subprocess
import tempfile
import time
import uuid
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from duckterm.helpers import instance
from duckterm.kits import adapters, install
from duckterm.kits.manifest import STEP_ID_RE
from duckterm.kits.plan import PathEscape, inside, sha256

RUNS = ".duckterm/runs"
FLOWS = ".duckterm/flows"
SKILLS_FILE = "skills.json"
STEP_KINDS = ("run", "decide", "gate", "parallel", "wait", "exec")
RUNNER_HARNESSES = ("claude-code", "codex")
DEFAULT_STEP_LIMIT = 25
DEFAULT_STEP_TIMEOUT = 45 * 60
DEFAULT_EXEC_TIMEOUT = 10 * 60
WAIT_POLL = 60
MAX_ATTEMPTS = 3  # one try plus two retries on an invalid typed result
WAIT_OUTCOMES = ("pass", "fail", "timeout")


class RunRefused(ValueError):
    """The runner will not start or continue; the message says why."""


class GatePending(Exception):
    """The run is paused at a gate; `duckterm kit run --resume … --gate` answers it."""

    def __init__(self, run: Run, gate: dict[str, Any]) -> None:
        super().__init__(f"{run.id} waits at gate {gate['step']!r} (visit {gate['visit']})")
        self.run = run
        self.gate = gate


@dataclass
class ProcessResult:
    exit: int
    stdout: str
    stderr: str


class Executor:
    """Where a step's process runs. The local executor is a subprocess in the
    project; autopilot wraps the same argv in `docker exec` with its proxies'
    env. `after_step` runs after a successful attempt and before its receipt
    is written, so a receipt always means "the work is where the next attempt
    will find it"."""

    name = "local"
    # True when steps run in a copy of the project (autopilot's per-run clone):
    # only then may a workspace step with an unknown outcome be rerun. Comes from
    # the executor, never from run.json, which the repository can forge.
    isolated = False

    def run_process(
        self,
        argv: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        stdin: str | None,
        timeout: float,
    ) -> ProcessResult:
        try:
            proc = subprocess.run(
                argv,
                cwd=cwd,
                env={**os.environ, **env},
                input=stdin,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return ProcessResult(124, "", "step_timeout")
        except OSError as exc:
            return ProcessResult(127, "", str(exc))
        return ProcessResult(proc.returncode, proc.stdout, proc.stderr)

    def after_step(self, run: Run, attempt: dict[str, Any]) -> None:
        return None

    def check(self, kind: str, name: str | None, head: str, cwd: Path) -> str:
        """`pass` / `fail` / `pending` for a `wait` step, read with the
        developer's own `gh`; autopilot reads through its git proxy."""
        if kind == "ci":
            out = _gh(["api", f"repos/{{owner}}/{{repo}}/commits/{head}/check-runs"], cwd)
            runs = (out or {}).get("check_runs") or []
            if name:
                runs = [r for r in runs if r.get("name") == name]
            if not runs or any(r.get("status") != "completed" for r in runs):
                return "pending"
            return (
                "pass"
                if all(r.get("conclusion") in ("success", "skipped", "neutral") for r in runs)
                else "fail"
            )
        if kind == "review":
            out = _gh(["pr", "view", head, "--json", "reviewDecision"], cwd)
            decision = (out or {}).get("reviewDecision") or ""
            return {"APPROVED": "pass", "CHANGES_REQUESTED": "fail"}.get(decision, "pending")
        raise RunRefused(f"unknown wait check {kind!r}")

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def harness_ready(
        self, harness: str, project: Path, *, hooks: bool, limits: Limits
    ) -> str | None:
        """Why this harness cannot run a step here, or None when it can. The
        host answers for a local run; autopilot answers from the sandbox,
        where the harness and its trust record actually live."""
        if not adapters.ADAPTERS[harness].installed_here():
            return f"{harness} is not on PATH"
        if harness == "codex" and hooks and not limits.codex_trusted:
            return (
                "this kit has hooks and Codex only runs project hooks the owner trusted; "
                "pass --codex-trusted once that is done"
            )
        if harness == "claude-code" and hooks and not _claude_trusts(project):
            return (
                "this kit has hooks and Claude Code loads project hooks only in a workspace "
                "the owner trusted: run `claude` once in this folder and accept the trust dialog"
            )
        return None


def _gh(args: list[str], cwd: Path) -> dict[str, Any] | None:
    try:
        proc = subprocess.run(["gh", *args], cwd=cwd, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


@dataclass
class Limits:
    steps: int = DEFAULT_STEP_LIMIT
    step_timeout: float = DEFAULT_STEP_TIMEOUT
    exec_timeout: float = DEFAULT_EXEC_TIMEOUT
    wait_poll: float = WAIT_POLL
    yolo: bool = False  # pass the harness's skip-permissions flag; autopilot sets it
    codex_trusted: bool = False  # the owner trusted this folder's Codex hooks


# ── run state ────────────────────────────────────────────────────────────────


@dataclass
class Run:
    project: Path
    state: dict[str, Any]
    flow: dict[str, Any]
    skills: dict[str, Any]

    @property
    def id(self) -> str:
        return str(self.state["run_id"])

    @property
    def dir(self) -> Path:
        return _inside(self.project, f"{RUNS}/{self.id}")

    def attempt_dir(self, step: str, attempt: int) -> Path:
        if not STEP_ID_RE.match(step):
            raise RunRefused(f"step id {step!r} must match {STEP_ID_RE.pattern}")
        return _inside(self.project, f"{RUNS}/{self.id}/{step}/{attempt}")

    def save(self) -> None:
        self.state["updated_at"] = int(time.time())
        public = dict(self.state)
        public["attempts"] = [
            {k: v for k, v in a.items() if not k.startswith("_")} for a in self.state["attempts"]
        ]
        text = json.dumps(public, indent=2, sort_keys=True) + "\n"
        # The run file sits in the repository, so a commit can forge one that is
        # already past its gate. This machine notes the hash of every run file
        # it writes; load_run believes a run file only while it matches. Both
        # writes happen under the ledger lock, so a load never pairs a new run
        # file with the previous hash.
        with _ledger_lock(self.project, shared=False):
            _write_text(self.dir / "run.json", text)
            _ledger(self.project, self.id).write_text(sha256(text.encode()))

    def step(self, sid: str) -> dict[str, Any]:
        for s in _all_steps(self.flow):
            if s.get("id") == sid:
                return s
        raise RunRefused(f"flow has no step {sid!r}")

    def receipts(self, sid: str) -> list[dict[str, Any]]:
        return [
            a for a in self.state["attempts"] if a["step"] == sid and a["status"] == "completed"
        ]

    def last_receipt(self, sid: str) -> dict[str, Any] | None:
        done = self.receipts(sid)
        return done[-1] if done else None


def _ledger(project: Path, run_id: str) -> Path:
    key = hashlib.sha256(str(project.resolve()).encode()).hexdigest()[:24]
    return instance.home() / "kits" / "runs" / key / run_id


@contextmanager
def _ledger_lock(project: Path, *, shared: bool) -> Iterator[None]:
    """The run file and its ledger entry are two files. A save holds this
    exclusively while it writes both; a read holds it shared while it reads
    both; so the pair a reader sees is always one the runner wrote together."""
    path = _ledger(project, ".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_SH if shared else fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _read_run(project: Path, run_id: str) -> tuple[bytes, bool, bool]:
    """The run file's bytes, whether this machine started the run, and whether
    it wrote these bytes; read together under the ledger lock."""
    ledger = _ledger(project, run_id)
    with _ledger_lock(project, shared=True):
        raw = _inside(project, f"{RUNS}/{run_id}/run.json").read_bytes()
        started = ledger.is_file()
        recorded = started and ledger.read_text().strip() == sha256(raw)
    return raw, started, recorded


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".run-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _inside(project: Path, rel: str) -> Path:
    """A run, attempt or shown path: under .duckterm, no symlink on the way,
    inside the project. Everything the runner reads or writes goes through
    here, so a clone with `.duckterm/runs -> elsewhere` or a forged run.json
    cannot point it outside."""
    try:
        return inside(project, rel)
    except PathEscape as exc:
        raise RunRefused(str(exc)) from exc


def _all_steps(flow: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for s in flow.get("steps", []):
        out.append(s)
        if "parallel" in s:
            out.extend(x for x in s["parallel"] if isinstance(x, dict))
    return out


def step_kind(step: dict[str, Any]) -> str:
    kinds = [k for k in STEP_KINDS if k in step]
    if len(kinds) != 1:
        raise RunRefused(f"step {step.get('id')!r}: exactly one of {' / '.join(STEP_KINDS)}")
    return kinds[0]


# ── loading ──────────────────────────────────────────────────────────────────


def flow_path(project: Path, ref: str) -> Path:
    """`<kit>/<flow>` → the installed flow file. A path to a .json file is
    accepted too, for a kit folder that is not installed (local trials)."""
    if ref.endswith(".json"):
        p = Path(ref)
        if not p.is_absolute():
            p = project / p
        if not p.is_file():
            raise RunRefused(f"no flow file at {ref}")
        return p
    kit, sep, name = ref.partition("/")
    if not sep or not kit or not name or "/" in name:
        raise RunRefused(f"flow reference {ref!r} must be <kit>/<flow>")
    try:
        p = inside(project, f"{FLOWS}/{kit}/{name}.json")
    except PathEscape as exc:
        raise RunRefused(str(exc)) from exc
    if not p.is_file():
        raise RunRefused(f"flow {ref!r} is not installed here (expected {p.relative_to(project)})")
    return p


def load_flow(project: Path, ref: str) -> tuple[dict[str, Any], dict[str, Any], str]:
    path = flow_path(project, ref)
    raw = path.read_bytes()
    flow = json.loads(raw)
    if not isinstance(flow, dict) or not isinstance(flow.get("steps"), list):
        raise RunRefused(f"{path}: not a flow (needs a `steps` list)")
    for s in _all_steps(flow):
        if not isinstance(s, dict) or not STEP_ID_RE.match(str(s.get("id"))):
            sid = s.get("id") if isinstance(s, dict) else s
            raise RunRefused(f"{path.name}: step id {sid!r} must match {STEP_ID_RE.pattern}")
    skills_file = path.parent / SKILLS_FILE
    skills_raw = skills_file.read_bytes() if skills_file.is_file() else b""
    skills = json.loads(skills_raw) if skills_raw else {}
    kit_name = (
        ref.partition("/")[0]
        if not ref.endswith(".json")
        else str((skills.get("kit") or {}).get("name") or path.parent.name)
    )
    flow.setdefault("name", path.stem)
    # The flow, skills.json and the scripts sit in the repository, so a clone
    # or a pulled commit can rewrite all three together. They are believed
    # only when they still hash as the install on this machine recorded them
    # (DUCKTERM_HOME/kits/records). A flow named by path is a local trial:
    # harness steps run, exec steps do not (see _exec).
    recorded: dict[str, str] = {}
    anchored = ref.endswith(".json") is False
    if anchored:
        found = install.recorded_files(project, kit_name)
        if found is None:
            raise RunRefused(
                f"kit {kit_name!r} is not installed on this machine; "
                "run `duckterm kit install` (it shows what the kit will write) first"
            )
        recorded = found
        for p, data in ((path, raw), (skills_file, skills_raw)):
            rel = p.relative_to(project).as_posix()
            if not data or recorded.get(rel) != sha256(data):
                raise RunRefused(
                    f"{rel} does not match the install record on this machine; "
                    "reinstall the kit to review and accept the change"
                )
    return (
        flow,
        {**skills, "kit_name": kit_name, "anchored": anchored, "recorded": recorded},
        sha256(raw),
    )


# ── starting and resuming ────────────────────────────────────────────────────


def start(
    project: Path,
    ref: str,
    *,
    harness: str,
    inputs: dict[str, str] | None = None,
    executor: Executor | None = None,
    limits: Limits | None = None,
    executor_state: dict[str, Any] | None = None,
    on_start: Callable[[Run], None] | None = None,
) -> Run:
    """Run a flow to its end, a gate or a failure. `on_start` is called once
    the run file exists and before the first step, so a server can answer
    with the run id while a model step is still working."""
    project = Path(project).resolve()
    flow, skills, flow_hash = load_flow(project, ref)
    if harness not in adapters.ADAPTERS:
        raise RunRefused(f"unknown harness {harness!r}")
    declared = flow.get("inputs") or {}
    inputs = dict(inputs or {})
    missing = [k for k in declared if k not in inputs]
    if missing:
        raise RunRefused(f"flow {flow['name']!r} needs input(s): {', '.join(missing)}")
    run_id = f"r-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"
    ex = executor or Executor()
    state: dict[str, Any] = {
        "run_id": run_id,
        "flow": f"{skills['kit_name']}/{flow['name']}",
        "flow_hash": flow_hash,
        "kit": skills.get("kit") or {"name": skills["kit_name"], "version": None},
        "executor": ex.name,
        "executor_state": executor_state or {},
        "project": str(project),
        "harness": harness,
        "inputs": inputs,
        "isolated": False,
        "state": "running",
        "started_at": int(time.time()),
        "updated_at": int(time.time()),
        "finished_at": None,
        "current": None,
        "steps_taken": 0,
        "loops": {},
        "attempts": [],
        "gates": [],
        "failure": None,
    }
    run = Run(project, state, flow, skills)
    run.save()
    if on_start is not None:
        on_start(run)
    return _drive(run, flow["steps"][0]["id"], ex, limits or Limits())


def load_run(project: Path, run_id: str) -> Run:
    project = Path(project).resolve()
    if not run_id.startswith("r-") or "/" in run_id or ".." in run_id:
        raise RunRefused(f"{run_id!r} is not a run id")
    path = _inside(project, f"{RUNS}/{run_id}/run.json")
    if not path.is_file():
        raise RunRefused(f"no run {run_id} here")
    raw, started, recorded = _read_run(project, run_id)
    if not started:
        raise RunRefused(f"run {run_id} was not started on this machine; it does not resume here")
    if not recorded:
        raise RunRefused(f"run {run_id}: its run file changed outside the runner; start a new run")
    state = json.loads(raw)
    ref = str(state.get("flow") or "")
    if ref.endswith(".json"):  # run.json is in the repository; only installed flows resume
        raise RunRefused(f"run {run_id} names a flow file, not an installed flow; start a new run")
    flow, skills, flow_hash = load_flow(project, ref)
    if flow_hash != state["flow_hash"]:
        raise RunRefused(
            f"flow {state['flow']} changed since run {run_id} started; start a new run"
        )
    return Run(project, state, flow, skills)


def resume(
    project: Path,
    run_id: str,
    *,
    gate: tuple[str, str] | None = None,
    executor: Executor | None = None,
    limits: Limits | None = None,
) -> Run:
    run = load_run(project, run_id)
    ex = executor or Executor()
    lim = limits or Limits()
    if run.state["state"] in ("succeeded", "failed", "cancelled"):
        raise RunRefused(f"run {run_id} already {run.state['state']}")
    if run.state["state"] == "waiting_gate":
        pending = [g for g in run.state["gates"] if g["response"] is None]
        if not pending:
            raise RunRefused(f"run {run_id} waits at a gate but none is recorded")
        g = pending[-1]
        if gate is None:
            raise GatePending(run, g)
        step_id, answer = gate
        if step_id != g["step"]:
            raise RunRefused(f"run {run_id} waits at gate {g['step']!r}, not {step_id!r}")
        step = run.step(step_id)
        routes = step.get("routes") or {"approve": None, "reject": None}
        if answer not in routes:
            raise RunRefused(f"gate {step_id!r} takes {', '.join(routes)}; not {answer!r}")
        # The answer is to what the gate showed. Anything running as the user
        # can rewrite the shown file between the ask and the answer, so the
        # gate re-hashes it now. A change means a step's output was altered
        # outside the runner; no later step may read it, so the run ends.
        if sha256(_shown_bytes(run, g["show"])) != g["shown_sha256"]:
            shown = g["show"].get("path") if isinstance(g["show"], dict) else g["show"]
            return _fail(
                run,
                step_id,
                g["visit"],
                f"gate_shown_changed: {shown} changed since the gate asked",
            )
        if g.get("tree"):
            if _workspace(run, run.dir)[1] != g["tree"]:
                reason = "workspace_changed: the worktree changed since the gate asked"
                return _fail(run, step_id, g["visit"], reason)
            # the next step reads the worktree the owner approved; _begin checks again
            run.state["approved_tree"] = g["tree"]
        g["response"] = {
            "answer": answer,
            "at": int(time.time()),
            "transition": routes.get(answer),
            "visit": g["visit"],
            "shown_sha256": g["shown_sha256"],
            "flow_hash": run.state["flow_hash"],
        }
        run.state["state"] = "running"
        run.state["attempts"].append(
            {
                "step": step_id,
                "attempt": g["visit"],
                "kind": "gate",
                "status": "completed",
                "started_at": g["asked_at"],
                "finished_at": int(time.time()),
                "choice": answer,
                "outputs": {},
                "exit": 0,
                "reason": "",
                "inputs_sha256": {},
                "harness": None,
            }
        )
        run.save()
        target = _route(run, step, answer)
        if target not in (None, "end", "fail") and not _count_loop(run, step, str(target)):
            return _fail(run, step_id, g["visit"], f"max_loops:{step_id}")
        return _drive(run, target, ex, lim)
    # running: a step started and has no receipt
    started = [a for a in run.state["attempts"] if a["status"] == "started"]
    for a in started:
        step = run.step(a["step"])
        effects = _effects(run, step)
        if a["kind"] == "wait" or effects == "read-only":
            a["status"] = "abandoned"
            a["reason"] = "outcome unknown; rerun"
        elif effects == "workspace" and ex.isolated:
            a["status"] = "abandoned"
            a["reason"] = "outcome unknown; rerun in the isolated worktree"
        else:
            a["status"] = "abandoned"
            a["reason"] = "outcome unknown"
            return _fail(run, a["step"], a["attempt"], "outcome_unknown")
    current = run.state.get("current")
    if not current:
        raise RunRefused(f"run {run_id} has nothing to resume")
    run.save()
    return _drive(run, current["step"], ex, lim)


def list_runs(project: Path) -> list[dict[str, Any]]:
    project = Path(project).resolve()
    root = _inside(project, RUNS)
    out = []
    if root.is_dir():
        for d in sorted(root.iterdir()):
            if not (d / "run.json").is_file():
                continue
            try:
                raw, _, recorded = _read_run(project, d.name)
                s = json.loads(raw)
            except (RunRefused, OSError, ValueError):
                continue
            summary = {
                k: s.get(k)
                for k in (
                    "run_id",
                    "flow",
                    "state",
                    "started_at",
                    "updated_at",
                    "current",
                    "failure",
                    "executor_state",
                )
            }
            summary["inputs"] = s.get("inputs", {})
            summary["session"] = (s.get("executor_state") or {}).get("session")
            summary["steps_done"] = len(
                {a["step"] for a in s.get("attempts", []) if a.get("status") == "completed"}
            )
            # a run file this machine did not write (a clone's, a commit's) is
            # listed but offers nothing: it does not resume and has no gate
            summary["recorded"] = recorded
            out.append(summary)
    return out


# ── the loop ─────────────────────────────────────────────────────────────────


def _drive(run: Run, start_id: str | None, ex: Executor, lim: Limits) -> Run:
    lock = run.dir / "lock"
    _take_lock(lock, run.id)
    try:
        target: str | None = start_id
        while target not in (None, "end", "fail"):
            assert target is not None
            step = run.step(target)
            if run.state["steps_taken"] >= lim.steps:
                return _fail(run, target, 0, "step_limit")
            run.state["current"] = {"step": target, "attempt": len(run.receipts(target)) + 1}
            run.state["steps_taken"] += 1
            run.save()
            kind = step_kind(step)
            if kind == "gate":
                return _gate(run, step)
            try:
                outcome = _perform(run, step, kind, ex, lim)
            except GatePending:
                raise
            except RunRefused as exc:
                return _fail(run, target, run.state["current"]["attempt"], str(exc))
            if outcome is None:
                return run  # already failed and saved
            choice, attempt = outcome
            nxt = _route(run, step, choice)
            if nxt not in (None, "end", "fail") and not _count_loop(run, step, str(nxt)):
                return _fail(run, target, attempt["attempt"], f"max_loops:{target}")
            target = nxt
        if target == "fail":
            return _fail(
                run, run.state["current"]["step"], run.state["current"]["attempt"], "routed_to_fail"
            )
        run.state["state"] = "succeeded"
        run.state["current"] = None
        run.state["finished_at"] = int(time.time())
        run.save()
        return run
    finally:
        lock.unlink(missing_ok=True)


def _take_lock(lock: Path, run_id: str) -> None:
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            pid = int(lock.read_text().strip() or "0")
        except ValueError:
            pid = 0
        if pid and _alive(pid):
            raise RunRefused(f"run {run_id} is already being driven by pid {pid}") from None
        lock.unlink(missing_ok=True)
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    with os.fdopen(fd, "w") as f:
        f.write(str(os.getpid()))


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _fail(run: Run, step: str, attempt: int, reason: str) -> Run:
    run.state["state"] = "failed"
    run.state["failure"] = {"step": step, "attempt": attempt, "reason": reason}
    run.state["finished_at"] = int(time.time())
    run.save()
    return run


def _gate(run: Run, step: dict[str, Any]) -> Run:
    visit = len([g for g in run.state["gates"] if g["step"] == step["id"]]) + 1
    shown = _resolve(run, str(step["show"])) if step.get("show") else None
    shown_bytes = _shown_bytes(run, shown)
    gate = {
        "step": step["id"],
        "visit": visit,
        "question": step.get("gate"),
        "show": shown,
        "shown_sha256": sha256(shown_bytes),
        # a gate on workspace.diff approves this exact worktree, not the diff file
        "tree": shown.get("tree") if isinstance(shown, dict) and shown.get("tree") else None,
        "choices": list((step.get("routes") or {"approve": None, "reject": None}).keys()),
        "asked_at": int(time.time()),
        "response": None,
    }
    run.state["gates"].append(gate)
    run.state["state"] = "waiting_gate"
    run.save()
    return run


def _shown_bytes(run: Run, shown: Any) -> bytes:
    if isinstance(shown, dict) and "path" in shown:
        p = _inside(run.project, str(shown["path"]))
        return p.read_bytes() if p.is_file() else b""
    return json.dumps(shown, sort_keys=True).encode()


def _route(run: Run, step: dict[str, Any], choice: str | None) -> str | None:
    routes = step.get("routes")
    if routes is not None:
        target = routes.get(choice) if choice is not None else None
        if target is None:
            target = step.get("default")
        if target is None:
            raise RunRefused(f"step {step['id']!r}: choice {choice!r} has no route and no default")
        return str(target)
    if step.get("next"):
        return str(step["next"])
    top = run.flow["steps"]
    ids = [s["id"] for s in top]
    if step["id"] not in ids:  # a parallel member never routes
        return None
    i = ids.index(step["id"])
    return top[i + 1]["id"] if i + 1 < len(top) else "end"


def _count_loop(run: Run, step: dict[str, Any], target: str) -> bool:
    ids = [s["id"] for s in run.flow["steps"]]
    if step["id"] not in ids or target not in ids or ids.index(target) > ids.index(step["id"]):
        return True
    taken = run.state["loops"].get(step["id"], 0) + 1
    run.state["loops"][step["id"]] = taken
    limit = step.get("max_loops")
    return isinstance(limit, int) and taken <= limit


def _effects(run: Run, step: dict[str, Any]) -> str:
    kind = step_kind(step)
    if kind in ("run", "decide"):
        skill = (run.skills.get("skills") or {}).get(str(step[kind])) or {}
        return str(skill.get("effects", "workspace"))
    if kind == "exec":
        return str(step.get("effects", "read-only"))
    return "read-only"


# ── performing one step ──────────────────────────────────────────────────────


def _perform(
    run: Run, step: dict[str, Any], kind: str, ex: Executor, lim: Limits
) -> tuple[str | None, dict[str, Any]] | None:
    if kind == "parallel":
        return _parallel(run, step, ex, lim)
    if kind == "wait":
        return _wait(run, step, ex, lim)
    last_error = ""
    for n in range(1, MAX_ATTEMPTS + 1):
        attempt = _begin(run, step, kind)
        try:
            if kind == "exec":
                result = _exec(run, step, attempt, ex, lim)
            else:
                result = _harness(run, step, kind, attempt, ex, lim, last_error)
        except RunRefused as exc:
            attempt["status"] = "failed"
            attempt["reason"] = str(exc)
            attempt["finished_at"] = int(time.time())
            _fail(run, step["id"], attempt["attempt"], str(exc))
            return None
        exit_code, payload, stderr = result
        attempt["exit"] = exit_code
        if exit_code != 0:
            attempt["status"] = "failed"
            attempt["reason"] = _reason_from(payload, stderr, exit_code)
            attempt["finished_at"] = int(time.time())
            run.save()
            _fail(run, step["id"], attempt["attempt"], f"exit:{exit_code}")
            return None
        error = _collect(run, step, kind, attempt, payload)
        if error:
            attempt["status"] = "invalid"
            attempt["reason"] = error
            attempt["finished_at"] = int(time.time())
            run.save()
            last_error = error
            if n == MAX_ATTEMPTS or kind == "exec":
                _fail(run, step["id"], attempt["attempt"], f"invalid_output: {error}")
                return None
            continue
        try:
            ex.after_step(run, attempt)
        except Exception as exc:  # the executor could not make the work durable
            attempt["status"] = "failed"
            attempt["reason"] = f"after_step: {exc}"
            attempt["finished_at"] = int(time.time())
            run.save()
            _fail(run, step["id"], attempt["attempt"], f"after_step: {exc}")
            return None
        attempt["status"] = "completed"
        attempt["finished_at"] = int(time.time())
        run.save()
        return attempt.get("choice"), attempt
    return None


def _begin(run: Run, step: dict[str, Any], kind: str) -> dict[str, Any]:
    approved = run.state.pop("approved_tree", None)
    if approved and _workspace(run, run.dir)[1] != approved:
        raise RunRefused("workspace_changed: the worktree changed since the gate approved it")
    number = len([a for a in run.state["attempts"] if a["step"] == step["id"]]) + 1
    resolved = _inputs(run, step)
    attempt: dict[str, Any] = {
        "step": step["id"],
        "attempt": number,
        "kind": kind,
        "harness": _harness_for(run, step) if kind in ("run", "decide") else None,
        "status": "started",
        "started_at": int(time.time()),
        "finished_at": None,
        "inputs_sha256": {
            k: sha256(json.dumps(v, sort_keys=True).encode()) for k, v in resolved.items()
        },
        "exit": None,
        "choice": None,
        "reason": "",
        "outputs": {},
    }
    attempt["_inputs"] = resolved
    run.state["attempts"].append(attempt)
    run.attempt_dir(step["id"], number).mkdir(parents=True, exist_ok=True)
    run.save()
    return attempt


def _harness_for(run: Run, step: dict[str, Any]) -> str:
    return str(step.get("harness") or run.state["harness"])


def _inputs(run: Run, step: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, ref in (step.get("with") or {}).items():
        value = _resolve(run, str(ref))
        # a file output is handed on as {path, sha256}; the step that reads
        # it must see the bytes the producing step (and any gate) saw
        is_file = isinstance(value, dict) and isinstance(value.get("path"), str)
        if is_file and "sha256" in value and sha256(_shown_bytes(run, value)) != value["sha256"]:
            raise RunRefused(f"input {name!r}: {value['path']} changed since it was produced")
        out[str(name)] = value
    return out


def _resolve(run: Run, ref: str) -> Any:
    optional = ref.endswith("?")
    ref = ref[:-1] if optional else ref
    if ref.startswith("inputs."):
        return run.state["inputs"].get(ref[7:])
    if ref == "workspace.diff":
        return _diff(run)
    if ref.startswith("steps."):
        parts = ref.split(".")
        if len(parts) != 4 or parts[2] != "outputs":
            raise RunRefused(f"unresolvable reference {ref!r}")
        receipt = run.last_receipt(parts[1])
        if receipt is None:
            if optional:
                return None
            raise RunRefused(f"reference {ref!r}: step {parts[1]!r} has no receipt in this run")
        value = receipt["outputs"].get(parts[3])
        if value is None:
            if optional:
                return None
            raise RunRefused(f"reference {ref!r}: output {parts[3]!r} was not produced")
        return value
    raise RunRefused(f"unresolvable reference {ref!r}")


def _workspace(run: Run, folder: Path) -> tuple[str, str]:
    """The worktree against HEAD, new files included, and a tree id naming its
    exact state. `git diff HEAD` alone leaves untracked files out, so a gate
    showing it hid a new workflow file that a later `git add -A` would ship;
    a temporary index takes `git add -A` (the runs folder excluded) and gives
    both the diff and `write-tree`. ("", "") when this is not a git checkout."""
    index = folder / ".index"
    env = {**os.environ, "GIT_INDEX_FILE": str(index)}

    def git(*args: str) -> subprocess.CompletedProcess[str]:
        # the repository's own config is a file an agent can write: no
        # external diff driver, no textconv, no fsmonitor, no hooks path
        return subprocess.run(
            ["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", *args],
            cwd=run.project,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )

    try:
        if git("read-tree", "HEAD").returncode != 0:
            return "", ""
        git("add", "-A", "--", ".", f":(exclude){RUNS}")
        diff = git("diff", "--no-ext-diff", "--no-textconv", "--cached", "HEAD")
        tree = git("write-tree")
    except (OSError, subprocess.TimeoutExpired):
        return "", ""
    finally:
        index.unlink(missing_ok=True)
    return (
        diff.stdout if diff.returncode == 0 else "",
        tree.stdout.strip() if tree.returncode == 0 else "",
    )


def _diff(run: Run) -> dict[str, Any]:
    cur = run.state["current"]
    folder = run.attempt_dir(cur["step"], cur["attempt"])
    folder.mkdir(parents=True, exist_ok=True)
    text, tree = _workspace(run, folder)
    path = folder / "workspace.diff"
    path.write_text(text)
    return {
        "path": str(path.relative_to(run.project)),
        "sha256": sha256(text.encode()),
        "tree": tree,
    }


# ── harness steps (run, decide) ──────────────────────────────────────────────


def _skill(run: Run, name: str) -> dict[str, Any]:
    skill = (run.skills.get("skills") or {}).get(name)
    if not isinstance(skill, dict):
        raise RunRefused(
            f"skill {name!r} is not declared in {FLOWS}/{run.skills['kit_name']}/{SKILLS_FILE}"
        )
    return skill


def _harness(
    run: Run,
    step: dict[str, Any],
    kind: str,
    attempt: dict[str, Any],
    ex: Executor,
    lim: Limits,
    last_error: str,
) -> tuple[int, Any, str]:
    harness = attempt["harness"]
    skill_name = str(step[kind])
    skill = _skill(run, skill_name)
    _precheck(run, harness, skill_name, skill, ex, lim)
    outdir = run.attempt_dir(step["id"], attempt["attempt"])
    schema = _schema(skill, kind)
    prompt = _prompt(run, step, skill_name, skill, attempt, outdir, last_error)
    env: dict[str, str] = {
        "SKILL_KIT_RUN": run.id,
        "SKILL_KIT_FLOW": str(run.state["flow"]),
        "SKILL_KIT_STEP": step["id"],
        "SKILL_KIT_ATTEMPT": str(attempt["attempt"]),
        "SKILL_KIT_OUTPUT_DIR": str(outdir.relative_to(run.project)),
    }
    timeout = _seconds(step.get("timeout"), lim.step_timeout)
    if harness == "claude-code":
        argv = [
            "claude",
            "-p",
            prompt,
            "--output-format",
            "json",
            "--json-schema",
            json.dumps(schema),
        ]
        argv += (
            ["--dangerously-skip-permissions"] if lim.yolo else ["--permission-mode", "acceptEdits"]
        )
        res = ex.run_process(argv, cwd=run.project, env=env, stdin="", timeout=timeout)
        payload = _last_json(res.stdout)
        if isinstance(payload, dict) and "structured_output" in payload:
            payload = {**payload, "_structured": payload.get("structured_output")}
        return res.exit, payload, res.stderr
    # codex
    schema_file = outdir / "schema.json"
    schema_file.write_text(json.dumps(schema))
    last = outdir / "last-message.json"
    argv = [
        "codex",
        "exec",
        "--skip-git-repo-check",
        "-C",
        str(run.project),
        "--output-schema",
        str(schema_file),
        "-o",
        str(last),
    ]
    # `codex exec` dropped --full-auto (0.155 rejects it); the sandbox flag is what it meant
    argv += (
        ["--dangerously-bypass-approvals-and-sandbox"]
        if lim.yolo
        else ["--sandbox", "workspace-write"]
    )
    argv.append(prompt)
    res = ex.run_process(argv, cwd=run.project, env=env, stdin="", timeout=timeout)
    final = _last_json(last.read_text()) if last.is_file() else None
    if final is None:
        final = _last_json(res.stdout)
    return res.exit, {"_structured": final, "raw": res.stdout[-2000:]}, res.stderr


def _precheck(
    run: Run, harness: str, skill_name: str, skill: dict[str, Any], ex: Executor, lim: Limits
) -> None:
    """The skill must be installed in the project (a file check the runner can
    make anywhere, since the project is what the executor mounts); whether the
    harness itself is present and trusts the folder is the executor's answer."""
    if harness not in RUNNER_HARNESSES:
        raise RunRefused(f"the runner does not drive {harness} yet (claude-code, codex)")
    a = adapters.ADAPTERS[harness]
    installed = str(skill.get("installed_as") or skill_name)
    if not any((run.project / root / installed / "SKILL.md").is_file() for root in a.skill_roots):
        raise RunRefused(
            f"skill {skill_name!r} is not installed for {harness} in this repository "
            f"(looked under {', '.join(a.skill_roots)})"
        )
    reason = ex.harness_ready(
        harness, run.project, hooks=bool(run.skills.get("has_hooks")), limits=lim
    )
    if reason:
        raise RunRefused(reason)


def _claude_trusts(project: Path) -> bool:
    """Claude Code records the trust dialog per project in ~/.claude.json; the
    runner reads it and never writes it (the owner trusts a folder, not DuckTerm)."""
    config = Path.home() / ".claude.json"
    try:
        data = json.loads(config.read_text())
    except (OSError, ValueError):
        return False
    projects = data.get("projects") if isinstance(data, dict) else None
    if not isinstance(projects, dict):
        return False
    for key in (str(project), str(project.resolve())):
        entry = projects.get(key)
        if isinstance(entry, dict) and entry.get("hasTrustDialogAccepted"):
            return True
    return False


def _choice_output(skill: dict[str, Any]) -> tuple[str, list[str]] | None:
    for name, spec in (skill.get("outputs") or {}).items():
        if isinstance(spec, dict) and isinstance(spec.get("choice"), list):
            return str(name), [str(c) for c in spec["choice"]]
    return None


def _file_outputs(skill: dict[str, Any]) -> dict[str, str]:
    """output name → file name inside the attempt directory (the basename of
    the skill's declared `file`, so `docs/reviews/findings.md` → findings.md)."""
    out = {}
    for name, spec in (skill.get("outputs") or {}).items():
        if isinstance(spec, dict) and "file" in spec:
            out[str(name)] = Path(str(spec["file"]) or str(name)).name
    return out


def _schema(skill: dict[str, Any], kind: str) -> dict[str, Any]:
    props: dict[str, Any] = {"reason": {"type": "string"}}
    # strict output schemas (Codex's --output-schema) require every property to be
    # listed in `required`; a free-text reason is always asked for anyway
    required: list[str] = ["reason"]
    choice = _choice_output(skill)
    if choice:
        props[choice[0]] = {"type": "string", "enum": choice[1]}
        required.append(choice[0])
    if kind == "run":
        props["status"] = {"type": "string", "enum": ["done", "failed"]}
        required.append("status")
    return {
        "type": "object",
        "properties": props,
        "required": required,
        "additionalProperties": False,
    }


def _prompt(
    run: Run,
    step: dict[str, Any],
    skill_name: str,
    skill: dict[str, Any],
    attempt: dict[str, Any],
    outdir: Path,
    last_error: str,
) -> str:
    lines = [
        f'Use the skill "{skill_name}" installed in this repository.',
        f"This is step \"{step['id']}\" of flow \"{run.state['flow']}\", run {run.id}, "
        f"attempt {attempt['attempt']}; it is non-interactive, so do not ask questions.",
    ]
    inputs = attempt.get("_inputs") or {}
    if inputs:
        lines.append("Inputs (JSON; file inputs are paths relative to the repository root):")
        lines.append(json.dumps(inputs, indent=2, sort_keys=True))
    files = _file_outputs(skill)
    if files:
        rel = outdir.relative_to(run.project).as_posix()
        lines.append("Write each file output to exactly this path, creating the folder if needed:")
        for name, filename in files.items():
            lines.append(f"  {name}: {rel}/{filename}")
    choice = _choice_output(skill)
    if choice:
        lines.append(f"Answer \"{choice[0]}\" with exactly one of: {', '.join(choice[1])}.")
    if last_error:
        lines.append(f"Your previous attempt was invalid: {last_error}. Fix that.")
    lines.append("When finished, respond only with JSON matching the given schema.")
    return "\n".join(lines)


def _seconds(value: Any, default: float) -> float:
    if value is None:
        return default
    if isinstance(value, int | float):
        return float(value)
    text = str(value).strip()
    units = {"s": 1, "m": 60, "h": 3600}
    if text and text[-1] in units and text[:-1].isdigit():
        return int(text[:-1]) * units[text[-1]]
    if text.isdigit():
        return float(text)
    raise RunRefused(f"timeout {value!r} must look like 10m, 90s or 2h")


def _last_json(text: str) -> Any:
    text = text.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        pass
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                continue
    return None


def _reason_from(payload: Any, stderr: str, exit_code: int) -> str:
    if isinstance(payload, dict):
        for key in ("api_error_status", "error", "subtype"):
            if payload.get(key) not in (None, "", "success"):
                return f"{key}={payload[key]}"
    return stderr.strip()[-400:] or f"exit {exit_code}"


def _collect(
    run: Run, step: dict[str, Any], kind: str, attempt: dict[str, Any], payload: Any
) -> str:
    """Validate the typed result and bind outputs to this attempt. Returns an
    error message, or "" when the attempt is good."""
    if kind == "exec":
        structured = payload if isinstance(payload, dict) else None
        if structured is None:
            return "no JSON result on stdout"
        outputs: dict[str, Any] = {}
        outdir = run.attempt_dir(step["id"], attempt["attempt"])
        for name, value in (structured.get("outputs") or {}).items():
            if isinstance(value, dict) and "path" in value:
                try:
                    f = _inside(run.project, str(value["path"]))
                except RunRefused as exc:
                    return f"output {name!r}: {exc}"
                if not f.is_relative_to(outdir) or not f.is_file():
                    where = outdir.relative_to(run.project).as_posix()
                    return f"output {name!r}: file outputs must be files under {where}/"
                value = {
                    "path": f.relative_to(run.project).as_posix(),
                    "sha256": sha256(f.read_bytes()),
                }
            outputs[str(name)] = value
        if step.get("routes") is not None:
            choice = structured.get("choice")
            if not isinstance(choice, str) or choice not in step["routes"]:
                return f"choice {choice!r} is not one of {', '.join(step['routes'])}"
            attempt["choice"] = choice
        attempt["reason"] = str(structured.get("reason") or "")
        attempt["outputs"] = outputs
        return ""
    skill = _skill(run, str(step[kind]))
    structured = payload.get("_structured") if isinstance(payload, dict) else None
    if not isinstance(structured, dict):
        return "no structured result"
    outdir = run.attempt_dir(step["id"], attempt["attempt"])
    outputs = {}
    choice = _choice_output(skill)
    if choice:
        value = structured.get(choice[0])
        if value not in choice[1]:
            return f"{choice[0]} must be one of {', '.join(choice[1])}, got {value!r}"
        outputs[choice[0]] = value
        attempt["choice"] = str(value)
    if kind == "run" and structured.get("status") == "failed":
        return f"the skill reported failure: {structured.get('reason') or ''}"
    for name, filename in _file_outputs(skill).items():
        f = outdir / filename
        if not f.is_file() or f.stat().st_size == 0:
            return f"file output {name!r} missing or empty at {f.relative_to(run.project)}"
        data = f.read_bytes()
        previous = run.last_receipt(step["id"])
        if previous and not skill.get("idempotent"):
            old = (previous["outputs"].get(name) or {}).get("sha256")
            if old == sha256(data):
                return f"file output {name!r} is unchanged from the previous attempt"
        outputs[name] = {"path": str(f.relative_to(run.project)), "sha256": sha256(data)}
    attempt["reason"] = str(structured.get("reason") or "")
    attempt["outputs"] = outputs
    return ""


# ── exec steps ───────────────────────────────────────────────────────────────


def _exec(
    run: Run, step: dict[str, Any], attempt: dict[str, Any], ex: Executor, lim: Limits
) -> tuple[int, Any, str]:
    rel = str(step["exec"])
    scripts = run.skills.get("scripts") or {}
    if rel not in scripts:
        raise RunRefused(f"exec {rel!r} is not a script this kit installed")
    if not run.skills.get("anchored"):
        raise RunRefused(
            f"exec {rel!r}: scripts run only from a kit installed on this machine "
            "(`duckterm kit install`), not from a flow named by path"
        )
    where = f"{FLOWS}/{run.skills['kit_name']}/{rel}"
    script = _inside(run.project, where)
    if not script.is_file():
        raise RunRefused(f"exec {rel!r}: {where} is missing")
    # the deciding hash is the one the install on this machine recorded; the
    # copy in skills.json is itself anchored to that record (load_flow)
    want = run.skills["recorded"].get(where)
    if want is None or sha256(script.read_bytes()) != want or scripts[rel] != want:
        raise RunRefused(f"exec {rel!r} changed since install; reinstall the kit")
    args = [str(a) for a in (step.get("args") or [])]
    outdir = run.attempt_dir(step["id"], attempt["attempt"])
    env = {
        "SKILL_KIT_RUN": run.id,
        "SKILL_KIT_STEP": step["id"],
        "SKILL_KIT_ATTEMPT": str(attempt["attempt"]),
        "SKILL_KIT_OUTPUT_DIR": str(outdir.relative_to(run.project)),
    }
    stdin = json.dumps(attempt.get("_inputs") or {}, sort_keys=True)
    res = ex.run_process(
        [str(script), *args],
        cwd=run.project,
        env=env,
        stdin=stdin,
        timeout=_seconds(step.get("timeout"), lim.exec_timeout),
    )
    return res.exit, _last_json(res.stdout), res.stderr


# ── parallel and wait ────────────────────────────────────────────────────────


def _parallel(
    run: Run, step: dict[str, Any], ex: Executor, lim: Limits
) -> tuple[str | None, dict[str, Any]] | None:
    members = [m for m in step["parallel"] if isinstance(m, dict)]
    for m in members:
        if _effects(run, m) != "read-only":
            raise RunRefused(f"parallel step {m.get('id')!r} is not read-only")
    results: dict[str, Any] = {}

    def one(m: dict[str, Any]) -> None:
        results[m["id"]] = _perform(run, m, step_kind(m), ex, lim)

    with ThreadPoolExecutor(max_workers=max(1, len(members))) as pool:
        list(pool.map(one, members))
    failed = [mid for mid, r in results.items() if r is None]
    if failed:
        if run.state["state"] != "failed":
            _fail(run, step["id"], 1, f"parallel member failed: {failed[0]}")
        return None
    group = {
        "step": step["id"],
        "attempt": len([a for a in run.state["attempts"] if a["step"] == step["id"]]) + 1,
        "kind": "parallel",
        "harness": None,
        "status": "completed",
        "started_at": min(r[1]["started_at"] for r in results.values()),
        "finished_at": int(time.time()),
        "inputs_sha256": {},
        "exit": 0,
        "choice": None,
        "reason": "",
        "outputs": {mid: r[1]["outputs"] for mid, r in results.items()},
    }
    run.state["attempts"].append(group)
    run.save()
    return None, group


def _wait(
    run: Run, step: dict[str, Any], ex: Executor, lim: Limits
) -> tuple[str | None, dict[str, Any]] | None:
    spec = step["wait"] if isinstance(step["wait"], dict) else {"check": str(step["wait"])}
    kind = str(spec.get("check") or "ci")
    name = spec.get("name")
    for outcome in WAIT_OUTCOMES:
        if outcome not in (step.get("routes") or {}):
            raise RunRefused(f"wait {step['id']!r} must route {outcome!r}")
    head = _head(run)
    attempt = _begin(run, step, "wait")
    attempt["head_sha"] = head
    deadline = time.time() + _seconds(step.get("timeout"), lim.step_timeout)
    outdir = run.attempt_dir(step["id"], attempt["attempt"])
    while True:
        result = ex.check(kind, str(name) if name else None, head, run.project)
        if result in ("pass", "fail"):
            break
        if time.time() >= deadline:
            result = "timeout"
            break
        ex.sleep(lim.wait_poll)
    outputs: dict[str, Any] = {"result": result}
    if result == "fail":
        log = outdir / "log"
        if not log.is_file():
            log.write_text(f"{kind} {name or ''} failed at {head}\n")
        outputs["log"] = {
            "path": str(log.relative_to(run.project)),
            "sha256": sha256(log.read_bytes()),
        }
    attempt["choice"] = result
    attempt["outputs"] = outputs
    attempt["exit"] = 0
    attempt["status"] = "completed"
    attempt["finished_at"] = int(time.time())
    run.save()
    return result, attempt


def _head(run: Run) -> str:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=run.project,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return "HEAD"


# ── install-time helpers (used by adapters) ──────────────────────────────────


def skills_manifest(kit: Any, flows: list[dict[str, Any]], kit_root: Path) -> dict[str, Any]:
    """`skills.json` beside a kit's installed flows: every skill's declared
    fields the runner needs, and the hash of every script a flow `exec`s."""
    skills: dict[str, Any] = {}
    for c in kit.by_kind("skill"):
        source = kit_root / str(c.path)
        try:
            from duckterm.kits import manifest as m

            front = m.frontmatter((source / "SKILL.md").read_text())
        except OSError:
            front = {}
        skills[c.name] = {
            "installed_as": str(c.fields.get("replaces") or source.name),
            "description": " ".join(str(front.get("description", "")).split()),
            "effects": c.fields.get("effects", "workspace"),
            "inputs": c.fields.get("inputs") or {},
            "outputs": c.fields.get("outputs") or {},
            "idempotent": bool(c.fields.get("idempotent", False)),
        }
    scripts: dict[str, str] = {}
    for flow in flows:
        for s in _all_steps(flow):
            if isinstance(s, dict) and isinstance(s.get("exec"), str):
                p = kit_root / s["exec"]
                if p.is_file():
                    scripts[s["exec"]] = hashlib.sha256(p.read_bytes()).hexdigest()
    return {
        "kit": {"name": kit.name, "version": kit.version},
        "skills": skills,
        "scripts": scripts,
        "has_hooks": bool(kit.by_kind("hook")),
    }
