"""What the server's /api/autopilot routes do, kept out of server.py. Each
function is blocking (files, docker, GitHub) and runs on a worker thread.
ValueError means a bad request, KeyError an unknown id."""

import time
from typing import Any

from duckterm import github_projects
from duckterm.autopilot import catalog, github_watch, runs, store

POLL_SECONDS = 120
# Per autopilot: when the GitHub watcher last looked, and what went wrong.
_watch: dict[str, dict[str, Any]] = {}


def in_workspace(item: dict[str, Any], workspace: str | None) -> bool:
    """A workspace includes its subfolders, as the workspace view does."""
    if workspace is None:
        return True
    own = str(item.get("workspace") or "")
    return own == workspace or own.startswith(workspace + "/")


def overview(workspace: str | None = None) -> dict[str, Any]:
    entries = [e for e in store.load() if in_workspace(e, workspace)]
    for entry in entries:
        entry["active"] = runs.active(entry["id"])
        entry["watch"] = _watch.get(entry["id"], {})
    return {
        "flows": [f.public() for f in catalog.flows()],
        "triggers": {
            kind: {"needs": needs, "available": kind in catalog.BUILT}
            for kind, needs in catalog.TRIGGERS.items()
        },
        "autopilots": entries,
        "runs": [r for r in runs.list_runs() if in_workspace(r, workspace)][:200],
        "summary": runs.summary(),
        "problems": runs.readiness(),
    }


def create(body: dict[str, Any]) -> dict[str, Any]:
    return store.create(body)


def update(autopilot_id: str, body: dict[str, Any]) -> dict[str, Any]:
    return store.update(autopilot_id, body)


def delete(autopilot_id: str) -> None:
    store.delete(autopilot_id)
    _watch.pop(autopilot_id, None)


def run_once(autopilot_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Start a run for one issue now, whatever its labels."""
    entry = store.get(autopilot_id)
    try:
        number = int(body.get("issue") or 0)
    except (TypeError, ValueError):
        number = 0
    if number <= 0:
        raise ValueError("Give an issue number")
    token, _ = github_projects.local_token()
    item = github_watch.issue(github_watch.api(token), store.issue_repo(entry), number)
    return {"run_id": runs.start(entry, "github-issue", item)}


def recover() -> None:
    runs.recover()


def run(run_id: str) -> dict[str, Any]:
    return runs.detail(run_id)


def summary() -> dict[str, Any]:
    return runs.summary()


def tick() -> None:
    """One pass of the GitHub issues trigger over every autopilot that is on."""
    entries = [e for e in store.load() if e["on"] and "github-issue" in e["triggers"]]
    if not entries:
        return
    try:
        token, _ = github_projects.local_token()
    except ValueError as exc:
        for entry in entries:
            _watch[entry["id"]] = {"checked_at": _now(), "error": str(exc)}
        return
    get = github_watch.api(token)
    for entry in entries:
        room = entry["max_runs"] - runs.active(entry["id"])
        try:
            ready = github_watch.poll(
                get,
                store.issue_repo(entry),
                entry["triggers"]["github-issue"]["labels"],
                store.root() / "seen" / f"{entry['id']}.json",
                max(0, room),
            )
            for item in ready:
                runs.start(entry, "github-issue", item)
            _watch[entry["id"]] = {"checked_at": _now()}
        except Exception as exc:  # noqa: BLE001 — one repository's failure must not stop the rest
            _watch[entry["id"]] = {"checked_at": _now(), "error": str(exc)[:300]}


def repositories() -> list[str]:
    try:
        listed = github_projects.list_repositories(1)
    except ValueError:
        return []
    return [str(r.get("full_name") or r.get("name")) for r in listed.get("repositories") or []]


def _now() -> int:
    return int(time.time() * 1000)
