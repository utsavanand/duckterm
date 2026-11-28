"""After a run's flow ships and its pull request is open: wait for CI, send a
failure back to a fix step, decide from the merge policy whether a person
approves, and merge. This runs on the DuckTerm server, which holds the GitHub
token; sandboxes never do.

The merge policy is the kit's scripts/policy.py, run here rather than in a
sandbox because its inputs include the autopilot's path rules, which live in
DuckTerm. Until auto-merge is turned on, every merge asks; while it is in
shadow mode a merge the policy would have made alone still asks, and each one
approved as it stood counts toward the 10 that let the owner turn it on
(issue-flow spec §10).
"""

import fnmatch
import json
import subprocess
import sys
import time
from collections.abc import Callable
from typing import Any

from duckterm.autopilot import catalog, github_push, store

CI_TIMEOUT = 60 * 60
CI_POLL = 30
# A repository with no CI reports nothing at all; workflows can take this long
# to register their first check run.
NO_CI_GRACE = 3 * 60
FIX_CI_MAX = 3


def wait_ci(
    repo: str,
    head: str,
    token: str,
    *,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.time,
    stop: Callable[[], bool] = lambda: False,
) -> tuple[str, str]:
    """("pass" | "fail" | "timeout" | "none" | "stopped", log) for CI on
    `head`. `stop` is asked before each look; a cancelled run stops waiting."""
    started = now()
    while True:
        if stop():
            return "stopped", ""
        result, log = github_push.checks(repo, head, token)
        if result in ("pass", "fail"):
            return result, log
        waited = now() - started
        if result == "none" and waited >= NO_CI_GRACE:
            return "none", ""
        if waited >= CI_TIMEOUT:
            return "timeout", ""
        sleep(CI_POLL)


def path_hits(files: list[str], rules: dict[str, list[str]]) -> list[dict[str, str]]:
    """Each changed file a path rule names, with the field it raises."""
    hits = []
    for field in store.RULE_FIELDS:
        for pattern in rules.get(field) or []:
            for f in files:
                if fnmatch.fnmatchcase(f, pattern):
                    hits.append({"field": field, "path": f})
    return hits


def merge_policy(
    spec_class: dict[str, Any] | None,
    diff_class: dict[str, Any] | None,
    hits: list[dict[str, str]],
    spec_gated: bool,
) -> list[str]:
    """Why a person must approve this merge; empty when the policy would merge
    alone. Unreadable output from the script counts as a reason."""
    script = catalog.KITS / "autopilot" / "scripts" / "policy.py"
    body = {
        "spec_class": spec_class,
        "diff_class": diff_class,
        "path_hits": hits,
        "spec_gated": spec_gated,
    }
    try:
        done = subprocess.run(
            [sys.executable, "-I", str(script), "merge"],
            input=json.dumps(body),
            capture_output=True,
            text=True,
            timeout=60,
        )
        reasons = json.loads(done.stdout.splitlines()[-1])["outputs"]["reasons"]
    except (OSError, ValueError, IndexError, KeyError, subprocess.SubprocessError):
        return ["the merge policy could not be read"]
    return [str(r) for r in reasons]


def merge_gate(
    reasons: list[str], mode: str, shadow_done: int, head: str, pr: dict[str, Any]
) -> dict[str, Any] | None:
    """The approval a merge waits for, or None when it merges alone."""
    if not reasons and mode == "on":
        return None
    shadow = not reasons and mode == "shadow"
    why = "; ".join(reasons) if reasons else "" if shadow else "you approve every merge"
    return {
        "step": "merge-gate",
        "question": f"Merge pull request #{pr['number']}?",
        "choices": ["approve", "reject"],
        "why": why,
        "shadow": shadow,
        "shadow_run": shadow_done + 1 if shadow else None,
        "head": head,
        "asked_at": int(time.time() * 1000),
        "shown": "",
    }


def count_shadow(autopilot_id: str, *, approved: bool) -> None:
    """A shadow merge approved as it stood adds one toward auto-merge; any
    rejection, or a head that moved before merging, starts the count over."""
    try:
        entry = store.get(autopilot_id)
    except KeyError:  # the autopilot was removed while the run waited
        return
    done = min(store.SHADOW_RUNS, int(entry.get("shadow_done") or 0) + 1) if approved else 0
    store.set_shadow(autopilot_id, done)
