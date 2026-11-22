"""Flows put on autopilot, saved in DuckTerm's home (not in the repository):
<home>/autopilot/autopilots.json. One file, rewritten whole; there are a
handful of entries, edited from one server."""

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from duckterm.autopilot import catalog
from duckterm.github_projects import repository_name
from duckterm.helpers import instance

_LOCK = threading.Lock()
MAX_LABELS = 20


def root() -> Path:
    return instance.home() / "autopilot"


def _path() -> Path:
    return root() / "autopilots.json"


def load() -> list[dict[str, Any]]:
    try:
        data = json.loads(_path().read_text())
    except FileNotFoundError:
        return []
    return list(data.get("autopilots") or [])


def _save(entries: list[dict[str, Any]]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"autopilots": entries}, indent=2) + "\n")
    os.replace(tmp, path)


def get(autopilot_id: str) -> dict[str, Any]:
    for entry in load():
        if entry["id"] == autopilot_id:
            return entry
    raise KeyError(autopilot_id)


def create(body: dict[str, Any]) -> dict[str, Any]:
    base = {"id": "ap-" + uuid.uuid4().hex[:8], "on": True, "created_at": int(time.time() * 1000)}
    entry = _validated(body, base)
    with _LOCK:
        entries = load()
        entries.append(entry)
        _save(entries)
    return entry


def update(autopilot_id: str, body: dict[str, Any]) -> dict[str, Any]:
    with _LOCK:
        entries = load()
        for i, entry in enumerate(entries):
            if entry["id"] == autopilot_id:
                merged = {**entry, **body}
                updated = _validated(merged, entry)
                if "on" in body:
                    updated["on"] = bool(body["on"])
                entries[i] = updated
                _save(entries)
                return updated
    raise KeyError(autopilot_id)


def delete(autopilot_id: str) -> None:
    with _LOCK:
        entries = load()
        kept = [e for e in entries if e["id"] != autopilot_id]
        if len(kept) == len(entries):
            raise KeyError(autopilot_id)
        _save(kept)


def _validated(body: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    """The saved shape, from an untrusted request body. Raises ValueError with
    a message the app can show."""
    try:
        flow = catalog.find(str(body.get("flow") or ""))
    except KeyError:
        raise ValueError("Choose a flow") from None
    repo = repository_name(str(body.get("repo") or ""))
    raw = body.get("triggers")
    if not isinstance(raw, dict) or not raw:
        raise ValueError("Turn on at least one trigger")
    triggers: dict[str, dict[str, Any]] = {}
    for kind, settings in raw.items():
        if kind not in flow.triggers:
            raise ValueError(f"{flow.name} cannot be started by {kind}")
        if kind not in catalog.BUILT:
            raise ValueError(f"The {kind} trigger is not available yet")
        settings = settings if isinstance(settings, dict) else {}
        labels = settings.get("labels") or []
        if not isinstance(labels, list) or len(labels) > MAX_LABELS:
            raise ValueError(f"Use at most {MAX_LABELS} labels")
        triggers[kind] = {"labels": sorted({str(x).strip() for x in labels if str(x).strip()})}
    name = str(body.get("name") or "").strip()[:80] or f"{repo.split('/')[1]} {flow.name}"
    raw_approvals = body.get("approvals")
    approvals = raw_approvals if isinstance(raw_approvals, dict) else {}
    return {
        "id": base["id"],
        "on": bool(base.get("on", True)),
        "created_at": base["created_at"],
        "flow": flow.id,
        "name": name,
        "repo": repo,
        "triggers": triggers,
        "approvals": {
            "spec_on_visible": bool(approvals.get("spec_on_visible")),
            "keep_merge_approval": bool(approvals.get("keep_merge_approval")),
        },
        "budget_m": max(1, min(50, int(body.get("budget_m") or 5))),
        "max_runs": max(1, min(4, int(body.get("max_runs") or 2))),
    }
