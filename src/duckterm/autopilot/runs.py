"""Starting autopilot runs and listing them.

Each run lives under <home>/autopilot/runs/<run-id>/: `autopilot.json` says
what started it, `repo/` is the host clone the sandbox mounts, and the kit
runner's own receipt is `repo/.duckterm/runs/<runner-id>/run.json`. The list
reads those files; there is no table (spec §7.3).
"""

import json
import os
import stat
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from duckterm import github_projects
from duckterm.autopilot import catalog, executor, github_push, sandbox, store
from duckterm.autopilot.executor import AutopilotExecutor, RunDirs, prepare_github
from duckterm.autopilot.sandbox import Gateway, Sandbox
from duckterm.kits import install, plan, runner
from duckterm.kits import manifest as kit_manifest

KEYCHAIN_SERVICE = "duckterm-autopilot-claude"
HARNESS = "claude-code"
ACTIVE = frozenset({"starting", "running"})
TOKEN_TTL = 6 * 3600
LIMITS = runner.Limits(yolo=True)
# What a gate shows the owner is a step's output; this much of it is plenty.
SHOWN_CHARS = 20000


def runs_root() -> Path:
    return store.root() / "runs"


def subscription_token() -> str | None:
    proc = subprocess.run(
        [
            "security",
            "find-generic-password",
            "-a",
            os.environ.get("USER", ""),
            "-s",
            KEYCHAIN_SERVICE,
            "-w",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    return proc.stdout.strip() or None if proc.returncode == 0 else None


def readiness() -> list[str]:
    """What stops a run from starting on this Mac, in words for the app."""
    problems = []
    if not sandbox.docker_available():
        problems.append("Docker is not running")
    elif sandbox.docker("image", "inspect", sandbox.IMAGE, check=False).returncode != 0:
        problems.append(f"The sandbox image {sandbox.IMAGE} is not built")
    if subscription_token() is None:
        problems.append("No Claude subscription token in the Keychain")
    return problems


def start(autopilot: dict[str, Any], trigger: str, item: dict[str, Any]) -> str:
    """Record a run and start it on a thread. Returns the run id at once."""
    run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
    meta = {
        "id": run_id,
        "autopilot": autopilot["id"],
        "autopilot_name": autopilot["name"],
        "workspace": autopilot.get("workspace") or "",
        "flow": autopilot["flow"],
        "repo": autopilot["repo"],
        "issue_repo": store.issue_repo(autopilot),
        "budget_m": autopilot["budget_m"],
        "trigger": trigger,
        "item": {k: item.get(k) for k in ("number", "title", "url", "started_by")},
        "branch": f"autopilot/{run_id}",
        "state": "starting",
        "started_at": int(time.time() * 1000),
    }
    _write_meta(runs_root() / run_id, meta)
    threading.Thread(
        target=_execute, args=(meta, item), name=f"autopilot-{run_id}", daemon=True
    ).start()
    return run_id


def _execute(meta: dict[str, Any], item: dict[str, Any]) -> None:
    root = runs_root() / meta["id"]
    try:
        problems = readiness()
        if problems:
            raise RuntimeError("; ".join(problems))
        flow = catalog.find(meta["flow"])
        dirs = prepare_github(meta["repo"], "", meta["branch"], root)
        _update(root, base_sha=_git_out(dirs.project, "rev-parse", "HEAD"))
        kit = kit_manifest.load(flow.kit_path)
        install.apply(plan.build(kit, dirs.project, [HARNESS]))
        executor.hide_install(dirs)
        inputs = (
            {flow.triggers[meta["trigger"]]: json.dumps(item)}
            if flow.triggers.get(meta["trigger"])
            else {}
        )
        _in_sandbox(
            root,
            dirs,
            lambda ex: runner.start(
                dirs.project,
                flow.id,
                harness=HARNESS,
                inputs=inputs,
                executor=ex,
                limits=LIMITS,
                executor_state={
                    "run_host": "mac",
                    "branch": meta["branch"],
                    "sandbox_id": ex.box.name,
                },
            ),
        )
    except Exception as exc:  # noqa: BLE001 — any failure ends the run with its reason
        _update(root, state="failed", error=str(exc)[:500])
    _settle(root)


def answer(run_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Answer the gate a run waits at, then carry the run on from it in a new
    sandbox. The sandbox it asked from was removed when it stopped to wait."""
    root = runs_root() / run_id
    if "/" in run_id or not (root / "autopilot.json").is_file():
        raise KeyError(run_id)
    step, choice = str(body.get("step") or ""), str(body.get("answer") or "")
    with _META_LOCK:
        meta = json.loads((root / "autopilot.json").read_text())
        gate = pending_gate(root)
        if meta["state"] != "waiting_gate" or gate is None:
            raise ValueError("This run is not waiting for an answer")
        if step != gate["step"] or choice not in gate["choices"]:
            raise ValueError(
                f"The run waits at {gate['step']} for one of: {', '.join(gate['choices'])}"
            )
        # Only the owner reaches this API today; `by` is where a person's
        # name goes once runs are shared (people-and-projects spec).
        meta.setdefault("answers", []).append(
            {"step": step, "answer": choice, "by": "owner", "at": int(time.time() * 1000)}
        )
        meta["state"] = "running"
        _write_meta(root, meta)
    threading.Thread(
        target=_continue, args=(meta, step, choice), name=f"autopilot-{run_id}", daemon=True
    ).start()
    return {"ok": True}


def _continue(meta: dict[str, Any], step: str, choice: str) -> None:
    root = runs_root() / meta["id"]
    dirs = RunDirs(root)
    rejected = choice == "reject"
    try:
        run = _in_sandbox(
            root,
            dirs,
            lambda ex: runner.resume(
                dirs.project, meta["runner_run"], gate=(step, choice), executor=ex, limits=LIMITS
            ),
            publish=not rejected,
        )
        if rejected and run.state["state"] == "succeeded":
            _update(root, state="rejected")
    except Exception as exc:  # noqa: BLE001 — any failure ends the run with its reason
        _update(root, state="failed", error=str(exc)[:500])
    _settle(root)


def _in_sandbox(
    root: Path,
    dirs: RunDirs,
    drive: Callable[[AutopilotExecutor], runner.Run],
    *,
    publish: bool = True,
) -> runner.Run:
    """Give the run a sandbox and a model token for as long as `drive` takes:
    to the end, a failure or a gate. Tokens a run used before a gate count
    against its budget after it. A run that succeeds is published unless the
    owner rejected it: rejecting routes the flow to its end, which the runner
    records as success."""
    meta = json.loads((root / "autopilot.json").read_text())
    used = float(meta.get("tokens") or 0)  # the gateway weighs tokens, so a float
    left = int(meta["budget_m"] * 1_000_000 - used)
    if left <= 0:
        raise RuntimeError("the run has used its token budget")
    gateway = Gateway(secret_path=store.root() / "gateway-secret")
    box: Sandbox | None = None
    token = None
    try:
        gateway.ensure(
            anthropic_key=None, openai_key=None, anthropic_oauth_token=subscription_token()
        )
        box = Sandbox.create(meta["id"], gateway=gateway.name, mounts=dirs.mounts())
        box.trust(str(dirs.project))
        token = gateway.mint(meta["id"], budget=left, ttl=TOKEN_TTL)
        _update(root, state="running", sandbox=box.name)
        run = drive(AutopilotExecutor(box, dirs, token, meta["branch"]))
        _update(root, state=run.state["state"], runner_run=run.id)
        if publish and run.state["state"] == "succeeded":
            _publish(root, meta, dirs.project, meta["base_sha"], run.state)
        return run
    finally:
        if token:
            spent = gateway.usage(token).get("used")
            used += spent if isinstance(spent, int | float) else 0
            gateway.revoke(token)
            _update(root, tokens=used)
        if box:
            box.destroy()


def _settle(root: Path) -> None:
    """A run waiting at a gate has not ended; anything else has."""
    if json.loads((root / "autopilot.json").read_text())["state"] != "waiting_gate":
        _update(root, ended_at=int(time.time() * 1000))


def _publish(
    root: Path, meta: dict[str, Any], project: Path, base_sha: str, state: dict[str, Any]
) -> None:
    """Push the run branch and open a draft pull request for it. A run that
    changed nothing opens nothing. A failure here is recorded on the run; the
    branch stays on this Mac either way."""
    if _git_out(project, "rev-list", "--count", f"{base_sha}..HEAD") == "0":
        _update(root, pr_skipped="the run changed no files")
        return
    try:
        token, _ = github_projects.local_token()
        github_push.push(project, meta["repo"], meta["branch"], token, base_sha)
        _update(root, pushed=True)
        pr = github_push.open_pull_request(
            meta["repo"],
            meta["branch"],
            pr_title(meta, meta["item"]),
            pr_body(meta, meta["item"], project, state),
            token,
        )
        _update(root, pr=pr)
    except github_push.PushRefused as exc:
        _update(root, pushed=False, pr_skipped=str(exc))
    except Exception as exc:  # noqa: BLE001 — the run succeeded; record why publishing did not
        _update(root, pr_error=str(exc)[:300])


def issue_ref(meta: dict[str, Any], item: dict[str, Any]) -> str:
    """`#7`, or `owner/reports#7` when the issue is in another repository
    than the pull request; GitHub links and closes that form across repos."""
    other = meta.get("issue_repo")
    prefix = other if other and other != meta.get("repo") else ""
    return f"{prefix}#{item['number']}"


def pr_title(meta: dict[str, Any], item: dict[str, Any]) -> str:
    number, title = item.get("number"), str(item.get("title") or "").strip()
    head = f"Fix {issue_ref(meta, item)}: " if number else ""
    return (head + title)[:120] or "Autopilot change"


def pr_body(
    meta: dict[str, Any], item: dict[str, Any], project: Path, state: dict[str, Any]
) -> str:
    """The spec the run wrote, a closing reference to the issue, and where it
    came from. Draft until a person reviews it."""
    spec = spec_text(project, state)
    parts = [spec[:20000].strip()] if spec else []
    if item.get("number"):
        parts.append(f"Closes {issue_ref(meta, item)}.")
    parts.append(
        f"Opened as a draft by DuckTerm autopilot ({meta['flow']}, run {meta['id']}). "
        "Review it before merging."
    )
    return "\n\n".join(parts)


def spec_text(project: Path, state: dict[str, Any]) -> str | None:
    """The newest spec the run wrote. The path comes from the run's receipt,
    which the sandbox could write, so a link or a path outside the clone is
    not read."""
    found = None
    for attempt in state.get("attempts") or []:
        spec = (attempt.get("outputs") or {}).get("spec")
        if isinstance(spec, dict) and spec.get("path"):
            found = str(spec["path"])
    if not found or Path(found).is_absolute() or ".." in Path(found).parts:
        return None
    data = _read_capped(project, found, SPEC_CAP)
    return None if data is None else data.decode(errors="replace")


SPEC_CAP = 1_000_000
RECEIPT_CAP = 1_000_000
DIFF_CAP = 400_000


def _read_capped(root: Path, rel: str, cap: int) -> bytes | None:
    """At most cap bytes of root/rel, a file the sandbox may have written, or
    None. Each folder is opened relative to the last with O_NOFOLLOW, so a link
    anywhere on the path, even one swapped in by a live sandbox, is refused;
    and only a regular file is read, since a FIFO would block the reading
    thread for good."""
    parts = Path(rel).parts
    try:
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    except OSError:
        return None
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        child = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        os.close(fd)
        fd = child
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        with os.fdopen(fd, "rb") as f:
            fd = -1
            return f.read(cap)
    except OSError:
        return None
    finally:
        if fd >= 0:
            os.close(fd)


def changes(project: Path, base: str) -> dict[str, Any] | None:
    """The run's change against the commit it started from: each file's line
    counts and the patch, cut at DIFF_CAP bytes."""
    try:
        numstat, _ = _git_capped(project, DIFF_CAP, "--numstat", base)
        files = []
        for line in numstat.splitlines():
            added, removed, path = line.split("\t", 2)
            binary = added == "-"
            files.append(
                {
                    "path": path,
                    "added": 0 if binary else int(added),
                    "removed": 0 if binary else int(removed),
                    "binary": binary,
                }
            )
        patch, cut = _git_capped(project, DIFF_CAP, "--no-color", base)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return {"files": files, "patch": patch, "cut": cut}


def _git_capped(project: Path, cap: int, *args: str) -> tuple[str, bool]:
    """`git diff <args> HEAD`, read up to cap bytes and then stopped, and
    whether it was cut. The clone's config is the sandbox's, so no external
    diff driver, textconv or fsmonitor runs."""
    argv = ["git", "-c", "core.fsmonitor=false", "-C", str(project), "diff", "--no-renames"]
    argv += ["--no-ext-diff", "--no-textconv", *args[:-1], "--end-of-options", args[-1], "HEAD"]
    with subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as proc:
        assert proc.stdout is not None
        timer = threading.Timer(60, proc.kill)
        timer.start()
        try:
            out = proc.stdout.read(cap + 1)
        finally:
            timer.cancel()
        if len(out) > cap:
            proc.kill()
            return out[:cap].decode(errors="replace"), True
        if proc.wait(timeout=60):
            raise subprocess.CalledProcessError(proc.returncode, argv)
    return out.decode(errors="replace").strip(), False


def _git_out(project: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(project), *args], capture_output=True, text=True, timeout=60, check=True
    )
    return proc.stdout.strip()


def recover() -> None:
    """At server start: a run whose thread died with the last server is over."""
    for meta in list_runs():
        if meta["state"] in ACTIVE:
            _update(runs_root() / meta["id"], state="interrupted", error="DuckTerm restarted")
            Sandbox(meta["id"], "runc").destroy()


def list_runs() -> list[dict[str, Any]]:
    out = []
    base = runs_root()
    for meta_path in base.glob("*/autopilot.json") if base.is_dir() else []:
        meta = json.loads(meta_path.read_text())
        receipt = _receipt(meta_path.parent)
        if receipt:
            meta["steps"] = [
                {
                    "step": a["step"],
                    "attempt": a["attempt"],
                    "exit": a.get("exit"),
                    "choice": a.get("choice"),
                }
                for a in receipt.get("attempts") or []
            ]
            meta["current"] = (receipt.get("current") or {}).get("step")
            if meta["state"] == "running" and receipt.get("state") != "running":
                meta["runner_state"] = receipt.get("state")
            if receipt.get("failure"):
                meta["failure"] = receipt["failure"]
            if meta["state"] == "waiting_gate":
                meta["gate"] = pending_gate(meta_path.parent, receipt)
        out.append(meta)
    return sorted(out, key=lambda m: m["started_at"], reverse=True)


def get(run_id: str) -> dict[str, Any]:
    for meta in list_runs():
        if meta["id"] == run_id:
            return meta
    raise KeyError(run_id)


def detail(run_id: str) -> dict[str, Any]:
    """One run for its detail view: every attempt with its timing and the
    line it ended on, the issue it started from, the spec it wrote and the
    change on its branch."""
    meta = get(run_id)
    root = runs_root() / run_id
    receipt = _receipt(root) or {}
    project = root / "repo"
    meta["attempts"] = [
        {
            key: a.get(key)
            for key in (
                "step",
                "attempt",
                "kind",
                "status",
                "exit",
                "choice",
                "reason",
                "started_at",
                "finished_at",
            )
        }
        for a in receipt.get("attempts") or []
    ]
    meta["gates"] = receipt.get("gates") or []
    meta["loops"] = receipt.get("loops") or {}
    try:
        issue = json.loads(next(iter((receipt.get("inputs") or {}).values()), "") or "{}")
    except (TypeError, ValueError):
        issue = {}
    meta["issue_body"] = str(issue.get("body") or "") if isinstance(issue, dict) else ""
    meta["spec"] = spec_text(project, receipt) if receipt else None
    base = meta.get("base_sha") or _merge_base(project)
    meta["changes"] = changes(project, base) if base else None
    return meta


def _merge_base(project: Path) -> str | None:
    """Runs from before base_sha was recorded: the branch point from the
    default branch."""
    try:
        return _git_out(project, "merge-base", "HEAD", "origin/HEAD") or None
    except (OSError, subprocess.SubprocessError):
        return None


def active(autopilot_id: str) -> int:
    return sum(1 for m in list_runs() if m["autopilot"] == autopilot_id and m["state"] in ACTIVE)


def summary() -> dict[str, Any]:
    """The Home tile's contract (spec §7.3). "Today" is the local day."""
    midnight = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1)) * 1000
    runs = list_runs()
    today = [m for m in runs if m["started_at"] >= midnight]
    return {
        "available": True,
        "active": sum(1 for m in runs if m["state"] in ACTIVE),
        "waiting_gate": sum(1 for m in runs if m["state"] == "waiting_gate"),
        "done_today": sum(1 for m in today if m["state"] == "succeeded"),
        "failed_today": sum(1 for m in today if m["state"] == "failed"),
    }


def pending_gate(root: Path, receipt: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """The gate the run waits at: its question, the answers it takes and the
    text of what it shows (a step's output file)."""
    receipt = receipt if receipt is not None else _receipt(root)
    pending = [g for g in (receipt or {}).get("gates") or [] if g.get("response") is None]
    if not pending:
        return None
    g = pending[-1]
    shown = ""
    project = (root / "repo").resolve()
    if isinstance(g.get("show"), dict) and g["show"].get("path"):
        path = project / str(g["show"]["path"])
        if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(project):
            shown = path.read_text(errors="replace")[:SHOWN_CHARS]
    return {
        "step": g["step"],
        "question": g.get("question") or "",
        "choices": g["choices"],
        "shown": shown,
    }


def _receipt(root: Path) -> dict[str, Any] | None:
    found = sorted((root / "repo" / runner.RUNS).glob("*/run.json"))
    if not found:
        return None
    raw = _read_capped(root, str(found[-1].relative_to(root)), RECEIPT_CAP + 1)
    if raw is None or len(raw) > RECEIPT_CAP:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


_META_LOCK = threading.Lock()


def _write_meta(root: Path, meta: dict[str, Any]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    tmp = root / "autopilot.json.tmp"
    tmp.write_text(json.dumps(meta, indent=2) + "\n")
    tmp.replace(root / "autopilot.json")


def _update(root: Path, **fields: Any) -> None:
    with _META_LOCK:
        meta = json.loads((root / "autopilot.json").read_text())
        meta.update(fields)
        _write_meta(root, meta)
