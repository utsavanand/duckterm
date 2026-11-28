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
from pathlib import Path
from typing import Any

from duckterm import github_projects
from duckterm.autopilot import catalog, executor, github_push, sandbox, store
from duckterm.autopilot.executor import AutopilotExecutor, prepare_github
from duckterm.autopilot.sandbox import Gateway, Sandbox
from duckterm.kits import install, plan, runner
from duckterm.kits import manifest as kit_manifest

KEYCHAIN_SERVICE = "duckterm-autopilot-claude"
HARNESS = "claude-code"
ACTIVE = frozenset({"starting", "running"})
TOKEN_TTL = 6 * 3600


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
        "trigger": trigger,
        "item": {k: item.get(k) for k in ("number", "title", "url", "started_by")},
        "branch": f"autopilot/{run_id}",
        "state": "starting",
        "started_at": int(time.time() * 1000),
    }
    _write_meta(runs_root() / run_id, meta)
    threading.Thread(
        target=_execute, args=(autopilot, meta, item), name=f"autopilot-{run_id}", daemon=True
    ).start()
    return run_id


def _execute(autopilot: dict[str, Any], meta: dict[str, Any], item: dict[str, Any]) -> None:
    root = runs_root() / meta["id"]
    box: Sandbox | None = None
    gateway = Gateway(secret_path=store.root() / "gateway-secret")
    token = None
    try:
        problems = readiness()
        if problems:
            raise RuntimeError("; ".join(problems))
        flow = catalog.find(autopilot["flow"])
        dirs = prepare_github(autopilot["repo"], "", meta["branch"], root)
        base_sha = _git_out(dirs.project, "rev-parse", "HEAD")
        _update(root, base_sha=base_sha)
        kit = kit_manifest.load(flow.kit_path)
        install.apply(plan.build(kit, dirs.project, [HARNESS]))
        executor.hide_install(dirs)
        gateway.ensure(
            anthropic_key=None, openai_key=None, anthropic_oauth_token=subscription_token()
        )
        box = Sandbox.create(meta["id"], gateway=gateway.name, mounts=dirs.mounts())
        box.trust(str(dirs.project))
        token = gateway.mint(meta["id"], budget=autopilot["budget_m"] * 1_000_000, ttl=TOKEN_TTL)
        _update(root, state="running", sandbox=box.name)
        inputs = (
            {flow.triggers[meta["trigger"]]: json.dumps(item)}
            if flow.triggers.get(meta["trigger"])
            else {}
        )
        run = runner.start(
            dirs.project,
            flow.id,
            harness=HARNESS,
            inputs=inputs,
            executor=AutopilotExecutor(box, dirs, token, meta["branch"]),
            limits=runner.Limits(yolo=True),
            executor_state={"run_host": "mac", "branch": meta["branch"], "sandbox_id": box.name},
        )
        _update(root, state=run.state["state"], runner_run=run.id)
        if run.state["state"] == "succeeded":
            _publish(root, autopilot, meta, item, dirs.project, base_sha, run.state)
    except Exception as exc:  # noqa: BLE001 — any failure ends the run with its reason
        _update(root, state="failed", error=str(exc)[:500])
    finally:
        if token:
            used = gateway.usage(token).get("used")
            gateway.revoke(token)
            _update(root, tokens=used)
        if box:
            box.destroy()
        _update(root, ended_at=int(time.time() * 1000))


def _publish(
    root: Path,
    autopilot: dict[str, Any],
    meta: dict[str, Any],
    item: dict[str, Any],
    project: Path,
    base_sha: str,
    state: dict[str, Any],
) -> None:
    """Push the run branch and open a draft pull request for it. A run that
    changed nothing opens nothing. A failure here is recorded on the run; the
    branch stays on this Mac either way."""
    if _git_out(project, "rev-list", "--count", f"{base_sha}..HEAD") == "0":
        _update(root, pr_skipped="the run changed no files")
        return
    try:
        token, _ = github_projects.local_token()
        github_push.push(project, autopilot["repo"], meta["branch"], token, base_sha)
        _update(root, pushed=True)
        pr = github_push.open_pull_request(
            autopilot["repo"],
            meta["branch"],
            pr_title(item),
            pr_body(meta, item, project, state),
            token,
        )
        _update(root, pr=pr)
    except github_push.PushRefused as exc:
        _update(root, pushed=False, pr_skipped=str(exc))
    except Exception as exc:  # noqa: BLE001 — the run succeeded; record why publishing did not
        _update(root, pr_error=str(exc)[:300])


def pr_title(item: dict[str, Any]) -> str:
    number, title = item.get("number"), str(item.get("title") or "").strip()
    head = f"Fix #{number}: " if number else ""
    return (head + title)[:120] or "Autopilot change"


def pr_body(
    meta: dict[str, Any], item: dict[str, Any], project: Path, state: dict[str, Any]
) -> str:
    """The spec the run wrote, a closing reference to the issue, and where it
    came from. Draft until a person reviews it."""
    spec = spec_text(project, state)
    parts = [spec[:20000].strip()] if spec else []
    if item.get("number"):
        parts.append(f"Closes #{item['number']}.")
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
    if not parts:
        return None
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
