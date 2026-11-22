"""Runs the kit runner's steps inside an autopilot sandbox.

The runner (duckterm.kits.runner) works on host paths: the project, its
attempt directories, `git diff`, `codex -C`. So each run gets its own clone on
the host, and the sandbox mounts that clone at the same path. Three overlays
keep a step from reaching past its workspace:

- `<project>/.git` is read-only. The host runs git in this clone (diff,
  commit, push); a step that could write hooks or config there would run code
  on the host.
- `<project>/.duckterm/flows` is read-only: the installed flow, skill records
  and exec scripts whose hashes the runner checks.
- `<project>/.duckterm/runs` is replaced by a scratch copy without run.json,
  so a step can read earlier outputs but cannot write its own receipt. After
  each process, the current attempt's directory is copied back; symbolic
  links are never copied in either direction.
"""

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from duckterm.autopilot.sandbox import Mount, Sandbox, harness_env
from duckterm.kits import runner
from duckterm.kits.runner import ProcessResult

RUNS = Path(runner.RUNS)
FLOWS = Path(runner.FLOWS)
# Hooks and fsmonitor come from config the sandbox cannot write (.git is
# read-only there); pinning them off as well costs nothing.
GIT = [
    "git",
    "-c", "core.hooksPath=/dev/null",
    "-c", "core.fsmonitor=false",
    "-c", "user.name=DuckTerm autopilot",
    "-c", "user.email=autopilot@duckterm.invalid",
    "-c", "commit.gpgsign=false",
]  # fmt: skip


@dataclass(frozen=True)
class RunDirs:
    root: Path

    @property
    def project(self) -> Path:
        return self.root / "repo"

    @property
    def scratch(self) -> Path:
        return self.root / "scratch"

    def mounts(self) -> tuple[Mount, ...]:
        p = self.project
        return (
            Mount(p, str(p)),
            Mount(p / ".git", str(p / ".git"), readonly=True),
            Mount(p / FLOWS, str(p / FLOWS), readonly=True),
            Mount(self.scratch, str(p / RUNS)),
        )


def prepare(source: str, ref: str, branch: str, root: Path) -> RunDirs:
    """Clone `ref` of `source` (a path or URL) into the run's directory and
    start the run branch there."""
    dirs = RunDirs(root)
    root.mkdir(parents=True, exist_ok=True)
    _git("clone", "-q", "--no-hardlinks", "--branch", ref, source, str(dirs.project))
    _git("-C", str(dirs.project), "checkout", "-q", "-b", branch)
    with (dirs.project / ".git" / "info" / "exclude").open("a") as f:
        f.write("\n.duckterm/\n")
    for d in (dirs.project / RUNS, dirs.project / FLOWS, dirs.scratch):
        d.mkdir(parents=True, exist_ok=True)
    return dirs


class AutopilotExecutor(runner.Executor):
    name = "autopilot"

    def __init__(self, box: Sandbox, dirs: RunDirs, token: str, branch: str) -> None:
        self.box = box
        self.dirs = dirs
        self.token = token
        self.branch = branch

    def run_process(
        self,
        argv: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        stdin: str | None,
        timeout: float,
    ) -> ProcessResult:
        _copy(self.dirs.project / RUNS, self.dirs.scratch, skip={"run.json", "run.lock"})
        # `timeout` inside the sandbox kills the step itself; killing only
        # the docker CLI here would leave it running.
        inner = ["timeout", "-k", "10", str(max(1, int(timeout))), *argv]
        try:
            proc = self.box.exec(
                inner,
                env={**env, **harness_env(self.token)},
                timeout=timeout + 30,
                stdin=stdin,
                workdir=str(cwd),
            )
        except subprocess.TimeoutExpired:
            return ProcessResult(124, "", "step_timeout")
        rel = env.get("SKILL_KIT_OUTPUT_DIR")
        if rel:
            out = Path(rel)
            _copy(self.dirs.scratch / out.relative_to(RUNS), self.dirs.project / out, skip=set())
        return ProcessResult(proc.returncode, proc.stdout, proc.stderr)

    def after_step(self, run: runner.Run, attempt: dict[str, Any]) -> None:
        """Commit the attempt's changes to the run branch before the runner
        writes its receipt. Pushing joins here when the git proxy exists."""
        project = str(self.dirs.project)
        if not _git("-C", project, "status", "--porcelain").strip():
            return
        _git("-C", project, "add", "-A")
        message = f"{run.state['flow']}: {attempt['step']}, attempt {attempt['attempt']}"
        _git("-C", project, "commit", "-q", "-m", message)


def _git(*args: str) -> str:
    proc = subprocess.run([*GIT, *args], capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed: {proc.stderr.strip()}")
    return proc.stdout


def _copy(src: Path, dst: Path, *, skip: set[str]) -> None:
    """Copy regular files from src to dst. Symbolic links are skipped: a link
    a step made would otherwise be followed on the host."""
    if not src.is_dir() or src.is_symlink():
        return
    for folder, dirs, files in os.walk(src, followlinks=False):
        here = Path(folder)
        dirs[:] = [d for d in dirs if not (here / d).is_symlink()]
        target = dst / here.relative_to(src)
        target.mkdir(parents=True, exist_ok=True)
        for name in files:
            path = here / name
            if name in skip or path.is_symlink() or not path.is_file():
                continue
            shutil.copyfile(path, target / name)
