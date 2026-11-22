"""The autopilot executor: the kit runner's steps run in a sandbox, the host
keeps the receipts.

The end-to-end test runs real Claude Code against the real API and is opt-in:
DUCKTERM_AUTOPILOT_REAL=1, docker running, the base image built, and a Claude
subscription token in the Keychain as `duckterm-autopilot-claude`.
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

from duckterm.autopilot import executor, sandbox
from duckterm.autopilot.executor import AutopilotExecutor, prepare
from duckterm.autopilot.sandbox import Gateway, Sandbox
from duckterm.kits import install, plan, runner
from duckterm.kits import manifest as kit_manifest

KIT = Path(__file__).parent / "greet-kit"


def test_copy_skips_links_and_receipts(tmp_path: Path) -> None:
    src, dst = tmp_path / "src", tmp_path / "dst"
    (src / "r1" / "write" / "1").mkdir(parents=True)
    (src / "r1" / "run.json").write_text("receipt")
    (src / "r1" / "write" / "1" / "out.md").write_text("output")
    secret = tmp_path / "secret"
    secret.write_text("host file")
    (src / "r1" / "write" / "1" / "leak.md").symlink_to(secret)
    (src / "r1" / "linked").symlink_to(tmp_path)
    executor._copy(src, dst, skip={"run.json"})
    assert (dst / "r1" / "write" / "1" / "out.md").read_text() == "output"
    assert not (dst / "r1" / "run.json").exists()
    assert not (dst / "r1" / "write" / "1" / "leak.md").exists()
    assert not (dst / "r1" / "linked").exists()


class _Box:
    """Answers `command -v` and `cat ~/.claude.json` as a sandbox would."""

    def __init__(self, binaries: set[str], claude_json: str) -> None:
        self.binaries = binaries
        self.claude_json = claude_json

    def exec(self, argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        script = argv[-1]
        if script.startswith("command -v"):
            found = any(f'"{b}"' in script for b in self.binaries)
            return subprocess.CompletedProcess(argv, 0 if found else 1, "", "")
        return subprocess.CompletedProcess(argv, 0, self.claude_json, "")


@pytest.mark.parametrize(
    ("binaries", "projects", "reason"),
    [
        (set(), {}, "claude-code is not installed in the sandbox image"),
        ({"claude"}, {}, "the sandbox has no Claude Code trust record for /run/repo"),
        ({"claude"}, {"/elsewhere": {"hasTrustDialogAccepted": True}}, "no Claude Code trust"),
        ({"claude"}, {"/run/repo": {"hasTrustDialogAccepted": True}}, None),
    ],
)
def test_harness_check_is_answered_by_the_sandbox_not_the_host(
    tmp_path: Path, binaries: set[str], projects: dict[str, object], reason: str | None
) -> None:
    box = _Box(binaries, json.dumps({"projects": projects}))
    ex = AutopilotExecutor(box, executor.RunDirs(tmp_path), "tok", "b")  # type: ignore[arg-type]
    got = ex.harness_ready("claude-code", Path("/run/repo"), hooks=True, limits=runner.Limits())
    if reason is None:
        assert got is None
    else:
        assert got is not None and reason in got


def _repo(path: Path) -> Path:
    path.mkdir()
    git = ["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    (path / "README.md").write_text("# demo\n")
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "-c", "commit.gpgsign=false", "commit", "-qm", "init"], check=True)
    return path


def test_prepare_clones_onto_a_run_branch_and_hides_duckterm_from_git(tmp_path: Path) -> None:
    dirs = prepare(str(_repo(tmp_path / "src")), "main", "autopilot/r1", tmp_path / "run")
    branch = subprocess.run(
        ["git", "-C", str(dirs.project), "branch", "--show-current"], capture_output=True, text=True
    ).stdout.strip()
    assert branch == "autopilot/r1"
    (dirs.project / ".duckterm" / "runs" / "x.json").write_text("{}")
    status = subprocess.run(
        ["git", "-C", str(dirs.project), "status", "--porcelain"], capture_output=True, text=True
    ).stdout
    assert status == ""


def test_a_real_flow_runs_in_a_sandbox_and_commits_to_the_run_branch(tmp_path: Path) -> None:
    if os.environ.get("DUCKTERM_AUTOPILOT_REAL") != "1" or not sandbox.docker_available():
        pytest.skip("set DUCKTERM_AUTOPILOT_REAL=1 with docker and the Keychain token")
    oauth = subprocess.run(
        [
            "security",
            "find-generic-password",
            "-a",
            os.environ["USER"],
            "-s",
            "duckterm-autopilot-claude",
            "-w",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    gateway = Gateway(name="duckterm-autopilot-gateway-e2e", secret_path=tmp_path / "secret")
    gateway.ensure(anthropic_key=None, openai_key=None, anthropic_oauth_token=oauth)
    run_id = "e2e-" + os.urandom(3).hex()
    branch = f"autopilot/{run_id}"
    dirs = prepare(str(_repo(tmp_path / "src")), "main", branch, tmp_path / "run")
    install.apply(plan.build(kit_manifest.load(KIT), dirs.project, ["claude-code"]))
    box = Sandbox.create(run_id, gateway=gateway.name, mounts=dirs.mounts())
    try:
        box.trust(str(dirs.project))
        token = gateway.mint(run_id, budget=500_000, ttl=1800)
        run = runner.start(
            dirs.project,
            "greet-kit/greet",
            harness="claude-code",
            executor=AutopilotExecutor(box, dirs, token, branch),
            limits=runner.Limits(yolo=True),
            executor_state={"run_host": "mac", "branch": branch, "sandbox_id": box.name},
        )
        state = json.loads((dirs.project / runner.RUNS / run.id / "run.json").read_text())
        assert state["state"] == "succeeded", state.get("failure")
        assert state["executor"] == "autopilot"
        assert [a["step"] for a in state["attempts"]] == ["write", "check"]
        assert state["attempts"][1]["choice"] == "ok"
        # The workspace step's change is committed to the run branch on the host.
        shown = subprocess.run(
            ["git", "-C", str(dirs.project), "show", f"{branch}:greeting.txt"],
            capture_output=True,
            text=True,
        ).stdout
        assert shown.strip() == "hello from autopilot"
        assert gateway.usage(token)["used"] > 0
        assert oauth not in box.exec(["env"]).stdout
    finally:
        box.destroy()
        sandbox.docker("rm", "-f", gateway.name, check=False)
